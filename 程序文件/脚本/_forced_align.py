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
_MODEL = None                 # 进程级模型缓存
# WhisperModel(“small”) 实际解析到的 HF 仓库；缓存目录名由它派生，不另写一份
MODEL_REPO_ID = f"Systran/faster-whisper-{MODEL_SIZE}"


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
         "match_ratio": float, "method": "asr_forced"}
        或 None（模型不可用 / 匹配率过低 / 首句零匹配 / 存在塌缩零长句
        → 调用方降级）
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

    def _attempt(prompt):
        """一次 ASR + 字符对齐。取数失败返回 None（区别于"跑通但不可靠"）。"""
        try:
            words = _transcribe_words(wav_path, model, initial_prompt=prompt)
        except Exception as e:
            print(f"  [WARN] forced-align transcribe failed on {Path(wav_path).name}: {e}")
            return None
        stream = _hyp_char_stream(words)
        if not stream:
            return None
        return _align_char_times(ref_norm, stream)

    aligned = _attempt(text[:180])
    if aligned is None:
        return None
    starts, ends, ratio, matched_flags = aligned
    if starts is None or ratio < MIN_MATCH_RATIO:
        # whisper 把参考文本当成"已经说过"：回显提示词的尾巴、吞掉音频开头的
        # 词流（2026-09-24 _修订01 实测：同批 wav 带 prompt 0.47/0.27，去 prompt
        # 0.86/0.88），整场时间戳退化为线性插值。去 prompt 重试一次；两次都不
        # 过线才落降级链——重试不放宽判据，只救被 prompt 自己打掉的那类失败。
        print(f"  [WARN] forced-align match ratio {ratio:.2f} < {MIN_MATCH_RATIO} "
              f"({Path(wav_path).name}) — retry without initial_prompt")
        aligned = _attempt(None)
        if aligned is None:
            return None
        starts, ends, ratio, matched_flags = aligned
    if starts is None or ratio < MIN_MATCH_RATIO:
        print(f"  [WARN] forced-align match ratio {ratio:.2f} < {MIN_MATCH_RATIO} "
              f"({Path(wav_path).name}) — unreliable, fallback")
        return None

    sentences = []
    seg_matched_counts = []
    norm_cursor = 0
    for seg, seg_norm in zip(segments, seg_norms):
        n = len(seg_norm)
        if n == 0:
            # 纯标点/空白分段（理论不出现）：沿用上一段结束时刻
            prev_end = sentences[-1]["rel_end"] if sentences else 0.0
            sentences.append({"text": seg, "rel_start": prev_end, "rel_end": prev_end})
            seg_matched_counts.append(0)
            continue
        rel_start = starts[norm_cursor]
        rel_end = ends[norm_cursor + n - 1]
        seg_matched_counts.append(sum(matched_flags[norm_cursor:norm_cursor + n]))
        norm_cursor += n
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
              f"({Path(wav_path).name}) — ASR dropped prefix, unreliable, fallback")
        return None

    # 分段间单调性 + 逐句时长校验（2026-09-19 修订01 复盘补强）
    for k in range(1, len(sentences)):
        if sentences[k]["rel_start"] < sentences[k - 1]["rel_end"] - 0.5:
            # 时间大幅倒退 → 对齐内部矛盾，判为不可靠
            print(f"  [WARN] forced-align non-monotonic segment times "
                  f"({Path(wav_path).name}) — unreliable, fallback")
            return None
    for s in sentences:
        # 塌缩零长句：正常语音任意字符 ≥50ms，中文字幕分段最短亦 >0.3s，
        # 亚 10ms 只可能是"两端都插值到同一个锚点"的失败信号，不可能是合法边界。
        if s["rel_end"] - s["rel_start"] < 0.01:
            print(f"  [WARN] forced-align zero-length sentence "
                  f"({Path(wav_path).name}) @ {s['rel_start']:.2f}s — "
                  f"unreliable, fallback")
            return None

    return {"sentences": sentences, "match_ratio": round(ratio, 3), "method": "asr_forced"}


def main():
    """独立 CLI：对单个 wav + 文本做对齐并打印 JSON（自测/离线复核用）。"""
    import argparse
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
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
