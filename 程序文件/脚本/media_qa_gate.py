#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终媒体质量验收模块（Final Media QA Gate）

功能：
  在交付前进行最后一层完整质量检查。每项检查按四态词汇报告
  （PASS / FAIL / UNTESTED / NOT_APPLICABLE，见 _gate_status.py），
  总裁定 verdict 按 FAIL > UNTESTED > PASS 推导——"检查没跑成"不得
  压成"全部通过"（2026-09-18 审核 A04）。生产链接入点：
  pipeline_runner.step_postprocess 末端，经 adjudicate() 裁定。

  检查项清单（以 `_mark` 登记名为权威键，运行时计数见报告 `total_checks`；
  本文档不写项数、代码注释不编"检查N"号——两处手抄均已实测漂移过，
  2026-09-19 审核 A12。清单与运行时的双向一致由回归用例锁定）：
  video_file_exists              视频文件存在
  video_stream_exists            视频流存在
  audio_stream_exists            音频流存在
  video_bitrate_ok               视频码率下限（双档：fail 拒收 / warn 预警，阈值源 video_quality_rules.json）
  duration_valid                 时长有效（ffprobe 取不到＝未测，测到 0＝违规）
  audio_not_silent               音频非静音（silencedetect）
  playable                       文件可播放性（ffmpeg 解码试跑）
  subtitle_file_exists           字幕文件存在
  subtitle_not_empty             字幕条目非空
  subtitle_times_ordered         字幕时间单调性
  subtitle_not_overflow          字幕不越过视频结尾
  subtitle_audio_alignment       字幕-语音对齐度（P0-b：silencedetect 起口 ↔ SRT 起点，p95 门禁；
                               BGM 遮蔽→合法不适用，纯旁白样本不足→未测不得记通过）
  subtitle_timestamp_source      字幕时间戳主路径命中率（step5 逐场实测来源分布，asr_forced
                               占比低于阈值→未测而非违规：punct-gap 是设计内一级链路）
  no_black_frame_with_subtitle   黑场-字幕重叠（blackdetect 物理测量：画面空档而音频持续）
  no_single_char_subtitle_line   字幕单字跨行（单个 CJK 汉字独占一行＝排版孤字）
  subtitle_terms_normalized      字幕术语规范（朗读层形态直通显示层，如『点Json』应为『.json』）
  output_naming_valid            输出路径与命名（AGENTS.md 交付约束：禁止技术词命名）
  no_dead_air                    死区终检（连续静音超 audio_sync_rules.dead_air 阈值→拒收；
                               场景感知：narration_required=false 的封面/转场窗内静音不计违规）
  visual_boundary_verified       视觉边界检查状态（上游 visual_boundary_check 结论透传）
  declared_matches_measured      配置声明-实测一致性（config 的 fps/resolution ↔ ffprobe 实测）

用法：
  from media_qa_gate import MediaQAGate

  qa = MediaQAGate()
  passed, result = qa.validate(
      video_path='wall-crack-remedy.mp4',
      subtitle_path='wall-crack-remedy.srt'
  )
  print(result['verdict'], result['status_counts'])

  if not passed:
      for error in result['errors']:
          print(f"ERROR: {error}")
      sys.exit(1)
"""

import os
import sys
import subprocess
import json
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional
import re

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gate_status as gs

def ffprobe_get_streams(video_path: str) -> Dict[str, Any]:
    """使用 ffprobe 获取媒体流信息"""
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'stream',
                '-of', 'json',
                video_path
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get('streams', [])
    except Exception:
        pass
    return []

def ffprobe_get_duration(video_path: str) -> float:
    """使用 ffprobe 获取媒体时长"""
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1:noprint_wrappers=1',
                video_path
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass
    return -1.0

def ffmpeg_detect_silence(audio_file: str, duration: float = None) -> float:
    """检测音频中的静音时长"""
    try:
        if duration is None:
            duration = ffprobe_get_duration(audio_file)
            if duration < 0:
                return -1.0
        
        # 静音阈值单一权威源：audio_sync_rules.json → alignment.silence_db
        noise_db = _load_alignment_rules().get('silence_db', -40)
        result = subprocess.run(
            [
                'ffmpeg',
                '-i', audio_file,
                '-af', f'silencedetect=n={noise_db:g}dB:d=0.1',
                '-f', 'null',
                '-'
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )
        
        silence_duration = 0.0
        for line in result.stderr.split('\n'):
            if 'silence_duration:' in line:
                try:
                    val = float(line.split('silence_duration:')[1].strip())
                    silence_duration += val
                except ValueError:
                    pass
        
        return silence_duration
    except Exception:
        pass
    return -1.0

def _load_alignment_rules(rules_path=None) -> Dict[str, Any]:
    """读取 config/quality/audio_sync_rules.json 的 alignment 节（带默认值）。

    与 enhance_video_audio / pipeline_runner 共用同一份规则文件，
    阅阈值只声明一处（单一权威源）。读不到 alignment.silence_db 时
    回退默认 -40dB 并打印告警，与 verify_tts_product._load_silence_db 同口径。
    显式传入 rules_path 时供回归测试验证回退路径。
    """
    defaults = {
        "p95_max_seconds": 0.6,
        "min_onsets": 5,
        "silence_db": -40.0,
        "min_silence_seconds": 0.5,
        "min_asr_forced_ratio": 0.8,
        "fail_on_violation": True,
    }
    if rules_path is None:
        rules_path = (Path(__file__).resolve().parents[1] / "配置" / "config"
                      / "quality" / "audio_sync_rules.json")
    else:
        rules_path = Path(rules_path)
    if not rules_path.exists():
        print(f"  [WARN] 无法读取 {rules_path.name} 的 alignment.silence_db，回退默认 -40dB")
        return defaults
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        alignment = loaded.get("alignment", {})
        for k, v in alignment.items():
            if not k.startswith("$"):
                defaults[k] = v
        if "silence_db" not in alignment:
            print(f"  [WARN] {rules_path.name} 的 alignment 节缺少 silence_db，回退默认 -40dB")
        if "min_asr_forced_ratio" not in alignment:
            print(f"  [WARN] {rules_path.name} 的 alignment 节缺少 "
                  f"min_asr_forced_ratio，回退默认 {defaults['min_asr_forced_ratio']:g}")
        return defaults
    except (json.JSONDecodeError, OSError):
        print(f"  [WARN] 无法读取 {rules_path.name} 的 alignment.silence_db，回退默认 -40dB")
        return defaults


def _load_black_frame_rules() -> Dict[str, Any]:
    """读取 config/quality/audio_sync_rules.json 的 black_frame 节（带默认值）。

    与 alignment 节同源，阈值只声明一处（单一权威源）。
    """
    defaults = {
        "min_black_seconds": 1.0,
        "pixel_threshold": 0.10,
        "max_overlap_with_narration_seconds": 1.0,
        "fail_on_violation": True,
    }
    rules_path = (Path(__file__).resolve().parents[1] / "配置" / "config"
                  / "quality" / "audio_sync_rules.json")
    if not rules_path.exists():
        return defaults
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        section = loaded.get("black_frame", {})
        for k, v in section.items():
            if not k.startswith("$"):
                defaults[k] = v
        return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


def _load_dead_air_rules() -> Dict[str, Any]:
    """读取 config/quality/audio_sync_rules.json 的 dead_air 节（带默认值）。

    与 enhance_video_audio / pipeline_runner 同源，阈值只声明一处（单一权威源）。
    """
    defaults = {
        "max_seconds_per_scene": 3.0,
        "fail_on_violation": True,
    }
    rules_path = (Path(__file__).resolve().parents[1] / "配置" / "config"
                  / "quality" / "audio_sync_rules.json")
    if not rules_path.exists():
        return defaults
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        section = loaded.get("dead_air", {})
        for k, v in section.items():
            if not k.startswith("$"):
                defaults[k] = v
        return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


def _load_video_quality_rules() -> Dict[str, Any]:
    """读取 config/quality/video_quality_rules.json（带默认值）。

    与 enhance_video_audio._check_video_quality 同阈值同源（单一权威源），
    读不到时回退默认双档 fail=200/warn=500 并打印告警。
    文件声明按原样返回（不与默认双档键合并），保证旧单档键
    min_video_bitrate_kbps 的兼容路径可被 _resolve_bitrate_thresholds 识别。
    """
    defaults = {"min_video_bitrate_kbps_fail": 200, "min_video_bitrate_kbps_warn": 500}
    rules_path = (Path(__file__).resolve().parents[1] / "配置" / "config"
                  / "quality" / "video_quality_rules.json")
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        declared = {k: v for k, v in loaded.items() if not k.startswith("$")}
        return declared if declared else defaults
    except (json.JSONDecodeError, OSError):
        print(f"  [WARN] 无法读取 {rules_path.name}，视频码率阈值回退默认 fail=200/warn=500kbps")
        return defaults


def _resolve_bitrate_thresholds(rules: Dict[str, Any]) -> Tuple[int, int, bool]:
    """解析码率双档阈值，返回 (fail_kbps, warn_kbps, legacy_single)。

    双档键优先：实测 < fail 档 → 判定渲染失败/内容缺失（阻断）；
    fail~warn 之间 → 低码率预警不阻断（纯文字动画码率天然偏低）。
    仅存在旧单档键 min_video_bitrate_kbps 时按旧口径单档阻断
    （legacy_single=True，调用方提示升级配置），不崩溃。
    与 enhance_video_audio._resolve_bitrate_thresholds 保持同口径。
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


def _classify_video_bitrate(v_br_kbps: int, rules: Dict[str, Any]) -> Tuple[str, int, int, bool]:
    """按双档阈值归类实测码率，返回 (level, fail_kbps, warn_kbps, legacy_single)。

    level ∈ 'fail'（阻断）/ 'warn'（预警不阻断）/ 'ok'。
    """
    fail_kbps, warn_kbps, legacy_single = _resolve_bitrate_thresholds(rules)
    if v_br_kbps < fail_kbps:
        return "fail", fail_kbps, warn_kbps, legacy_single
    if v_br_kbps < warn_kbps:
        return "warn", fail_kbps, warn_kbps, legacy_single
    return "ok", fail_kbps, warn_kbps, legacy_single


def _parse_bitrate_value(raw) -> Any:
    """解析 ffprobe 的 bit_rate 字段为 int（bps），失败返回 None（码率未知）。

    ffprobe 对部分容器/流返回 "N/A" 或缺失该键，直接 int() 会抛
    ValueError 使门禁崩溃。返回 None 时调用方跳过码率门禁判定，
    并追加 warning 保证可追溯（不判 FAIL/WARN 阻断，也不虚报）。
    与 enhance_video_audio._parse_bitrate_value 保持同口径。
    """
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _config_bgm_declared(config_path: str) -> bool:
    """config 是否声明启用 BGM（顶层 bgm_enabled，回退 audio.bgm_enabled）。

    供对齐度检查归类：BGM 遮蔽全轨静音时 silencedetect 起口不可测属
    声明导致的合法不适用（NOT_APPLICABLE），而非漏检。读不到 config 时
    按未声明处理（False）——保守归入未测面，不放行。
    """
    if not config_path:
        return False
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    value = cfg.get('bgm_enabled', (cfg.get('audio') or {}).get('bgm_enabled', False))
    if isinstance(value, str):
        return value.strip().lower() in ('true', '1', 'yes', 'on')
    return bool(value)


def _resolve_narration_from_config(config_path: str) -> Tuple[Any, float]:
    """从 pipeline config 解析 narration.json 路径与 cover_duration。

    路径解析口径与 enhance_video_audio.load_config 一致（P0-03）：相对
    narration_source 优先按 HTML 项目目录（源码/hyperframes/<html_project>/）
    解析，回退到配置文件所在目录。解析失败返回 (None, cover_duration)，不崩溃。
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None, 0.0
    cover_duration = float(cfg.get('cover_duration', 0.0) or 0.0)
    raw_source = cfg.get('narration_source')
    if not raw_source:
        return None, cover_duration
    raw_source = str(raw_source)
    candidates = []
    if os.path.isabs(raw_source):
        candidates.append(Path(raw_source))
    else:
        html_project = (cfg.get('paths') or {}).get('html_project', '')
        if html_project:
            candidates.append(Path(__file__).resolve().parents[1] / "源码"
                              / "hyperframes" / html_project / raw_source)
        candidates.append(Path(config_path).resolve().parent / raw_source)
    for c in candidates:
        if c.is_file():
            return c, cover_duration
    return None, cover_duration


def _load_narration_scenes(narration_path: str, cover_duration: float = 0.0):
    """读取项目 narration.json，返回死区终检用的场景窗列表（或 None）。

    narration_required 判定与 compile_narration_to_scenes 同口径：
    cover/transition 类型强制不需要旁白。legacy 相对时间约定（首个非封面
    场景 start 落在 cover 窗内）自动平移 cover_duration 为绝对时间，与
    enhance_video_audio._normalize_scenes_to_absolute 保持一致。
    读取失败返回 None（调用方降级为全片扫描）。
    """
    try:
        with open(narration_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        raw_scenes = data.get('scenes') or []
    except (json.JSONDecodeError, OSError):
        return None
    scenes = []
    for i, s in enumerate(raw_scenes):
        stype = str(s.get('type', 'content')).lower()
        required = bool(s.get('narration_required', True))
        if stype in ('cover', 'transition'):
            required = False
        try:
            start = float(s.get('start', 0))
            end = float(s.get('end', 0))
        except (TypeError, ValueError):
            continue
        scenes.append({'scene_id': s.get('scene_id', i), 'type': stype,
                       'start': start, 'end': end,
                       'narration_required': required})
    if not scenes:
        return None
    non_cover = sorted((s for s in scenes if s['type'] != 'cover'),
                       key=lambda s: s['start'])
    if cover_duration > 0 and non_cover and non_cover[0]['start'] < cover_duration * 0.5:
        for s in non_cover:
            s['start'] += cover_duration
            s['end'] += cover_duration
    return scenes


def filter_dead_air_violations(segments: List[Tuple[float, float, float]],
                               scenes,
                               max_dead: float) -> List[Tuple[float, float, float]]:
    """场景感知的死区违规过滤（no_dead_air 的核心过滤，纯函数供回归测试）。

    封面/转场（narration_required=false）场景窗内的静音属设计特征，
    不计入死区违规；跨越边界的静音段只计入落在 narration_required=true
    场景窗内的部分时长。scenes 为 None 时降级为全片口径（原样判定）。
    返回 [(start, end, counted_dur), ...]，counted_dur 为计入的违规时长。
    """
    if scenes is None:
        return list(segments)
    windows = sorted((s['start'], s['end']) for s in scenes
                     if s.get('narration_required', True))
    violations = []
    for st, en, _dur in segments:
        counted = sum(max(0.0, min(en, we) - max(st, ws)) for ws, we in windows)
        if counted > max_dead:
            violations.append((st, en, counted))
    return violations


# 交付命名门禁（AGENTS.md → 文件与交付 → 命名）：技术词不得出现在交付文件名中，
# 交付名应为业务描述性名称（如 quickstart-demo.mp4 / 墙体裂缝修补.mp4）。
# 清单集中定义于此，output_naming_valid 为唯一消费方。
FORBIDDEN_NAME_TOKENS = ("final", "v01", "render", "raw", "tmp")
_FORBIDDEN_NAME_RE = re.compile(
    r'(?<![a-z0-9])(' + '|'.join(FORBIDDEN_NAME_TOKENS) + r')(?![a-z0-9])')


def _load_term_lint_patterns() -> List[Dict[str, str]]:
    """读取 config/quality/subtitle_term_rules.json 的 lint_patterns。

    正常流程 step5 已按 replacements 规范化显示文本，此处是交付前
    最后防线：仍命中说明有外部 SRT 导入或词典缺口。
    """
    rules_path = (Path(__file__).resolve().parents[1] / "配置" / "config"
                  / "quality" / "subtitle_term_rules.json")
    if not rules_path.exists():
        return []
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        patterns = loaded.get("lint_patterns", [])
        return [p for p in patterns if isinstance(p, dict) and p.get("pattern")]
    except (json.JSONDecodeError, OSError):
        return []


def ffmpeg_detect_black_intervals(video_path: str, min_black: float = 1.0,
                                  pix_th: float = 0.10) -> Optional[List[Tuple[float, float]]]:
    """用 blackdetect 物理扫描成片，返回黑场区间列表 [(start, end), ...]。

    这是对"观众实际看到什么"的直接测量，与声明层（T-block/S-block）
    无关——即使时间轴声明自洽，渲染产物出现黑场也会被此检测捕获。
    扫描失败（ffmpeg 异常/超时/非零退出）返回 None，与"扫过且无黑场"的
    空列表区分——前者是未测，不得压成通过（四态契约，2026-09-18 A04）。
    """
    try:
        result = subprocess.run(
            ['ffmpeg', '-i', str(video_path), '-vf',
             f'blackdetect=d={min_black}:pix_th={pix_th}',
             '-an', '-f', 'null', '-'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=180
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    intervals = []
    for line in (result.stderr or '').splitlines():
        m = re.search(r'black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)', line)
        if m:
            intervals.append((float(m.group(1)), float(m.group(2))))
    return intervals


def ffmpeg_detect_long_silences(video_path: str, noise_db: float = -40,
                                min_silence: float = 3.0,
                                duration: float = None
                                ) -> Optional[List[Tuple[float, float, float]]]:
    """用 silencedetect 扫描成片，返回超过 min_silence 的连续静音段 [(start, end, dur), ...]。

    d= 直接设为死区上限，ffmpeg 只报告超限段 —— 输出即违规清单。
    尾部静音可能只有 silence_start 没有 silence_end，给定 duration 时补齐为收尾段。
    扫描失败返回 None（未测），与"扫过且无超限静音"的空列表区分（四态契约，A04）。
    """
    try:
        result = subprocess.run(
            ['ffmpeg', '-i', str(video_path), '-af',
             f'silencedetect=noise={noise_db:g}dB:d={min_silence:g}',
             '-f', 'null', '-'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=120
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    segments = []
    start = None
    for line in (result.stderr or '').splitlines():
        m_s = re.search(r'silence_start:\s*(-?[\d.]+)', line)
        if m_s:
            start = max(float(m_s.group(1)), 0.0)
            continue
        m_e = re.search(r'silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)', line)
        if m_e and start is not None:
            segments.append((start, float(m_e.group(1)), float(m_e.group(2))))
            start = None
    if start is not None and duration and duration - start >= min_silence:
        segments.append((start, duration, duration - start))
    return segments


def ffmpeg_detect_speech_onsets(video_path: str, duration: float,
                                noise_db: float = -38,
                                min_silence: float = 0.5
                                ) -> Optional[List[float]]:
    """从成片音轨提取语音起口时刻（silence_end 事件 = 长停顿后开口）。

    适用于纯旁白成片（bgm_enabled=false）；含 BGM 时全轨无静音段，
    返回空列表，调用方按"合法不适用（BGM 遮蔽声明）/未测（样本不足）"归类。
    扫描失败（ffmpeg 异常/非零退出）返回 None＝未测，不得读成通过（A04）。
    """
    try:
        result = subprocess.run(
            ['ffmpeg', '-i', str(video_path), '-af',
             f'silencedetect=noise={noise_db}dB:d={min_silence}',
             '-f', 'null', '-'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=120
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    onsets = []
    for line in (result.stderr or '').splitlines():
        m = re.search(r'silence_end:\s*([\d.]+)', line)
        if m:
            t = float(m.group(1))
            # 尾部淡出后的伪起口不计
            if t < duration - 0.5:
                onsets.append(t)
    return onsets


def parse_srt(srt_path: str) -> List[Dict[str, Any]]:
    """解析 SRT 字幕文件"""
    entries = []
    try:
        with open(srt_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        
        # 按空行分割条目
        blocks = re.split(r'\n\s*\n', content.strip())
        
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                try:
                    # 第1行：序号
                    idx = int(lines[0])
                    
                    # 第2行：时间码
                    time_match = re.match(r'(\d+):(\d+):(\d+),(\d+)\s+-->\s+(\d+):(\d+):(\d+),(\d+)',
                                         lines[1])
                    if not time_match:
                        continue
                    
                    start_ms = (int(time_match.group(1)) * 3600 +
                               int(time_match.group(2)) * 60 +
                               int(time_match.group(3))) * 1000 + int(time_match.group(4))
                    end_ms = (int(time_match.group(5)) * 3600 +
                             int(time_match.group(6)) * 60 +
                             int(time_match.group(7))) * 1000 + int(time_match.group(8))
                    
                    # 第3+行：文本
                    text = '\n'.join(lines[2:]).strip()
                    
                    entries.append({
                        'index': idx,
                        'start_ms': start_ms,
                        'end_ms': end_ms,
                        'text': text
                    })
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    
    return entries


def _load_subtitle_timestamp_source(source_path):
    """读取 step5 落盘的逐场时间戳来源记录（temp/_subtitle_timestamp_source.json）。

    返回 (data 或 None, 不可裁定原因 或 None)。消费方是 subtitle_timestamp_source：
    该记录是"本版 SRT 的每条时间戳由哪条链产出"的实测面，缺失/损坏/不同代都判未测，
    不得用旧记录或 manifest 声明面顶替（A06 同族：登记面须＝真实读取路径）。
    """
    if not source_path:
        return None, "未提供时间戳来源记录路径（step5 落盘的 _subtitle_timestamp_source.json）"
    p = Path(source_path)
    if not p.exists():
        return None, f"时间戳来源记录不存在: {p.name}（step5 未产出或 temp 已清理）"
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return None, f"时间戳来源记录不可读: {p.name} — {e}"
    if not isinstance(data.get('scenes'), list):
        return None, f"时间戳来源记录缺少 scenes 清单: {p.name}"
    return data, None


def format_subtitle_source_line(facts: Dict[str, Any]) -> str:
    """字幕时间戳来源分布的人读行 — 唯一生成点。

    print_report 与 pipeline_runner 的交付说明提示共用本函数，终检报告行与交付
    文档里的数字不得各写一份（A12 同源纪律；交付说明 md 是该项唯一的 git 可追溯面）。
    """
    total = facts.get('scenes_total') or 0
    forced = facts.get('asr_forced') or 0
    ratio = facts.get('asr_forced_ratio')
    th = facts.get('threshold_ratio')
    if facts.get('adjudicable') is False:
        # 取数面缺失/损坏：报告与交付文档必须显式写"无从裁定"，不得印 0/0 让人读成 0%
        return ("Subtitle timestamp source: 无从裁定 — " + str(facts.get('reason') or '')
                + (f"（阈值 ≥{th:.0%}，未测默认阻断）"
                   if isinstance(th, (int, float)) else "（未测默认阻断）"))
    line = (f"Subtitle timestamp source: asr_forced {forced}/{total}"
            + (f" ({ratio:.0%})" if isinstance(ratio, (int, float)) else "")
            + f" | punct_gap {facts.get('punct_gap', 0)}"
            f" | char_prop {facts.get('char_prop', 0)}"
            + (f" — 阈值 ≥{th:.0%}" if isinstance(th, (int, float)) else ""))
    if facts.get('stale_record'):
        line += " [记录与被测字幕不同代，未裁定]"
    return line


# 字幕组检查项清单（单一权威源）：字幕缺失/声明豁免时整组一并裁定，
# 新增字幕类检查项只改这里与 docstring 目录，不再抄第二份清单（A12）。
SUBTITLE_CHECK_KEYS = ('subtitle_file_exists', 'subtitle_not_empty',
                       'subtitle_times_ordered', 'subtitle_not_overflow',
                       'subtitle_audio_alignment',
                       'subtitle_timestamp_source',
                       'no_black_frame_with_subtitle',
                       'no_single_char_subtitle_line',
                       'subtitle_terms_normalized')


class MediaQAGate:
    """最终媒体质量验收门禁（四态报告，2026-09-18 A04）。

    每项检查记录 PASS / FAIL / UNTESTED / NOT_APPLICABLE 之一（_gate_status 词汇），
    总裁定 verdict 按 FAIL > UNTESTED > PASS 推导；"检查没跑成"与"跑过且干净"
    在结果里可区分——旧实现把不可测（BGM 遮蔽起口、ffmpeg 扫描失败、bitrate
    解析不到）压成 checks=True，正是"全部通过"假象的来源。
    """

    def __init__(self):
        self.errors = []
        self.warnings = []
        self.statuses = {}
        self.status_notes = {}
        self.alignment = {}
        self.media_facts = {}

    @property
    def checks(self) -> Dict[str, bool]:
        """兼容视图：仅 PASS 记 True。新消费方应读 check_statuses。"""
        return {k: v == gs.PASS for k, v in self.statuses.items()}

    def _mark(self, name: str, status: str, note: str = ""):
        self.statuses[name] = status
        if note:
            self.status_notes[name] = note

    def validate(self,
                 video_path: str,
                 subtitle_path: str = None,
                 visual_check_passed=False,
                 config_path: str = None,
                 narration_path: str = None,
                 subtitle_source_path: str = None) -> Tuple[bool, Dict[str, Any]]:
        """执行完整的媒体质量验收

        参数：
          video_path: 视频文件路径
          subtitle_path: 字幕文件路径（None＝按声明豁免字幕，字幕组检查记 NOT_APPLICABLE）
          visual_check_passed: True/False，或 "not_applicable"（quick-fix 等声明性跳过，
                               不伪装成通过也不判违规）
          config_path: pipeline 配置文件路径（可选，提供后执行 declared_matches_measured：声明-实测一致性；
                       同时用于解析 narration_source 供 no_dead_air 场景感知排除）
          narration_path: 项目 narration.json 路径（可选，显式指定优先于 config 指针解析）
          subtitle_source_path: step5 落盘的 _subtitle_timestamp_source.json 路径（可选，
                       subtitle_timestamp_source 的唯一取数面；缺失即未测，不猜）

        返回：
          (无 FAIL 违规, 详细检查结果；含 verdict/check_statuses/untested/not_applicable)
        """
        self.errors = []
        self.warnings = []
        self.statuses = {}
        self.status_notes = {}
        self.alignment = {}
        self.media_facts = {}

        video_path = Path(video_path)

        # 检查 video_file_exists：视频文件存在
        if not video_path.exists():
            self._mark('video_file_exists', gs.FAIL, f"文件不存在: {video_path}")
            self.errors.append(f"Video file not found: {video_path}")
            return False, self._result()
        self._mark('video_file_exists', gs.PASS)

        # 获取媒体流信息
        streams = ffprobe_get_streams(str(video_path))
        duration = ffprobe_get_duration(str(video_path))

        # 检查 video_stream_exists：视频流存在
        video_streams = [s for s in streams if s.get('codec_type') == 'video']
        if video_streams:
            self._mark('video_stream_exists', gs.PASS)
        else:
            self._mark('video_stream_exists', gs.FAIL)
            self.errors.append("No video stream found")

        # 检查 audio_stream_exists：音频流存在
        audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
        if audio_streams:
            self._mark('audio_stream_exists', gs.PASS)
        else:
            self._mark('audio_stream_exists', gs.FAIL)
            self.errors.append("No audio stream found")

        # 检查 video_bitrate_ok：视频码率下限（阈值单一权威源：video_quality_rules.json，双档制）
        # < fail 档 = 画面近乎空白（HTML 场景未渲染）阻断；fail~warn 之间 =
        # 低码率预警不阻断（纯文字动画合法内容码率天然偏低）。与
        # enhance_video_audio step7 同口径，此处是交付前最后防线。
        if len(video_streams) > 0:
            v_bitrate = _parse_bitrate_value(video_streams[0].get('bit_rate'))
            if v_bitrate is not None:
                vq_rules = _load_video_quality_rules()
                v_br_kbps = v_bitrate // 1000
                level, fail_kbps, warn_kbps, legacy_single = _classify_video_bitrate(
                    v_br_kbps, vq_rules)
                if legacy_single:
                    self.warnings.append(
                        f"video_quality_rules.json 仍为旧单档键 min_video_bitrate_kbps，"
                        f"建议升级为双档 min_video_bitrate_kbps_fail/_warn")
                if level == 'fail':
                    self._mark('video_bitrate_ok', gs.FAIL)
                    self.errors.append(
                        f"Video bitrate too low: {v_br_kbps}kbps < {fail_kbps}kbps "
                        f"(video_quality_rules.min_video_bitrate_kbps_fail) — 判定渲染失败或内容缺失")
                elif level == 'warn':
                    self._mark('video_bitrate_ok', gs.PASS, "低码率预警未阻断")
                    self.warnings.append(
                        f"Video bitrate low: {v_br_kbps}kbps < {warn_kbps}kbps "
                        f"(video_quality_rules.min_video_bitrate_kbps_warn) — 可能视觉内容不足，不阻断")
                else:
                    self._mark('video_bitrate_ok', gs.PASS)
            else:
                self._mark('video_bitrate_ok', gs.UNTESTED,
                           "ffprobe bit_rate 为 N/A 或缺失，码率门禁未执行")
                self.warnings.append(
                    "Video bitrate 无法解析（ffprobe 返回 N/A 或缺失），已跳过码率门禁")
        else:
            self._mark('video_bitrate_ok', gs.UNTESTED, "无视频流，码率不可测")

        # 检查 duration_valid：时长有效（ffprobe 失败＝未测，由裁定层阻断；测到 0＝违规）
        if duration < 0:
            self._mark('duration_valid', gs.UNTESTED, "ffprobe 取不到时长")
            self.warnings.append("Could not read duration via ffprobe")
        elif duration > 0:
            self._mark('duration_valid', gs.PASS)
        else:
            self._mark('duration_valid', gs.FAIL)
            self.errors.append(f"Invalid duration: {duration}")

        # 检查 audio_not_silent：音频非静音
        if duration > 0:
            if len(audio_streams) > 0:
                silence_dur = ffmpeg_detect_silence(str(video_path), duration)
                if silence_dur < 0:
                    self._mark('audio_not_silent', gs.UNTESTED, "silencedetect 扫描失败")
                    self.warnings.append("Could not detect silence")
                elif silence_dur >= duration - 1.0:
                    self._mark('audio_not_silent', gs.FAIL)
                    self.errors.append(f"Audio is silent (or nearly silent): {silence_dur:.1f}s of {duration:.1f}s")
                else:
                    self._mark('audio_not_silent', gs.PASS)
                    if silence_dur > duration * 0.5:
                        self.warnings.append(f"Audio contains excessive silence: {silence_dur:.1f}s of {duration:.1f}s")
            else:
                self._mark('audio_not_silent', gs.UNTESTED, "无音频流，静音不可测")
        else:
            self._mark('audio_not_silent', gs.UNTESTED, "时长不可测")

        # 检查 playable：文件可播放性
        try:
            result = subprocess.run(
                ['ffmpeg', '-t', '1', '-i', str(video_path), '-f', 'null', '-'],
                capture_output=True,
                timeout=10
            )
            if result.returncode == 0:
                self._mark('playable', gs.PASS)
            else:
                self._mark('playable', gs.FAIL)
                self.errors.append("Video file is not playable or corrupted")
        except Exception as e:
            self._mark('playable', gs.UNTESTED, f"可播放性探测异常: {e}")
            self.warnings.append(f"Could not verify playability: {e}")

        # 字幕相关检查（组清单见模块级 SUBTITLE_CHECK_KEYS，单一权威源）
        if subtitle_path is None:
            # 声明性豁免（如纯 BGM 项目）：字幕组检查合法不适用，不计违规也不计通过
            for _k in SUBTITLE_CHECK_KEYS:
                self._mark(_k, gs.NOT_APPLICABLE, "未提供字幕（项目声明豁免）")
        else:
            subtitle_path = Path(subtitle_path)

            # 检查 subtitle_file_exists：字幕文件存在
            if not subtitle_path.exists():
                self._mark('subtitle_file_exists', gs.FAIL, str(subtitle_path))
                self.errors.append(f"Subtitle file not found: {subtitle_path}")
                for _k in SUBTITLE_CHECK_KEYS:
                    if _k not in self.statuses:
                        self._mark(_k, gs.UNTESTED, "字幕文件缺失，无法测量")
            else:
                self._mark('subtitle_file_exists', gs.PASS)
                # 解析字幕
                entries = parse_srt(str(subtitle_path))

                # 检查 subtitle_not_empty：字幕条目非空
                if entries:
                    self._mark('subtitle_not_empty', gs.PASS)
                else:
                    self._mark('subtitle_not_empty', gs.FAIL)
                    self.errors.append("Subtitle file is empty or malformed")
                    for _k in SUBTITLE_CHECK_KEYS:
                        # 未测的是"还没跑过"的那几项——已裁定项（存在性/空文件）不覆写
                        if _k not in self.statuses:
                            self._mark(_k, gs.UNTESTED, "字幕解析 0 条，无法测量")
                    entries = None

                if entries is not None:
                    # 检查 subtitle_times_ordered：时间单调性
                    all_ordered = True
                    for i in range(len(entries) - 1):
                        if entries[i]['end_ms'] > entries[i+1]['start_ms']:
                            all_ordered = False
                            self.errors.append(
                                f"Subtitle overlap: entry {entries[i]['index']} "
                                f"ends at {entries[i]['end_ms']}, "
                                f"entry {entries[i+1]['index']} starts at {entries[i+1]['start_ms']}"
                            )
                            break
                    self._mark('subtitle_times_ordered',
                               gs.PASS if all_ordered else gs.FAIL)

                    # 检查 subtitle_not_overflow：字幕不越过视频结尾
                    if duration > 0 and len(entries) > 0:
                        max_end_ms = max(e['end_ms'] for e in entries)
                        max_end_s = max_end_ms / 1000.0
                        overflow = max_end_s - duration
                        if overflow > 0.5:  # 允许 500ms 误差
                            self._mark('subtitle_not_overflow', gs.FAIL)
                            self.errors.append(
                                f"Subtitle overflow: last entry ends at {max_end_s:.1f}s, "
                                f"video ends at {duration:.1f}s (+{overflow:.1f}s over)"
                            )
                        else:
                            self._mark('subtitle_not_overflow', gs.PASS)
                    else:
                        self._mark('subtitle_not_overflow', gs.UNTESTED,
                                   "视频时长不可测，无法比对字幕结尾")

                    # 检查 subtitle_audio_alignment：字幕-语音对齐度（P0-b，Audio-First 门禁）
                    # 成片音轨 silencedetect 起口 ↔ 最近 SRT 条目起点，p95 偏差超阈值拒收。
                    # 阈值单一权威源：config/quality/audio_sync_rules.json → alignment 节。
                    if duration > 0 and len(audio_streams) > 0:
                        rules = _load_alignment_rules()
                        onsets = ffmpeg_detect_speech_onsets(
                            str(video_path), duration,
                            noise_db=rules['silence_db'],
                            min_silence=rules['min_silence_seconds'])
                        srt_windows = sorted(
                            (e['start_ms'] / 1000.0, e['end_ms'] / 1000.0)
                            for e in entries)
                        srt_starts = [w[0] for w in srt_windows]

                        def _onset_deviation(t):
                            # 句内停顿再开口：起口落在某条字幕的展示区间内时
                            # 字幕本就在屏（TTS 句内换气/标点停顿 > min_silence
                            # 被检出为新起口），观众无失步感 → 偏差记 0。
                            # 只对字幕窗口之外的起口测最近起点偏差，
                            # 真正的整体错位（起口在无字幕区）仍会被拦截。
                            # 注意：该归零口径使 p95=0 只说明"每个起口都落在某条
                            # 字幕窗口内"，不得读成逐句精确同步（A08 语义澄清）。
                            for st, en in srt_windows:
                                if st - 0.15 <= t <= en:
                                    return 0.0
                            return min(abs(s - t) for s in srt_starts)

                        if onsets is None:
                            self._mark('subtitle_audio_alignment', gs.UNTESTED,
                                       "silencedetect 起口扫描失败")
                            self.warnings.append(
                                "Subtitle-audio alignment scan failed (ffmpeg error)")
                        else:
                            deviations = sorted(_onset_deviation(t) for t in onsets)
                            p95 = None
                            if deviations:
                                idx = max(0, int(len(deviations) * 0.95 + 0.999) - 1)
                                p95 = deviations[idx]
                            self.alignment = {
                                'onsets_detected': len(onsets),
                                'p95_deviation_seconds': round(p95, 3) if p95 is not None else None,
                                'max_deviation_seconds': round(deviations[-1], 3) if deviations else None,
                                'threshold_p95_seconds': rules['p95_max_seconds'],
                                # A08 语义澄清（随记录面下发，禁止下游把 p95=0
                                # 读成逐句精确同步）：本指标只裁定"每个语音
                                # 起口是否落在某条字幕窗口内/距最近起点多远"。
                                'measure_semantics': (
                                    "onset↔nearest SRT-window deviation with "
                                    "in-window zeroing; p95=0 means every onset "
                                    "falls inside some subtitle window — NOT "
                                    "per-sentence sync precision"),
                            }
                            if len(onsets) < rules['min_onsets']:
                                if _config_bgm_declared(config_path):
                                    # config 声明启用 BGM：全轨无静音段是设计形态，
                                    # silencedetect 口径合法不适用（不阻断，也不计通过）
                                    self._mark('subtitle_audio_alignment', gs.NOT_APPLICABLE,
                                               f"仅 {len(onsets)} 起口（min {rules['min_onsets']}），"
                                               f"config 声明 bgm_enabled，silencedetect 口径不适用")
                                    self.warnings.append(
                                        f"Subtitle-audio alignment not measurable: only "
                                        f"{len(onsets)} speech onsets detected "
                                        f"(min {rules['min_onsets']}, BGM declared — "
                                        f"silence-based check not applicable)"
                                    )
                                else:
                                    # 纯旁白项目起口样本不足：裁定依据缺失＝未测，
                                    # 不得压成通过（旧实现 checks=True 即 A04 原缺陷）
                                    self._mark('subtitle_audio_alignment', gs.UNTESTED,
                                               f"仅 {len(onsets)} 起口 < min_onsets "
                                               f"{rules['min_onsets']}，p95 无从裁定")
                                    self.warnings.append(
                                        f"Subtitle-audio alignment UNTESTED: only "
                                        f"{len(onsets)} speech onsets detected "
                                        f"(min {rules['min_onsets']}) — not adjudicated"
                                    )
                            elif p95 > rules['p95_max_seconds']:
                                msg = (f"Subtitle-audio misalignment: p95 deviation "
                                       f"{p95:.3f}s > {rules['p95_max_seconds']}s "
                                       f"({len(onsets)} onsets, max {deviations[-1]:.3f}s)")
                                if rules.get('fail_on_violation', True):
                                    self._mark('subtitle_audio_alignment', gs.FAIL)
                                    self.errors.append(msg)
                                else:
                                    self._mark('subtitle_audio_alignment', gs.PASS,
                                               "fail_on_violation=false，超限仅预警")
                                    self.warnings.append(msg)
                            else:
                                self._mark('subtitle_audio_alignment', gs.PASS)
                    elif len(audio_streams) == 0:
                        self._mark('subtitle_audio_alignment', gs.UNTESTED,
                                   "无音频流，起口不可测")
                    else:
                        self._mark('subtitle_audio_alignment', gs.UNTESTED,
                                   "时长不可测")

                    # 检查 subtitle_timestamp_source：字幕时间戳主路径命中率
                    # （2026-09-19 普查裁定，方案 A）。动因：subtitle_audio_alignment
                    # 量的是"语音起口 ↔ 最近字幕窗口"，punct-gap 一级降级同样由真实
                    # 静音驱动，两条链在该口径下 7 份成片一律 p95=0.0s——整项目对齐全
                    # 降级在终检报告里与主路径不可区分（09-18 那次即此形态）。本项读
                    # step5 逐场实测来源记录，占比不足即判 UNTESTED。
                    # 不判 FAIL：punct-gap 是设计内一级链路（离线环境合法路径），
                    # 把它判成缺陷属篡改既有取舍；未测按四态规则默认阻断、可知情放行。
                    # 阈值单一权威源：audio_sync_rules.json → alignment.min_asr_forced_ratio。
                    src_data, src_err = _load_subtitle_timestamp_source(subtitle_source_path)
                    min_ratio = float(_load_alignment_rules()['min_asr_forced_ratio'])
                    src_facts = None
                    if src_err:
                        self._mark('subtitle_timestamp_source', gs.UNTESTED, src_err)
                        self.warnings.append(
                            f"Subtitle timestamp source UNTESTED: {src_err} — 主路径命中率"
                            f"无从裁定（不得读成主路径已跑成）")
                        # 未测也要有事实位：交付说明的必填节要能写出"为何无从裁定"，
                        # 而不是因为取不到数就在交付面上留白（要求3：不得只留日志行）。
                        src_facts = {'scenes_total': 0, 'asr_forced': 0,
                                     'punct_gap': 0, 'char_prop': 0,
                                     'adjudicable': False, 'reason': src_err,
                                     'threshold_ratio': min_ratio}
                    else:
                        s_list = src_data.get('scenes') or []
                        cnt = {k: sum(1 for s in s_list if s.get('source') == k)
                               for k in ('asr_forced', 'punct_gap', 'char_prop')}
                        total = len(s_list)
                        ratio = (cnt['asr_forced'] / total) if total else None
                        # 代次绑定：记录必须描述被测的这份 SRT。_timeline_manifest 同族
                        # 教训——交付后磁盘现存的记录可能属于后一版成片，旧记录不得裁定。
                        stale = (str(src_data.get('subtitle_file') or '') != subtitle_path.name
                                 or src_data.get('srt_entries') != len(entries))
                        src_facts = {
                            'asr_forced': cnt['asr_forced'],
                            'punct_gap': cnt['punct_gap'],
                            'char_prop': cnt['char_prop'],
                            'scenes_total': total,
                            'asr_forced_ratio': (round(ratio, 3)
                                                 if ratio is not None else None),
                            'threshold_ratio': min_ratio,
                            'source_record': Path(subtitle_source_path).name,
                            'record_subtitle_file': src_data.get('subtitle_file'),
                            'record_srt_entries': src_data.get('srt_entries'),
                            'stale_record': bool(stale),
                        }
                        if stale:
                            self._mark('subtitle_timestamp_source', gs.UNTESTED,
                                       f"来源记录属 {src_data.get('subtitle_file')} "
                                       f"({src_data.get('srt_entries')} 条)，被测字幕为 "
                                       f"{subtitle_path.name} ({len(entries)} 条) — 不同代")
                            self.warnings.append(
                                "Subtitle timestamp source UNTESTED: 来源记录与被测字幕"
                                "不同代（文件名或条目数不符），占比不作裁定")
                        elif total == 0:
                            self._mark('subtitle_timestamp_source', gs.UNTESTED,
                                       "来源记录 0 场景，占比无从裁定")
                            self.warnings.append(
                                "Subtitle timestamp source UNTESTED: 来源记录无逐场归属")
                        elif ratio < min_ratio:
                            self._mark('subtitle_timestamp_source', gs.UNTESTED,
                                       f"主路径 asr_forced {cnt['asr_forced']}/{total}"
                                       f"＝{ratio:.0%} < min_asr_forced_ratio "
                                       f"{min_ratio:g}（punct_gap {cnt['punct_gap']} / "
                                       f"char_prop {cnt['char_prop']}）")
                            self.warnings.append(
                                f"Subtitle timestamp source UNTESTED: asr_forced "
                                f"{cnt['asr_forced']}/{total}＝{ratio:.0%} 低于阈值 "
                                f"{min_ratio:g} — 降级是设计内链路故不判违规，但主路径"
                                f"未跑成须经知情放行 --accept-media-untested 并留痕")
                        else:
                            self._mark('subtitle_timestamp_source', gs.PASS)
                    if src_facts:
                        self.media_facts['subtitle_timestamp_source'] = src_facts

                    # 检查 no_black_frame_with_subtitle：黑场-字幕重叠（物理测量，直接回答"观众看到什么"）
                    # 背景：曾出现 end-fade 声明与场景窗口脱钩，尾部 ~5s 黑场
                    # 但音频/字幕持续播放。声明层门禁（step0）只能拦截已知
                    # 形态，此处用 blackdetect 对渲染产物做无假设扫描。
                    # 阈值单一权威源：audio_sync_rules.json → black_frame 节。
                    if duration > 0 and len(video_streams) > 0:
                        bf_rules = _load_black_frame_rules()
                        black_intervals = ffmpeg_detect_black_intervals(
                            str(video_path),
                            min_black=bf_rules['min_black_seconds'],
                            pix_th=bf_rules['pixel_threshold'])
                        if black_intervals is None:
                            self._mark('no_black_frame_with_subtitle', gs.UNTESTED,
                                       "blackdetect 扫描失败")
                            self.warnings.append("Black-frame scan failed (ffmpeg error)")
                        else:
                            max_ov = bf_rules['max_overlap_with_narration_seconds']
                            srt_spans = [(e['start_ms'] / 1000.0, e['end_ms'] / 1000.0)
                                         for e in entries]
                            violations = []
                            for bs, be in black_intervals:
                                for ss, se in srt_spans:
                                    overlap = min(be, se) - max(bs, ss)
                                    if overlap > max_ov:
                                        violations.append((bs, be, ss, se, overlap))
                            self.media_facts['black_intervals'] = [
                                (round(bs, 2), round(be, 2))
                                for bs, be in black_intervals]
                            if violations and bf_rules.get('fail_on_violation', True):
                                self._mark('no_black_frame_with_subtitle', gs.FAIL)
                            elif violations:
                                self._mark('no_black_frame_with_subtitle', gs.PASS,
                                           "fail_on_violation=false，重叠仅预警")
                            else:
                                self._mark('no_black_frame_with_subtitle', gs.PASS)
                            for bs, be, ss, se, overlap in violations:
                                msg = (f"Black screen while subtitle showing: black "
                                       f"{bs:.1f}-{be:.1f}s overlaps subtitle "
                                       f"{ss:.1f}-{se:.1f}s by {overlap:.1f}s "
                                       f"(threshold {max_ov}s) — 画面空档但音频/字幕持续")
                                if bf_rules.get('fail_on_violation', True):
                                    self.errors.append(msg)
                                else:
                                    self.warnings.append(msg)
                    elif len(video_streams) == 0:
                        self._mark('no_black_frame_with_subtitle', gs.UNTESTED,
                                   "无视频流")
                    else:
                        self._mark('no_black_frame_with_subtitle', gs.UNTESTED,
                                   "时长不可测")

                    # 检查 no_single_char_subtitle_line：字幕单字跨行（排版孤字，严重破坏阅读体验）
                    # step5 均衡分行算法已从源头消除，此处是交付前复查：
                    # 拦截外部导入/手工编辑的 SRT。
                    _cjk_re = re.compile(r'^[\u4e00-\u9fff]$')
                    orphan_lines = []
                    for e in entries:
                        for ln in e['text'].split('\n'):
                            if _cjk_re.match(ln.strip()):
                                orphan_lines.append((e['index'], ln.strip()))
                    self._mark('no_single_char_subtitle_line',
                               gs.PASS if not orphan_lines else gs.FAIL)
                    for idx, ch in orphan_lines:
                        self.errors.append(
                            f"Single-character subtitle line: entry #{idx} "
                            f"has orphan char '{ch}' on its own line — 孤字跨行")

                    # 检查 subtitle_terms_normalized：字幕术语规范 lint（朗读层形态直通显示层）
                    # 词典单一权威源：subtitle_term_rules.json → lint_patterns。
                    term_hits = []
                    for pat in _load_term_lint_patterns():
                        try:
                            regex = re.compile(pat['pattern'])
                        except re.error:
                            continue
                        for e in entries:
                            for m_hit in regex.findall(e['text']):
                                term_hits.append(
                                    (e['index'], m_hit,
                                     pat.get('message', 'term lint hit')))
                    self._mark('subtitle_terms_normalized',
                               gs.PASS if not term_hits else gs.FAIL)
                    for idx, hit, msg in term_hits:
                        self.errors.append(
                            f"Subtitle term violation: entry #{idx} contains "
                            f"'{hit}' — {msg}（补充 subtitle_term_rules.json 词典后重跑 step5）")

        # 检查 output_naming_valid：输出路径命名正确性（AGENTS.md 交付约束：禁止技术词命名）
        # 被验收视频/字幕文件名命中 final/v01/render/raw/tmp → 拒收，
        # 交付名须用业务描述性名称。清单集中定义：FORBIDDEN_NAME_TOKENS。
        naming_violations = []
        for label, p in (("视频", video_path), ("字幕", subtitle_path)):
            if not p:
                continue
            hits = _FORBIDDEN_NAME_RE.findall(Path(p).stem.lower())
            if hits:
                naming_violations.append((label, Path(p).name, sorted(set(hits))))
        self._mark('output_naming_valid', gs.PASS if not naming_violations else gs.FAIL)
        for label, name, hits in naming_violations:
            self.errors.append(
                f"Delivery naming violation: {label}文件名 '{name}' 含技术词 "
                f"{'/'.join(hits)} — 交付名须为业务描述性名称（AGENTS.md 交付约束）")

        # 检查 no_dead_air：死区终检（阈值单一权威源：audio_sync_rules.json → dead_air 节）
        # step7 的死区检查基于声明的场景窗口；此处对最终成品做物理测量兜底，
        # 拦截任何来源的超长连续静音（含 BGM 成片全轨无静音，自然通过）。
        # 场景感知：narration_required=false（cover/transition）窗内静音属设计
        # 特征不计入违规；无场景数据时降级为全片扫描并告警。
        if duration > 0 and len(audio_streams) > 0:
            da_rules = _load_dead_air_rules()
            max_dead = float(da_rules.get('max_seconds_per_scene', 3.0))
            long_silences = ffmpeg_detect_long_silences(
                str(video_path),
                noise_db=_load_alignment_rules().get('silence_db', -40),
                min_silence=max_dead,
                duration=duration)
            if long_silences is None:
                self._mark('no_dead_air', gs.UNTESTED, "silencedetect 死区扫描失败")
                self.warnings.append("Dead-air scan failed (ffmpeg error)")
            else:
                narration_scenes = None
                if narration_path:
                    cover_dur = 0.0
                    if config_path:
                        _, cover_dur = _resolve_narration_from_config(config_path)
                    narration_scenes = _load_narration_scenes(narration_path, cover_dur)
                elif config_path:
                    resolved_narration, cover_dur = _resolve_narration_from_config(config_path)
                    if resolved_narration:
                        narration_scenes = _load_narration_scenes(resolved_narration, cover_dur)
                if narration_scenes is None and long_silences:
                    self.warnings.append(
                        "未提供场景数据（--narration 或 config 的 narration_source），"
                        "死区终检降级为全片扫描，封面/转场静音可能误报")
                violations = filter_dead_air_violations(
                    long_silences, narration_scenes, max_dead)
                self.media_facts['dead_air_intervals'] = [
                    (round(st, 2), round(en, 2)) for st, en, _ in violations]
                if violations and da_rules.get('fail_on_violation', True):
                    self._mark('no_dead_air', gs.FAIL)
                elif violations:
                    self._mark('no_dead_air', gs.PASS,
                               "fail_on_violation=false，超限仅预警")
                else:
                    self._mark('no_dead_air', gs.PASS)
                for st, en, dur_s in violations:
                    msg = (f"Dead air in final video: {st:.1f}-{en:.1f}s 连续静音 "
                           f"计入 {dur_s:.1f}s > {max_dead}s（dead_air.max_seconds_per_scene，"
                           f"已排除旁白非必需场景窗）" if narration_scenes is not None else
                           f"Dead air in final video: {st:.1f}-{en:.1f}s 连续静音 "
                           f"{dur_s:.1f}s > {max_dead}s（dead_air.max_seconds_per_scene）")
                    if da_rules.get('fail_on_violation', True):
                        self.errors.append(msg)
                    else:
                        self.warnings.append(msg)
        elif len(audio_streams) == 0:
            self._mark('no_dead_air', gs.UNTESTED, "无音频流，死区不可测")
        else:
            self._mark('no_dead_air', gs.UNTESTED, "时长不可测")

        # 检查 visual_boundary_verified：视觉边界检查状态（上游 visual_boundary_check 的结论透传）
        # "not_applicable"＝上游按声明合法跳过（quick-fix），不计通过也不判违规；
        # False＝上游未确认（未跑/未过）＝未测，旧实现只发警告不留状态位。
        if visual_check_passed == "not_applicable":
            self._mark('visual_boundary_verified', gs.NOT_APPLICABLE,
                       "上游视觉检查被声明性跳过（如 quick-fix）")
        elif visual_check_passed:
            self._mark('visual_boundary_verified', gs.PASS)
        else:
            self._mark('visual_boundary_verified', gs.UNTESTED,
                       "视觉边界检查未确认（上游未通过或未回报）")
            self.warnings.append("Visual boundary check not passed or not verified")

        # 检查 declared_matches_measured：配置声明-实测一致性（fps / resolution）
        # 背景：曾出现 config 声明 fps=30 但渲染器实际输出 25fps 的失效配置，
        # 声明与产物脱钩会污染输入指纹缓存判定，必须在交付前拦截。
        if not config_path:
            self._mark('declared_matches_measured', gs.NOT_APPLICABLE,
                       "未提供 config，声明-实测比对无对照面")
        elif len(video_streams) > 0:
            self._check_declared_vs_measured(config_path, video_streams[0])
        else:
            self._mark('declared_matches_measured', gs.UNTESTED, "无视频流，实测值缺失")

        # 总体判定：passed 保持旧语义（无 FAIL 违规）；四态总裁定见 verdict
        passed = len(self.errors) == 0
        return passed, self._result()

    def _check_declared_vs_measured(self, config_path: str,
                                    video_stream: Dict[str, Any]) -> None:
        """declared_matches_measured：config 声明的 fps/resolution 必须与 ffprobe 实测一致。

        实测值同时记录到 media_facts，供完工报告/交付说明直接引用，
        避免交付文档手工填写产生漂移。
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self._mark('declared_matches_measured', gs.UNTESTED, f"config 读取失败: {e}")
            self.warnings.append(f"Cannot read config for declared-vs-measured check: {e}")
            return

        # 实测 fps（r_frame_rate 形如 "25/1"）
        measured_fps = None
        rate_str = video_stream.get('r_frame_rate', '')
        m = re.match(r'^(\d+)/(\d+)$', rate_str)
        if m and int(m.group(2)) > 0:
            measured_fps = int(m.group(1)) / int(m.group(2))
        measured_w = video_stream.get('width')
        measured_h = video_stream.get('height')
        self.media_facts['measured_fps'] = measured_fps
        self.media_facts['measured_resolution'] = (
            f"{measured_w}x{measured_h}" if measured_w and measured_h else None)

        # 声明值（顶层优先，回退 project_info）
        declared_fps = cfg.get('fps', cfg.get('project_info', {}).get('fps'))
        declared_res = cfg.get('resolution',
                               cfg.get('project_info', {}).get('resolution'))
        self.media_facts['declared_fps'] = declared_fps
        self.media_facts['declared_resolution'] = declared_res

        compared = False
        ok = True
        if declared_fps is not None and measured_fps is not None:
            compared = True
            if abs(float(declared_fps) - measured_fps) > 0.1:
                ok = False
                self.errors.append(
                    f"Declared-vs-measured fps mismatch: config declares "
                    f"{declared_fps}fps but ffprobe measures {measured_fps:g}fps "
                    f"— fix config or renderer, do not ship stale declarations"
                )
        if declared_res and measured_w and measured_h:
            compared = True
            rm = re.match(r'^(\d+)\s*[xX×]\s*(\d+)$', str(declared_res).strip())
            if rm and (int(rm.group(1)) != measured_w or int(rm.group(2)) != measured_h):
                ok = False
                self.errors.append(
                    f"Declared-vs-measured resolution mismatch: config declares "
                    f"{declared_res} but ffprobe measures {measured_w}x{measured_h}"
                )
        if not compared:
            self._mark('declared_matches_measured', gs.UNTESTED,
                       "config 未声明 fps/resolution，无对照面")
        else:
            self._mark('declared_matches_measured', gs.PASS if ok else gs.FAIL)

    def _result(self) -> Dict[str, Any]:
        """返回结构化结果（四态报告：未测/不适用独立成清单，不并入通过数）。"""
        untested = [
            f"{k} — {self.status_notes.get(k, '未测')}"
            for k, v in self.statuses.items() if v == gs.UNTESTED
        ]
        not_applicable = [
            f"{k} — {self.status_notes.get(k, '合法不适用')}"
            for k, v in self.statuses.items() if v == gs.NOT_APPLICABLE
        ]
        return {
            'passed': len(self.errors) == 0,
            'verdict': gs.verdict(self.errors, untested),
            'errors': self.errors,
            'warnings': self.warnings,
            'checks': self.checks,
            'check_statuses': dict(self.statuses),
            'status_counts': gs.count_statuses(
                [{'status': s} for s in self.statuses.values()]),
            'untested': untested,
            'not_applicable': not_applicable,
            'alignment': self.alignment,
            'media_facts': self.media_facts,
            'total_checks': len(self.statuses),
            'passed_checks': sum(1 for v in self.statuses.values() if v == gs.PASS)
        }


def adjudicate(result: Dict[str, Any], accept_untested: bool = False) -> Tuple[bool, str]:
    """生产链接入点的统一裁定（2026-09-18 A04）。

    返回 (放行与否, 原因)。规则：
    - verdict=FAIL → 不放行，首条错误入原因；
    - verdict=UNTESTED → "没跑成的检查"不得读成通过——默认不放行，
      仅显式 accept_untested（操作者知情放行并留痕）时放行；
    - verdict=PASS（NOT_APPLICABLE 不计违规也不计通过）→ 放行。
    """
    verdict = result.get('verdict')
    if verdict == gs.FAIL:
        errs = result.get('errors') or []
        return False, f"Final media QA FAIL: {errs[0] if errs else 'see details'}"
    if verdict == gs.UNTESTED:
        untested = result.get('untested') or []
        detail = untested[0] if untested else 'see details'
        if accept_untested:
            return True, (f"UNTESTED accepted via --accept-media-untested "
                          f"({len(untested)} check(s)): {detail}")
        return False, (f"Final media QA incomplete: {len(untested)} check(s) UNTESTED "
                       f"— {detail}（UNTESTED 不是通过；修复测量环境后重跑，"
                       f"或知情放行 --accept-media-untested 并留痕）")
    return True, ""

def print_report(result: Dict[str, Any]) -> None:
    """人读报告行（从 main 拆出：报告文案属可断言的行为面，不靠 grep 源码锁）。"""
    counts = result.get('status_counts', {})
    print(f"[{result['verdict']}] Media QA Gate "
          f"(PASS={counts.get(gs.PASS, 0)}, FAIL={counts.get(gs.FAIL, 0)}, "
          f"UNTESTED={counts.get(gs.UNTESTED, 0)}, "
          f"NOT_APPLICABLE={counts.get(gs.NOT_APPLICABLE, 0)}, "
          f"total={result['total_checks']})")

    align = result.get('alignment') or {}
    if align.get('onsets_detected') is not None:
        p95 = align.get('p95_deviation_seconds')
        print(f"  Alignment: {align['onsets_detected']} onsets, "
              f"p95={p95 if p95 is not None else 'n/a'}s "
              f"(threshold {align.get('threshold_p95_seconds')}s)")
        if p95 == 0:
            # A08：归零口径最易被读成"逐句精确同步"，报告行当场点名语义边界
            print("    note: p95=0 = 所有起口均落在某条字幕窗口内（窗口内归零口径），"
                  "非逐句精确同步")

    facts = result.get('media_facts') or {}
    if facts.get('measured_fps') is not None:
        print(f"  Media facts: measured {facts['measured_fps']:g}fps "
              f"{facts.get('measured_resolution')} "
              f"(declared {facts.get('declared_fps')}fps {facts.get('declared_resolution')})")

    src = facts.get('subtitle_timestamp_source')
    if src:
        # 交付说明 md 里"字幕时间戳来源"一节以此行为准（同一生成点，见
        # format_subtitle_source_line）——占比与分布不得散落在日志行里
        print(f"  {format_subtitle_source_line(src)}")

    if result['errors']:
        print(f"[FAIL — ERRORS]")
        for err in result['errors']:
            print(f"  - {err}")

    if result['untested']:
        print(f"[UNTESTED — 不得读成通过]")
        for u in result['untested']:
            print(f"  - {u}")

    if result['not_applicable']:
        print(f"[NOT_APPLICABLE — 不计违规也不计通过]")
        for n in result['not_applicable']:
            print(f"  - {n}")

    if result['warnings']:
        print(f"[WARNINGS]")
        for warn in result['warnings']:
            print(f"  - {warn}")


def main():
    """命令行工具"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Final media quality assurance')
    parser.add_argument('--video', required=True, help='Path to video file')
    parser.add_argument('--subtitle', help='Path to subtitle file')
    parser.add_argument('--visual-check-passed', action='store_true', 
                       help='Mark visual check as passed')
    parser.add_argument('--config', help='Pipeline config for declared-vs-measured check')
    parser.add_argument('--narration', help='Project narration.json for scene-aware dead-air check '
                                            '(缺省从 --config 的 narration_source 指针解析)')
    parser.add_argument('--subtitle-source',
                        help='step5 落盘的 _subtitle_timestamp_source.json（项目 temp 目录内），'
                             'subtitle_timestamp_source 检查项的唯一取数面；不提供即判未测')
    
    args = parser.parse_args()
    
    qa = MediaQAGate()
    passed, result = qa.validate(
        video_path=args.video,
        subtitle_path=args.subtitle,
        visual_check_passed=args.visual_check_passed,
        config_path=args.config,
        narration_path=args.narration,
        subtitle_source_path=args.subtitle_source
    )
    
    print_report(result)

    import sys
    # 退出码词汇对齐 _gate_status：2＝未测（跑不成），1＝FAIL，0＝PASS
    if not passed:
        sys.exit(1)
    if result['verdict'] == gs.UNTESTED:
        sys.exit(2)
    sys.exit(0)

if __name__ == '__main__':
    main()
