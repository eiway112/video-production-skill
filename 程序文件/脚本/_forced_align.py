#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
强制对齐模块 — 已知旁白文本 + TTS 音频 → 句级真实时间戳

架构定位（音频优先时间轴 P0-a）：
  字幕时刻必须从音频物理波形派生，而不是从文本字符数估算。
  本模块用 faster-whisper（本地离线，word_timestamps=True）识别 TTS 音频，
  将识别字符流与已知旁白原文做序列对齐（known-text alignment），
  输出每个字幕分段在音频内的相对起止时刻（rel_start / rel_end）。

设计要点：
  - 旁白原文已知 → 对齐是"约束搜索"而非自由识别：即使 ASR 把
    "1200毫米" 读成 "一千二百毫米"，SequenceMatcher 锚定前后文匹配块，
    未匹配区间线性插值，误差仍被锚点封在亚秒级。
  - match_ratio < MIN_MATCH_RATIO 视为对齐不可靠 → 返回 None，
    调用方回落到 silencedetect gap 对齐 / 字符比例估算（保底链不变）。
  - match_ratio 是全局聚合量，抓不住局部灾难：ASR 整段漏识别首句时，
    其余区间仍可把匹配率抬过线，而首句时间戳全由插值造出。故另设分段面
    守卫——首句零匹配字符、任意句子时长塌缩（<10ms）均判不可靠 → None。
  - 模型加载一次复用（module-level cache）；HF_HUB_OFFLINE=1 强制离线，
    模型缓存缺失时优雅降级而不是卡在网络下载。

消费者：
  - enhance_video_audio.py::_build_timeline_manifest（TTS 步骤后生成
    _timeline_manifest.json 的 sentences[]）
  - 独立 CLI（自测 / 离线复核）：
    python _forced_align.py --wav tts_xxx_hq.wav --text-file narration.txt
"""

import io
import json
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

# 强制离线：模型已在本地 HF 缓存（Systran/faster-whisper-small），
# 不允许运行中触发网络下载（网络不稳定会让 TTS 步骤挂死）。
os.environ.setdefault("HF_HUB_OFFLINE", "1")

MODEL_SIZE = "small"          # 已验证本地缓存存在；中文句界对齐精度足够
MIN_MATCH_RATIO = 0.5         # 识别字符与原文匹配率低于此值 → 对齐不可信
# 起口守卫阈值（2026-10-20 修法批）：首句起点晚于音频真实语音起口的允许上限。
# 取 0.6s 的理由与终检 `audio_sync_rules.json → alignment` 的 p95 门同档：
# 字幕起点晚于语音 0.6s 以上即属"对不上"，这里提前在对齐侧拦一次。
MAX_ONSET_LAG_SECONDS = 0.6
# 判定"有语音"的窗口 RMS 阈值：≈-34dB。TTS 音频的句间静音实测落在
# -60~-40dB，语音段在 -25dB 上下，取中间值两侧都留得住余量。
SPEECH_ONSET_RMS = 0.02
_MODEL = None                 # 进程级模型缓存
# WhisperModel(“small”) 实际解析到的 HF 仓库；缓存目录名由它派生，不另写一份
MODEL_REPO_ID = f"Systran/faster-whisper-{MODEL_SIZE}"

# main() 内换包装的保活表（模块级非 CLI 路径不触及）：持旧防旧包装析构关底层
# buffer，持新防本轮新代被下个重绑点顶掉后引用归零（无需 gc.collect 即 finalize）。
# 理由详见 script_interface 内同名表。用例105 锁定。
_stdout_keepalive = []


def load_align_model():
    """加载 faster-whisper 模型（进程级单例）。失败返回 None（调用方降级）。"""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        return _MODEL
    except Exception as e:
        print(f"  [WARN] forced-align model unavailable ({e}) — fallback to gap detection")
        return None


def align_model_cache_root():
    """HF hub 缓存根目录（与 huggingface_hub 的环境变量优先级同序，不引新依赖）。"""
    env = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.environ.get("HF_HUB_CACHE")
    if env:
        return Path(env)
    hf_home = os.environ.get("HF_HOME")
    base = Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface"
    return Path(base) / "hub"


def align_model_cache_ready():
    """离线可加载性探测：HF_HUB_OFFLINE=1 下只认本地快照。返回 (就位?, 说明)。

    判据取 snapshots/*/model.bin 非空——权重缺位时 WhisperModel 抛
    "Cannot find an appropriate cached snapshot folder"，调用方
    （_build_timeline_manifest）会把**整项目**字幕时间戳落到 punct-gap 一级
    降级（合法但非主路径）。2026-09-18 那次交付即此形态，且当轮终检零告警，
    故把"缓存是否就位"提前到渲染前数十分钟暴露（preflight 只告警不阻断）。
    """
    root = align_model_cache_root()
    repo_dir = root / f"models--{MODEL_REPO_ID.replace('/', '--')}"
    if not repo_dir.exists():
        return False, f"{repo_dir} 不存在"
    weights = [p for p in sorted(repo_dir.glob("snapshots/*/model.bin"))
               if p.stat().st_size > 0]
    if not weights:
        return False, f"{repo_dir} 下无有效 model.bin 快照（HF_HUB_OFFLINE=1 不可加载）"
    return True, str(weights[0])


# 归一化：对齐只看"发音承载字符"（汉字/字母/数字），标点与空白剔除
_NORM_KEEP_RE = re.compile(r'[\u4e00-\u9fffA-Za-z0-9]')


def _normalize_chars(text):
    """返回 (norm_chars, orig_index_map)：剔除非发音字符，保留原文索引映射。"""
    chars = []
    index_map = []
    for i, ch in enumerate(text):
        if _NORM_KEEP_RE.match(ch):
            chars.append(ch.lower())
            index_map.append(i)
    return ''.join(chars), index_map


def segment_char_offsets(segments):
    """每个字幕分段在**归一化字符流**里的索引区间 [(start, end_exclusive), ...]。

    单一权威源：`align_scene` 用它切句，`cue_resolve` 用它把一枚字符时刻归到某句。
    两处各写一遍累加规则即两套索引——偏移一位就会让 cue 落在别的句上，
    且这种错在画面上表现为"亮早/亮晚一点"，没有任何门禁会响（A06 同族）。
    """
    offs, cur = [], 0
    for seg in segments or []:
        n = len(_normalize_chars(seg)[0])
        offs.append((cur, cur + n))
        cur += n
    return offs


def _transcribe_words(wav_path, model, initial_prompt=None):
    """识别音频，返回 [(word_text, start, end), ...]（保持时间升序）。"""
    segments, _info = model.transcribe(
        str(wav_path),
        language="zh",
        word_timestamps=True,
        beam_size=5,
        initial_prompt=initial_prompt,
        condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        for w in (seg.words or []):
            words.append((w.word, float(w.start), float(w.end)))
    return words


def _speech_onset(wav_path):
    """测音频里第一处语音起点（秒）。取不到返回 None（无从裁定）。

    用途：给"起口守卫"当基准。语音起口可直接从波形量出，而 ASR 的**首词
    时间戳**不准（实测同一份 wav 的首句起点在 0.000/0.340/2.260 之间跳），
    两者一比即知是对齐不准还是音频真静音——只看 ASR 的读数分不出这两者。

    口径：10ms 窗口 RMS，首次超过 SPEECH_ONSET_RMS 的窗口起点即语音起口。
    读不出文件／格式不支持／全静音 一律返回 None——调用方据此**跳过**守卫，
    不得因"测不出"把合格读数判成不合格（无从裁定不等于不合格）。
    """
    try:
        import wave
        import numpy as np
        with wave.open(str(wav_path), 'rb') as wf:
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
    except Exception:
        return None
    if sw != 2 or sr <= 0 or not raw or n_ch < 1:
        return None
    try:
        audio = np.frombuffer(raw, dtype=np.int16).reshape(-1, n_ch)
    except Exception:
        return None
    mono = audio.astype(np.float64).mean(axis=1) / 32768.0
    win = max(1, int(0.01 * sr))
    n_win = len(mono) // win
    if n_win == 0:
        return None
    env = np.sqrt((mono[:n_win * win].reshape(n_win, win) ** 2).mean(axis=1))
    hits = np.nonzero(env > SPEECH_ONSET_RMS)[0]
    if hits.size == 0:
        return None
    return round(float(hits[0] * win) / sr, 3)


def _hyp_char_stream(words):
    """把识别词序列展开为字符级时间流：[(char, start, end), ...]。

    词内各字符按词时长线性均分——中文单词通常 1-3 字，均分误差 <0.15s。
    """
    stream = []
    for word_text, w_start, w_end in words:
        norm, _ = _normalize_chars(word_text)
        if not norm:
            continue
        n = len(norm)
        span = max(w_end - w_start, 1e-3)
        for k, ch in enumerate(norm):
            c_start = w_start + span * k / n
            c_end = w_start + span * (k + 1) / n
            stream.append((ch, c_start, c_end))
    return stream


def _align_char_times(ref_norm, hyp_stream):
    """SequenceMatcher 对齐 → 每个 ref 归一化字符的 (start, end)。

    未匹配 ref 字符（ASR 误读/数字改写区间）在相邻匹配锚点之间线性插值。
    返回 (starts, ends, match_ratio, matched_flags)；无任何匹配时
    matched_flags 为 None。
    """
    hyp_norm = ''.join(c for c, _, _ in hyp_stream)
    sm = SequenceMatcher(None, ref_norm, hyp_norm, autojunk=False)
    n_ref = len(ref_norm)
    starts = [None] * n_ref
    ends = [None] * n_ref
    matched_flags = [False] * n_ref
    matched = 0
    for block in sm.get_matching_blocks():
        for k in range(block.size):
            ri = block.a + k
            hi = block.b + k
            starts[ri] = hyp_stream[hi][1]
            ends[ri] = hyp_stream[hi][2]
            matched_flags[ri] = True
        matched += block.size
    if matched == 0:
        return None, None, 0.0, None

    # 未匹配区间：在左右锚点之间按字符数线性插值
    i = 0
    while i < n_ref:
        if starts[i] is not None:
            i += 1
            continue
        j = i
        while j < n_ref and starts[j] is None:
            j += 1
        left_t = ends[i - 1] if i > 0 else (hyp_stream[0][1] if hyp_stream else 0.0)
        right_t = starts[j] if j < n_ref else (hyp_stream[-1][2] if hyp_stream else left_t)
        span = max(right_t - left_t, 0.0)
        gap_n = j - i
        for k in range(gap_n):
            starts[i + k] = left_t + span * k / gap_n
            ends[i + k] = left_t + span * (k + 1) / gap_n
        i = j

    # 单调性兜底（插值/锚点边缘可能出现微小倒退）
    for k in range(1, n_ref):
        if starts[k] < starts[k - 1]:
            starts[k] = starts[k - 1]
        if ends[k] < starts[k]:
            ends[k] = starts[k]
    return starts, ends, matched / n_ref, matched_flags


def align_scene(wav_path, text, segments, model=None):
    """对单场景 TTS 音频做强制对齐，产出每个字幕分段的相对时刻。

    Args:
        wav_path: 场景 TTS 音频（44.1kHz wav）
        text: 该场景旁白原文（仅用作 ASR initial_prompt）
        segments: 字幕分段列表（由 _split_text_to_segments 产出）。
                  对齐基准是分段拼接后的发音字符流，空白/标点差异不影响索引
        model: 复用的 WhisperModel；None 时自动加载

    Returns:
        {"sentences": [{"text", "rel_start", "rel_end"}, ...],
         "match_ratio": float, "method": "asr_forced",
         "norm": <对齐基准的归一化字符流>,
         "chars": [[rel_start, rel_end, matched01], ...]}   # 与 norm 逐字符同长
        或 None（模型不可用 / 匹配率过低 / 首句零匹配 / 存在塌缩零长句
        → 调用方降级）

    `chars` 是词级 cue 动画的数据源（2026-10-12 词级 cue 批）。三点不得误读：
      - **它一直在被计算**：`_align_char_times` 本就返回逐字符 starts/ends/matched_flags，
        旧实现切完句即丢弃，故本批是"把已有数据接出去"，不是新算法、不改任何既有裁定。
      - `matched=0` 的字符时间是**插值造出来的**（未匹配区间在左右锚点间线性均分，
        `_align_char_times` 的 166-186 行），只能当"大致时候"用；驱动画面须由消费方
        按旗标裁定（见 cue_resolve），不得把插值时刻当实测。
      - `sentences[]` 的字面输出与顺序逐字未动（用例锁定），句级消费方（step5 字幕、
        media_qa 对齐检查）零变化。
    """
    if model is None:
        model = load_align_model()
    if model is None:
        return None

    # 对齐基准从分段拼接构建（而非原文）：分段器会 strip 空白，
    # 若用原文字符索引会与分段边界错位；归一化只保留发音字符，
    # 因此 normalize(''.join(segments)) 与 normalize(text) 等价。
    seg_norms = [_normalize_chars(seg)[0] for seg in segments]
    ref_norm = ''.join(seg_norms)
    if not ref_norm:
        return None

    def _attempt(prompt, tag):
        """一次 ASR + 字符对齐。取数失败返回 None（区别于"跑通但不可靠"）。"""
        try:
            words = _transcribe_words(wav_path, model, initial_prompt=prompt)
        except Exception as e:
            print(f"  [WARN] forced-align transcribe failed on {Path(wav_path).name}: {e}")
            return None
        stream = _hyp_char_stream(words)
        if not stream:
            print(f"  [WARN] forced-align empty hypothesis ({tag}) on "
                  f"{Path(wav_path).name}")
            return None
        out = _align_char_times(ref_norm, stream)
        if out[0] is None:
            # 零字符匹配：ASR 转写与参考文本毫无交集。这一形态此前**静默**返回
            # None（无任何告警），调用方只看到"对齐失败"而拿不到原因；
            # 且它与"比例不足"走的是两条路——比例不足会去 prompt 重试，
            # 零匹配直接放弃，故整场静默降级为 punct-gap。
            print(f"  [WARN] forced-align zero character overlap ({tag}) on "
                  f"{Path(wav_path).name} — ASR 与参考文本无交集")
            return None
        return out

    # 重试序列（2026-10-20 修法批）：[带 prompt, 去 prompt]
    #
    # 存在理由（实测，非推断）：ASR 同一段音频**逐次读数会漂**——同一份
    # build-expert-overview 第 2 场 wav，6 次对齐实测 3 次返回 None（零字符
    # 匹配或空假设）、3 次成功（ratio 1.0/1.0/0.787），成功者的首句起点还在
    # 0.000/0.340/2.260 之间跳。旧实现只对"比例不足"重试，**零匹配这条
    # 静默路径一次都不重试**，于是同一份音频逐轮对齐结果不同：本轮成功、
    # 下轮静默降级 punct-gap，终检的 asr_forced 占比随之忽高忽低（判据阈值
    # 0.8），且字幕时刻在重对齐轮次里跳变——这正是"转场节奏"读起来不稳的
    # 一条来源。
    #
    # 形态不变、判据不放宽：每次尝试都过同一道 MIN_MATCH_RATIO 闸门与同一组
    # 分段面守卫，只是把"零匹配""守卫不合格"这些形态也纳入这唯一一次重试
    # （旧实现对它们直接放弃，一轮定生死）。尝试次数仍是 2——既有断言锁着
    # "重试不得退化成 ASR 循环"。
    onset = _speech_onset(wav_path)

    def _build_and_check(starts, ends, matched_flags):
        """由一次对齐结果构句并过全部可靠性守卫；不合格返回 None（换形态重试）。

        守卫全部搬进尝试序列是本批的关键：守卫判"不可靠"时**换一种 ASR 形态
        再试**，而不是整场降级——同一份音频换一种形态常常就好了。
        """
        sentences = []
        seg_matched_counts = []
        # 索引区间取自单一权威源 `segment_char_offsets`（与 cue_resolve 同一函数），
        # 不再在本函数内维护第二个 norm_cursor 累加器。
        for seg, (a, b) in zip(segments, segment_char_offsets(segments)):
            n = b - a
            if n == 0:
                # 纯标点/空白分段（理论不出现）：沿用上一段结束时刻
                prev_end = sentences[-1]["rel_end"] if sentences else 0.0
                sentences.append({"text": seg, "rel_start": prev_end, "rel_end": prev_end})
                seg_matched_counts.append(0)
                continue
            rel_start = starts[a]
            rel_end = ends[b - 1]
            seg_matched_counts.append(sum(matched_flags[a:b]))
            sentences.append({
                "text": seg,
                "rel_start": round(rel_start, 3),
                "rel_end": round(max(rel_end, rel_start), 3),
            })

        # 可靠性守卫（2026-09-19 修订01 复盘）：match_ratio 是全局聚合量，
        # ASR 整段漏识别局部灾难时仍可过线，须按分段面裁定。

        # 前缀整段未匹配：第一句发音字符零匹配 → ASR 把开头整段吞了
        # （场景 4 实证：转写从 5.94s 才起口，首句词流缺失），其时间戳全部
        # 由"0 → 首个锚点"线性插值造出，字幕窗口与语音起口无关。
        if seg_norms and seg_norms[0] and seg_matched_counts and seg_matched_counts[0] == 0:
            print(f"  [WARN] forced-align first segment has zero matched chars "
                  f"({Path(wav_path).name}) — ASR dropped prefix, unreliable")
            return None

        # 分段间单调性 + 逐句时长校验（2026-09-19 修订01 复盘补强）
        for k in range(1, len(sentences)):
            if sentences[k]["rel_start"] < sentences[k - 1]["rel_end"] - 0.5:
                # 时间大幅倒退 → 对齐内部矛盾，判为不可靠
                print(f"  [WARN] forced-align non-monotonic segment times "
                      f"({Path(wav_path).name}) — unreliable")
                return None
        for s in sentences:
            # 塌缩零长句：正常语音任意字符 ≥50ms，中文字幕分段最短亦 >0.3s，
            # 亚 10ms 只可能是"两端都插值到同一个锚点"的失败信号，不可能是合法边界。
            if s["rel_end"] - s["rel_start"] < 0.01:
                print(f"  [WARN] forced-align zero-length sentence "
                      f"({Path(wav_path).name}) @ {s['rel_start']:.2f}s — "
                      f"unreliable")
                return None

        # 起口守卫（2026-10-20 修法批）：首句起点不得明显晚于音频的真实语音起口。
        #
        # 存在理由（实测）：ASR 的**首词时间戳**不准——同一份 wav 六次对齐，
        # 首句起点在 0.000/0.340/2.260 之间跳。2.260 那次意味着首句整段字幕
        # 迟到 2 秒多、字级 cue 也跟着落在错处。语音起口本身可以直接从波形
        # 量出来（`_speech_onset`），两者一比即知是对齐不准而非音频真静音。
        # 量不到起口（文件读不出/非 wav）即无从裁定，跳过本守卫——不得因
        # "测不出"把合格读数判成不合格。
        if onset is not None and sentences and                 sentences[0]["rel_start"] > onset + MAX_ONSET_LAG_SECONDS:
            print(f"  [WARN] forced-align first segment starts "
                  f"{sentences[0]['rel_start'] - onset:.2f}s after speech onset "
                  f"({Path(wav_path).name}) — ASR mis-timed the opening, unreliable")
            return None
        return sentences

    # 尝试序列：**两种形态，各一次**（带 prompt → 去 prompt）。
    #
    # 形态数锁 2 是既有断言（用例 forced_align_segment_face_guards：
    # "exactly one retry, no ASR loop"）——重试不得退化成 ASR 循环。
    # 本批把"零匹配""守卫不合格"这些形态也纳入这唯一一次重试（旧实现对它们
    # 直接放弃，一轮定生死），而不是增加尝试次数。
    sentences = None
    ratio = 0.0
    starts = ends = matched_flags = None
    forms = [(text[:180], "with prompt"), (None, "no prompt")]
    for idx, (prompt, tag) in enumerate(forms):
        last = (idx == len(forms) - 1)
        # 出口顺序（既有断言锁）：先点名重试、再点名降级——只印降级说明第二次
        # 结果从未被裁定。末次尝试不再印"重试"（没有下一次了），直接印降级。
        verdict = ("unreliable, fallback" if last else "retry without initial_prompt")
        aligned = _attempt(prompt, tag)
        if aligned is None:
            # 零匹配 / 空假设 / 转写异常：旧实现此处直接 return None（整场静默
            # 降级 punct-gap），本批让它走那唯一一次"去 prompt"重试。
            print(f"  [WARN] forced-align attempt unusable ({tag}) on "
                  f"{Path(wav_path).name} — {verdict}")
            continue
        starts, ends, ratio, matched_flags = aligned
        if ratio < MIN_MATCH_RATIO:
            # whisper 把参考文本当成"已经说过"：回显提示词的尾巴、吞掉音频开头的
            # 词流（2026-09-24 _修订01 实测：同批 wav 带 prompt 0.47/0.27，去 prompt
            # 0.86/0.88），整场时间戳退化为线性插值。换形态重试；重试不放宽判据，
            # 只救被 prompt 自己打掉的那类失败。
            print(f"  [WARN] forced-align match ratio {ratio:.2f} < {MIN_MATCH_RATIO} "
                  f"({Path(wav_path).name}) — {verdict}")
            continue
        built = _build_and_check(starts, ends, matched_flags)
        if built is None:
            # 分段面守卫判不可靠：换形态再试一次，而不是整场降级。
            print(f"  [WARN] forced-align segment guards failed on "
                  f"{Path(wav_path).name} — {verdict}")
            continue
        sentences = built
        break
    if sentences is None:
        print(f"  [WARN] forced-align unreliable, fallback ({Path(wav_path).name}) "
              f"— {len(forms)} attempts exhausted")
        return None

    # 字符级流随结果一并出（词级 cue 的数据源；与 ref_norm 逐字符同长）。
    # matched_flags 为 None 只发生在"无任何匹配"分支，那已在上面 return None 处拦下。
    chars = [[round(float(starts[i]), 3), round(float(ends[i]), 3),
              1 if matched_flags[i] else 0] for i in range(len(ref_norm))]
    return {"sentences": sentences, "match_ratio": round(ratio, 3),
            "method": "asr_forced", "norm": ref_norm, "chars": chars}


def main():
    """独立 CLI：对单个 wav + 文本做对齐并打印 JSON（自测/离线复核用）。"""
    import argparse
    _stdout_keepalive.append(sys.stdout)   # 持旧：被本次顶掉的那一代
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    _stdout_keepalive.append(sys.stdout)   # 持新：本次造出的这一代
    parser = argparse.ArgumentParser(description="Forced alignment: TTS wav + known text -> sentence times")
    parser.add_argument("--wav", required=True)
    parser.add_argument("--text", help="narration text inline")
    parser.add_argument("--text-file", help="narration text from file (utf-8)")
    parser.add_argument("--segments-json", help="optional JSON array of pre-split segments")
    args = parser.parse_args()

    text = args.text or ""
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
    if not text:
        print("ERROR: --text or --text-file required")
        return 1

    if args.segments_json:
        segments = json.loads(Path(args.segments_json).read_text(encoding="utf-8"))
    else:
        # 与 enhance_video_audio 相同的分段器，保证 CLI 结果与流水线一致
        sys.path.insert(0, str(Path(__file__).parent))
        from enhance_video_audio import _split_text_to_segments
        segments = _split_text_to_segments(text)

    result = align_scene(args.wav, text, segments)
    if result is None:
        print(json.dumps({"error": "alignment failed"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
