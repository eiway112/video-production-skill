#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终媒体质量验收模块（Final Media QA Gate）

功能：
  在交付前进行最后一层完整质量检查
  
  检查项目（18项）：
  1. 视频/音频流存在性
  2. 音频非静音检测
  3. 音频响度检测
  4. 时长一致性检查
  5. 字幕格式有效性
  6. 字幕时间单调性
  7. 字幕不越过视频结尾
  8. 字幕文本非空
  9. 视觉边界检查状态
  10. 文件可播放性检查
  11. 文件输出路径和命名正确性
  12. 字幕-语音对齐度（P0-b：silencedetect 语音起口 ↔ SRT 起点，p95 门禁）
  13. 配置声明-实测一致性（config 声明的 fps/resolution ↔ ffprobe 实测，防失效配置）
  14. 黑场-字幕重叠（blackdetect 物理测量：黑场区间与字幕窗口重叠超阈值 = 画面空档但音频持续）
  15. 字幕单字跨行（单个 CJK 汉字独占一行 = 排版孤字，破坏阅读体验）
  16. 字幕术语规范（朗读层形态直通显示层，如『点Json』应为『.json』）
  17. 死区终检（silencedetect 物理扫描成品，连续静音超过 dead_air.max_seconds_per_scene → 拒收；
      场景感知：narration_required=false 的封面/转场窗内静音属设计特征，不计入违规）
  18. 视频码率下限（双档：实测 < min_video_bitrate_kbps_fail → 拒收；fail~warn 之间 → 低码率预警不阻断）

用法：
  from media_qa_gate import MediaQAGate
  
  qa = MediaQAGate()
  passed, result = qa.validate(
      video_path='wall-crack-remedy.mp4',
      subtitle_path='wall-crack-remedy.srt'
  )
  
  if not passed:
      for error in result['errors']:
          print(f"ERROR: {error}")
      sys.exit(1)
"""

import os
import subprocess
import json
from pathlib import Path
from typing import Tuple, Dict, Any, List
import re

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

def ffmpeg_get_loudness(audio_file: str) -> Tuple[float, float]:
    """检测音频响度 (LUFS)"""
    try:
        result = subprocess.run(
            [
                'ffmpeg',
                '-i', audio_file,
                '-af', 'ebur128=video=0',
                '-f', 'null',
                '-'
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )
        
        # 解析 LUFS 值
        integrated = None
        for line in result.stderr.split('\n'):
            if 'Integrated loudness:' in line:
                match = re.search(r'([-\d.]+)\s+LUFS', line)
                if match:
                    integrated = float(match.group(1))
                    break
        
        return integrated, 0.0
    except Exception:
        pass
    return None, None

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
    读不到时回退默认双档 fail=300/warn=500 并打印告警。
    文件声明按原样返回（不与默认双档键合并），保证旧单档键
    min_video_bitrate_kbps 的兼容路径可被 _resolve_bitrate_thresholds 识别。
    """
    defaults = {"min_video_bitrate_kbps_fail": 300, "min_video_bitrate_kbps_warn": 500}
    rules_path = (Path(__file__).resolve().parents[1] / "配置" / "config"
                  / "quality" / "video_quality_rules.json")
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        declared = {k: v for k, v in loaded.items() if not k.startswith("$")}
        return declared if declared else defaults
    except (json.JSONDecodeError, OSError):
        print(f"  [WARN] 无法读取 {rules_path.name}，视频码率阈值回退默认 fail=300/warn=500kbps")
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
    """场景感知的死区违规过滤（检查17核心逻辑，纯函数供回归测试）。

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
# 清单集中定义于此，检查11为唯一消费方。
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
                                  pix_th: float = 0.10) -> List[Tuple[float, float]]:
    """用 blackdetect 物理扫描成片，返回黑场区间列表 [(start, end), ...]。

    这是对"观众实际看到什么"的直接测量，与声明层（T-block/S-block）
    无关——即使时间轴声明自洽，渲染产物出现黑场也会被此检测捕获。
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
        return []
    intervals = []
    for line in (result.stderr or '').splitlines():
        m = re.search(r'black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)', line)
        if m:
            intervals.append((float(m.group(1)), float(m.group(2))))
    return intervals


def ffmpeg_detect_long_silences(video_path: str, noise_db: float = -40,
                                min_silence: float = 3.0,
                                duration: float = None) -> List[Tuple[float, float, float]]:
    """用 silencedetect 扫描成片，返回超过 min_silence 的连续静音段 [(start, end, dur), ...]。

    d= 直接设为死区上限，ffmpeg 只报告超限段 —— 输出即违规清单。
    尾部静音可能只有 silence_start 没有 silence_end，给定 duration 时补齐为收尾段。
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
        return []
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
                                min_silence: float = 0.5) -> List[float]:
    """从成片音轨提取语音起口时刻（silence_end 事件 = 长停顿后开口）。

    适用于纯旁白成片（bgm_enabled=false）；含 BGM 时全轨无静音段，
    返回空列表，调用方降级为警告（min_onsets 门槛）。
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
        return []
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

class MediaQAGate:
    """最终媒体质量验收门禁"""
    
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.checks = {}
        self.alignment = {}
        self.media_facts = {}
    
    def validate(self, 
                 video_path: str, 
                 subtitle_path: str = None,
                 visual_check_passed: bool = False,
                 config_path: str = None,
                 narration_path: str = None) -> Tuple[bool, Dict[str, Any]]:
        """执行完整的媒体质量验收
        
        参数：
          video_path: 视频文件路径
          subtitle_path: 字幕文件路径（可选）
          visual_check_passed: 视觉边界检查是否通过
          config_path: pipeline 配置文件路径（可选，提供后执行检查13：声明-实测一致性；
                       同时用于解析 narration_source 供检查17场景感知排除）
          narration_path: 项目 narration.json 路径（可选，显式指定优先于 config 指针解析）
        
        返回：
          (通过/失败, 详细检查结果)
        """
        self.errors = []
        self.warnings = []
        self.checks = {}
        self.alignment = {}
        self.media_facts = {}
        
        video_path = Path(video_path)
        
        # 检查1：视频文件存在
        if not video_path.exists():
            self.errors.append(f"Video file not found: {video_path}")
            return False, self._result()
        
        # 获取媒体流信息
        streams = ffprobe_get_streams(str(video_path))
        duration = ffprobe_get_duration(str(video_path))
        
        # 检查2：视频流存在
        video_streams = [s for s in streams if s.get('codec_type') == 'video']
        self.checks['video_stream_exists'] = len(video_streams) > 0
        if not self.checks['video_stream_exists']:
            self.errors.append("No video stream found")
        
        # 检查3：音频流存在
        audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
        self.checks['audio_stream_exists'] = len(audio_streams) > 0
        if not self.checks['audio_stream_exists']:
            self.errors.append("No audio stream found")
        
        # 检查18：视频码率下限（阈值单一权威源：video_quality_rules.json，双档制）
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
                self.checks['video_bitrate_ok'] = level != 'fail'
                if level == 'fail':
                    self.errors.append(
                        f"Video bitrate too low: {v_br_kbps}kbps < {fail_kbps}kbps "
                        f"(video_quality_rules.min_video_bitrate_kbps_fail) — 判定渲染失败或内容缺失")
                elif level == 'warn':
                    self.warnings.append(
                        f"Video bitrate low: {v_br_kbps}kbps < {warn_kbps}kbps "
                        f"(video_quality_rules.min_video_bitrate_kbps_warn) — 可能视觉内容不足，不阻断")
            else:
                self.warnings.append(
                    "Video bitrate 无法解析（ffprobe 返回 N/A 或缺失），已跳过码率门禁")
        
        # 检查4：时长有效
        self.checks['duration_valid'] = duration > 0
        if not self.checks['duration_valid']:
            self.errors.append(f"Invalid duration: {duration}")
        else:
            # 检查5：音频非静音
            if len(audio_streams) > 0:
                silence_dur = ffmpeg_detect_silence(str(video_path), duration)
                self.checks['audio_not_silent'] = silence_dur >= 0 and silence_dur < duration - 1.0
                if silence_dur < 0:
                    self.warnings.append("Could not detect silence")
                elif silence_dur >= duration - 1.0:
                    self.errors.append(f"Audio is silent (or nearly silent): {silence_dur:.1f}s of {duration:.1f}s")
                elif silence_dur > duration * 0.5:
                    self.warnings.append(f"Audio contains excessive silence: {silence_dur:.1f}s of {duration:.1f}s")
        
        # 检查6：文件可播放性
        try:
            result = subprocess.run(
                ['ffmpeg', '-t', '1', '-i', str(video_path), '-f', 'null', '-'],
                capture_output=True,
                timeout=10
            )
            self.checks['playable'] = result.returncode == 0
            if not self.checks['playable']:
                self.errors.append("Video file is not playable or corrupted")
        except Exception as e:
            self.warnings.append(f"Could not verify playability: {e}")
        
        # 字幕相关检查
        if subtitle_path:
            subtitle_path = Path(subtitle_path)
            
            # 检查7：字幕文件存在
            if not subtitle_path.exists():
                self.errors.append(f"Subtitle file not found: {subtitle_path}")
            else:
                # 解析字幕
                entries = parse_srt(str(subtitle_path))
                
                # 检查8：字幕条目非空
                self.checks['subtitle_not_empty'] = len(entries) > 0
                if not self.checks['subtitle_not_empty']:
                    self.errors.append("Subtitle file is empty or malformed")
                else:
                    # 检查9：时间单调性
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
                    self.checks['subtitle_times_ordered'] = all_ordered
                    
                    # 检查10：字幕不越过视频结尾
                    if duration > 0 and len(entries) > 0:
                        max_end_ms = max(e['end_ms'] for e in entries)
                        max_end_s = max_end_ms / 1000.0
                        overflow = max_end_s - duration
                        self.checks['subtitle_not_overflow'] = overflow <= 0.5  # 允许 500ms 误差
                        if overflow > 0.5:
                            self.errors.append(
                                f"Subtitle overflow: last entry ends at {max_end_s:.1f}s, "
                                f"video ends at {duration:.1f}s (+{overflow:.1f}s over)"
                            )
                    
                    # 检查12：字幕-语音对齐度（P0-b，Audio-First 门禁）
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
                            for st, en in srt_windows:
                                if st - 0.15 <= t <= en:
                                    return 0.0
                            return min(abs(s - t) for s in srt_starts)

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
                        }
                        if len(onsets) < rules['min_onsets']:
                            # 含 BGM 成片全轨无静音段 → 无法测量，降级为警告不拦截
                            self.checks['subtitle_audio_alignment'] = True
                            self.warnings.append(
                                f"Subtitle-audio alignment not measurable: only "
                                f"{len(onsets)} speech onsets detected "
                                f"(min {rules['min_onsets']}, BGM may mask silences)"
                            )
                        elif p95 > rules['p95_max_seconds']:
                            self.checks['subtitle_audio_alignment'] = False
                            msg = (f"Subtitle-audio misalignment: p95 deviation "
                                   f"{p95:.3f}s > {rules['p95_max_seconds']}s "
                                   f"({len(onsets)} onsets, max {deviations[-1]:.3f}s)")
                            if rules.get('fail_on_violation', True):
                                self.errors.append(msg)
                            else:
                                self.warnings.append(msg)
                        else:
                            self.checks['subtitle_audio_alignment'] = True

                    # 检查14：黑场-字幕重叠（物理测量，直接回答"观众看到什么"）
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
                        self.checks['no_black_frame_with_subtitle'] = not violations
                        for bs, be, ss, se, overlap in violations:
                            msg = (f"Black screen while subtitle showing: black "
                                   f"{bs:.1f}-{be:.1f}s overlaps subtitle "
                                   f"{ss:.1f}-{se:.1f}s by {overlap:.1f}s "
                                   f"(threshold {max_ov}s) — 画面空档但音频/字幕持续")
                            if bf_rules.get('fail_on_violation', True):
                                self.errors.append(msg)
                            else:
                                self.warnings.append(msg)

                    # 检查15：字幕单字跨行（排版孤字，严重破坏阅读体验）
                    # step5 均衡分行算法已从源头消除，此处是交付前复查：
                    # 拦截外部导入/手工编辑的 SRT。
                    _cjk_re = re.compile(r'^[\u4e00-\u9fff]$')
                    orphan_lines = []
                    for e in entries:
                        for ln in e['text'].split('\n'):
                            if _cjk_re.match(ln.strip()):
                                orphan_lines.append((e['index'], ln.strip()))
                    self.checks['no_single_char_subtitle_line'] = not orphan_lines
                    for idx, ch in orphan_lines:
                        self.errors.append(
                            f"Single-character subtitle line: entry #{idx} "
                            f"has orphan char '{ch}' on its own line — 孤字跨行")

                    # 检查16：字幕术语规范 lint（朗读层形态直通显示层）
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
                    self.checks['subtitle_terms_normalized'] = not term_hits
                    for idx, hit, msg in term_hits:
                        self.errors.append(
                            f"Subtitle term violation: entry #{idx} contains "
                            f"'{hit}' — {msg}（补充 subtitle_term_rules.json 词典后重跑 step5）")
        
        # 检查11：输出路径命名正确性（AGENTS.md 交付约束：禁止技术词命名）
        # 被验收视频/字幕文件名命中 final/v01/render/raw/tmp → 拒收，
        # 交付名须用业务描述性名称。清单集中定义：FORBIDDEN_NAME_TOKENS。
        naming_violations = []
        for label, p in (("视频", video_path), ("字幕", subtitle_path)):
            if not p:
                continue
            hits = _FORBIDDEN_NAME_RE.findall(Path(p).stem.lower())
            if hits:
                naming_violations.append((label, Path(p).name, sorted(set(hits))))
        self.checks['output_naming_valid'] = not naming_violations
        for label, name, hits in naming_violations:
            self.errors.append(
                f"Delivery naming violation: {label}文件名 '{name}' 含技术词 "
                f"{'/'.join(hits)} — 交付名须为业务描述性名称（AGENTS.md 交付约束）")
        
        # 检查17：死区终检（阈值单一权威源：audio_sync_rules.json → dead_air 节）
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
            self.checks['no_dead_air'] = not violations
            self.media_facts['dead_air_intervals'] = [
                (round(st, 2), round(en, 2)) for st, en, _ in violations]
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
        
        # 检查9：视觉边界检查状态
        self.checks['visual_check_passed'] = visual_check_passed
        if not visual_check_passed:
            self.warnings.append("Visual boundary check not passed or not verified")
        
        # 检查13：配置声明-实测一致性（fps / resolution）
        # 背景：曾出现 config 声明 fps=30 但渲染器实际输出 25fps 的失效配置，
        # 声明与产物脱钩会污染输入指纹缓存判定，必须在交付前拦截。
        if config_path and len(video_streams) > 0:
            self._check_declared_vs_measured(config_path, video_streams[0])
        
        # 总体判定
        passed = len(self.errors) == 0
        return passed, self._result()
    
    def _check_declared_vs_measured(self, config_path: str,
                                    video_stream: Dict[str, Any]) -> None:
        """检查13：config 声明的 fps/resolution 必须与 ffprobe 实测一致。

        实测值同时记录到 media_facts，供完工报告/交付说明直接引用，
        避免交付文档手工填写产生漂移。
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
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
        
        ok = True
        if declared_fps is not None and measured_fps is not None:
            if abs(float(declared_fps) - measured_fps) > 0.1:
                ok = False
                self.errors.append(
                    f"Declared-vs-measured fps mismatch: config declares "
                    f"{declared_fps}fps but ffprobe measures {measured_fps:g}fps "
                    f"— fix config or renderer, do not ship stale declarations"
                )
        if declared_res and measured_w and measured_h:
            rm = re.match(r'^(\d+)\s*[xX×]\s*(\d+)$', str(declared_res).strip())
            if rm and (int(rm.group(1)) != measured_w or int(rm.group(2)) != measured_h):
                ok = False
                self.errors.append(
                    f"Declared-vs-measured resolution mismatch: config declares "
                    f"{declared_res} but ffprobe measures {measured_w}x{measured_h}"
                )
        self.checks['declared_matches_measured'] = ok
    
    def _result(self) -> Dict[str, Any]:
        """返回结构化结果"""
        return {
            'passed': len(self.errors) == 0,
            'errors': self.errors,
            'warnings': self.warnings,
            'checks': self.checks,
            'alignment': self.alignment,
            'media_facts': self.media_facts,
            'total_checks': len(self.checks),
            'passed_checks': sum(1 for v in self.checks.values() if v)
        }

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
    
    args = parser.parse_args()
    
    qa = MediaQAGate()
    passed, result = qa.validate(
        video_path=args.video,
        subtitle_path=args.subtitle,
        visual_check_passed=args.visual_check_passed,
        config_path=args.config,
        narration_path=args.narration
    )
    
    print(f"[{'PASS' if passed else 'FAIL'}] Media QA Gate")
    print(f"  Checks: {result['passed_checks']}/{result['total_checks']} passed")
    
    align = result.get('alignment') or {}
    if align.get('onsets_detected') is not None:
        p95 = align.get('p95_deviation_seconds')
        print(f"  Alignment: {align['onsets_detected']} onsets, "
              f"p95={p95 if p95 is not None else 'n/a'}s "
              f"(threshold {align.get('threshold_p95_seconds')}s)")
    
    facts = result.get('media_facts') or {}
    if facts.get('measured_fps') is not None:
        print(f"  Media facts: measured {facts['measured_fps']:g}fps "
              f"{facts.get('measured_resolution')} "
              f"(declared {facts.get('declared_fps')}fps {facts.get('declared_resolution')})")
    
    if result['errors']:
        print(f"[ERRORS]")
        for err in result['errors']:
            print(f"  - {err}")
    
    if result['warnings']:
        print(f"[WARNINGS]")
        for warn in result['warnings']:
            print(f"  - {warn}")
    
    import sys
    sys.exit(0 if passed else 1)

if __name__ == '__main__':
    main()
