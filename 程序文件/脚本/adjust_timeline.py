#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Timeline Adjuster — 根据 TTS 实际时长动态调整 HTML GSAP 时间轴（双向）

核心逻辑：
  1. 读取 TTS 音频时长（从 tts_44k 缓存）
  2. 计算每个场景的延展或收缩量（TTS时长 vs 场景窗口）
  3. 计算累积偏移量（正=延展/负=收缩）
  4. 将 HTML 中所有 GSAP 时间位置加上对应偏移
  5. 更新 data-duration 和 config JSON 中的场景边界

使用方式：
  python adjust_timeline.py --html index.html --config pw_enhance.json --tts-dir tts_44k
  python adjust_timeline.py --html index.html --config pw_enhance.json --tts-dir tts_44k --shrink

设计原则：
  - 始终从备份恢复原始 HTML/Config，避免重复应用偏移
  - 双向调整：TTS 超窗口→延展，窗口超 TTS+margin→收缩（--shrink 模式）
  - 保留原始动画相对时序，只平移场景边界
  - 收缩时仅削减静态留白，不影响 GSAP 入场动画（入场通常在3-7s内完成）
"""

import json
import re
import sys
import os
import io
import hashlib
import shutil
import argparse
import time
from pathlib import Path
from script_interface import Config, Result, Logger, EXIT_CODE, atomic_write_json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Import shared GSAP time resolution utilities
sys.path.insert(0, str(Path(__file__).parent))
from _gsap_time_utils import parse_scene_map


def get_duration(filepath):
    import subprocess
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "json", filepath],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def parse_scene_boundaries(config_path, cover_duration):
    """Read original scene boundaries from config JSON."""
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    scenes = cfg['scenes']
    boundaries = []
    for s in scenes:
        start = float(s['start']) + cover_duration
        end = float(s['end']) + cover_duration
        boundaries.append((start, end))
    return boundaries, cfg


def measure_tts_durations(tts_dir, num_scenes, config_scenes=None):
    """Measure actual TTS audio durations from cached files.

    Supports both hash-based cache (new) and scene-ID-based naming (legacy).
    """
    durations = []

    # Try to load manifest for hash-based cache
    manifest = {}
    manifest_path = tts_dir.parent / "tts_manifest.json"
    if manifest_path.exists():
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)

    for i in range(1, num_scenes + 1):
        hq_file = None

        # Try hash-based name from manifest
        sid = str(i)
        if sid in manifest:
            entry = manifest[sid]
            h = entry.get('hash') if isinstance(entry, dict) else entry
            if h:
                candidate = tts_dir / f"tts_{h}_hq.wav"
                if candidate.exists():
                    hq_file = candidate

        # Fall back to legacy naming
        if hq_file is None:
            candidate = tts_dir / f"scene_{i}_hq.wav"
            if candidate.exists():
                hq_file = candidate

        if hq_file:
            dur = get_duration(str(hq_file))
        else:
            # 硬失败：dur=0 会让 --shrink 把该场景窗口坍缩到 ~1s，
            # 产出画面一闪而过的废片。缺 TTS 只能是上游步骤没跑/文本已改，
            # 必须先重跑 TTS，不允许带 0 值继续算时间轴。
            print(f"  ERROR: TTS file not found for scene {i} — "
                  f"run TTS step first (narration text may have changed).")
            sys.exit(EXIT_CODE.GATE_FAILURE)
        durations.append(dur)
    return durations


def calculate_offsets(boundaries, tts_durations, buffer=0.5, shrink=False, shrink_margin=1.5):
    """Calculate extension/shrink and cumulative offset for each scene.

    Bidirectional adjustment: the target margin between TTS end and scene end
    is `shrink_margin` when --shrink is on, otherwise `buffer`. Either way, we
    close the gap on BOTH sides (extend if under target, shrink if over) so
    that GSAP pre-fade (~0.5s) never fires while audio is still playing.

    Args:
        boundaries: [(start, end), ...] absolute times (with cover offset)
        tts_durations: [dur1, dur2, ...] actual TTS audio lengths
        buffer: minimum margin after TTS when --shrink is OFF (default 0.5s)
        shrink: enable bidirectional convergence to `shrink_margin`
        shrink_margin: target margin beyond TTS (default 1.5s = audio_sync_rules.json
            authoritative value; active when --shrink)

    Returns:
        extensions: [ext1, ext2, ...] how much each scene grows (negative = shrink)
        offsets: [off1, off2, ...] cumulative offset at each scene's start
        total_ext: total extension/shrink applied to video
    """
    extensions = []
    # 目标边距：--shrink 打开时用 shrink_margin，否则用 buffer
    target_margin = shrink_margin if shrink else buffer
    for i, (start, end) in enumerate(boundaries):
        window = end - start
        tts = tts_durations[i]
        target_window = tts + target_margin

        if window < target_window:
            # 窗口不足以覆盖 TTS + target margin → 扩展
            ext = target_window - window
        elif shrink and window > target_window:
            # 窗口盈余 → 收敛到 target（仅在 --shrink 时才反向收缩）
            ext = target_window - window  # negative value
        else:
            # 窗口刚好或未开启收缩 → 不动
            ext = 0.0

        extensions.append(ext)

    # Cumulative offset at each scene's start
    offsets = [0.0] * len(boundaries)
    cum = 0.0
    for i in range(len(boundaries)):
        offsets[i] = cum
        cum += extensions[i]

    return extensions, offsets, sum(extensions)


def get_offset_for_time(time_val, boundaries, extensions):
    """Get cumulative offset for a given absolute time position.

    Sums extensions for all scenes whose end boundary has been passed.
    Transition timestamps should be placed at or after the scene's
    configured end boundary (start + cover_duration + scene_end) to
    ensure correct offset application.
    """
    offset = 0.0
    for i, (start, end) in enumerate(boundaries):
        if time_val >= end - 0.01:  # >= scene end means this scene is done
            offset += extensions[i]
    return offset


def adjust_gsap_times(html_content, boundaries, extensions):
    """Replace all time positions in GSAP code with offset-adjusted values.

    Timestamps beyond the last scene boundary are preserved as-is
    (treated as manually designed for the adjusted timeline).
    """
    max_boundary = boundaries[-1][1] if boundaries else float('inf')

    def replace_time_in_tl_calls(content):
        """Replace time positions in tl.to/tl.fromTo/tl.set calls.

        Pattern: }, NUMBER) at the end of a tl.* call line.
        The } is the closing brace of the options object(s),
        NUMBER is the absolute time position on the GSAP timeline.
        """
        lines = content.split('\n')
        new_lines = []
        for line in lines:
            if 'tl.' in line and ')' in line:
                def replacer(m):
                    old_time = float(m.group(1))
                    if old_time > max_boundary:
                        return m.group(0)  # Preserve manual edits
                    offset = get_offset_for_time(old_time, boundaries, extensions)
                    new_time = old_time + offset
                    # Produce: }, NEW_NUMBER)
                    return '}, ' + f'{new_time:.1f}' + ')'

                # Match: }, NUMBER) — closing brace + comma + number + close paren
                line = re.sub(r'\},\s*([\d.]+)\s*\)', replacer, line)

            new_lines.append(line)
        return '\n'.join(new_lines)

    def replace_starts_arrays(content):
        """Replace time values in `var starts = [N1, N2, ...];` arrays."""
        def replacer(m):
            numbers_str = m.group(1)
            numbers = [float(x.strip()) for x in numbers_str.split(',')]
            new_numbers = []
            for n in numbers:
                if n > max_boundary:
                    new_numbers.append(f'{n:.1f}')  # Preserve manual edits
                else:
                    offset = get_offset_for_time(n, boundaries, extensions)
                    new_numbers.append(f'{n + offset:.1f}')
            return 'var starts = [' + ', '.join(new_numbers) + ']'

        return re.sub(r'var\s+starts\s*=\s*\[([0-9.,\s]+)\]', replacer, content)

    content = replace_time_in_tl_calls(html_content)
    content = replace_starts_arrays(content)
    return content


def detect_t_block(html_content):
    """Detect structured T-block (var T = { s1: N, s2: N, ... };) in HTML.

    Returns the regex match object if found, None otherwise.
    """
    return re.search(r'var\s+T\s*=\s*\{([^}]+)\}\s*;', html_content)


def adjust_t_block(html_content, boundaries, extensions):
    """Rewrite T-block values with cumulative offsets (structured timeline path).

    When the compiler emits a T-block (var T = { s1: 3.0, s2: 14.0, ... };),
    all GSAP positions reference T.sN + offset. Rewriting just this block
    automatically shifts all animations — no per-line regex needed.

    Returns (adjusted_html, True) if T-block was found and rewritten,
    or (html_content, False) if no T-block exists (fallback to legacy path).
    """
    match = detect_t_block(html_content)
    if not match:
        return html_content, False

    block_content = match.group(1)
    entries = []
    for entry_match in re.finditer(r's(\d+)\s*:\s*([\d.]+)', block_content):
        scene_idx = int(entry_match.group(1)) - 1  # 0-based
        old_val = float(entry_match.group(2))

        if scene_idx < len(boundaries):
            offset = get_offset_for_time(old_val, boundaries, extensions)
            new_val = old_val + offset
        else:
            new_val = old_val  # Preserve entries beyond known boundaries

        entries.append(f's{scene_idx + 1}: {new_val:.1f}')

    new_block = 'var T = { ' + ', '.join(entries) + ' };'
    result = html_content[:match.start()] + new_block + html_content[match.end():]
    return result, True


def parse_boundaries_from_sblock(html_content):
    """Read scene boundaries from S-block in HTML (authoritative source).

    Returns (boundaries, s_map) where boundaries is [(start, end), ...] for
    content scenes only (cover excluded). s_map is the parsed S-block dict.
    Returns (None, None) if no S-block found.
    """
    script_match = re.search(r'<script>(.*?)</script>', html_content, re.DOTALL)
    if not script_match:
        return None, None
    s_map = parse_scene_map(script_match.group(1))
    if not s_map:
        return None, None

    boundaries = []
    for s in s_map['scenes']:
        if not s.get('cover'):
            boundaries.append((s['start'], s['end']))
    return boundaries, s_map


def adjust_s_block(html_content, boundaries, extensions):
    """Rewrite S-block with adjusted scene start/end/dur values.

    Applies cumulative offsets to each scene's timing, keeping the declaration
    authoritative and in sync with the T-block.
    """
    s_match = re.search(r'(var\s+S\s*=\s*)(\[[\s\S]*?\])(\s*;)', html_content)
    if not s_match:
        return html_content, False

    try:
        scenes = json.loads(s_match.group(2))
    except (json.JSONDecodeError, ValueError):
        return html_content, False

    new_scenes = []
    content_idx = 0
    for s in scenes:
        if s.get('cover'):
            new_scenes.append(s)  # Cover timing doesn't change
            continue
        if content_idx < len(boundaries):
            offset = get_offset_for_time(s['start'], boundaries, extensions)
            ext = extensions[content_idx] if content_idx < len(extensions) else 0
            s = dict(s)  # Copy
            s['start'] = round(s['start'] + offset, 1)
            s['end'] = round(s['end'] + offset + ext, 1)
            s['dur'] = round(s['end'] - s['start'], 1)
        new_scenes.append(s)
        content_idx += 1

    new_json = json.dumps(new_scenes, separators=(',', ': '))
    # Reformat with line breaks for readability
    new_json = new_json.replace('}, {', '},\n    {')
    new_block = s_match.group(1) + new_json + s_match.group(3)
    result = html_content[:s_match.start()] + new_block + html_content[s_match.end():]
    return result, True


def update_data_duration(content, new_duration):
    """Update data-duration attribute in HTML."""
    return re.sub(
        r'data-duration="[\d.]+"',
        f'data-duration="{new_duration:.1f}"',
        content
    )


def update_config(cfg, scenes, extensions, offsets, total_ext, cover_duration, boundaries=None):
    """Update config JSON with new scene boundaries and video duration.

    When S-block boundaries are given (authoritative), config scenes are
    DERIVED from them (converted back to cover-relative time) instead of
    incrementing config's own old values. Incrementing both sides let the
    two timelines drift whenever their baselines differed — audio gets
    placed by config while the picture is rendered by the S-block, so the
    tail audio silently falls off the end of the video.
    """
    new_scenes = []
    for i, s in enumerate(scenes):
        if boundaries is not None:
            new_start = boundaries[i][0] + offsets[i] - cover_duration
            new_end = boundaries[i][1] + offsets[i] + extensions[i] - cover_duration
        else:
            new_start = float(s['start']) + offsets[i]
            new_end = float(s['end']) + offsets[i] + extensions[i]
        # 只覆写时间字段，其余字段原样带过（A05，2026-09-18 审核）。本函数的产出
        # 会被 _write_narration_scenes 写回 narration.json —— 而 narration.json 是
        # 场景定义的单一权威源（AGENTS.md 权威源表）。旧实现用四个字面键重建场景
        # dict，等于每跑一次 timeline 就把 title/type/duration/narration_required/
        # subtitle_required/assets 从权威源上裁掉（富字段实例见
        # 程序文件/源码/hyperframes/eiway-122-wall/narration.json 的 10 字段 schema）。
        updated = dict(s)
        updated['start'] = round(new_start, 1)
        updated['end'] = round(new_end, 1)
        if 'duration' in updated:
            # duration 是 start/end 的派生量：不同步就是自相矛盾的权威源
            updated['duration'] = round(updated['end'] - updated['start'], 1)
        new_scenes.append(updated)

    cfg['scenes'] = new_scenes
    if boundaries is not None:
        # video_duration = last scene absolute end (cover included)
        cfg['video_duration'] = round(new_scenes[-1]['end'] + cover_duration, 1)
    else:
        cfg['video_duration'] = round(float(cfg['video_duration']) + total_ext, 1)
    return cfg


def detect_cover_duration(html_content):
    """Auto-detect cover duration from HTML data-cover-duration attribute."""
    match = re.search(r'data-cover-duration="([\d.]+)"', html_content)
    if match:
        return float(match.group(1))
    return 0.0


def _read_hash(hash_path):
    """Read stored MD5 hash from .hash file."""
    try:
        return Path(hash_path).read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return None


def _write_hash(hash_path, content):
    """Write MD5 hash of content to .hash file."""
    h = hashlib.md5(content.encode('utf-8')).hexdigest()
    Path(hash_path).write_text(h, encoding='utf-8')


def _write_narration_scenes(narration_path, scenes, cover_duration):
    """P0-03 指针模式写回：调整后的场景写回 narration.json（绝对时间）。

    config 不内嵌 scenes，narration.json 保持唯一权威源，避免双源分叉。
    update_config 产出的是 cover 相对时间，这里统一平移回绝对时间，
    与 HTML S-block / visual_boundary_check.py 的时间坐标系一致。

    按 scene_id 就地合并，不整表替换（A05，2026-09-18 审核）：入参 scenes 来自
    resolve_narration_scenes，它已滤掉 cover 场景；旧实现 data['scenes'] = 新表
    等于每次写回顺手删除 cover，并把入参未携带的字段一并丢掉。合并保留原顺序与
    未被本轮触及的场景；入参里有而文件里没有的场景仍追加（不静默丢）。
    """
    with open(narration_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    shifted = {}
    for s in scenes:
        m = dict(s)
        m['start'] = round(float(s['start']) + cover_duration, 1)
        m['end'] = round(float(s['end']) + cover_duration, 1)
        if 'duration' in m:
            m['duration'] = round(m['end'] - m['start'], 1)
        shifted[str(s.get('scene_id'))] = m
    existing = data.get('scenes') or []
    seen = set()
    merged = []
    for s in existing:
        sid = str(s.get('scene_id'))
        seen.add(sid)
        merged.append(shifted.get(sid, s))
    merged.extend(m for sid, m in shifted.items() if sid not in seen)
    data['scenes'] = merged
    atomic_write_json(narration_path, data)
    print(f"  Updated narration_source: {narration_path}")


def _default_shrink_margin():
    """--shrink-margin 单一权威源：config/quality/audio_sync_rules.json。

    prefab 复盘教训（2026-08-04）：本脚本 CLI 曾硬编码默认 1.0s，与权威配置
    1.5s 分叉——人工运行与流水线运行产出不同时间轴，造成 618.9→621.7→623.8s
    三次漂移、连环全量重渲。现在未显式传参时一律从配置读取；读取失败回退
    1.5s（与配置 documented 值一致）并打印告警。
    """
    try:
        from _script_env import ROOT
        rules_path = ROOT / "程序文件" / "配置" / "config" / "quality" / "audio_sync_rules.json"
        with open(rules_path, 'r', encoding='utf-8') as f:
            return float(json.load(f)["shrink"]["shrink_margin_seconds"])
    except Exception:
        print("  WARNING: audio_sync_rules.json unreadable — shrink margin falls back to 1.5s")
        return 1.5


def main():
    parser = argparse.ArgumentParser(description="Adjust GSAP timeline to fit TTS audio (bidirectional)")
    parser.add_argument("--html", default=None, help="HTML project name (e.g. wall-crack-remedy)")
    parser.add_argument("--config", required=True, help="Path to config JSON")
    parser.add_argument("--tts-dir", required=True, help="Path to tts_44k directory")
    parser.add_argument("--shrink", action="store_true",
                        help="Enable window shrinking when surplus > shrink-margin")
    parser.add_argument("--shrink-margin", type=float, default=None,
                        help="Target margin beyond TTS when shrinking. Default: read from "
                             "config/quality/audio_sync_rules.json (single source of truth, "
                             "currently 1.5s). Pass explicitly only for one-off experiments.")
    args = parser.parse_args()

    # margin 单一权威源：未显式传参 → 从 audio_sync_rules.json 读取
    if args.shrink_margin is None:
        args.shrink_margin = _default_shrink_margin()

    # Determine HTML project name (from arg or config)
    from _script_env import HTML_BASE, resolve_narration_scenes
    
    if args.html is None:
        # Read from config
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                args.html = cfg.get('paths', {}).get('html_project', 'hotel-partition-wall')
        except (json.JSONDecodeError, FileNotFoundError, ValueError):
            args.html = 'hotel-partition-wall'
    
    html_path = HTML_BASE / args.html / "index.html"
    config_path = Path(args.config)
    tts_dir = Path(args.tts_dir)

    # Read HTML
    html_content = html_path.read_text(encoding='utf-8')

    # Detect cover duration
    cover_duration = detect_cover_duration(html_content)
    print(f"Cover duration: {cover_duration}s")

    # Backup original files (if not already backed up)
    # Use .html.bak (not .bak.html) to avoid HyperFrames lint conflict
    html_bak = html_path.parent / (html_path.name + '.bak')
    config_bak = config_path.with_suffix('.bak.json')
    html_hash_path = html_path.parent / (html_path.name + '.hash')
    config_hash_path = config_path.with_suffix('.hash')

    html_restore_pending = False
    if not html_bak.exists():
        # First run: backup original, record hash
        shutil.copy2(html_path, html_bak)
        _write_hash(html_hash_path, html_content)
        print(f"  Backed up HTML: {html_bak.name}")
    else:
        # Subsequent run: detect manual edits via hash comparison
        stored_hash = _read_hash(html_hash_path)
        current_hash = hashlib.md5(html_content.encode('utf-8')).hexdigest()

        if stored_hash and current_hash != stored_hash:
            # HTML was manually edited since last adjust_timeline run
            # Discard stale backup, use current HTML as new baseline
            shutil.copy2(html_path, html_bak)
            _write_hash(html_hash_path, html_content)
            html_content = html_path.read_text(encoding='utf-8')
            print(f"  Manual edit detected, fresh baseline")
        else:
            # No manual edit: restoring the backup prevents double-application,
            # but ONLY when boundaries are parsed from the HTML itself
            # (S-block/config legacy). In narration_source pointer mode the
            # boundaries already carry the adjusted values, so restoring the
            # baseline HTML would roll back GSAP times/data-duration while
            # narration keeps adjusted values -> permanent divergence
            # (2026-08-22 agent-wiki-promo incident: rendered 150s vs 160.6s).
            # Defer the decision until the scene source is known.
            html_restore_pending = True

    if not config_bak.exists():
        # First run: backup original, record hash
        shutil.copy2(config_path, config_bak)
        _write_hash(config_hash_path, config_path.read_text(encoding='utf-8'))
        print(f"  Backed up config: {config_bak.name}")
    else:
        # Subsequent run: detect manual edits via hash comparison (parity with HTML)
        stored_cfg_hash = _read_hash(config_hash_path)
        current_cfg_text = config_path.read_text(encoding='utf-8')
        current_cfg_hash = hashlib.md5(current_cfg_text.encode('utf-8')).hexdigest()
        if stored_cfg_hash and current_cfg_hash != stored_cfg_hash:
            # Config was manually edited since last adjust_timeline run
            shutil.copy2(config_path, config_bak)
            _write_hash(config_hash_path, current_cfg_text)
            print(f"  Manual config edit detected, fresh baseline")
        else:
            shutil.copy2(config_bak, config_path)
            print(f"  Restored config from backup")

    # Re-read restored config
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    # P0-03 指针模式：narration_source 为场景权威源，config 不内嵌 scenes
    try:
        ptr_scenes, narration_path = resolve_narration_scenes(cfg, config_path, cover_duration)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(EXIT_CODE.GATE_FAILURE)
    if narration_path is not None:
        cfg['scenes'] = ptr_scenes
        print(f"  Scene source: narration_source ({narration_path.name}, {len(ptr_scenes)} scenes)")

    # Deferred baseline restore (see backup section). In pointer mode the
    # hash-verified HTML is already consistent with narration boundaries;
    # restoring the pre-adjustment backup would desynchronize them.
    if html_restore_pending:
        if narration_path is not None:
            print("  Pointer mode: HTML hash-verified, keeping current (no baseline restore)")
        else:
            shutil.copy2(html_bak, html_path)
            html_content = html_path.read_text(encoding='utf-8')
            print(f"  Restored HTML from backup")

    # Parse scene boundaries — prefer S-block (authoritative), fall back to config JSON
    boundaries, s_map = parse_boundaries_from_sblock(html_content)
    if boundaries:
        num_scenes = len(boundaries)
        print(f"  Scene boundaries: S-block ({num_scenes} scenes, authoritative)")
    elif narration_path is not None:
        # 指针场景已归一为绝对时间，直接作为边界，不再叠加 cover
        boundaries = [(float(s['start']), float(s['end'])) for s in cfg['scenes']]
        num_scenes = len(boundaries)
        print(f"  Scene boundaries: narration_source ({num_scenes} scenes, absolute)")
    else:
        boundaries, cfg = parse_scene_boundaries(config_path, cover_duration)
        num_scenes = len(cfg['scenes'])
        print(f"  Scene boundaries: config JSON ({num_scenes} scenes, legacy)")

    # Measure TTS durations
    tts_durations = measure_tts_durations(tts_dir, num_scenes, cfg.get('scenes'))

    print(f"\nTTS Duration Analysis (shrink={'ON' if args.shrink else 'OFF'}" +
          (f", margin={args.shrink_margin}s" if args.shrink else "") + "):")
    for i, (start, end) in enumerate(boundaries):
        window = end - start
        tts = tts_durations[i]
        surplus = window - tts
        if tts > window + 0.5:
            status = f"OVER +{tts - window:.1f}s"
        elif args.shrink and surplus > 0.5 + args.shrink_margin:
            status = f"SHRINK -{surplus - args.shrink_margin:.1f}s"
        else:
            status = "OK"
        print(f"  Scene {i+1}: TTS={tts:.1f}s / window={window:.1f}s {status}")

    # Calculate extensions and offsets (bidirectional)
    extensions, offsets, total_ext = calculate_offsets(
        boundaries, tts_durations,
        shrink=args.shrink, shrink_margin=args.shrink_margin
    )

    ext_label = f"{total_ext:+.1f}s" if total_ext < 0 else f"+{total_ext:.1f}s"
    print(f"\nTimeline Adjustment:")
    print(f"  Total change: {ext_label}")
    print(f"  Original duration: {cfg['video_duration']:.1f}s")
    print(f"  New duration: {cfg['video_duration'] + total_ext:.1f}s")

    for i in range(num_scenes):
        orig_start, orig_end = boundaries[i]
        new_start = orig_start + offsets[i]
        new_end = orig_end + offsets[i] + extensions[i]
        ext_str = f"{extensions[i]:+.1f}s" if extensions[i] < 0 else f"+{extensions[i]:.1f}s"
        print(f"  Scene {i+1}: {orig_start:.1f}-{orig_end:.1f} -> {new_start:.1f}-{new_end:.1f} (ext={ext_str})")

    if abs(total_ext) < 0.1 and all(abs(e) < 0.1 for e in extensions):
        print("\n  No adjustment needed (all scenes within tolerance).")
        # Pointer mode drift guard: hash-verified HTML should already match
        # narration boundaries. If data-duration diverges, the HTML was
        # externally clobbered (e.g. restored from a stale baseline backup);
        # GSAP times cannot be resynced from here -> fail loud, recover by
        # deleting index.html.bak/.hash and re-running.
        if narration_path is not None:
            dur_m = re.search(r'data-duration="([\d.]+)"', html_content)
            html_dur = float(dur_m.group(1)) if dur_m else None
            if html_dur is None or abs(html_dur - boundaries[-1][1]) >= 0.05:
                print(f"\n  ERROR: HTML data-duration ({html_dur}s) diverges from "
                      f"narration_source end ({boundaries[-1][1]:.1f}s).\n"
                      f"  Recover: delete {html_bak.name} + {html_hash_path.name} "
                      f"in the project dir, then re-run this step.")
                return EXIT_CODE.GATE_FAILURE
        _write_hash(html_hash_path, html_content)
        # Zero adjustment does NOT imply config is in sync: when the HTML was
        # manually edited (fresh baseline, already-converged S-block) while the
        # config was restored from an older backup, config still carries the
        # stale video_duration/scenes. Re-derive config from the authoritative
        # boundaries (S-block or narration_source) and write it back if drifted,
        # so the two sources never disagree.
        if s_map or narration_path is not None:
            zeros = [0.0] * num_scenes
            synced = update_config(dict(cfg), cfg['scenes'], zeros, zeros, 0.0,
                                   cover_duration, boundaries=boundaries)
            if narration_path is not None:
                # 指针模式：cfg['scenes'] 为绝对时间、synced['scenes'] 为 cover
                # 相对时间，坐标系不同不可直接比对，仅以 video_duration 判定漂移
                drifted = abs(float(synced['video_duration']) - float(cfg['video_duration'])) >= 0.05
            else:
                drifted = (synced['scenes'] != cfg['scenes'] or
                           abs(float(synced['video_duration']) - float(cfg['video_duration'])) >= 0.05)
            if drifted:
                cfg = synced
                if narration_path is not None:
                    _write_narration_scenes(narration_path, cfg.pop('scenes'), cover_duration)
                atomic_write_json(config_path, cfg)
                # Current state IS the baseline (nothing was applied on top),
                # so refresh backup + hash to keep restore idempotent.
                shutil.copy2(config_path, config_bak)
                _write_hash(config_hash_path, config_path.read_text(encoding='utf-8'))
                source_label = 'S-block' if s_map else 'narration_source'
                print(f"  Config drift detected — resynced from {source_label}: "
                      f"video_duration={cfg['video_duration']:.1f}s")
        return 0

    # Adjust GSAP time positions in HTML
    # Prefer T-block (structured: rewrite one block), fall back to regex (legacy: per-line)
    html_content, used_t_block = adjust_t_block(html_content, boundaries, extensions)
    if used_t_block:
        print("  Timeline adjustment: T-block rewrite (structured)")
    else:
        html_content = adjust_gsap_times(html_content, boundaries, extensions)
        print("  Timeline adjustment: regex scan (legacy)")

    # Rewrite S-block (authoritative scene metadata stays in sync)
    html_content, used_s_block = adjust_s_block(html_content, boundaries, extensions)
    if used_s_block:
        print("  S-block: rewritten (structured)")

    # Update data-duration — prefer S-block duration, fall back to config
    if s_map:
        new_video_duration = s_map['duration'] + total_ext
    elif narration_path is not None:
        # 指针模式：与 update_config 同源推导（末场景绝对结束时刻）。
        # 旧值累加会把 config 之外的尾部时长带进 HTML，与 config 的
        # video_duration（末场景 end + cover）分叉，触发渲染后时长门禁。
        new_video_duration = boundaries[-1][1] + offsets[-1] + extensions[-1]
    else:
        new_video_duration = cfg['video_duration'] + total_ext
    html_content = update_data_duration(html_content, new_video_duration)

    # Write modified HTML
    html_path.write_text(html_content, encoding='utf-8')
    _write_hash(html_hash_path, html_content)
    print(f"\n  Updated HTML: {html_path}")

    # Update config JSON — when S-block is authoritative, derive config scenes
    # from S-block boundaries so the two timelines can never drift apart
    cfg = update_config(cfg, cfg['scenes'], extensions, offsets, total_ext, cover_duration,
                        boundaries=boundaries if (s_map or narration_path is not None) else None)
    if narration_path is not None:
        _write_narration_scenes(narration_path, cfg.pop('scenes'), cover_duration)
    atomic_write_json(config_path, cfg)
    _write_hash(config_hash_path, config_path.read_text(encoding='utf-8'))
    print(f"  Updated config: {config_path}")
    print(f"  New video_duration: {cfg['video_duration']:.1f}s")

    # 规范化输出：Result Object
    scenes_adjusted = sum(1 for e in extensions if e != 0)
    total_shrink_ms = int(total_ext * 1000) if total_ext < 0 else 0
    result = Result.success(
        data={
            "html_file": str(html_path),
            "config_file": str(config_path),
            "scenes_adjusted": scenes_adjusted,
            "total_extension_ms": int(total_ext * 1000),
            "total_shrink_ms": total_shrink_ms,
            "new_duration": cfg['video_duration']
        },
        message=f"Timeline adjusted: {scenes_adjusted} scenes, duration: {cfg['video_duration']:.1f}s"
    )
    result.print_to_stdout()
    return result.code


if __name__ == "__main__":
    sys.exit(main())
