#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
HyperFrames 视频后处理工作流 — TTS旁白 + BGM + 字幕烧录

适配新视频时只需修改以下三处:
  1. SCENES: 场景列表 (scene_id, start_time, end_time, narration_text)
  2. VIDEO_DURATION: 视频总时长（秒）
  3. get_paths() 中的文件名

前置条件:
  - HTML 模板每个场景必须包含 <div class="subtitle-safe"></div>
  - .sc 的 padding-bottom >= 150px（为字幕预留空间）
  - 字幕样式参数见 step6_burn_subtitles() 中的 style 变量
"""

import asyncio
import subprocess
import sys
import os
import io
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import re
import hashlib
from datetime import datetime
from pathlib import Path

# Import shared GSAP time resolution utilities (T-block aware)
sys.path.insert(0, str(Path(__file__).parent))
from _gsap_time_utils import parse_t_block, resolve_line_time, resolve_script_times, parse_scene_map
from _narration_lint import scan_srt_file as _lint_scan_srt, format_findings as _lint_format
from _script_env import ROOT as WF_ROOT, HTML_BASE as WF_HTML_BASE
import _forced_align as _fa  # 叶子模块（faster_whisper 仅在 load 时导入）；版本面消费 MODEL_SIZE

import argparse


# === Env loader ===
# 模块 import 时按顺序加载 openmontage.env → openmontage.env.local，
# .local 覆盖同名变量。只在 os.environ 未设置时注入（shell 环境变量优先级最高）。
# 目的：让 DASHSCOPE_API_KEY 等敏感变量放在 gitignored 的 .env.local 里也能被脚本读到。
def _load_env_files():
    cfg_dir = Path(__file__).resolve().parent.parent / "配置"
    for name in ("openmontage.env", "openmontage.env.local"):
        p = cfg_dir / name
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                # 占位符（包含 < 或 > ）直接跳过，不污染环境变量
                if not k or not v or v.startswith('<') or v.endswith('>'):
                    continue
                # .local 优先于 .env；但 shell 已存在的变量不覆盖
                if name.endswith('.local') or k not in os.environ:
                    os.environ[k] = v
        except Exception as e:
            print(f"[WARN] Failed to load {p.name}: {e}")

_load_env_files()

# === TTS 引擎参数 ===
# tts_engine 控制使用哪个 TTS 后端：
#   - "qwen"（默认）：阿里云百炼 DashScope 的 CosyVoice / Qwen-Audio-TTS（需 DASHSCOPE_API_KEY）
#   - "edge"：Edge-TTS（旧默认，无需 Key，仅存量项目显式声明保留）
# 引擎不自动 fallback — AGENTS.md 明令禁止跨引擎自动降级（造成音色不一致）。
TTS_ENGINE = "qwen"
VOICE = "longanling_v3"       # Qwen 默认音色：龙安灵V3（思维灵动女，cosyvoice-v3-flash）
# QWEN_MODEL 留空 = 按音色自动推断（_infer_qwen_model）；config 显式声明 qwen_model 为权威。
# 2026-08-06 实测：v3 系列走标准 dashscope 端点即可（旧注释"需专属域名"不成立）。
QWEN_MODEL = ""
RATE = "+0%"                  # Edge 语法，Qwen 分支会自动换算成 float（0.5-2.0）
PITCH = "+0Hz"
TTS_INSTRUCTION = ""          # config tts_instruction：Instruct 音色的情感/场景指令（如 longanyang/longanhuan）
SAMPLE_RATE = 44100

# 官方音色规格（2026-08-06 核对）：支持 Instruct 情感指令的音色仅这几个，
# 其余音色（含默认 longanling_v3）声明 instruction 无效，加载配置时告警。
_INSTRUCT_CAPABLE_VOICES = {"longanyang", "longanhuan", "longhuhu_v3"}


def _infer_qwen_model(voice):
    """按音色推断 CosyVoice 模型——音色与模型严格配对（官方约束，不得混用）。

    v3 专属音色（_v3 后缀 / longanyang / longanhuan）→ cosyvoice-v3-flash；
    其余 v2 音色（longanling 等存量项目声明）→ cosyvoice-v2，保证既有项目零变化。
    """
    v = (voice or "").strip()
    if v.endswith("_v3") or v in ("longanyang", "longanhuan"):
        return "cosyvoice-v3-flash"
    return "cosyvoice-v2"

# === 默认配置 (CRM视频)，可通过 --config JSON 覆盖 ===
# 格式: (场景ID, 起始秒, 结束秒, 旁白文本)
SCENES = [
    (1, 0.0, 9.5,
     "客户线索越来越多，跟进却越来越难？你需要一个轻量化的销售工作台。"),
    (2, 9.5, 19.5,
     "五大核心设计原则：轻录入优先，围绕销售动作，AI辅助不替代，报价工具复用，双端可用。"),
    (3, 19.5, 30.5,
     "首页工作台一目了然，今日待跟进、高意向客户、本周新增线索，关键数据实时掌握，待办事项清晰明了。"),
    (4, 30.5, 42.5,
     "客户管理与销售推进，从新线索到成交，每一步状态清晰可见。客户卡片记录完整信息，跟进动作有据可依。"),
    (5, 42.5, 54.5,
     "AI智能提取，上传微信截图或通话录音，自动识别客户信息、需求和意向等级，减少手动录入，一键确认写入。"),
    (6, 54.5, 67.0,
     "用最少录入，判断今天最该跟进谁。减少录入，减少遗忘，聚焦价值。从获客到成交，全流程高效运转。"),
]

VIDEO_DURATION = 70.0  # includes 3s cover scene
COVER_DURATION = 0.0  # auto-detected from HTML data-cover-duration, or set via config
BGM_ENABLED = True  # config 可用 "bgm_enabled": false 关闭合成 BGM（低频 drone 容易被感知为嗡嗡声）
SUBTITLE_DISPLAY_REPLACEMENTS = {}  # 字幕显示层替换（如 毫米→mm），不影响 TTS 朗读文本

# === P0 类型体系（2026-07-29，eiway-122-wall 纯BGM项目逃逸教训） ===
VIDEO_TYPE = ""         # config "video_type"：视频类型声明（如 product_showcase），日志/报告可读
TTS_ENABLED = True      # config "tts_enabled"：false = 纯BGM无旁白路径（默认 true，既有项目零变化）
BGM_SOURCE = None       # config "bgm_source" 解析后的外部 BGM 文件 Path（纯BGM路径必填）
AUDIO_BITRATE = "256k"  # config "audio_bitrate"：成片 AAC 码率（现状默认 256k 不变）


def _coerce_bool(value, default=True):
    """配置布尔的安全解析：JSON 布尔直接用；字符串 "false"/"0"/"no" 视为 False。

    防御 bool("false") is True 的经典陷阱（手写配置可能把布尔写成字符串）。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


def _tts_cache_key(text):
    """Compute short hash from narration text for cache keying.
    
    Includes engine/model/voice/rate/pitch in hash. Any change to these
    parameters (including switching TTS_ENGINE between 'qwen' and 'edge')
    yields a fresh cache entry — old audio is never silently reused with
    a different engine, which would produce voice inconsistency across
    scenes if a project were partially re-rendered after switching.
    """
    normalized = re.sub(r'\s+', '', text.strip())
    model_tag = QWEN_MODEL if TTS_ENGINE == "qwen" else "edge"
    cache_input = f"{normalized}|{TTS_ENGINE}|{model_tag}|{VOICE}|{RATE}|{PITCH}|{TTS_INSTRUCTION or ''}"
    return hashlib.sha256(cache_input.encode('utf-8')).hexdigest()[:10]


def _get_tts_hq_path(scene_id, text, tts_dir):
    """Get the hq WAV path for a scene based on text hash."""
    h = _tts_cache_key(text)
    return tts_dir / f"tts_{h}_hq.wav"


def _get_tts_hash_from_manifest(manifest, scene_id, text):
    """解析场景对应的 TTS hash——**当前旁白文本是唯一权威源**。

    历史教训（soundproof-craft 事故）：旧实现 manifest 优先——只要 manifest
    有条目就返回旧 hash，旁白文本修订后仍然复用旧文案的音频，产出
    "音轨读旧文案、字幕显新文案" 的成品，且所有时长/边界校验全部通过
    （旧音频文件真实存在、时长合法）。

    现在恒返回当前文本的 hash：文本一改 → hash 变 → 旧文件不再匹配 →
    调用方的存在性检查(step3/step5/step7)硬失败，强制先重跑 TTS。
    manifest 降级为记录（用于对照告警），不再是查找依据。
    """
    h = _tts_cache_key(text)
    entry = manifest.get(str(scene_id))
    if isinstance(entry, dict) and entry.get('hash') and entry['hash'] != h:
        print(f"  [WARN] Scene {scene_id}: narration text changed since last TTS run "
              f"(manifest hash {entry['hash'][:6]} != current {h[:6]}) — "
              f"cached audio is STALE, TTS must be regenerated.")
    return h


def _load_tts_manifest(temp_dir):
    """Load TTS cache manifest mapping scene_id to hash."""
    manifest_path = temp_dir / "tts_manifest.json"
    if manifest_path.exists():
        with open(manifest_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_tts_manifest(manifest, temp_dir):
    """Save TTS cache manifest."""
    manifest_path = temp_dir / "tts_manifest.json"
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _load_audio_sync_rules():
    """Load config/quality/audio_sync_rules.json with defaults.

    Kept in sync with pipeline_runner._load_audio_sync_rules() — both scripts
    read the same file, so pipeline behavior and post-pipeline validation
    stay aligned even when the file is edited.
    """
    rules_path = (
        Path(__file__).resolve().parents[1] / "配置" / "config" / "quality"
        / "audio_sync_rules.json"
    )
    defaults = {
        "dead_air": {"max_seconds_per_scene": 3.0, "fail_on_violation": True},
        "shrink": {"enabled_by_default": True, "shrink_margin_seconds": 1.0},
        "sentence_pause": {"min_seconds": 0.4},
    }
    if not rules_path.exists():
        return defaults
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        for k, v in loaded.items():
            if not k.startswith("$"):
                defaults[k] = v
        return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


_VIDEO_QUALITY_RULES_CACHE = None


def _load_video_quality_rules(rules_path=None):
    """Load config/quality/video_quality_rules.json with defaults.

    码率阈值单一权威源（双档制），media_qa_gate 交付终检读同一份文件。
    读不到时回退默认双档 fail=200/warn=500 并打印告警。
    文件声明按原样返回（不与默认双档键合并），保证旧单档键
    min_video_bitrate_kbps 的兼容路径可被 _resolve_bitrate_thresholds 识别。
    显式传入 rules_path 时绕过缓存（供回归测试验证配置生效）。
    """
    global _VIDEO_QUALITY_RULES_CACHE
    use_cache = rules_path is None
    if use_cache:
        if _VIDEO_QUALITY_RULES_CACHE is not None:
            return _VIDEO_QUALITY_RULES_CACHE
        rules_path = (
            Path(__file__).resolve().parents[1] / "配置" / "config" / "quality"
            / "video_quality_rules.json"
        )
    defaults = {"min_video_bitrate_kbps_fail": 200, "min_video_bitrate_kbps_warn": 500}
    result = defaults
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        declared = {k: v for k, v in loaded.items() if not k.startswith("$")}
        if declared:
            result = declared
    except (json.JSONDecodeError, OSError):
        print(f"  [WARN] 无法读取 {Path(rules_path).name}，视频码率阈值回退默认 fail=200/warn=500kbps")
    if use_cache:
        _VIDEO_QUALITY_RULES_CACHE = result
    return result


def _resolve_bitrate_thresholds(rules):
    """解析码率双档阈值，返回 (fail_kbps, warn_kbps, legacy_single)。

    双档键优先：实测 < fail 档 → 判定渲染失败/内容缺失（阻断）；
    fail~warn 之间 → 低码率预警不阻断（纯文字动画码率天然偏低）。
    仅存在旧单档键 min_video_bitrate_kbps 时按旧口径单档阻断
    （legacy_single=True，调用方提示升级配置），不崩溃。
    与 media_qa_gate._resolve_bitrate_thresholds 保持同口径。
    """
    fail_k = rules.get("min_video_bitrate_kbps_fail")
    warn_k = rules.get("min_video_bitrate_kbps_warn")
    if fail_k is not None or warn_k is not None:
        fail_v = int(fail_k) if fail_k is not None else 300
        warn_v = int(warn_k) if warn_k is not None else 500
        return fail_v, max(fail_v, warn_v), False
    legacy = rules.get("min_video_bitrate_kbps")
    if legacy is not None:
        return int(legacy), int(legacy), True
    return 300, 500, False


_TTS_CONCURRENCY_DEFAULT = 3
_TTS_CONCURRENCY_MAX = 5


def _load_tts_concurrency(cfg_path=None):
    """Read tts_concurrency from config/system/hyperframes_config.json.

    P2-01 TTS 并发数单一权威源。回退+告警模式（同 P1 阈值治理）：
    文件缺失/键缺失/值非法一律回退默认 3 并打印一行告警；
    硬上限 5（TTS 服务限流保护），超限按 5 执行并告警。
    显式传入 cfg_path 供测试注入。
    """
    if cfg_path is None:
        cfg_path = (
            Path(__file__).resolve().parents[1] / "配置" / "config" / "system"
            / "hyperframes_config.json"
        )
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        n = loaded["tts_concurrency"]
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ValueError(f"tts_concurrency={n!r}")
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        print(f"  [WARN] 无法从 {Path(cfg_path).name} 读取合法 tts_concurrency，"
              f"回退默认 {_TTS_CONCURRENCY_DEFAULT}")
        return _TTS_CONCURRENCY_DEFAULT
    if n > _TTS_CONCURRENCY_MAX:
        print(f"  [WARN] tts_concurrency={n} 超过硬上限 {_TTS_CONCURRENCY_MAX}，"
              f"按 {_TTS_CONCURRENCY_MAX} 执行")
        return _TTS_CONCURRENCY_MAX
    return n


def _parse_bitrate_value(raw):
    """解析 ffprobe 的 bit_rate 字段为 int（bps），失败返回 None（码率未知）。

    ffprobe 对部分容器/流返回 "N/A" 或缺失该键，直接 int() 会抛
    ValueError 使门禁崩溃。返回 None 时调用方跳过码率门禁判定，
    并追加 warning 保证可追溯（不判 FAIL/WARN 阻断，也不虚报）。
    与 media_qa_gate._parse_bitrate_value 保持同口径。
    """
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _enforce_sentence_pauses(hq_file, sentences, min_pause):
    """段间停顿强制——波形层根治（2026-07-28，skill-promo 句间零停顿事故）。

    根因：TTS 引擎整段合成时句间停顿由内部韵律决定，同一段音频里
    句号停顿可在 0~0.4s 浮动甚至为 0（实测 "……写着通过。" 与 "字幕
    和画面……" rel 时间戳零间隔紧贴）。改标点只动文本层，约束不了
    引擎，故在物理波形层强制：相邻字幕段声学间隔 < min_pause 时，
    在边界 ±0.25s 内的最低能量点插入差额静音（8ms 淡入淡出防咔哒），
    并把插入点之后的段时间戳整体平移。间隔度量基于 ASR 强制对齐
    时间戳（单一权威源），平移后字幕/时间轴/溢出判定自动一致。

    幂等：补齐后间隔 ≥ min_pause，重跑即 no-op（缓存命中路径也每轮
    复检，无需重跑 ASR）。插入后仍不达标 → RuntimeError，禁止静默放行。

    Returns (modified, sentences, total_inserted_seconds)
    """
    if min_pause <= 0 or len(sentences) < 2:
        return False, sentences, 0.0
    deficits = [
        i for i in range(len(sentences) - 1)
        if sentences[i + 1]['rel_start'] - sentences[i]['rel_end'] < min_pause - 0.02
    ]
    if not deficits:
        return False, sentences, 0.0

    import wave
    import numpy as np
    with wave.open(str(hq_file), 'rb') as wf:
        n_ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise RuntimeError(f"sentence-pause enforce: {hq_file.name} 非 s16 wav (sampwidth={sw})")
    audio = np.frombuffer(raw, dtype=np.int16).reshape(-1, n_ch).copy()

    sentences = [dict(s) for s in sentences]
    total_inserted = 0.0
    for i in deficits:
        gap = sentences[i + 1]['rel_start'] - sentences[i]['rel_end']
        deficit = min_pause - gap
        boundary = (sentences[i]['rel_end'] + sentences[i + 1]['rel_start']) / 2.0
        # 窗口收紧到 ±0.08s：只允许在 ASR 边界附近微调下刀点。±0.25s 实测会
        # 漂进前一句词内部（低能量起音段被误判为句间谷底），把静音插在词中间
        lo = max(0, int((boundary - 0.08) * sr))
        hi = min(len(audio), int((boundary + 0.08) * sr))
        if hi - lo < 2:
            cut = min(len(audio), max(0, int(boundary * sr)))
        else:
            env = np.abs(audio[lo:hi].astype(np.int32)).sum(axis=1).astype(np.float64)
            win = max(1, int(0.015 * sr))
            env = np.convolve(env, np.ones(win) / win, mode='same')
            cut = lo + int(np.argmin(env))
        fade = min(int(0.008 * sr), cut, len(audio) - cut)
        if fade > 0:
            audio[cut - fade:cut] = (
                audio[cut - fade:cut].astype(np.float64)
                * np.linspace(1.0, 0.0, fade)[:, None]).astype(np.int16)
            audio[cut:cut + fade] = (
                audio[cut:cut + fade].astype(np.float64)
                * np.linspace(0.0, 1.0, fade)[:, None]).astype(np.int16)
        n_ins = int(round(deficit * sr))
        audio = np.concatenate(
            [audio[:cut], np.zeros((n_ins, n_ch), dtype=np.int16), audio[cut:]])
        inserted = n_ins / sr
        cut_t = cut / sr
        # 分侧收拢式平移：前段尾时刻不得越过插入点，后段起时刻先拉到插入点
        # 再加平移量——无论 cut 落在边界哪一侧，新间隔数学上恒 ≥ min_pause
        # （旧规则按位置同向平移，cut 早于段尾时两侧同移，间隔仍为 0）
        for j, s in enumerate(sentences):
            if j <= i:
                if s['rel_end'] > cut_t:
                    s['rel_end'] = round(cut_t, 3)
            else:
                new_start = max(s['rel_start'], cut_t) if j == i + 1 else s['rel_start']
                s['rel_start'] = round(new_start + inserted, 3)
                s['rel_end'] = round(s['rel_end'] + inserted, 3)
        total_inserted += inserted

    # 验证闭环：插入后所有间隔必须达标，否则炸给调用方（不写回残波形）
    for i in range(len(sentences) - 1):
        gap = sentences[i + 1]['rel_start'] - sentences[i]['rel_end']
        if gap < min_pause - 0.05:
            raise RuntimeError(
                f"sentence-pause enforce 失败：{hq_file.name} 段 {i}→{i+1} 插入后间隔仍为 "
                f"{gap:.3f}s < {min_pause}s，拒绝静默放行")

    with wave.open(str(hq_file), 'wb') as wf:
        wf.setnchannels(n_ch)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.ascontiguousarray(audio).tobytes())
    return True, sentences, total_inserted


def _wave_sha256(wav_path):
    """波形内容哈希（A08）：对齐缓存必须绑定实际音频字节而非文本派生键。

    tts_hash 由旁白文本+引擎参数派生，但 hq 波形可被文本之外的因素改变
    （_enforce_sentence_pauses 插静音、同参数重新合成）——只绑文本哈希
    会让旧时间戳被误复用（与 A06"登记路径＝真实读取路径"同族）。
    """
    h = hashlib.sha256()
    try:
        with open(wav_path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()[:16]


# 对齐算法版本（A08）：分段逻辑（_split_text_to_segments）、对齐模型/
# 后端（_forced_align）、时间戳语义变化时必须 bump；版本不符 → 缓存失效。
ALIGN_ALGO_VERSION = "v1"


def _build_timeline_manifest(temp_dir):
    """生成/刷新 _timeline_manifest.json —— 句级时间戳单一权威文件（P0-a）。

    架构原则：字幕时刻从音频物理波形派生（ASR 强制对齐），不再由
    字符数估算。每个场景记录：
      - tts_hash / tts_duration：产物真实性锚点（文本一改 hash 即失效）
      - wave_sha256 / align_algo_ver：对齐结果的真实绑定（A08）——缓存复用
        要求波形字节与算法版本双双一致，文本哈希一致但波形已变则重对齐。
      - sentences[]：每条字幕分段在音频内的 rel_start/rel_end（秒）。
        相对时刻对 adjust_timeline 的边界平移不变 —— 消费方用
        当前 SCENES 的 scene_start 锚定绝对位置，因此 tts → timeline →
        postprocess 任意阶段重跑都不需要重新对齐。
      - method：asr_forced | unaligned（降级链由 step5 处理）

    增量缓存：tts_hash 未变且已有 asr_forced 结果 → 跳过重对齐，
    仅刷新场景绝对边界（与 TTS 文本哈希缓存同一套失效逻辑）。
    """
    tts_dir = temp_dir / "tts_44k"
    manifest_path = temp_dir / "_timeline_manifest.json"
    old_scenes = {}
    if manifest_path.exists():
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                old = json.load(f)
            old_scenes = {str(s.get('scene_id')): s for s in old.get('scenes', [])}
        except (json.JSONDecodeError, OSError):
            old_scenes = {}

    model = None
    model_failed = False
    scenes_out = []
    aligned = 0
    pause_min = float((_load_audio_sync_rules().get('sentence_pause') or {}).get('min_seconds', 0.4))
    algo_ver = f"{ALIGN_ALGO_VERSION}|{_fa.MODEL_SIZE}|pause={pause_min:g}"
    for scene_id, start, end, text in SCENES:
        h = _tts_cache_key(text)
        hq_file = tts_dir / f"tts_{h}_hq.wav"
        if not hq_file.exists():
            print(f"  [WARN] timeline manifest: TTS missing for scene {scene_id}, skipped")
            continue
        tts_dur = get_duration(str(hq_file))
        wave_sha = _wave_sha256(hq_file)
        entry = {
            'scene_id': scene_id,
            'start': round(float(start), 3),
            'end': round(float(end), 3),
            'tts_hash': h,
            'tts_file': hq_file.name,
            'tts_duration': round(tts_dur, 3),
            'wave_sha256': wave_sha,
            'align_algo_ver': algo_ver,
        }

        prev = old_scenes.get(str(scene_id))
        # 复用四条件（A08）：文本键一致 ∧ 记录的是同一个波形文件 ∧
        # 波形字节实测未变 ∧ 算法版本未 bump。缺任一即重对齐——
        # 旧实现只比 tts_hash，波形被插静音/重新合成后仍复用旧时间戳。
        if (prev and prev.get('tts_hash') == h
                and prev.get('tts_file') == hq_file.name
                and prev.get('method') == 'asr_forced'
                and prev.get('sentences')
                and prev.get('wave_sha256') == wave_sha
                and prev.get('align_algo_ver', algo_ver) == algo_ver):
            # 缓存命中：rel 时刻不变，只刷新绝对边界（不 continue，仍要过停顿强制复检）
            entry['method'] = 'asr_forced'
            entry['match_ratio'] = prev.get('match_ratio')
            entry['sentences'] = prev['sentences']
            aligned += 1
        else:
            segments = _split_text_to_segments(text) if text and text.strip() else []
            result = None
            if segments and not model_failed:
                from _forced_align import load_align_model, align_scene
                if model is None:
                    model = load_align_model()
                    if model is None:
                        model_failed = True
                if model is not None:
                    print(f"  Aligning scene {scene_id} (asr forced, {tts_dur:.1f}s)...")
                    result = align_scene(hq_file, text, segments, model=model)

            if result:
                entry['method'] = result['method']
                entry['match_ratio'] = result['match_ratio']
                entry['sentences'] = result['sentences']
                aligned += 1
            else:
                entry['method'] = 'unaligned'
                entry['sentences'] = []

        # 段间停顿强制：每轮必检（含缓存命中），达标即 no-op，不达标则
        # 修改波形+平移时间戳后重测时长，下游（字幕/adjust_timeline/merge）
        # 自动消费新值。unaligned 场景无可靠边界，不强制（对齐失败已有告警）。
        if entry.get('method') == 'asr_forced' and entry.get('sentences'):
            modified, new_sents, ins = _enforce_sentence_pauses(
                hq_file, entry['sentences'], pause_min)
            if modified:
                entry['sentences'] = new_sents
                entry['tts_duration'] = round(get_duration(str(hq_file)), 3)
                entry['pause_inserted'] = round(ins, 3)
                # 停顿强制改写了波形字节：记录面重绑到改写后的实测哈希，
                # 否则下一轮"当前波形 vs 记录哈希"恒不等，缓存命中被永久击穿。
                entry['wave_sha256'] = _wave_sha256(hq_file)
                print(f"  Scene {scene_id}: sentence-pause enforced "
                      f"(+{ins:.2f}s silence → {entry['tts_duration']:.2f}s)")
        scenes_out.append(entry)

    data = {
        '$schema': 'timeline-manifest v1 — 句级时间戳单一权威源（音频实测，非文本估算）',
        'time_base': 'absolute',
        'cover_duration': COVER_DURATION,
        'video_duration': VIDEO_DURATION,
        'align_backend': 'faster-whisper-small/int8',
        'scenes': scenes_out,
    }
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Timeline manifest: {aligned}/{len(scenes_out)} scenes asr-aligned → {manifest_path.name}")
    return True


def _load_timeline_manifest(temp_dir):
    """读取 _timeline_manifest.json，返回 {scene_id(str): scene_entry}。失败返回 {}。"""
    manifest_path = temp_dir / "_timeline_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {str(s.get('scene_id')): s for s in data.get('scenes', [])}
    except (json.JSONDecodeError, OSError):
        return {}


def _normalize_scenes_to_absolute(scenes_list, cover_duration):
    """Normalize a scene list to absolute video time (post-cover baseline).

    Two conventions coexist in configs and narration sources:

    - LEGACY (relative): the first content scene has ``start == 0``. Times
      are measured from the end of the cover animation. Most files under
      ``程序文件/配置/config/pipelines/`` use this convention.

    - NEW (absolute): the first content scene has ``start == cover_duration``.
      Times are absolute video positions. This matches the HTML S-block
      declaration and what ``visual_boundary_check.py`` consumes directly.

    Downstream logic in this module (audio ``adelay``, SRT cursor / end)
    now treats ``SCENES`` as absolute video time — the same frame of
    reference used by ``visual_boundary_check.py``. This helper detects
    the incoming convention from the first content scene and shifts
    LEGACY tuples by ``cover_duration`` so both inputs produce identical
    SRT/audio positioning.

    Cover placeholders (``type == 'cover'`` or ``scene_id == 0`` with
    empty narration) are filtered out — they have no TTS or subtitle.
    """
    if not scenes_list:
        return []

    content = []
    for s in scenes_list:
        stype = str(s.get('type', '')).lower()
        sid = s.get('scene_id')
        narration = (s.get('narration') or '').strip()
        if stype == 'cover':
            continue
        if sid == 0 and not narration:
            continue
        content.append(s)

    if not content:
        return []

    first_start = float(content[0].get('start', 0))
    if cover_duration > 0 and first_start < cover_duration * 0.5:
        # LEGACY relative — shift to absolute so downstream calcs align
        # with the HTML S-block / visual_boundary_check.py convention.
        shifted = []
        for s in content:
            s2 = dict(s)
            s2['start'] = float(s.get('start', 0)) + cover_duration
            s2['end'] = float(s.get('end', 0)) + cover_duration
            shifted.append(s2)
        return shifted

    # Already absolute — return copies for safety
    return [dict(s) for s in content]


def load_config(config_path):
    """Load video-specific config from JSON, overriding SCENES/VIDEO_DURATION/paths.
    
    新增支持三层场景源回退逻辑：
    1. narration_source 字段 → 从外部 JSON 文件读取场景
    2. _compiled_scenes.json → 从编译后的规范化场景读取
    3. scenes 字段 → 从当前配置读取（旧方式）
    
    如果三层都没有找到有效场景，则报错并退出。

    SCENES 使用**视频绝对时间**（含 cover 偏移），与 HTML S-block 及
    visual_boundary_check.py 保持同一时间坐标系。对于沿用 legacy 相对
    时间约定（scene 1 start=0）的配置，_normalize_scenes_to_absolute
    会自动加上 cover_duration，避免下游脚本再次叠加 COVER_DURATION。
    """
    global SCENES, VIDEO_DURATION, VOICE, RATE, PITCH, TTS_ENGINE, QWEN_MODEL, BGM_ENABLED
    global SUBTITLE_DISPLAY_REPLACEMENTS, TTS_INSTRUCTION
    global VIDEO_TYPE, TTS_ENABLED, BGM_SOURCE, AUDIO_BITRATE
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    
    VIDEO_DURATION = float(cfg.get('video_duration', VIDEO_DURATION))
    BGM_ENABLED = _coerce_bool(cfg.get('bgm_enabled', BGM_ENABLED))

    # ---- P0 类型体系（2026-07-29）：纯BGM无旁白路径的类型化配置 ----
    VIDEO_TYPE = str(cfg.get('video_type', '') or '')
    TTS_ENABLED = _coerce_bool(cfg.get('tts_enabled', True))
    AUDIO_BITRATE = str(cfg.get('audio_bitrate', AUDIO_BITRATE))
    BGM_SOURCE = None
    if cfg.get('bgm_source'):
        # bgm_source 解析：绝对路径 → 工作流根目录相对 → HTML 项目目录。
        # 声明了指针即以指针为权威，找不到直接报错（禁止静默回退合成 drone）。
        raw_bgm = str(cfg['bgm_source'])
        bgm_candidates = []
        if os.path.isabs(raw_bgm):
            bgm_candidates.append(Path(raw_bgm))
        else:
            bgm_candidates.append(WF_ROOT / raw_bgm)
            _bgm_html_project = (cfg.get('paths') or {}).get('html_project', '')
            if _bgm_html_project:
                bgm_candidates.append(WF_HTML_BASE / _bgm_html_project / raw_bgm)
        BGM_SOURCE = next((c for c in bgm_candidates if c.is_file()), None)
        if BGM_SOURCE is None:
            print(f"[ERROR] bgm_source='{raw_bgm}' 声明了外部 BGM，但以下位置均未找到：")
            for c in bgm_candidates:
                print(f"[ERROR]   - {c}")
            print("[ERROR] 请修正 bgm_source 指针（禁止静默回退合成 BGM）。")
            sys.exit(1)
    if not TTS_ENABLED:
        # 纯BGM路径门禁：无旁白时音频只能来自 BGM——必须显式声明来源，
        # 禁止"无 TTS 又无 BGM"的静音成片，也不用合成正弦 drone 兜底。
        if not BGM_ENABLED:
            print("[ERROR] tts_enabled=false 但 bgm_enabled=false — 纯BGM路径要求 bgm_enabled=true")
            sys.exit(1)
        if BGM_SOURCE is None:
            print("[ERROR] tts_enabled=false 时必须声明 bgm_source（外部 BGM 文件）——")
            print("[ERROR] 合成正弦 drone 不适合作为成片唯一音轨。")
            sys.exit(1)
    SUBTITLE_DISPLAY_REPLACEMENTS = cfg.get('subtitle_display_replacements', {}) or {}
    VOICE = cfg.get('voice', VOICE)
    RATE = cfg.get('rate', RATE)
    PITCH = cfg.get('pitch', PITCH)
    TTS_ENGINE = cfg.get('tts_engine', TTS_ENGINE).lower()
    QWEN_MODEL = cfg.get('qwen_model', QWEN_MODEL)
    TTS_INSTRUCTION = str(cfg.get('tts_instruction', TTS_INSTRUCTION) or '')
    
    # 支持 audio 对象中的语音参数（高优先级）
    if 'audio' in cfg and isinstance(cfg['audio'], dict):
        _audio_cfg = cfg['audio']
        if 'engine' in _audio_cfg:
            TTS_ENGINE = str(_audio_cfg['engine']).lower()
        if 'voice' in _audio_cfg:
            VOICE = _audio_cfg['voice']
        if 'rate' in _audio_cfg:
            RATE = _audio_cfg['rate']
        if 'pitch' in _audio_cfg:
            PITCH = _audio_cfg['pitch']
        if 'qwen_model' in _audio_cfg:
            QWEN_MODEL = _audio_cfg['qwen_model']
        if 'instruction' in _audio_cfg:
            TTS_INSTRUCTION = str(_audio_cfg['instruction'] or '')

    # 模型解析：config 显式声明 qwen_model 为权威；未声明时按音色推断，
    # 保证音色与模型严格配对（官方约束：不得跨模型混用音色）。
    if TTS_ENGINE == 'qwen' and not QWEN_MODEL:
        QWEN_MODEL = _infer_qwen_model(VOICE)

    # Instruct 能力告警：非 Instruct 音色声明情感指令无效（官方音色规格），
    # 不阻断——避免误伤平台后续放开，但必须让操作者知情。
    if TTS_ENABLED and TTS_ENGINE == 'qwen' and TTS_INSTRUCTION \
            and VOICE not in _INSTRUCT_CAPABLE_VOICES:
        print(f"[WARN] voice='{VOICE}' 不支持 Instruct 情感指令，tts_instruction 不会生效")
        print(f"[WARN] 需要情感控制时改用 Instruct 音色：{'/'.join(sorted(_INSTRUCT_CAPABLE_VOICES))}")
    
    # 引擎与音色一致性门禁：qwen 引擎但 voice 是 Edge 格式（zh-CN-*Neural）→ 报错退出。
    # 不允许静默改写：配置指纹哈希的是声明值，运行时改写会让指纹与实际产物脱钩，
    # 引擎切换时缓存不会失效（缓存投毒隐患）。纯BGM路径（TTS_ENABLED=false）不涉及 TTS 引擎，豁免。
    if TTS_ENABLED and TTS_ENGINE == 'qwen' and re.match(r'^[a-z]{2}-[A-Z]{2}-\w+Neural$', VOICE or ''):
        print(f"[ERROR] tts_engine=qwen 但 voice='{VOICE}' 是 Edge-TTS 格式，配置声明与实际引擎不符")
        print(f"[ERROR] 请修正 config：用 qwen 音色（如 'longanling'），或显式声明 tts_engine: 'edge'")
        sys.exit(1)
    
    if TTS_ENABLED and TTS_ENGINE not in ('qwen', 'edge'):
        print(f"[ERROR] Unknown tts_engine='{TTS_ENGINE}' — supported: 'qwen', 'edge'")
        sys.exit(1)
    
    if TTS_ENABLED:
        print(f"[INFO] TTS engine: {TTS_ENGINE}, voice: {VOICE}" +
              (f", model: {QWEN_MODEL}" if TTS_ENGINE == 'qwen' else '') +
              (f", instruction: {TTS_INSTRUCTION}" if TTS_ENGINE == 'qwen' and TTS_INSTRUCTION else ''))
    else:
        print(f"[INFO] TTS disabled (tts_enabled=false) — pure-BGM audio path" +
              (f", video_type={VIDEO_TYPE}" if VIDEO_TYPE else ""))
    
    if 'cover_duration' in cfg:
        global COVER_DURATION
        COVER_DURATION = float(cfg['cover_duration'])
    
    # ========== 三层场景源回退逻辑 ==========
    
    # 第1层：检查 narration_source（外部旁白源）
    # P0-03 修复（2026-07-28）：相对路径优先按 HTML 项目目录解析（与指纹机制
    # 的 narration.json 位置一致），兼容旧约定回退到配置文件目录。
    # 声明了指针即以指针为权威：解析/加载失败直接报错退出，
    # 禁止静默回退内嵌 scenes（否则双源分叉永远不会暴露）。
    if 'narration_source' in cfg:
        raw_source = str(cfg['narration_source'])
        candidates = []
        if os.path.isabs(raw_source):
            candidates.append(Path(raw_source))
        else:
            html_project = (cfg.get('paths') or {}).get('html_project', '')
            if html_project:
                candidates.append(WF_HTML_BASE / html_project / raw_source)
            candidates.append(Path(os.path.dirname(config_path)) / raw_source)
        narration_source_path = next((c for c in candidates if c.is_file()), None)

        if narration_source_path is None:
            print(f"[ERROR] narration_source='{raw_source}' 声明了外部旁白源，但以下位置均未找到：")
            for c in candidates:
                print(f"[ERROR]   - {c}")
            print("[ERROR] 请修正指针或移除 narration_source 字段（单一权威源原则，禁止静默回退）。")
            sys.exit(1)

        try:
            with open(narration_source_path, 'r', encoding='utf-8') as f:
                narration_data = json.load(f)
        except Exception as e:
            print(f"[ERROR] narration_source 解析失败: {narration_source_path}: {e}")
            sys.exit(1)

        if not narration_data.get('scenes'):
            print(f"[ERROR] narration_source 中无有效 scenes: {narration_source_path}")
            sys.exit(1)

        scenes_list = _normalize_scenes_to_absolute(
            narration_data['scenes'], COVER_DURATION
        )
        SCENES = [(
            s.get('scene_id', i),
            float(s.get('start', 0)),
            float(s.get('end', 0)),
            s.get('narration', '')
        ) for i, s in enumerate(scenes_list)]
        print(f"[INFO] Loaded {len(SCENES)} scenes from narration_source: {narration_source_path}")
        return cfg
    
    # 第2层：检查 _compiled_scenes.json（规范化编译场景）
    config_dir = os.path.dirname(config_path)
    compiled_scenes_path = os.path.join(config_dir, '_compiled_scenes.json')
    
    if os.path.exists(compiled_scenes_path):
        try:
            with open(compiled_scenes_path, 'r', encoding='utf-8') as f:
                compiled_data = json.load(f)
            
            if 'scenes' in compiled_data and compiled_data['scenes']:
                scenes_list = _normalize_scenes_to_absolute(
                    compiled_data['scenes'], COVER_DURATION
                )
                SCENES = [(
                    s.get('scene_id', f"s{i}"),
                    float(s.get('start', 0)),
                    float(s.get('end', 0)),
                    s.get('narration', '')
                ) for i, s in enumerate(scenes_list)]
                print(f"[INFO] Loaded {len(SCENES)} scenes from _compiled_scenes.json")
                return cfg
        except Exception as e:
            print(f"[WARN] Failed to load _compiled_scenes.json: {e}")
    
    # 第3层：检查 scenes 字段（旧方式）
    if 'scenes' in cfg and cfg['scenes']:
        scenes_list = _normalize_scenes_to_absolute(cfg['scenes'], COVER_DURATION)
        SCENES = [(s['scene_id'], float(s['start']), float(s['end']), s['narration'])
                  for s in scenes_list]
        print(f"[INFO] Loaded {len(SCENES)} scenes from config 'scenes' field")
        return cfg
    
    # 三层都没有找到有效场景
    print("[ERROR] No valid scenes found in:")
    print("  1. narration_source (external narration file)")
    print("  2. _compiled_scenes.json (compiled normalized scenes)")
    print("  3. 'scenes' field in config")
    print("[ERROR] TTS cannot proceed without scene definitions. Exiting.")
    sys.exit(1)



def get_paths(config_cfg=None):
    root = WF_ROOT
    if config_cfg and 'paths' in config_cfg:
        p = config_cfg['paths']
        video_name = p.get('video_name', '')
        # 兼容两种配置写法：paths.subtitle_name 或 subtitle.file
        subtitle_name = p.get('subtitle_name', '') or config_cfg.get('subtitle', {}).get('file', '')
        temp_subdir = p.get('temp_subdir', 'crm_audio')
        subtitle_file = root / "成果文件" / "字幕" / subtitle_name
    else:
        video_name = "销售CRM系统工具开发_赋能个性化销售场景.mp4"
        temp_subdir = "crm_audio"
        subtitle_file = root / "成果文件" / "字幕" / "销售CRM系统工具开发_赋能个性化销售场景.srt"

    output_file = root / "成果文件" / "视频" / video_name
    temp_dir = root / "过程产物" / "临时产物" / temp_subdir
    # 输入源与交付槽位分离（2026-09-03）：后处理输入取纯净渲染 render_raw.mp4，
    # 成果目录只在链路成功末端（step3 混音 / step6 烧字幕）被写入。
    # 旧实现把槽位当输入（render 步先把无声裸片复制进去），两类后果实测发生过：
    #   ① 后处理失败时裸片留在交付槽冒充成片（2026-09-02 WSI 批次 4 条）；
    #   ② 重跑时把已混音、已烧字幕的成片当输入再处理一遍（字幕重复）。
    # 无 render_raw 时回退槽位文件，保留"手工放一个视频进成果目录再单独调用
    # 本脚本"的用法。
    render_raw = temp_dir / "render_raw.mp4"
    video_file = render_raw if render_raw.exists() else output_file
    return root, video_file, temp_dir, output_file, subtitle_file


_html_override_path = None

def step0_validate_html_template():
    """Pre-check: HTML template must have subtitle-safe divs in every scene"""
    print("=== Step 0: Validate HTML Template ===")
    root = WF_ROOT
    html_path = _html_override_path or (root / "程序文件" / "源码" / "hyperframes" / "crm-video" / "index.html")

    if not html_path.exists():
        print(f"  WARNING: HTML template not found at {html_path}")
        print("  Skipping validation. Subtitle overlap may occur.\n")
        return True

    content = html_path.read_text(encoding="utf-8")

    # Count scenes and subtitle-safe divs
    import re as _re
    scene_count = len(_re.findall(r'id="scene\d+"', content))
    safe_count = len(_re.findall(r'class="subtitle-safe"', content))

    # Parse S-block for authoritative metadata (cover duration, scene count)
    script_match_early = _re.search(r'<script>(.*?)</script>', content, _re.DOTALL)
    s_map = {}
    if script_match_early:
        s_map = parse_scene_map(script_match_early.group(1))

    # Check padding-bottom — try explicit padding-bottom first, then shorthand
    padding_ok = False
    # 1. Try explicit "padding-bottom:NNNpx"
    pb_explicit = _re.search(r'\.sc\s*\{[^}]*padding-bottom\s*:\s*(\d+)px', content)
    if pb_explicit:
        padding_ok = int(pb_explicit.group(1)) >= 150
    if not padding_ok:
        # 2. Try shorthand "padding:T R B" or "padding:T R B L" — last value = bottom
        sc_match = _re.search(r'\.sc\s*\{[^}]*padding\s*:[^}]*\}', content)
        if sc_match:
            px_values = _re.findall(r'(\d+)px', sc_match.group())
            if len(px_values) >= 3:
                bottom_padding = int(px_values[2])  # 3-value: T R B; 4-value: T R B L
                padding_ok = bottom_padding >= 150
    if not padding_ok:
        # 3. calc(var(--xxx) + NNpx) 形态（safe-zone 变量驱动的模板）：
        #    从 :root 解析变量值后求和，避免对合规模板误报。
        calc_match = _re.search(
            r'\.sc\s*\{[^}]*padding(?:-bottom)?\s*:[^};]*calc\(\s*var\(\s*(--[\w-]+)\s*\)\s*\+\s*(\d+)px\s*\)',
            content)
        if calc_match:
            var_name, extra_px = calc_match.group(1), int(calc_match.group(2))
            var_def = _re.search(
                _re.escape(var_name) + r'\s*:\s*(\d+)px', content)
            if var_def:
                padding_ok = int(var_def.group(1)) + extra_px >= 150

    issues = []
    if safe_count < scene_count:
        issues.append(f"  Only {safe_count}/{scene_count} scenes have .subtitle-safe div")
    if not padding_ok:
        issues.append("  .sc padding-bottom < 150px — subtitle may overlap content")

    # Auto-detect cover duration — prefer S-block (authoritative), fall back to data-cover-duration
    global COVER_DURATION
    if s_map and s_map.get('cover_duration'):
        COVER_DURATION = s_map['cover_duration']
        # S-block already excludes cover from scene_count
        scene_count = s_map['scene_count']
    else:
        cover_match = _re.search(r'data-cover-duration="([\d.]+)"', content)
        if cover_match:
            COVER_DURATION = float(cover_match.group(1))
            # Exclude cover scene from scene_count for validation accuracy
            if _re.search(r'id="scene0"', content):
                scene_count -= 1

    # --- Timeline validation: detect known anti-patterns in GSAP code ---
    # Parse T-block from script section (before comment stripping)
    script_match = _re.search(r'<script>(.*?)</script>', content, _re.DOTALL)
    t_map = {}
    if script_match:
        t_map = parse_t_block(script_match.group(1))

    # Strip JS comments to avoid matching patterns inside /* ... */ and // ...
    js_code = _re.sub(r'/\*[\s\S]*?\*/', '', content)
    js_code = _re.sub(r'//[^\n]*', '', js_code)

    # Check 1: Independent gsap.timeline() not controlled by HyperFrames seek-and-capture
    for m in _re.finditer(r'gsap\.timeline\s*\(', js_code):
        start = max(0, m.start() - 80)
        ctx = js_code[start:m.end() + 20]
        # Only flag if NOT the main timeline (paused:true assigned to var tl)
        if 'paused' in ctx and ('var tl' in ctx or 'let tl' in ctx or 'const tl' in ctx):
            continue
        issues.append(
            "  [TIMELINE] Independent gsap.timeline() detected — not controlled by "
            "HyperFrames seek-and-capture, will cause frame flashing. "
            "Merge all tweens into the main tl."
        )

    # Check 2: Scene1 content animations starting before cover fully fades out
    if COVER_DURATION > 0:
        for m in _re.finditer(
            r'(?:fromTo|to)\s*\(\s*"#(s1[a-zA-Z][a-zA-Z0-9]*)"', js_code
        ):
            el_id = m.group(1)
            # Skip non-content elements (glow/bg effects: s1g1, s1g2, s1gh, etc.)
            if _re.match(r'^s1g', el_id, _re.IGNORECASE):
                continue
            # Find the timeline position on this line (T-block or numeric literal)
            line_end = js_code.find('\n', m.end())
            if line_end < 0:
                line_end = min(m.end() + 120, len(js_code))
            line = js_code[m.start():line_end]
            anim_start = resolve_line_time(line, t_map)
            if anim_start is not None and anim_start < COVER_DURATION:
                issues.append(
                    f"  [TIMING] #{el_id} animation starts at T={anim_start}s "
                    f"before cover fades at T={COVER_DURATION}s — text overlap. "
                    f"Delay to T>={COVER_DURATION}s."
                )

    # Check 3: Opacity crossfade transitions between scenes (flicker in seek-and-capture)
    fade_outs = []  # (time, scene_id)
    fade_ins = []   # (time, scene_id)
    for m in _re.finditer(
        r'tl\.(to|fromTo)\s*\(\s*"#scene(\d+)"[^}]*opacity\s*:\s*([\d.]+)', js_code
    ):
        func_name = m.group(1)
        scene_num = int(m.group(2))
        opacity_val = float(m.group(3))
        line_end = js_code.find('\n', m.end())
        if line_end < 0:
            line_end = min(m.end() + 120, len(js_code))
        line = js_code[m.start():line_end]
        time_pos = resolve_line_time(line, t_map)
        if time_pos is None:
            continue
        if func_name == 'to' and opacity_val == 0:
            fade_outs.append((time_pos, scene_num))
        elif func_name == 'fromTo' and opacity_val == 0:
            fade_ins.append((time_pos, scene_num))
    for t_out, s_out in fade_outs:
        for t_in, s_in in fade_ins:
            if s_in != s_out and abs(t_out - t_in) < 2.0:
                issues.append(
                    f"  [TRANSITION] Opacity crossfade between scene{s_out} and scene{s_in} "
                    f"at T={t_out}s/{t_in}s — flickers in seek-and-capture. "
                    f"Use transform-based transition (Push Slide / Vertical Push)."
                )

    # Check 4: end-fade must not fire long before data-duration.
    # quickstart-demo 事故：#scene-end-fade 按"收缩后窗口"手算写死 T.s5+5.8，
    # 而 S-block 窗口未收缩 → 收尾 ~4s 黑场配音频。静态拦截声明脱钩：
    # 淡出触发时刻距 data-duration 超过 dead-air 阈值 → 拒绝渲染。
    dur_attr = _re.search(r'data-duration="([\d.]+)"', content)
    if dur_attr:
        _data_dur = float(dur_attr.group(1))
        m_fade = _re.search(r'tl\.to\s*\(\s*"#scene-end-fade"[^\n]*', js_code)
        if m_fade:
            _fade_t = resolve_line_time(m_fade.group(0), t_map)
            _max_dead = float(_load_audio_sync_rules().get("dead_air", {})
                              .get("max_seconds_per_scene", 3.0))
            # 尾部推导惯用形态：从权威 S-block 末场景 end 算触发时刻
            # （adjust_timeline 重写 S-block 时自动同步，构造上不会脱钩）。
            _tail_derived = bool(_re.search(
                r'S\s*\[\s*S\.length\s*-\s*1\s*\]\s*\.\s*end', js_code))
            if _fade_t is not None and _fade_t < _data_dur - _max_dead:
                issues.append(
                    f"  [END-FADE] #scene-end-fade fires at T={_fade_t:.1f}s but "
                    f"data-duration={_data_dur}s — {_data_dur - _fade_t:.1f}s of black "
                    f"screen while audio/subtitles may still play. Shrink the last "
                    f"scene window (adjust_timeline --shrink) or move the fade to the tail."
                )
            elif _fade_t is None and not _tail_derived:
                issues.append(
                    "  [END-FADE] #scene-end-fade trigger time is not statically "
                    "resolvable and not derived from the S-block tail — use "
                    "`S[S.length - 1].end - fadeDur` so adjust_timeline keeps it in sync."
                )

    if issues:
        print("  HTML template validation FAILED:")
        for issue in issues:
            print(issue)
        print("  Fix: add <div class=\"subtitle-safe\"></div> to each scene")
        print("  Fix: set .sc padding-bottom >= 150px\n")
        return False

    cover_info = f", cover={COVER_DURATION}s" if COVER_DURATION > 0 else ""
    print(f"  OK: {scene_count} scenes, {safe_count} subtitle-safe zones, padding adequate{cover_info}")
    print()
    return True


def run_ffmpeg(cmd, desc=""):
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        stderr = result.stderr or ""
        print(f"  FFmpeg error ({desc}): {stderr[-500:]}")
        return False
    return True


def _sapi_tts(text, output_path):
    """Generate TTS using .NET System.Speech (fallback when edge_tts is unavailable)."""
    import tempfile
    root = WF_ROOT
    sapi_script = root / "过程产物" / "临时产物" / "sapi_tts.ps1"

    # Write text to temp file (avoid PowerShell encoding issues)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(text)
        text_file = f.name

    wav_path = str(output_path).replace('.mp3', '_sapi.wav')

    # Parse rate: "+5%" -> 1, "-10%" -> -1
    rate_pct = 0
    if RATE.startswith('+'):
        rate_pct = int(RATE[1:].rstrip('%'))
    elif RATE.startswith('-'):
        rate_pct = -int(RATE[1:].rstrip('%'))
    sapi_rate = max(-10, min(10, rate_pct // 5))

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(sapi_script), "-TextFile", text_file, "-OutputWav", wav_path, "-Rate", str(sapi_rate)],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120
        )
        if result.returncode != 0:
            raise Exception(f"SAPI PowerShell failed: {result.stderr[:200]}")

        # Convert WAV to MP3
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", str(output_path)],
            capture_output=True, timeout=60
        )

        if not os.path.exists(output_path) or os.path.getsize(output_path) < 100:
            raise Exception("SAPI TTS output too small or missing")

        print(f"  SAPI fallback OK: {os.path.getsize(output_path)} bytes")
    finally:
        if os.path.exists(text_file):
            os.unlink(text_file)
        if os.path.exists(wav_path):
            os.remove(wav_path)


def _edge_rate_to_float(rate_str, kind='rate'):
    """Convert Edge-TTS rate/pitch syntax to CosyVoice float in [0.5, 2.0].

    Edge-TTS uses percent-style deltas relative to 100% baseline:
      RATE="+5%"  → 5% 途快→ rate=1.05
      RATE="-10%" → 10% 途慢→ rate=0.90
      PITCH="+0Hz" / "+2st" → pitch 以 100% 为基准，非百分比时默认 1.0

    CosyVoice accepts float in [0.5, 2.0] for both rate and pitch, with 1.0
    as the natural baseline. We do a best-effort mapping; anything we can't
    parse becomes 1.0 (natural) plus a WARN.
    """
    if not rate_str:
        return 1.0
    s = str(rate_str).strip()
    # Edge 常见中性写法（"+0Hz" / "+0st" / "+0%"）——语义就是不变，直接返回 1.0，不制造噪警
    if re.match(r'^[+-]?0(\.0+)?\s*(Hz|hz|st|ST|%)?$', s):
        return 1.0
    m = re.match(r'^([+-]?)(\d+(?:\.\d+)?)\s*%$', s)
    if m:
        sign = -1 if m.group(1) == '-' else 1
        pct = float(m.group(2)) * sign
        val = 1.0 + pct / 100.0
        return max(0.5, min(2.0, val))
    # 完整 float（已写成 CosyVoice 风格）
    try:
        val = float(s)
        return max(0.5, min(2.0, val))
    except ValueError:
        pass
    print(f"[WARN] Cannot convert {kind}='{s}' to CosyVoice float, using 1.0")
    return 1.0


async def _qwen_tts(text, output_path):
    """Generate TTS using Alibaba Cloud DashScope (CosyVoice / Qwen-Audio-TTS).

    Direct 44.1kHz WAV output — matches SAMPLE_RATE, so downstream ffmpeg
    upsample is a no-op (but kept for uniformity with Edge branch).

    Fails loudly on error — NO fallback to Edge-TTS (would violate
    AGENTS.md single-engine consistency rule).
    """
    import asyncio as aio
    try:
        import dashscope  # noqa: F401  (验证安装)
        from dashscope.audio.http_tts.http_speech_synthesizer import HttpSpeechSynthesizer
    except ImportError as e:
        raise RuntimeError(
            "dashscope SDK not installed. Run: pip install 'dashscope>=1.25.17'\n"
            "可选：在 config 里声明 tts_engine: 'edge' 临时回退到 Edge-TTS。"
        ) from e

    api_key = os.environ.get('DASHSCOPE_API_KEY')
    if not api_key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY not set in environment.\n"
            "在 shell 里 set DASHSCOPE_API_KEY=sk-xxx 后重跑，"
            "或在 config 里声明 tts_engine: 'edge' 回退到 Edge-TTS。"
        )

    rate_f = _edge_rate_to_float(RATE, 'rate')
    pitch_f = _edge_rate_to_float(PITCH, 'pitch')
    model = QWEN_MODEL or _infer_qwen_model(VOICE)  # 防御：免配置路径（默认 SCENES）直接调用时模型未解析

    max_retries = 5
    retry_delays = [3, 5, 10, 15, 20]
    for attempt in range(1, max_retries + 1):
        try:
            # SDK 同步接口，放到线程里避免阻塞 event loop
            def _call_sync():
                kwargs = {}
                if TTS_INSTRUCTION:  # 仅 Instruct 音色有效（加载配置时已告警检查）
                    # HTTP 接口参数名是 instruct（不是 instruction，2026-08-07 实测：
                    # instruction 会被引擎拒收 428）；官方限长 128 字符
                    kwargs['instruct'] = TTS_INSTRUCTION
                return HttpSpeechSynthesizer.call(
                    model=model,
                    text=text,
                    voice=VOICE,
                    format='wav',
                    sample_rate=SAMPLE_RATE,   # 直接要 44100Hz，免下游重采样
                    rate=rate_f,
                    pitch=pitch_f,
                    stream=False,
                    api_key=api_key,
                    **kwargs,
                )
            result = await aio.to_thread(_call_sync)

            audio_url = getattr(result, 'audio_url', None) or ''
            if not audio_url:
                raise Exception(f"DashScope response missing audio_url: {result!r}")

            # 下载 WAV（OSS URL，24h 有效期）
            import urllib.request
            req = urllib.request.Request(audio_url, headers={'User-Agent': 'hyperframes-pipeline/1.0'})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            with open(output_path, 'wb') as f:
                f.write(data)

            if not os.path.exists(output_path) or os.path.getsize(output_path) < 200:
                size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                raise Exception(f"Qwen TTS produced invalid file ({size} bytes)")
            return  # success
        except Exception as e:
            if attempt < max_retries:
                wait = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                print(f"  qwen_tts attempt {attempt}/{max_retries} failed: {e}")
                print(f"  Retrying in {wait}s...")
                await aio.sleep(wait)
            else:
                raise RuntimeError(
                    f"Qwen TTS generation failed after {max_retries} retries. "
                    f"Model={model}, Voice={VOICE}, Text={text[:50]}... "
                    f"不自动降级到 Edge-TTS（避免音色不一致）——修复后重跑，"
                    f"或在 config 里声明 tts_engine: 'edge' 后重跑。"
                ) from e


async def generate_tts(text, output_path):
    """Dispatch TTS generation by TTS_ENGINE.

    Empty text → 3s silence (opening/transition scenes).
    Otherwise → Qwen (default) or Edge branch. Both retry+fail-loudly;
    neither auto-fallbacks to the other (AGENTS.md 禁止 cross-engine fallback).
    """
    # Skip empty narration (opening scenes often have no text)
    if not text or text.strip() == "":
        print(f"  Skipping empty narration: creating 3-second silence")
        subprocess.run([
            "ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "3", "-q:a", "9", "-acodec", "libmp3lame", str(output_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    if TTS_ENGINE == 'qwen':
        await _qwen_tts(text, output_path)
        return
    # Fall through to Edge-TTS branch
    await _edge_tts(text, output_path)


async def _edge_tts(text, output_path):
    """Generate TTS using edge_tts with retry. NO SAPI fallback (causes voice inconsistency).

    Previous versions silently fell back to .NET SAPI (Microsoft Huihui Desktop) when
    edge_tts failed. This produced a DIFFERENT VOICE for affected scenes, resulting in
    severe audio inconsistency across the video. Now we retry and fail loudly instead.
    """
    import asyncio as aio
    max_retries = 10
    retry_delays = [5, 10, 15, 20, 30, 30, 30, 30, 30]  # seconds between retries
    for attempt in range(1, max_retries + 1):
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH)
            await communicate.save(str(output_path))
            if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
                return  # Success
            size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            raise Exception(f"edge_tts produced invalid file ({size} bytes)")
        except Exception as e:
            if attempt < max_retries:
                wait = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                print(f"  edge_tts attempt {attempt}/{max_retries} failed: {e}")
                print(f"  Retrying in {wait}s...")
                await aio.sleep(wait)
            else:
                print(f"  edge_tts FAILED after {max_retries} attempts: {e}")
                raise RuntimeError(
                    f"TTS generation failed after {max_retries} retries. "
                    f"Voice: {VOICE}, Text: {text[:50]}... "
                    f"DO NOT use SAPI fallback \u2014 it produces a different voice and causes audio inconsistency."
                ) from e


def get_duration(filepath):
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "json", filepath],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    return float(json.loads(result.stdout)["format"]["duration"])


async def step1_generate_tts(temp_dir):
    print("=== Step 1: Generate TTS (44.1kHz, text-hash cache) ===")
    tts_dir = temp_dir / "tts_44k"
    tts_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_tts_manifest(temp_dir)

    # ========== P2-01 阶段1：并发生成 raw mp3（仅网络 TTS 步骤） ==========
    # 预先分拣 cache-miss 且 raw 缺失的场景，Semaphore 限流并发调用 generate_tts。
    # ffmpeg 上采样/manifest 写入/时长检查仍在下方原串行循环里按 SCENES 顺序执行，
    # 保证确定性输出顺序与产物语义与串行版完全等价。concurrency=1 时不进池，
    # raw 生成留在原循环内逐场景执行（原串行路径原样保留）。
    concurrency = _load_tts_concurrency()
    if concurrency > 1:
        pending = []
        for scene_id, start, end, text in SCENES:
            h = _tts_cache_key(text)
            hq_file = tts_dir / f"tts_{h}_hq.wav"
            raw_file = temp_dir / f"scene_{scene_id}_{h}.mp3"
            if not hq_file.exists() and not raw_file.exists():
                pending.append((scene_id, text, raw_file))
        if pending:
            print(f"  [TTS-POOL] concurrency={concurrency} (cache-miss scenes: {len(pending)})")
            sem = asyncio.Semaphore(concurrency)

            async def _pool_worker(scene_id, text, raw_file):
                async with sem:
                    print(f"  Generating scene {scene_id} TTS...")
                    await generate_tts(text, raw_file)
                    print(f"  Scene {scene_id}: raw TTS generated")

            results = await asyncio.gather(
                *[_pool_worker(sid, t, rf) for sid, t, rf in pending],
                return_exceptions=True,
            )
            failed = [pending[i] for i, r in enumerate(results)
                      if isinstance(r, BaseException)]
            # 单场景失败不中止其他任务；全部结束后对失败场景串行兜底重试一次
            still_failed = []
            for scene_id, text, raw_file in failed:
                print(f"  Scene {scene_id}: concurrent TTS failed, serial retry...")
                try:
                    await generate_tts(text, raw_file)
                    print(f"  Scene {scene_id}: raw TTS generated (serial retry)")
                except Exception as e:
                    still_failed.append((scene_id, e))
                    # 清掉可能的半成品 raw，避免下次续跑把坏文件洗进缓存
                    if raw_file.exists():
                        raw_file.unlink()
            print(f"  [TTS-POOL] done: {len(pending) - len(still_failed)}"
                  f"/{len(pending)} scene(s) ok, {len(still_failed)} failed")
            if still_failed:
                print(f"  [ERROR] TTS generation FAILED for {len(still_failed)} scene(s):")
                for scene_id, e in still_failed:
                    print(f"    ✗ scene {scene_id}: {e}")
                print("  已成功场景的 raw mp3 已按文本哈希缓存，修复后重跑免重做。")
                return False

    overflow_scenes = []
    new_entries = 0
    for scene_id, start, end, text in SCENES:
        h = _tts_cache_key(text)
        hq_file = tts_dir / f"tts_{h}_hq.wav"

        if hq_file.exists():
            # Cache hit — text unchanged, reuse existing audio
            cached_sid = str(scene_id)
            if manifest.get(cached_sid) is None or (isinstance(manifest.get(cached_sid), dict) and manifest[cached_sid].get('hash') != h):
                manifest[str(scene_id)] = {'hash': h, 'file': str(hq_file.relative_to(temp_dir))}
                new_entries += 1
        else:
            # Cache miss — generate TTS for this text.
            # raw mp3 is keyed by text hash too: a plain scene_{id}.mp3 would
            # survive narration edits and get upsampled under the NEW hash,
            # laundering stale audio content into a fresh cache entry.
            raw_file = temp_dir / f"scene_{scene_id}_{h}.mp3"
            if not raw_file.exists():
                print(f"  Generating scene {scene_id} TTS...")
                await generate_tts(text, raw_file)

            print(f"  Upsampling scene {scene_id} to 44.1kHz stereo...")
            ok = run_ffmpeg([
                "ffmpeg", "-y", "-i", str(raw_file),
                "-ar", str(SAMPLE_RATE), "-ac", "2",
                "-sample_fmt", "s16",
                str(hq_file)
            ], f"upsample scene {scene_id}")
            if not ok:
                return False
            manifest[str(scene_id)] = {'hash': h, 'file': str(hq_file.relative_to(temp_dir))}
            new_entries += 1

        dur = get_duration(str(hq_file))
        avail = (end - start)
        if dur > avail + 0.5:
            tag = f"OVER +{dur-avail:.1f}s"
            overflow_scenes.append((scene_id, dur, avail))
        else:
            tag = "OK"
        # SCENES stores absolute video time (see _normalize_scenes_to_absolute)
        actual_start = start
        print(f"  Scene {scene_id}: {dur:.1f}s / {avail:.1f}s {tag} (actual @{actual_start:.1f}s) [hash:{h[:6]}]")

    if new_entries > 0:
        _save_tts_manifest(manifest, temp_dir)

    # ========== P0-04：TTS 产物验证 ==========
    # 在返回前验证 TTS 产物
    print("  Validating TTS products...")
    from verify_tts_product import validate_tts_output
    
    manifest_path = temp_dir / "tts_manifest.json"
    expected_tts_count = len([
        (sid, s, e, t) for sid, s, e, t in SCENES
        if t and t.strip()  # 只计算有旁白的场景
    ])
    
    is_valid, details = validate_tts_output(
        manifest_path=str(manifest_path),
        expected_scenes=expected_tts_count,
        temp_dir=str(temp_dir)
    )
    
    # 验证结果持久化：落盘供 pipeline_runner 合并进 pipeline_state（完工报告追溯）。
    # 无论通过与否都写，确保失败明细也可被追溯。
    try:
        from verify_tts_product import write_verify_result
        write_verify_result(details, str(temp_dir))
    except Exception:
        pass
    
    if not is_valid:
        print(f"\n  [ERROR] TTS product validation FAILED:")
        for error in details['errors']:
            print(f"    ✗ {error}")
        if details['warnings']:
            print(f"  [WARNINGS]")
            for warn in details['warnings']:
                print(f"    ⚠ {warn}")
        print(f"\n  TTS returned exit code 0 but product validation failed.")
        print(f"  This may indicate corrupted audio, cache poisoning, or Edge-TTS silent failure.\n")
        return False
    
    if details['warnings']:
        print(f"  [TTS WARNINGS]")
        for warn in details['warnings']:
            print(f"    ⚠ {warn}")
    
    if overflow_scenes:
        print(f"\n  ⚠ {len(overflow_scenes)} scene(s) TTS overflow detected!")
        print("  Run adjust_timeline.py to extend timeline before rendering.")
        print("  DO NOT proceed to render until timeline is adjusted.\n")
    else:
        print("  All TTS validated and ready\n")

    # P0-a：TTS 产物验证通过后立即生成句级时间戳权威文件。
    # 对齐失败不阻断 TTS 步骤（manifest 记 method=unaligned，
    # step5 自动回落 gap 检测），但失败会在日志中显式告警。
    _build_timeline_manifest(temp_dir)
    return True


def step2_generate_bgm(temp_dir):
    """Generate ambient background music using FFmpeg synthesis"""
    print("=== Step 2: Generate Background Music ===")
    bgm_file = temp_dir / "bgm.wav"

    if not BGM_ENABLED:
        # 项目级关闭 BGM（合成正弦波低频 drone 存在嗡嗡声观感问题）
        if bgm_file.exists():
            bgm_file.unlink()
            print("  BGM disabled by config — removed stale bgm.wav\n")
        else:
            print("  BGM disabled by config, skipping\n")
        return True

    if BGM_SOURCE:
        # 外部 BGM（P0 纯BGM通路，2026-07-29）：每次重新生成（源文件/时长可能变化，
        # 不走 exists 缓存），-stream_loop -1 循环补齐后按视频时长截断，
        # 1s 淡入 + 尾部 2s 淡出，统一立体声 44.1kHz s16。
        # 音量：纯BGM模式 BGM 即主音轨（1.0，响度交给 step3 loudnorm 统一到 -16 LUFS）；
        # TTS 模式作背景垫底（mix.bgm_volume，默认 0.25）。
        _mix_cfg = _load_audio_sync_rules().get("mix", {})
        _bgm_vol = float(_mix_cfg.get("bgm_volume", 0.25)) if TTS_ENABLED else 1.0
        _fade_start = max(0.0, VIDEO_DURATION - 2.0)
        ok = run_ffmpeg([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(BGM_SOURCE),
            "-t", str(VIDEO_DURATION),
            "-af", f"volume={_bgm_vol},afade=t=in:d=1,afade=t=out:st={_fade_start}:d=2",
            "-ar", str(SAMPLE_RATE), "-ac", "2",
            "-sample_fmt", "s16",
            str(bgm_file)
        ], "external bgm preparation")
        if ok:
            print(f"  External BGM prepared: {BGM_SOURCE.name} -> {bgm_file}\n")
        return ok

    if bgm_file.exists():
        print("  BGM already exists, skipping\n")
        return True

    # Multi-layer ambient pad: low drone + mid shimmer + subtle rhythm
    # Layer 1: Deep drone (C2 + G2)
    # Layer 2: Mid pad (C3 + E3 + G3)
    # Layer 3: High shimmer (C5) with tremolo
    dur = VIDEO_DURATION + 2  # slight padding

    filter_complex = (
        # Layer 1: Deep drone
        f"sine=frequency=65.41:duration={dur}[drone1];"
        f"sine=frequency=98.00:duration={dur}[drone2];"
        f"[drone1][drone2]amix=inputs=2:weights=0.6 0.4[drone];"
        # Layer 2: Mid pad with slow volume swell
        f"sine=frequency=130.81:duration={dur}[mid1];"
        f"sine=frequency=164.81:duration={dur}[mid2];"
        f"sine=frequency=196.00:duration={dur}[mid3];"
        f"[mid1][mid2][mid3]amix=inputs=3:weights=0.4 0.3 0.3[midraw];"
        f"[midraw]tremolo=f=0.15:d=0.3[mid];"
        # Layer 3: High shimmer
        f"sine=frequency=523.25:duration={dur}[hi];"
        f"[hi]tremolo=f=0.25:d=0.6,afade=t=in:d=3,afade=t=out:st={dur-4}:d=4[shimmer];"
        # Mix all layers with BGM at low volume
        f"[drone][mid][shimmer]amix=inputs=3:weights=0.35 0.4 0.25,"
        f"volume=0.08,"
        f"afade=t=in:d=2,afade=t=out:st={VIDEO_DURATION-2}:d=2[bgm]"
    )

    ok = run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc",
        "-filter_complex", filter_complex,
        "-map", "[bgm]",
        "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-sample_fmt", "s16",
        "-t", str(VIDEO_DURATION),
        str(bgm_file)
    ], "bgm generation")

    if ok:
        print(f"  BGM generated: {bgm_file}\n")
    return ok


def step3_merge_all(video_path, temp_dir, output_path):
    """Merge: video + TTS narration (louder) + BGM (softer)

    Audio chain:
      1. Each TTS stream → volume boost → adelay to scene position
      2. BGM → volume=0.08 (already applied in step2)
      3. amix with normalize=0 (no per-input division, since TTS streams
         don't overlap — at any moment only 1 TTS + BGM is active)
      4. alimiter → prevent digital clipping
      5. loudnorm → EBU R128 loudness standardization (-16 LUFS)
         This ensures consistent volume across all produced videos and
         matches platform requirements (Douyin, Bilibili, WeChat).
    """
    print("=== Step 3: Merge Video + Narration + BGM ===")

    # Load audio mixing parameters from config
    audio_rules = _load_audio_sync_rules()
    mix_cfg = audio_rules.get("mix", {})
    tts_volume = mix_cfg.get("tts_volume", 1.5)
    loudnorm_target = mix_cfg.get("loudnorm_I", -16)
    loudnorm_tp = mix_cfg.get("loudnorm_TP", -1.5)
    loudnorm_lra = mix_cfg.get("loudnorm_LRA", 11)
    # 音频过渡平滑（transition 节）：微淡入防爆音 + 尾部淡出自然收音。
    # atrim 在波形非零点硬切会产生喀嗒声，TTS 裸拼进混音听感突兀（用户反馈：
    # 场景切换时音频衔接仓促）。参数单一权威源：audio_sync_rules.json。
    trans_cfg = audio_rules.get("transition", {})
    fade_in_s = float(trans_cfg.get("tts_fade_in_ms", 40)) / 1000.0
    fade_out_s = float(trans_cfg.get("tts_fade_out_ms", 150)) / 1000.0

    tts_dir = temp_dir / "tts_44k"
    bgm_file = temp_dir / "bgm.wav"
    temp_output = temp_dir / "final_output.mp4"
    manifest = _load_tts_manifest(temp_dir)

    # HARD GATE: config timeline must match the actual rendered video.
    # If config and the HTML S-block ever drift apart, audio gets placed by
    # config while the picture was rendered by the S-block — the tail scene's
    # narration then falls off the end of the video and is silently cut by
    # -shortest. The 5%-tolerance duration check downstream is too coarse to
    # catch a few seconds of drift, so verify placement here, per scene.
    actual_video_dur = get_duration(str(video_path))
    if actual_video_dur > 0:
        if abs(actual_video_dur - VIDEO_DURATION) > 1.0:
            print(f"  ERROR: config video_duration ({VIDEO_DURATION:.1f}s) does not match "
                  f"rendered video ({actual_video_dur:.1f}s) — config/S-block timelines "
                  f"have drifted. Re-run adjust_timeline.py + re-render.")
            return False
        # 尾场旁白越界检查只对有旁白的项目成立：纯BGM路径无 TTS 输入，
        # SCENES 仅作为视觉分镜元数据，不参与音频放置（空列表时 max() 也会崩）。
        if TTS_ENABLED and SCENES:
            last_end = max(end for _, _, end, _ in SCENES)
            if last_end > actual_video_dur + 0.5:
                print(f"  ERROR: last scene ends at {last_end:.1f}s but video is only "
                      f"{actual_video_dur:.1f}s — narration would be cut off at the tail.")
                return False

    # Build inputs: [0]=video, [1..N]=TTS scenes, [N+1]=BGM
    inputs = ["-i", str(video_path)]
    filter_parts = []

    # 纯BGM路径（TTS_ENABLED=false）：无 TTS 输入，BGM 即唯一音轨（amix inputs=1）
    for i, (scene_id, start, end, text) in enumerate(SCENES if TTS_ENABLED else []):
        h = _get_tts_hash_from_manifest(manifest, scene_id, text)
        hq_file = tts_dir / f"tts_{h}_hq.wav"
        if not hq_file.exists():
            print(f"  ERROR: TTS file not found: {hq_file.name}")
            return False
        inputs.extend(["-i", str(hq_file)])
        # SCENES already carries absolute video time (post _normalize_scenes_to_absolute),
        # so we can adelay directly by `start` without adding COVER_DURATION.
        delay_ms = int(start * 1000)
        input_idx = i + 1
        # HARD GATE: TTS must fit its scene window. atrim used to silently cut
        # overflowing audio mid-sentence ("音频没读完就跳场景") — that is a
        # timeline bug and must fail loudly, not be masked here.
        audio_dur = get_duration(str(hq_file))
        window = end - start
        if audio_dur > window + 0.1:
            print(f"  ERROR: scene {scene_id} TTS ({audio_dur:.2f}s) overflows its "
                  f"window ({window:.2f}s, +{audio_dur - window:.2f}s).")
            print(f"  Timeline is stale — run adjust_timeline.py (then re-render) before merging.")
            return False
        max_dur = min(audio_dur, window)  # ms-level safety only, guaranteed no-op above
        fade_out_st = max(max_dur - fade_out_s, 0.0)
        filter_parts.append(
            f"[{input_idx}]atrim=0:{max_dur:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={fade_in_s:.3f},afade=t=out:st={fade_out_st:.3f}:d={fade_out_s:.3f},"
            f"adelay={delay_ms}|{delay_ms},apad,volume={tts_volume}[n{i}]")

    # BGM input（可选：bgm_enabled=false 时纯旁白混音，无背景音）
    # 输入索引以实际 TTS 数量为准：纯BGM路径 _n_tts=0 → BGM 是输入[1]、amix inputs=1
    _n_tts = len(SCENES) if TTS_ENABLED else 0
    if BGM_ENABLED:
        inputs.extend(["-i", str(bgm_file)])
        bgm_idx = _n_tts + 1
        mix_labels = "".join(f"[n{i}]" for i in range(_n_tts)) + f"[{bgm_idx}]"
        n_inputs = _n_tts + 1
    else:
        print("  BGM disabled — mixing narration only")
        mix_labels = "".join(f"[n{i}]" for i in range(_n_tts))
        n_inputs = _n_tts
    filter_parts.append(
        f"{mix_labels}amix=inputs={n_inputs}:duration=longest:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.95,"
        f"loudnorm=I={loudnorm_target}:TP={loudnorm_tp}:LRA={loudnorm_lra}[aout]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", str(SAMPLE_RATE),
        "-ac", "2",
        "-shortest",
        "-movflags", "+faststart",
        str(temp_output)
    ]

    print(f"  Running FFmpeg merge...")
    ok = run_ffmpeg(cmd, "final merge")

    if not ok:
        return False

    # Replace output file. Previous version is backed up into temp_dir —
    # NEVER into the delivery directory: a stale "*.noaudio.mp4" (which
    # actually contained audio + old burned subtitles) once sat next to the
    # real deliverable and was mistaken for a final product.
    if output_path.exists():
        backup = temp_dir / f"{output_path.stem}.prev.mp4"
        if backup.exists():
            backup.unlink()
        output_path.rename(backup)
        print(f"  Previous output moved to temp: {backup}")

    temp_output.rename(output_path)
    print(f"  Output: {output_path}\n")
    return True


def _detect_speech_gaps(wav_path, min_silence=0.35, noise_db=-40):
    """用 ffmpeg silencedetect 找出 TTS 内部的停顿分界。

    返回 sorted 停顿列表：[(gap_start, gap_end, gap_center), ...]，仅保留
    时长 >= min_silence 的停顿。**尾部长静音（wav 末尾的 fade-out）会被剔除**，
    因为它不是段间分界。

    这些停顿会用作字幕切分的"真实语音边界"，替代按字符数比例线性分配的
    旧算法（char 权重无法反映真实朗读节奏，累积误差可达 2-3s）。
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(wav_path), "-af",
             f"silencedetect=noise={noise_db}dB:d={min_silence}",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30
        )
    except Exception as e:
        print(f"  [WARN] silencedetect failed on {wav_path.name}: {e}")
        return []

    raw_dur = get_duration(str(wav_path))
    gaps = []
    cur_start = None
    for line in (result.stderr or '').splitlines():
        m = re.search(r'silence_start:\s*([\d.]+)', line)
        if m:
            cur_start = float(m.group(1))
            continue
        m = re.search(r'silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)', line)
        if m and cur_start is not None:
            gap_end = float(m.group(1))
            if gap_end >= raw_dur - 0.1:
                # 尾部静音，不是段间分界
                cur_start = None
                continue
            if cur_start < 0.3:
                # 起始静音（TTS 起头轻微延迟），也不是段间分界
                cur_start = None
                continue
            gaps.append((cur_start, gap_end, (cur_start + gap_end) / 2))
            cur_start = None
    return gaps


def _split_text_by_target(text, target_count):
    """按 [。？！；] 先切，然后贪心合并最短相邻句，直到剩下 target_count 段。

    当 wav 静音检测已知真实段落数时，不再交给 group_limit=40 自作主张地合并，
    而是严格向 wav 段数对齐，避免"文本合并方式与 wav 停顿方式不一致"导致的内容错位。
    """
    raw = [s.strip() for s in re.split(r'(?<=[。？！；])', text) if s.strip()]
    if not raw:
        return [text]
    if len(raw) <= target_count:
        return raw
    # 贪心合并：每次找相邻两句总长最短的位置合并，直到剩下 target_count
    segs = raw[:]
    while len(segs) > target_count:
        best_i = 0
        best_sum = len(segs[0]) + len(segs[1])
        for i in range(1, len(segs) - 1):
            s = len(segs[i]) + len(segs[i+1])
            if s < best_sum:
                best_sum = s
                best_i = i
        segs = segs[:best_i] + [segs[best_i] + segs[best_i+1]] + segs[best_i+2:]
    return segs


def _adaptive_punct_gap_segments(text, gaps, tts_dur, min_seg=1.5, max_dev_chars=6):
    """自适应标点-gap对齐：为每个真实语音停顿找最近的文本标点断点。

    修复“句子数=gap数+1”强制映射的根因缺陷：当句内换气被检为 gap、
    而真正句界停顿低于检测阈值时，旧算法把第 k 个 gap 硬套到第 k 个句界，
    导致字幕文本与音频错位（用户反馈：1:02 起音频未读完字幕就切换）。

    新策略——文本切分点自适应跟随停顿位置：
    1. 扣除停顿时长后折算每个 gap 的期望字符位置（纯语速均匀假设）
    2. 在文本所有标点（。？！；，、：）断点中找离期望位置最近者作为切分点
    3. 偏差超过 max_dev_chars 或与已选断点冲突 → 跳过该 gap（字幕跨停顿不切换）
    4. 时间边界取 gap 结束点（下一句语音真实起点），文本与音频严格一致

    返回 (segments, cut_times_rel)；segments 比 cut_times_rel 多 1 个元素。
    无可用切分时返回 ([text], [])。
    """
    n = len(text)
    if n == 0 or tts_dur <= 0 or not gaps:
        return [text], []
    breakpoints = [m.end() for m in re.finditer(r'[。？！；，、：]', text) if 0 < m.end() < n]
    if not breakpoints:
        return [text], []
    gaps_sorted = sorted(gaps, key=lambda g: g[2])
    total_gap_dur = sum(g[1] - g[0] for g in gaps_sorted)
    speech_dur = max(tts_dur - total_gap_dur, 0.1)
    chosen = []  # [(char_pos, gap_end_time)]
    gap_dur_before = 0.0
    for g in gaps_sorted:
        # gap 起点 = 前文已读完时刻；扣除之前所有停顿得到纯语速时间轴位置
        speech_before = max(g[0] - gap_dur_before, 0.0)
        gap_dur_before += g[1] - g[0]
        expect = speech_before / speech_dur * n
        bp = min(breakpoints, key=lambda p: abs(p - expect))
        if abs(bp - expect) > max_dev_chars:
            continue  # 停顿附近无标点：句内换气，字幕不切换
        if g[1] < min_seg or g[1] > tts_dur - min_seg:
            continue  # 首/末段会短于 min_seg
        if chosen:
            prev_pos, prev_t = chosen[-1]
            if bp <= prev_pos or g[1] - prev_t < min_seg:
                continue  # 与已选断点冲突或时间过近
        chosen.append((bp, g[1]))
    if not chosen:
        return [text], []
    segments = []
    prev = 0
    for bp, _ in chosen:
        segments.append(text[prev:bp])
        prev = bp
    segments.append(text[prev:])
    return segments, [t for _, t in chosen]


def _disp_width(text):
    """字幕显示宽度：CJK/全角计 1，ASCII/半角计 0.5（字形约为汉字一半宽）。

    按字符数计长会把 Skill 等 ASCII 词高估一倍宽度，导致混排文本
    提前换行；改用显示宽度后纯 CJK 行上限不变，混排行可容纳更多字符。
    """
    return sum(0.5 if ord(ch) < 128 else 1.0 for ch in text)


_SUB_ASCII_RE = re.compile(r'[A-Za-z0-9]')


def _sub_can_break(s, i):
    a, b = s[i-1], s[i]
    if _SUB_ASCII_RE.match(b) and (_SUB_ASCII_RE.match(a) or a == ' '):
        return False  # ASCII 词组内部（QU38 / WSI 84 / 1200mm）不可断
    if b in '，。；、：？！%…）」】》':
        return False  # 避免标点悬挂行首
    return True


def _wrap_once(text, limit):
    """按显示宽度 limit 贪心断行，返回行列表（断点规则同原算法）。"""
    lines = []
    rest = text
    while _disp_width(rest) > limit:
        # 宽度不超 limit 的最大前缀长度
        w = 0.0
        max_i = 0
        for i, ch in enumerate(rest):
            w += 0.5 if ord(ch) < 128 else 1.0
            if w > limit:
                break
            max_i = i + 1
        if max_i >= len(rest):
            break
        cut = -1
        # 优先：窗口内最靠右的标点后断点
        for i in range(max_i, 0, -1):
            if rest[i-1] in '，。；、：？！' and _sub_can_break(rest, i):
                cut = i
                break
        if cut < 0 or _disp_width(rest[:cut]) < limit * 0.4:
            # 标点太靠前或没有 → 退而求其次找最靠右的合法断点
            for i in range(max_i, 0, -1):
                if _sub_can_break(rest, i):
                    cut = i
                    break
        if cut <= 0:
            cut = max_i
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        lines.append(rest)
    return lines


def _wrap_subtitle_text(text, line_limit=22, min_tail=4):
    """SRT 显示层智能换行：标点优先断行、禁拆 ASCII 词组、孤行抑制。

    libass 自动换行对 CJK 文本逐字硬断，会把 QU38、WSI 84、1200mm 等
    完整名词拆到两行（用户反馈：QU38 被拆成 QU3/8）。改为在写 SRT 时
    预先插入换行符：行宽不超 line_limit（低于 libass 自动换行阈值，
    确保不被二次换行）。

    孤行抑制（用户反馈："题。"/"Skill。"单字孤行）：贪心填满会把
    首行塞到上限、尾行只剩 1-2 字符。当尾行显示宽度 < min_tail 时，
    按行数均衡目标宽度重新断行，两行 12+12 优于 22+2。
    """
    if _disp_width(text) <= line_limit:
        return text
    lines = _wrap_once(text, line_limit)
    if len(lines) >= 2 and _disp_width(lines[-1]) < min_tail:
        total = _disp_width(text)
        n = len(lines)
        base = int(total / n) + 1  # 均衡目标：各行接近 total/n
        for lim in range(base, line_limit + 1):
            cand = _wrap_once(text, lim)
            if len(cand) <= n and _disp_width(cand[-1]) >= min_tail:
                lines = cand
                break
    return '\n'.join(lines)


_TERM_RULES_CACHE = None


def _load_subtitle_term_rules():
    """读取 config/quality/subtitle_term_rules.json（显示层术语词典，单一权威源）。

    消费方：step5（生成时替换）、step7 与 media_qa_gate（lint 拦截）。
    文件缺失/损坏时返回空规则，不阻断流水线（lint 层会提醒）。
    """
    global _TERM_RULES_CACHE
    if _TERM_RULES_CACHE is not None:
        return _TERM_RULES_CACHE
    rules_path = (Path(__file__).resolve().parents[1] / "配置" / "config"
                  / "quality" / "subtitle_term_rules.json")
    rules = {"replacements": [], "lint_patterns": []}
    if rules_path.exists():
        try:
            with open(rules_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            for k in ("replacements", "lint_patterns"):
                if isinstance(loaded.get(k), list):
                    rules[k] = loaded[k]
        except (json.JSONDecodeError, OSError) as e:
            print(f"[WARN] subtitle_term_rules.json unreadable: {e}")
    _TERM_RULES_CACHE = rules
    return rules


def _normalize_subtitle_terms(text):
    """显示层术语规范化（点Json→.json / 毫米→mm 等）。仅改字幕，不影响 TTS 朗读。"""
    for rule in _load_subtitle_term_rules().get("replacements", []):
        try:
            text = re.sub(rule.get("pattern", ""), rule.get("replace", ""), text)
        except re.error:
            continue
    return text


def _split_text_to_segments(text, group_limit=40):
    """Split narration into subtitle segments respecting semantic boundaries.

    Design principle: subtitle breaks follow LANGUAGE structure, not character
    counts.  The primary split criterion is sentence-ending punctuation (。？！；).
    Comma splitting (，、) serves as a secondary safety mechanism for sentences
    that exceed group_limit.

    Split strategy:
    1. Split at sentence/clause boundaries (。？！；) — ；marks semantically
       independent clauses common in technical/formal Chinese writing
    2. Greedily group adjacent short sentences (combined ≤ group_limit)
       to avoid fragmented 8-char subtitles
    3. If a sentence exceeds group_limit, sub-split at comma boundaries (，、)
       to ensure no subtitle segment overflows the screen

    The comma sub-split only activates when a single sentence is too long.
    Short sentences stay intact — commas do not cause splitting.
    """
    # Step 1: Split at sentence/clause-ending punctuation
    raw_sentences = re.split(r'(?<=[。？！；])', text)
    sentences = [s.strip() for s in raw_sentences if s.strip()]

    # Step 2: Greedy-group adjacent sentences within group_limit
    groups = []
    buf = ""
    for sent in sentences:
        if buf and len(buf) + len(sent) > group_limit:
            groups.append(buf)
            buf = sent
        else:
            buf = buf + sent
    if buf:
        groups.append(buf)

    # Step 3: Sub-split long groups at comma boundaries
    segments = []
    for group in groups:
        if len(group) <= group_limit:
            segments.append(group)
        else:
            # This group is a single long sentence — split at commas
            comma_parts = re.split(r'(?<=[，、])', group)
            comma_parts = [p for p in comma_parts if p.strip()]
            sub_buf = ""
            for cp in comma_parts:
                if sub_buf and len(sub_buf) + len(cp) > group_limit:
                    segments.append(sub_buf.strip())
                    sub_buf = cp
                else:
                    sub_buf = sub_buf + cp
            if sub_buf.strip():
                segments.append(sub_buf.strip())

    return segments


def _format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def step5_generate_subtitles(subtitle_path, temp_dir):
    """Generate SRT subtitle file from scene text and TTS durations.

    时间戳来源三级链（P0-a 架构：音频实测优先，估算仅作保底）：
      1. _timeline_manifest.json 句级强制对齐时间戳（asr_forced，主路径）
      2. silencedetect 标点-gap 对齐（修订02 遗留，一级降级）
      3. 字符数比例估算（二级降级，仅在前两者都不可用时触发）

    Scale-invariant parameters:
      - start_offset, end_shrink: decrease for longer videos to prevent
        cumulative subtitle lag (N scenes × offset must stay < 0.5s total)
      - min_dur: dynamic floor based on video duration, not hardcoded 1.5s
      - tts_dur capped to scene window to match atrim truncation in step3
    """
    print("=== Step 5: Generate Subtitles ===")
    tts_dir = temp_dir / "tts_44k"
    manifest = _load_tts_manifest(temp_dir)
    tl_manifest = _load_timeline_manifest(temp_dir)
    subtitle_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Scale-invariant parameter computation ---
    # For short videos (< 60s): generous offsets (imperceptible at this scale)
    # For long videos (> 300s): tight offsets to prevent N×offset cumulative drift
    N = len(SCENES)
    total_dur = VIDEO_DURATION
    if total_dur > 300:
        start_offset = 0.05   # 11 scenes × 0.05 = 0.55s total drift (was 3.3s with 0.3)
        end_shrink = 0.02
    elif total_dur > 120:
        start_offset = 0.10
        end_shrink = 0.3   # 保底：字幕最迟在场景边界前 0.3s 消失；实际消失时刻由 TTS 结束时刻驱动（见下方 scene_abs_end 计算）
    else:
        start_offset = 0.15   # short videos: slight breathing room
        end_shrink = 0.05
    # Dynamic min_dur: scales with video length, floor at 1.2s
    min_dur = max(1.2, total_dur * 0.003)

    entries = []
    idx = 1
    gap = 0.15
    # Strip non-terminal trailing punctuation only.
    # Keep sentence-ending marks (。？！) — they carry semantic closure.
    # Only strip commas, enumeration pauses, colons, ellipsis, dashes, whitespace.
    trailing_punct_re = re.compile(r'[，、,：；…—\s]+$')

    for scene_id, scene_start, scene_end, text in SCENES:
        h = _get_tts_hash_from_manifest(manifest, scene_id, text)
        hq_file = tts_dir / f"tts_{h}_hq.wav"
        if not hq_file.exists():
            print(f"  ERROR: TTS file not found: {hq_file.name} (scene {scene_id})")
            print(f"  Text hash changed — narration text was modified. Run full pipeline first.")
            return False
        raw_tts_dur = get_duration(str(hq_file))
        scene_window = scene_end - scene_start
        # Cap TTS duration to scene window — matches atrim truncation in step3_merge_all
        tts_dur = min(raw_tts_dur, scene_window)

        segments = _split_text_to_segments(text)
        if not segments:
            continue

        total_chars = sum(len(s) for s in segments)

        # Absolute time window for this scene's subtitles (scale-invariant).
        # SCENES already carries absolute video time — do NOT add COVER_DURATION
        # (that would double-shift and cause SRT to overflow the video tail).
        #
        # 字幕消失时刻 = min(TTS 音频真实结束 + 0.8s 停留, scene_end - end_shrink)
        # 让字幕跟随音频并在读完后停留片刻再撤（用户反馈：字幕不得早于音频切换），
        # 避免 "字幕比音频提前消失" 或 "音频结束前字幕先撤" 的观感错位。
        cursor = scene_start + start_offset
        tts_abs_end = scene_start + tts_dur
        scene_abs_end = min(scene_end - end_shrink, tts_abs_end + 0.8)
        available = scene_abs_end - cursor

        # ==================================================================
        # 主路径（P0-a）：manifest 句级强制对齐时间戳（音频实测）
        # ==================================================================
        # rel 时刻由 faster-whisper 对 TTS 波形实测，字幕切换边界取
        # 下一句真实开口时刻（与旧 gap 对齐的用户体验约定一致：
        # 字幕不得早于音频切换）。tts_hash 不匹配 = manifest 陈旧 →
        # 拒绝使用（与 TTS 文本哈希缓存同一套失效逻辑），降级。
        use_timed = False
        seg_starts_abs = None
        m_entry = tl_manifest.get(str(scene_id))
        if (m_entry and m_entry.get('method') == 'asr_forced'
                and m_entry.get('tts_hash') == h and m_entry.get('sentences')):
            sents = m_entry['sentences']
            segments = [s['text'] for s in sents]
            seg_starts_abs = []
            durs = []
            for i, s in enumerate(sents):
                st = scene_start + float(s['rel_start'])
                if i == 0:
                    st = max(st, scene_start + start_offset)  # 首段轻微延后避免抢跑
                if i + 1 < len(sents):
                    en = scene_start + float(sents[i + 1]['rel_start'])
                else:
                    en = scene_abs_end  # 末段：读完停留 ~0.8s 再撤（不越场景边界）
                st = min(st, max(scene_abs_end - 0.2, scene_start))
                seg_starts_abs.append(st)
                durs.append(max(en - st, min_dur))
            use_timed = True
            print(f"  Scene {scene_id}: manifest asr-forced "
                  f"({len(segments)} segs, match {m_entry.get('match_ratio')})")
        elif m_entry and m_entry.get('tts_hash') and m_entry.get('tts_hash') != h:
            print(f"  [WARN] Scene {scene_id}: timeline manifest is STALE "
                  f"(hash {str(m_entry.get('tts_hash'))[:6]} != {h[:6]}) — "
                  f"re-run TTS step to refresh alignment; falling back.")

        # ==================================================================
        # 一级降级（修订02）：自适应标点-gap对齐
        # ==================================================================
        # 修订01的“句子数=gap数+1”强制映射存在根因缺陷：当句内换气被检
        # 为 gap、真正句界停顿低于阈值未被检出时，第 k 个 gap 被硬套到第 k
        # 个句界，导致字幕与音频错位（1:02 失步）。现改为：每个 gap 独立
        # 寻找其时间位置附近的文本标点作为切分点，找不到就跳过该 gap
        # （字幕跨停顿不切换），文本与音频天然一致，不再依赖
        # “gap 数 == 句界数”假设。切换边界仍用 gap 结束点（下一句开口瞬间）。
        gaps = _detect_speech_gaps(hq_file, min_silence=0.28) if not use_timed else []
        use_gap_aligned = False
        if gaps:
            adaptive_segs, cut_times_rel = _adaptive_punct_gap_segments(
                text, gaps, tts_dur, min_seg=1.5)
            if cut_times_rel:
                segments = adaptive_segs
                seg_starts_rel = [0.0] + cut_times_rel
                seg_ends_rel = cut_times_rel + [tts_dur]
                durs = []
                seg_starts_abs = []
                for i, (rs, re_) in enumerate(zip(seg_starts_rel, seg_ends_rel)):
                    seg_start_abs = scene_start + rs
                    if i == 0:
                        seg_start_abs += start_offset  # 首段轻微延后避免抢跑
                    seg_end_abs = scene_start + re_
                    # 末段：音频读完后停留 ~0.8s 再撤（不越场景边界，见 scene_abs_end）
                    if i == len(seg_ends_rel) - 1:
                        seg_end_abs = scene_abs_end
                    seg_starts_abs.append(seg_start_abs)
                    durs.append(max(seg_end_abs - seg_start_abs, min_dur))
                use_gap_aligned = True
                print(f"  Scene {scene_id}: punct-gap aligned ({len(gaps)} gaps → {len(segments)} segs)")
            else:
                print(f"  Scene {scene_id}: no punct-matched gaps, fallback to char-prop")

        if not use_timed and not use_gap_aligned:
            # --- 保底算法：按字符数比例分配 + 最小时长下限 ---
            raw_durs = [(len(seg) / total_chars) * tts_dur for seg in segments]
            durs = [max(d, min_dur) for d in raw_durs]

            # --- scale down if total (segments + gaps) exceeds window ---
            total_gaps = gap * max(0, len(segments) - 1)
            total_seg = sum(durs)
            needed = total_seg + total_gaps

            if needed > available and needed > 0:
                # Compress durations; keep min_dur floor after scaling
                scale = (available - total_gaps) / total_seg
                scale = max(scale, 0)  # prevent negative
                durs = [max(d * scale, min_dur) for d in durs]

                # If still over (many segments hitting the floor), merge overflow
                # segments into the last visible entry instead of dropping them
                total_gaps = gap * max(0, len(segments) - 1)
                total_seg = sum(durs)
                if total_seg + total_gaps > available and len(durs) > 1:
                    # Find how many segments fit; merge the rest into the last one
                    running = 0.0
                    keep = len(segments)
                    for j in range(len(durs)):
                        needed_j = durs[j] + (gap if j < len(durs) - 1 else 0)
                        if running + needed_j <= available + 0.05:
                            running += needed_j
                        else:
                            keep = max(j, 1)  # always keep at least 1
                            break

                    if keep < len(segments):
                        # Merge all dropped segment texts into the last kept entry
                        merged_text = ''.join(segments[keep - 1:])
                        merged_dur = available - running + durs[keep - 1] + gap
                        merged_dur = max(merged_dur, min_dur)
                        segments = segments[:keep - 1] + [merged_text]
                        durs = durs[:keep - 1] + [merged_dur]
            seg_starts_abs = None  # 用旧 cursor 累加逻辑

        # --- build entries ---
        for i, seg in enumerate(segments):
            seg_dur = durs[i]
            if (use_timed or use_gap_aligned) and seg_starts_abs is not None:
                # 音频实测路径（manifest / gap）：用绝对起点，忽略 cursor 累加
                start_t = seg_starts_abs[i]
                end_t = start_t + seg_dur
            else:
                # 保底：延续 cursor 累加
                start_t = cursor
                end_t = cursor + seg_dur

            # Strip trailing punctuation only, preserve internal semantic punctuation (commas, semicolons)
            display_text = trailing_punct_re.sub('', seg).strip()
            if not display_text:
                display_text = seg  # fallback: keep original if all stripped

            # 显示层替换（如 毫米→mm）：仅改字幕文本，TTS 朗读文本不变
            for _k, _v in SUBTITLE_DISPLAY_REPLACEMENTS.items():
                display_text = display_text.replace(_k, _v)
            # 全局术语词典（subtitle_term_rules.json）：项目级替换之后应用，
            # 点Json→.json 等朗读层写法不再直通显示层
            display_text = _normalize_subtitle_terms(display_text)
            # 智能换行：标点优先断行，禁止拆散 QU38 / WSI 84 等 ASCII 词组
            display_text = _wrap_subtitle_text(display_text)

            entries.append((idx, start_t, end_t, display_text))
            idx += 1
            cursor = end_t + gap

    # Write SRT file
    # 输出前最终保底：non-overlap 校准（gap-aligned + char-prop 混摆时可能出现边界重叠）
    entries.sort(key=lambda x: x[1])  # 按开始时间排序
    fixed = []
    for i, (idx, s, e, txt) in enumerate(entries):
        if fixed:
            prev_end = fixed[-1][2]
            if s < prev_end + 0.05:
                shift = prev_end + 0.05 - s
                s += shift
                e = max(s + min_dur, e)
        if e <= s:
            e = s + min_dur
        fixed.append((idx, s, e, txt))
    entries = fixed

    with open(subtitle_path, "w", encoding="utf-8-sig") as f:
        for i, start, end, text in entries:
            f.write(f"{i}\n")
            f.write(f"{_format_srt_time(start)} --> {_format_srt_time(end)}\n")
            f.write(f"{text}\n\n")

    print(f"  Generated {len(entries)} subtitle entries")

    # Self-check: validate subtitle timing immediately
    issues = []
    for i, (idx, s, e, text) in enumerate(entries):
        if s >= e:
            issues.append(f"#{idx}: start >= end ({s:.1f} >= {e:.1f})")
        if i > 0 and entries[i-1][2] > s + 0.01:
            issues.append(f"#{idx}: overlaps #{entries[i-1][0]}")
    if issues:
        print(f"  ⚠ Subtitle self-check: {len(issues)} issue(s)!")
        for iss in issues:
            print(f"    ✗ {iss}")
    else:
        print(f"  ✓ Subtitle self-check passed")

    print(f"  Output: {subtitle_path}\n")
    return True


def step6_burn_subtitles(video_path, subtitle_path, temp_dir):
    """Burn SRT subtitles into video (hardcoded)"""
    print("=== Step 6: Burn Subtitles into Video ===")

    if not subtitle_path.exists():
        print("  No SRT file found, skipping subtitle burn-in")
        return True

    temp_output = temp_dir / "final_with_subs.mp4"

    # FFmpeg subtitles filter: use forward slashes, escape colon in drive letter
    srt_str = str(subtitle_path).replace("\\", "/")
    # Escape the colon after drive letter (D: -> D\:)
    srt_str = srt_str.replace(":", "\\:", 1)

    # Subtitle style: compact, within safe zone, outline only (no opaque box)
    style = (
        "FontName=Microsoft YaHei,"
        "FontSize=15,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=1,"
        "MarginV=25,"
        "Alignment=2"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"subtitles='{srt_str}':force_style='{style}'",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(temp_output)
    ]

    print(f"  Burning subtitles...")
    ok = run_ffmpeg(cmd, "subtitle burn-in")
    if not ok:
        return False

    # Replace the video file
    video_path.unlink()
    temp_output.rename(video_path)

    size_mb = video_path.stat().st_size / 1024 / 1024
    print(f"  Output: {video_path} ({size_mb:.1f} MB)\n")
    return True


def step4_verify(output_path):
    print("=== Step 4: Final Verify ===")
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_format", "-show_streams", "-of", "json", str(output_path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    for s in streams:
        ct = s["codec_type"]
        cn = s.get("codec_name", "?")
        if ct == "video":
            w, h = s.get("width", "?"), s.get("height", "?")
            fps = s.get("r_frame_rate", "?")
            br_bps = _parse_bitrate_value(s.get("bit_rate"))
            br = br_bps // 1000 if br_bps is not None else "N/A"
            print(f"  Video: {cn} {w}x{h} {fps}fps {br}kbps")
        elif ct == "audio":
            sr = s.get("sample_rate", "?")
            ch = s.get("channels", "?")
            br_bps = _parse_bitrate_value(s.get("bit_rate"))
            br = br_bps // 1000 if br_bps is not None else "N/A"
            print(f"  Audio: {cn} {sr}Hz {ch}ch {br}kbps")

    dur = float(fmt.get("duration", 0))
    size_mb = int(fmt.get("size", 0)) / 1024 / 1024
    print(f"  Duration: {dur:.1f}s  Size: {size_mb:.1f} MB")

    has_v = any(s["codec_type"] == "video" for s in streams)
    has_a = any(s["codec_type"] == "audio" for s in streams)
    audio_ok = has_a and any(
        int(s.get("sample_rate", 0)) >= 44100 and s.get("channels", 0) >= 2
        for s in streams if s["codec_type"] == "audio"
    )
    print(f"  Status: video={'OK' if has_v else 'MISSING'} audio={'OK(stereo 44.1kHz)' if audio_ok else 'DEGRADED'}")
    return has_v and audio_ok


def _parse_srt_time(t):
    """Parse SRT time string (00:01:23,456) to seconds."""
    h, m, s = t.replace(',', '.').split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def _write_media_quality_result(temp_dir, passed, errors, warnings, untested=None,
                                not_applicable=None):
    """媒体质检结果落盘（供 pipeline_runner 合并进 pipeline_state 追溯）。

    汇总 step7 的视频质检（_check_video_quality）、死区、字幕等全部错误/警告，
    并保留"未测/合法不适用"两个独立清单——passed=True 只表示无错误，不表示全部
    检查项都完成了。写入失败不抛异常，避免影响主流程。
    """
    try:
        out_path = Path(temp_dir) / "media_quality_result.json"
        payload = {
            "passed": bool(passed),
            "errors": errors,
            "warnings": warnings,
            "untested": untested or [],
            "not_applicable": not_applicable or [],
            "checked_at": datetime.now().isoformat(),
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# astats 的 RMS 行有两种拼写：本机 FFmpeg 打印 "RMS level dB: -52.41"，旧版与
# 元数据导出为 "RMS_level=-52.41"。只认后者会让整项音量检查永不命中、静默跳过
# （2026-09-18 审核 A07 实测复现）。两种拼写由同一条正则覆盖，测试亦复用此常量，
# 保证"用真值喂正则"而不是另写一份字面量。
_RMS_LEVEL_RE = re.compile(r'RMS[ _](?:level dB:\s*|level=)(-?\d+(?:\.\d+)?)')


def _check_video_quality(video_path, expected_duration=None):
    """Check video for blank frames, low bitrate, duration mismatch, and audio inconsistency.

    Returns (errors, warnings, untested, not_applicable) lists — 四态分开落盘：
    "没测到"不得写成通过，也不得与"按规则不适用"混用一个字段。
    """
    errors = []
    warnings = []
    untested = []
    not_applicable = []

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_format", "-show_streams", "-of", "json", str(video_path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    try:
        probe_data = json.loads(probe.stdout)
    except json.JSONDecodeError:
        errors.append("Cannot probe video file \u2014 ffprobe returned invalid JSON")
        return errors, warnings, untested, not_applicable

    v_streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"]
    a_streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"]

    if not v_streams:
        errors.append("No video stream found in output file")
        return errors, warnings, untested, not_applicable

    vs = v_streams[0]
    v_bitrate = _parse_bitrate_value(vs.get("bit_rate"))
    v_dur = float(vs.get("duration", 0))

    # --- Bitrate check: 阈值单一权威源 video_quality_rules.json（双档制） ---
    # < fail 档 → 判定渲染失败/内容缺失阻断；fail~warn 之间 → 低码率预警
    # 不阻断（合法纯文字动画实测 239-343kbps）。
    # 码率无法解析（N/A/缺失）时跳过门禁并追加 warning 保证可追溯。
    if v_bitrate is not None:
        rules = _load_video_quality_rules()
        fail_kbps, warn_kbps, legacy_single = _resolve_bitrate_thresholds(rules)
        v_br_kbps = v_bitrate // 1000
        if legacy_single:
            warnings.append(
                "video_quality_rules.json 仍为旧单档键 min_video_bitrate_kbps，"
                "建议升级为双档 min_video_bitrate_kbps_fail/_warn"
            )
        if v_br_kbps < fail_kbps:
            errors.append(
                f"Video bitrate too low: {v_br_kbps}kbps < {fail_kbps}kbps "
                f"(video_quality_rules.min_video_bitrate_kbps_fail) "
                f"\u2014 video likely blank (HTML scenes not rendering)"
            )
        elif v_br_kbps < warn_kbps:
            warnings.append(
                f"Video bitrate low: {v_br_kbps}kbps < {warn_kbps}kbps "
                f"(video_quality_rules.min_video_bitrate_kbps_warn) "
                f"\u2014 可能视觉内容不足，不阻断"
            )
    else:
        warnings.append(
            "Video bitrate 无法解析（ffprobe 返回 N/A 或缺失），已跳过码率门禁"
        )

    # --- Duration check: actual vs expected ---
    if expected_duration and v_dur > 0:
        diff = abs(v_dur - expected_duration)
        pct = diff / expected_duration * 100
        if pct > 10:
            errors.append(
                f"Video duration mismatch: actual={v_dur:.1f}s vs expected={expected_duration:.1f}s "
                f"(off by {pct:.0f}%)"
            )
        elif pct > 5:
            warnings.append(
                f"Video duration off by {pct:.0f}%: actual={v_dur:.1f}s vs expected={expected_duration:.1f}s"
            )

    # --- Frame content check: extract samples, detect uniform/blank frames ---
    if v_dur > 2:
        import tempfile
        num_samples = min(5, max(2, int(v_dur / 15)))
        timestamps = [v_dur * (i + 0.5) / num_samples for i in range(num_samples)]
        frame_sizes = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, ts in enumerate(timestamps):
                frame_path = os.path.join(tmpdir, f"f{i}.png")
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{ts:.1f}", "-i", str(video_path),
                     "-frames:v", "1", "-q:v", "2", frame_path],
                    capture_output=True
                )
                if os.path.exists(frame_path):
                    frame_sizes.append(os.path.getsize(frame_path))

        if len(frame_sizes) >= 2:
            if len(set(frame_sizes)) == 1:
                errors.append(
                    f"Blank video detected: all {len(frame_sizes)} sample frames identical "
                    f"(size={frame_sizes[0]} bytes) \u2014 HTML content not rendering"
                )
            elif all(s < 15000 for s in frame_sizes):
                errors.append(
                    f"Video likely blank: all {len(frame_sizes)} sample frames < 15KB "
                    f"(sizes: {frame_sizes})"
                )
        else:
            untested.append(
                f"Blank-frame check: UNTESTED — 请求 {num_samples} 帧，ffmpeg 只产出 "
                f"{len(frame_sizes)} 帧（{video_path}），无法裁定画面是否为空"
            )

    # --- Audio level consistency check: RMS at multiple sample points ---
    if a_streams and v_dur > 10:
        sample_ts = [v_dur * frac for frac in (0.15, 0.40, 0.65, 0.90)]
        rms_values = []
        for ts in sample_ts:
            result = subprocess.run(
                ["ffmpeg", "-ss", f"{ts:.1f}", "-t", "2", "-i", str(video_path),
                 "-af", "astats=metadata=1:reset=1",
                 "-f", "null", "-"],
                capture_output=True, text=True, encoding='utf-8', errors='replace'
            )
            rms_matches = _RMS_LEVEL_RE.findall(result.stderr)
            if rms_matches:
                try:
                    rms_values.append(float(rms_matches[-1]))
                except ValueError:
                    pass

        if len(rms_values) >= 2:
            rms_range = max(rms_values) - min(rms_values)
            rms_str = [f"{v:.1f}dB" for v in rms_values]
            if rms_range > 30:
                errors.append(
                    f"Audio level severely inconsistent: RMS range={rms_range:.1f}dB "
                    f"({rms_str}) \u2014 TTS may have been generated with different voices/methods"
                )
            elif rms_range > 20:
                warnings.append(
                    f"Audio level variation: RMS range={rms_range:.1f}dB ({rms_str})"
                )
        else:
            # 2026-09-18 审核 A07：解析口径与 FFmpeg 实际输出分叉时，此项曾静默跳过，
            # 表现为"音量检查存在但从未执行"。取不到值必须点名，不得留在隐式通过里。
            untested.append(
                f"Audio RMS consistency: UNTESTED — astats 取到 {len(rms_values)}/"
                f"{len(sample_ts)} 个 RMS 测量值，无法裁定音量一致性"
                "（检查 FFmpeg astats 输出拼写是否再次变化）"
            )
    else:
        reason = ("无音频流" if not a_streams
                  else f"时长 {v_dur:.1f}s ≤ 10s，多点 RMS 采样不适用")
        not_applicable.append(f"Audio RMS consistency: NOT_APPLICABLE — {reason}")

    return errors, warnings, untested, not_applicable


def step7_validate(subtitle_path, temp_dir, video_path=None, expected_duration=None):
    """Comprehensive post-pipeline validation gate.

    Checks:
      0. Video quality: bitrate, duration, frame content, audio levels
      1. TTS overflow: audio duration vs scene window for each scene
      2. Subtitle timing: no start>=end, no overlap, all within scene windows
      3. Scene coverage: every scene has at least one subtitle
      4. Trailing punctuation: no punctuation at end of subtitle text
      5. HTML image references: all src= paths resolve to actual files
      6. GSAP timestamp bounds: all time values within data-duration
    """
    print("=== Step 7: Validation Gate ===")
    errors = []
    warnings = []
    untested = []
    not_applicable = []
    tts_dir = temp_dir / "tts_44k"
    manifest = _load_tts_manifest(temp_dir)

    # --- 0. Video & Audio Quality Checks ---
    if video_path and video_path.exists():
        v_errors, v_warnings, v_untested, v_na = _check_video_quality(
            video_path, expected_duration)
        errors.extend(v_errors)
        warnings.extend(v_warnings)
        untested.extend(v_untested)
        not_applicable.extend(v_na)
    else:
        untested.append(
            "Video quality checks: UNTESTED — 未拿到成片路径"
            f"（video_path={video_path}），码率/空帧/时长/RMS 全部未执行"
        )

    # --- 1. TTS Overflow Check (Adaptive Thresholds) ---
    # Tolerance scales with video duration — short videos need tighter sync
    scene_count = len(SCENES)
    if VIDEO_DURATION > 300:
        overflow_tol = 0.8   # long videos: slightly more tolerance
        utilization_min = 0.35  # at least 35% of window used for narration
    elif VIDEO_DURATION > 120:
        overflow_tol = 0.5
        utilization_min = 0.45
    else:
        overflow_tol = 0.3   # short videos: tight sync
        utilization_min = 0.55

    # 纯BGM路径：无 TTS 基准 → overflow/utilization/dead-air 检查不适用（空列表短路）
    for scene_id, start, end, text in (SCENES if TTS_ENABLED else []):
        h = _get_tts_hash_from_manifest(manifest, scene_id, text)
        hq_file = tts_dir / f"tts_{h}_hq.wav"
        if not hq_file.exists():
            errors.append(f"TTS missing: scene {scene_id} ({hq_file.name})")
            continue
        dur = get_duration(str(hq_file))
        window = end - start
        if dur > window + overflow_tol:
            errors.append(
                f"TTS overflow: scene {scene_id} audio={dur:.1f}s > window={window:.1f}s "
                f"(+{dur-window:.1f}s, tol={overflow_tol}s) — run adjust_timeline.py to fix"
            )
        elif dur > window:
            warnings.append(
                f"TTS near-overflow: scene {scene_id} audio={dur:.1f}s vs window={window:.1f}s"
            )
        # TTS utilization: flag dead-air scenes where narration fills < min% of window
        if window > 0 and dur / window < utilization_min:
            warnings.append(
                f"Low TTS utilization: scene {scene_id} audio={dur:.1f}s / window={window:.1f}s "
                f"({dur/window*100:.0f}%, min={utilization_min*100:.0f}%) — consider shrinking window"
            )
        # Dead-air hard gate: silent tail after TTS ends must not exceed threshold.
        # This is the audio-sync error introduced after the wall-calc-promo regression.
        _sync_rules = _load_audio_sync_rules()
        _dead_air_cfg = _sync_rules.get("dead_air", {})
        _max_dead = float(_dead_air_cfg.get("max_seconds_per_scene", 3.0))
        _fail_on = bool(_dead_air_cfg.get("fail_on_violation", True))
        _dead_air = max(0.0, window - dur)
        if _dead_air > _max_dead:
            msg = (
                f"Audio dead-air: scene {scene_id} silent for {_dead_air:.1f}s "
                f"(TTS={dur:.1f}s, window={window:.1f}s, max_allowed={_max_dead}s) — "
                f"re-run pipeline_runner without --no-shrink, or trim scene end in config"
            )
            if _fail_on:
                errors.append(msg)
            else:
                warnings.append(msg)

    # --- 1b. 场景间旁白停顿检查（transition 节，防听感仓促）---
    # 上一场景 TTS 结束 → 下一场景开口的实际间隔低于阈值 → 拦截。
    # shrink_margin(1.5s) 正常收敛时天然满足；这里拦的是 TTS 实测时长
    # 漂移/未收敛时间轴导致的"嗘不上气"式衔接。
    _trans_cfg = _load_audio_sync_rules().get("transition", {})
    _min_gap = float(_trans_cfg.get("min_inter_narration_gap_seconds", 0.5))
    _gap_fail = bool(_trans_cfg.get("fail_on_violation", True))
    narr_spans = []  # (scene_id, narration_start, narration_end)
    for scene_id, start, end, text in SCENES:
        if not (text and text.strip()):
            continue
        h = _get_tts_hash_from_manifest(manifest, scene_id, text)
        hq_file = tts_dir / f"tts_{h}_hq.wav"
        if hq_file.exists():
            _tts_d = min(get_duration(str(hq_file)), end - start)
            narr_spans.append((scene_id, start, start + _tts_d))
    narr_spans.sort(key=lambda x: x[1])
    for (sid_a, _, end_a), (sid_b, start_b, _) in zip(narr_spans, narr_spans[1:]):
        pause = start_b - end_a
        if pause < _min_gap:
            msg = (
                f"Narration pause too short between scene {sid_a} and {sid_b}: "
                f"{pause:.2f}s < {_min_gap}s — audio transition feels rushed; "
                f"re-run adjust_timeline (shrink_margin 覆盖此阈值) 或延长 scene {sid_a} 窗口"
            )
            if _gap_fail:
                errors.append(msg)
            else:
                warnings.append(msg)

    # --- 2. Subtitle Timing Validation ---
    if not TTS_ENABLED:
        # 纯BGM路径：无旁白 → SRT 不是交付要求（类型化豁免，非放松门禁）
        print("  [INFO] Subtitle checks skipped: tts_enabled=false (no narration, SRT not required)")
        not_applicable.append(
            "Subtitle checks: NOT_APPLICABLE — tts_enabled=false，无旁白不要求 SRT")
    elif not subtitle_path.exists():
        errors.append(f"Subtitle file not found: {subtitle_path}")
    else:
        srt_content = subtitle_path.read_text(encoding='utf-8-sig')
        # Parse SRT entries
        entries = []
        blocks = re.split(r'\n\s*\n', srt_content.strip())
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                idx = int(lines[0])
                time_match = re.match(
                    r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})',
                    lines[1]
                )
                if time_match:
                    start_t = _parse_srt_time(time_match.group(1))
                    end_t = _parse_srt_time(time_match.group(2))
                    text = ' '.join(lines[2:])
                    entries.append((idx, start_t, end_t, text))

        if not entries:
            errors.append("No valid subtitle entries found in SRT file")
        else:
            # Check start < end
            for idx, start_t, end_t, text in entries:
                if start_t >= end_t:
                    errors.append(
                        f"Subtitle #{idx}: start >= end "
                        f"({start_t:.1f}s >= {end_t:.1f}s) — text: '{text[:20]}...'"
                    )

            # Check overlapping
            for i in range(len(entries) - 1):
                _, _, curr_end, _ = entries[i]
                _, next_start, _, _ = entries[i + 1]
                if curr_end > next_start + 0.01:
                    errors.append(
                        f"Subtitle overlap: #{entries[i][0]} ends at {curr_end:.1f}s, "
                        f"#{entries[i+1][0]} starts at {next_start:.1f}s"
                    )

            # Check scene coverage (using same scale-invariant params as step5)
            total_dur_v = VIDEO_DURATION
            if total_dur_v > 300:
                v_start_offset, v_end_shrink = 0.05, 0.02
            elif total_dur_v > 120:
                v_start_offset, v_end_shrink = 0.10, 0.05
            else:
                v_start_offset, v_end_shrink = 0.15, 0.05
            for scene_id, scene_start, scene_end, _ in SCENES:
                # SCENES stores absolute video time — no COVER_DURATION shift
                abs_start = scene_start + v_start_offset
                abs_end = scene_end - v_end_shrink
                # Allow a small tolerance for millisecond rounding in SRT timestamps
                has_sub = any(
                    abs_start - 0.02 <= s < abs_end for _, s, e, _ in entries
                )
                if not has_sub:
                    errors.append(
                        f"No subtitle in scene {scene_id} "
                        f"(window {abs_start:.1f}s-{abs_end:.1f}s)"
                    )

            # Check trailing non-terminal punctuation (，、etc — should be stripped)
            # Sentence-ending marks (。？！) are now preserved, so only flag non-terminal ones.
            trailing_punct_check = re.compile(r'[，、,：；…—\s]+$')
            for idx, _, _, text in entries:
                if trailing_punct_check.search(text.strip()):
                    warnings.append(
                        f"Subtitle #{idx}: trailing non-terminal punctuation — '{text[-10:]}'"
                    )

            # 单字跨行检查（排版门禁）：任一显示行仅含 1 个 CJK 字符 → error。
            # 正常流程下 _wrap_subtitle_text 均衡分行已消除孤字；仍命中说明
            # 有外部 SRT 导入或算法回退，不得交付。
            _cjk_re = re.compile(r'[\u4e00-\u9fff]')
            for block in blocks:
                _bl = block.strip().split('\n')
                if len(_bl) < 3:
                    continue
                for _ln in _bl[2:]:
                    _ln_s = _ln.strip()
                    if len(_ln_s) == 1 and _cjk_re.match(_ln_s):
                        errors.append(
                            f"Subtitle #{_bl[0]}: single CJK char on its own line "
                            f"('{_ln_s}') — orphan line break, rewrap required"
                        )

    # --- 5. HTML Image Reference Integrity ---
    html_path = _html_override_path
    if html_path and html_path.exists():
        html_content = html_path.read_text(encoding='utf-8')
        html_dir = html_path.parent
        src_refs = re.findall(r'src="([^"]+\.(jpg|jpeg|png|gif|webp))"', html_content, re.IGNORECASE)
        missing_imgs = []
        for src, ext in src_refs:
            img_file = html_dir / src
            if not img_file.exists():
                missing_imgs.append(src)
        if missing_imgs:
            errors.append(
                f"HTML references {len(missing_imgs)} missing image(s): "
                f"{missing_imgs}"
            )

        # --- 6. GSAP Timestamp Bounds ---
        dur_match = re.search(r'data-duration="(\d+(?:\.\d+)?)"', html_content)
        if dur_match:
            html_dur = float(dur_match.group(1))
            script_match = re.search(r'<script>(.*?)</script>', html_content, re.DOTALL)
            if script_match:
                t_map = parse_t_block(script_match.group(1))
                time_entries = resolve_script_times(script_match.group(1), t_map)
                gsap_times = [t for t, _ in time_entries]
                out_of_bounds = [t for t in gsap_times if t > html_dur + 0.5]
                if out_of_bounds:
                    errors.append(
                        f"GSAP timestamps exceed data-duration ({html_dur}s): "
                        f"{[f'{t:.1f}' for t in sorted(set(out_of_bounds))]}"
                    )

    # --- 7. Subtitle digit-normalization scan (safety net for AGENTS.md 数字规范) ---
    # preflight_check.narration_digits catches config-level violations upstream.
    # This step catches anything that slips through (hand-written HTML paths,
    # external SRT imports, whitelist gaps).
    if TTS_ENABLED and subtitle_path.exists():
        try:
            _digit_findings = _lint_scan_srt(subtitle_path)
        except Exception as _e:
            _digit_findings = []
            warnings.append(f"Subtitle digit lint skipped: {_e}")
        if _digit_findings:
            summary = _lint_format(_digit_findings)
            errors.append(
                f"Subtitle contains Chinese numerals for quantitative values "
                f"({len(_digit_findings)} hit(s)) — must be Arabic digits per AGENTS.md:\n{summary}"
            )

    # --- 8. 术语规范化 lint（subtitle_term_rules.json → lint_patterns）---
    # 正常流程下 step5 的 _normalize_subtitle_terms 已消除这些形态；
    # 仍命中 = 外部 SRT 导入或词典缺口（如新扩展名），拒收并提示补词典。
    if TTS_ENABLED and subtitle_path.exists():
        _srt_text = subtitle_path.read_text(encoding='utf-8-sig')
        for _rule in _load_subtitle_term_rules().get("lint_patterns", []):
            try:
                _hits = re.findall(_rule.get("pattern", ""), _srt_text)
            except re.error:
                continue
            if _hits:
                errors.append(
                    f"Subtitle term lint: {_rule.get('message', 'term violation')} — "
                    f"hits: {sorted(set(_hits))} — 补充 subtitle_term_rules.json 替换规则后重跑 step5"
                )

    # --- Report ---
    if errors:
        print(f"  [FAIL] {len(errors)} error(s):")
        for e in errors:
            print(f"    ✗ {e}")
    elif untested:
        # 未测项存在时不得输出"全部检查通过"——这是"没发现问题＝检查完成"的旧形态
        print(f"  [PASS-WITH-GAP] 无错误，但 {len(untested)} 项检查未完成（未测）：")
        for u in untested:
            print(f"    ? {u}")
    else:
        print(f"  [PASS] All critical checks passed")

    if warnings:
        print(f"  [WARN] {len(warnings)} warning(s):")
        for w in warnings:
            print(f"    ⚠ {w}")

    if not_applicable:
        print(f"  [N/A] {len(not_applicable)} 项按规则合法不适用（不计通过也不计失败）：")
        for na in not_applicable:
            print(f"    - {na}")

    # 媒体质检结果持久化：落盘供 pipeline_runner 合并进 pipeline_state（完工报告追溯）
    _write_media_quality_result(temp_dir, len(errors) == 0, errors, warnings,
                                untested, not_applicable)

    return len(errors) == 0


async def main():
    parser = argparse.ArgumentParser(description="HyperFrames 视频后处理")
    parser.add_argument("--config", help="JSON config file path for video-specific settings")
    parser.add_argument("--tts-only", action="store_true",
                        help="Only generate TTS audio, then exit (for pre-pass before rendering)")
    parser.add_argument("--quick-fix", action="store_true",
                        help="Re-run audio/subtitle/burn steps from the clean render_raw.mp4 "
                             "(saves ~5min render time; TTS step still runs — cache hits are "
                             "instant, stale text regenerates). The delivery slot is written "
                             "only when the merge succeeds.")
    args = parser.parse_args()

    config_cfg = None
    if args.config:
        print(f"Loading config: {args.config}")
        config_cfg = load_config(args.config)

    root, video_file, temp_dir, output_file, subtitle_file = get_paths(config_cfg)

    if config_cfg and 'paths' in config_cfg:
        html_project = config_cfg['paths'].get('html_project', '')
        if html_project:
            global _html_override_path
            _html_override_path = root / "程序文件" / "源码" / "hyperframes" / html_project / "index.html"

    if not args.tts_only and not args.quick_fix and not video_file.exists():
        print(f"ERROR: video not found: {video_file}")
        sys.exit(1)

    if not args.tts_only:
        print(f"Input:  {video_file}")
        print(f"Output: {output_file}")
        print(f"Subtitle: {subtitle_file}")
        print(f"Temp:   {temp_dir}\n")
    else:
        print(f"Temp:   {temp_dir}\n")

    # Quick-fix 模式：输入必须是纯净渲染 render_raw.mp4（get_paths 已如此解析）。
    # 旧实现在这里 unlink 成果槽位再把裸片复制进去——等于在后处理成功之前就把
    # 无声裸片写进交付目录，还会直接删掉已交付成片。现在槽位只由 step3（混音）
    # 与 step6（烧字幕）在成功路径上写入，中途失败则槽位保持原状。
    if args.quick_fix:
        render_raw = temp_dir / "render_raw.mp4"
        if not render_raw.exists():
            print(f"ERROR: render_raw.mp4 not found at {render_raw}")
            print("Quick-fix requires a previous render. Run full pipeline first.")
            sys.exit(1)
        print(f"=== Quick-Fix: clean input = {render_raw.name}; "
              f"delivery slot written only on success ===\n")

    # Pre-check: HTML template must have subtitle safe zones
    if not step0_validate_html_template():
        print("WARNING: HTML template missing subtitle-safe zones.")
        print("Subtitles may overlap with scene content.\n")

    # Step 1: TTS — 任何模式都不可跳过。
    # quick-fix 省的是"视频重渲染"，不是 TTS：文本未变时 step1 全部缓存命中
    # （只做 ffprobe 校验，秒级）；文本已变时必须重新生成，否则会用旧文案
    # 音频配新文案字幕（soundproof-craft 事故根因之一：旧代码在 quick-fix
    # 下整体跳过 step1，"TTS is already cached" 的假设从未被验证）。
    if TTS_ENABLED:
        ok = await step1_generate_tts(temp_dir)
        if not ok:
            sys.exit(1)
    else:
        # P0 纯BGM路径（2026-07-29）：类型化豁免，非门禁绕过——
        # 该跳过由 config tts_enabled=false 显式声明，与 --quick-fix 无关。
        print("=== Step 1: TTS skipped (tts_enabled=false, pure-BGM path) ===\n")

    # --tts-only: exit after TTS generation (pre-pass for adjust_timeline.py)
    if args.tts_only:
        print("TTS pre-pass complete. Exiting.")
        return

    ok = step2_generate_bgm(temp_dir)
    if not ok:
        sys.exit(1)

    ok = step3_merge_all(video_file, temp_dir, output_file)
    if not ok:
        sys.exit(1)

    if TTS_ENABLED:
        ok = step5_generate_subtitles(subtitle_file, temp_dir)
        if not ok:
            print("\nSubtitle generation FAILED")
            sys.exit(1)

        ok = step6_burn_subtitles(output_file, subtitle_file, temp_dir)
        if not ok:
            print("\nSubtitle burn-in FAILED")
            sys.exit(1)
    else:
        # 纯BGM路径：无旁白 → 无 SRT/烧录（类型化豁免，step7 同步豁免字幕检查）
        print("=== Steps 5-6: Subtitles skipped (tts_enabled=false, no narration) ===\n")

    ok = step4_verify(output_file)
    if not ok:
        print("\nFinal verification FAILED")
        sys.exit(1)

    # Step 7: Comprehensive validation gate
    ok = step7_validate(subtitle_file, temp_dir, output_file, VIDEO_DURATION)
    if not ok:
        print("\n⚠ VALIDATION GATE FAILED — see errors above")
        print("Fix the issues and re-run with --quick-fix (no need to re-render video)")
        sys.exit(1)

    print("\n✓ All checks passed. Done!")


if __name__ == "__main__":
    asyncio.run(main())
