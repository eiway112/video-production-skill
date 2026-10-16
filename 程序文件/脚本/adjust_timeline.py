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
from script_interface import (Config, Result, Logger, EXIT_CODE, atomic_write_json,
                              atomic_write_text)

# 换包装时必须持住旧流对象，换完后还须持住本次造出的新代（与 pipeline_runner /
# enhance_video_audio 同名保活对同一修法）：旧 TextIOWrapper 一旦引用归零即 finalize
# （无需 gc.collect），其析构会关掉共享的底层 buffer，import 本模块的宿主（回归
# 套件、探针）之后任何打印都报 "I/O operation on closed file"。只持旧不足以防住
# 自造新代——它没有别的持有者，下个重绑点一顶掉它即引用归零。用例105 锁定。
_streams_prev = (sys.stdout, sys.stderr)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
_streams_kept = (sys.stdout, sys.stderr)

# Import shared GSAP time resolution utilities
sys.path.insert(0, str(Path(__file__).parent))
from _gsap_time_utils import parse_scene_map
import cue_resolve


def load_cue_time_source(tts_dir):
    """词级 cue 的时间记录源：`_timeline_manifest.json`（tts 步末尾落盘）。

    路径按真实读取处推导——`tts_dir.parent` 就是项目 temp 根，与
    `_build_timeline_manifest` 的写入处同源；登记面（`pipeline_runner._step_inputs("timeline")`）
    也必须登记同一个路径，否则就是 A06"登记面≠真实读取路径"的同族缺陷。
    文件缺失返回 {}：让调用侧把"声明在位但无记录"显式报出来，而不是静默当"没有 cue"。
    """
    p = Path(tts_dir).parent / "_timeline_manifest.json"
    if not p.exists():
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(s.get('scene_id')): s for s in data.get('scenes', [])}


def resolve_cue_block(html_content, tts_dir):
    """词级 cue 声明 → 解析 → 整块写入的唯一入口（两个写盘分支共用）。

    三条设计约束，逐条对应一种失败形态：
      1) 无声明 → 原样返回、零字节动作。写成"总是输出空块"会让每个存量项目的
         index.html 变字节 → render 步判 inputs-changed → 一次全量渲染
         （前置实测：稳态重跑三件字节逐位相同，见 `_取证/20261012/交接_词级cue驱动动画批.md` §5）。
      2) 解析有 FAIL/UNTESTED → 由 problems 带回，调用侧必须在**写盘之前**拦下，
         不产出"块里半截 cue、元素永不亮"的成片。
      3) 绝不在此处重新对齐：句级与字符级时刻必须取自同一次对齐输出，而实测对齐读数随
         计算线程环境移动（±0.02~0.42s），自跑即让 cue 脱离已交付字幕的时间基准。
    """
    return cue_resolve.resolve_and_apply(html_content, load_cue_time_source(tts_dir))


def report_cue_problems(problems):
    """失败文案的唯一生成点（两个写盘分支共用，各拼一份即两套口径）。"""
    for _p in problems:
        print(f"  [CUE] {_p}")
    print("  ERROR: cue 声明未能全部解析——拒绝写出半截 cue 块（禁止静默回退）")
    return EXIT_CODE.GATE_FAILURE


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


def calculate_offsets(boundaries, tts_durations, buffer=0.5, shrink=False, shrink_margin=1.5,
                      trim_durations=None):
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
        trim_durations: [dur1, dur2, ...] 可裁基准（仅收缩侧使用）。缺省＝tts_durations，
            即与既有行为逐字等价。传入时**调用侧须保证已逐项 min(wav)**——扩展侧永不使用
            本参数，因为扩展要保证音频放得下，只能按 wav 全时长。

    Returns:
        extensions: [ext1, ext2, ...] how much each scene grows (negative = shrink)
        offsets: [off1, off2, ...] cumulative offset at each scene's start
        total_ext: total extension/shrink applied to video
    """
    extensions = []
    if trim_durations is None:
        trim_durations = tts_durations
    # 目标边距：--shrink 打开时用 shrink_margin，否则用 buffer
    target_margin = shrink_margin if shrink else buffer
    for i, (start, end) in enumerate(boundaries):
        window = end - start
        tts = tts_durations[i]
        trim = trim_durations[i]
        if window < tts + target_margin:
            # 窗口不足以覆盖 TTS + target margin → 扩展（**恒按 wav，不按可裁基准**）
            ext = (tts + target_margin) - window
        elif shrink and window > trim + target_margin:
            # 窗口盈余 → 收敛到 target（仅在 --shrink 时才反向收缩）
            ext = (trim + target_margin) - window  # negative value
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
        # 程序文件/源码/hyperframes/{项目目录}/narration.json 的 10 字段 schema）。
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
    """读 .hash。返回合法 MD5 串；任何断链形态一律返回 None。

    缺失 / 空文件 / 半截（写盘被打断）/ 非十六进制 / 读不出，全部归并为 None 是有意的：
    调用方对"没有可信哈希"只能采取同一个动作（按当前内容重设基线），区分形态只影响
    归因文案、不影响裁定方向。**旧实现把断链返回的 None/"" 交给 `if stored_hash and ...`
    判断，短路成"无手工编辑"，随后无条件从 .bak 恢复——等于把"哈希没读到"读成"没人改过"，
    本轮刚补进 HTML/config 的修复会被抹掉（2026-10-15 面板建议批实证形态）。**
    """
    try:
        raw = Path(hash_path).read_text(encoding='utf-8').strip()
    except OSError:
        return None
    if len(raw) == 32 and all(c in '0123456789abcdefABCDEF' for c in raw):
        return raw
    return None


def _write_hash(hash_path, content):
    """Write MD5 hash of content to .hash（原子：半截写即断链，见 _read_hash）。"""
    h = hashlib.md5(content.encode('utf-8')).hexdigest()
    atomic_write_text(hash_path, h)


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


VALID_SHRINK_BASES = ("tts_wav", "speech_end")


def resolve_shrink_basis(cli_value, config_value, rules_value):
    """收缩基准的三级优先：CLI > 项目 config 的 shrink_basis > 配置权威源。

    非法值**不静默穿过**：点名后回退 `tts_wav`（＝既有行为）。
    这是 fail-closed 到旧行为，不是 fail-open 到新行为——配置写坏不该让时间轴悄悄换基准。
    """
    for tag, v in (("--shrink-basis", cli_value), ("config.shrink_basis", config_value)):
        if v is None:
            continue
        if v in VALID_SHRINK_BASES:
            return v
        print(f"  WARNING: {tag} = {v!r} 非合法值（{VALID_SHRINK_BASES}）— 回退 tts_wav")
        return "tts_wav"
    return rules_value if rules_value in VALID_SHRINK_BASES else "tts_wav"


def _default_shrink_basis():
    """--shrink-basis 单一权威源：`audio_sync_rules.json → shrink.basis`。

    两个取值（2026-10-13 用户裁定"换余量基准，挂在本来就要重渲的那一批顺带生效"）：
      `tts_wav`（缺省）＝窗口对 wav 全时长留 `shrink_margin`。既有行为，逐字不变。
      `speech_end`     ＝窗口对 **ASR 末句实测收尾**留 `shrink_margin`，把 wav 尾部静音
        （本仓浅色片实测 0.31–0.46s／场）从"必须保留"改为"可裁"。收益是名义余量＝听感余量，
        代价是全仓时间轴基准变更 → 缺省不动，由项目 config `shrink_basis` 或 CLI 显式声明。

    读不回（缺文件／缺节／缺键／值非法）一律回 `tts_wav` 并打印归因——fail-closed 到既有行为，
    不得由"配置断链"读出"新行为"（A06 同族：断链必须点名，不能伪装成裁定）。
    """
    valid = ("tts_wav", "speech_end")
    try:
        from _script_env import ROOT
        rules_path = ROOT / "程序文件" / "配置" / "config" / "quality" / "audio_sync_rules.json"
        with open(rules_path, 'r', encoding='utf-8') as f:
            basis = json.load(f)["shrink"].get("basis", "tts_wav")
        if basis not in valid:
            print(f"  WARNING: shrink.basis = {basis!r} 非合法值（{valid}）— 回退 tts_wav")
            return "tts_wav"
        return basis
    except Exception as exc:
        print(f"  WARNING: audio_sync_rules.json 的 shrink.basis 取不回"
              f"（{type(exc).__name__}: {exc}）— 回退 tts_wav")
        return "tts_wav"


def measure_speech_ends(tts_dir, num_scenes):
    """逐场 ASR 末句收尾时刻（场内相对秒），取自生产落盘的 `_timeline_manifest.json`。

    与词级 cue 同一个时间源（`load_cue_time_source`），不新起第二套取数：
    句级 `sentences[].rel_end` 与 `chars` 出自同一次对齐，两处各跑一遍对齐就会因
    线程环境漂移（实测 ±0.02~0.42s）而使"留白基准"与"字幕基准"分叉。

    返回 `{scene_id(str): rel_end}`，缺项即不返回该键——调用侧按"无实测"回退 wav，
    禁止用 0.0 或整场时长兜底（前者会把窗口裁到音频开头，后者等于没改）。
    """
    by_id = load_cue_time_source(tts_dir)
    out = {}
    for sid in range(1, num_scenes + 1):
        sc = by_id.get(str(sid))
        if not sc:
            continue
        sents = sc.get("sentences") or []
        vals = []
        for s in sents:
            try:
                vals.append(float(s["rel_end"]))
            except (KeyError, TypeError, ValueError):
                continue
        if not vals:
            continue
        rel_end = max(vals)
        tts_dur = sc.get("tts_duration")
        try:
            tts_dur = float(tts_dur) if tts_dur is not None else None
        except (TypeError, ValueError):
            tts_dur = None
        # 一致性哨兵：末句收尾不得晚于 wav 时长（超出即记录与音频不同代），
        # 也不得逼近 0（降级/塌缩记录会让整场被裁光）。两种形态一律不采信。
        if tts_dur is not None and (rel_end <= 0 or rel_end > tts_dur + 0.05):
            print(f"  [SHRINK-BASIS] 场 {sid} 末句 rel_end={rel_end:.2f}s 与 "
                  f"wav={tts_dur:.2f}s 不符 — 该场回退 wav 基准")
            continue
        out[str(sid)] = rel_end
    return out


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
    parser.add_argument("--shrink-basis", choices=("tts_wav", "speech_end"), default=None,
                        help="收缩侧的留白基准：tts_wav（缺省，既有行为）按 wav 全时长；"
                             "speech_end 按 ASR 末句实测收尾，把 wav 尾部静音从'必须保留'改为"
                             "'可裁'。未显式传参时读项目 config 的 shrink_basis，再回 "
                             "audio_sync_rules.json → shrink.basis。扩展侧永不使用本参数。")
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

        if stored_hash is None:
            # 断链 → 按当前 HTML 重设基线。走 else 分支会从 .bak 恢复，把本轮刚补的
            # 修复（如场景根 opacity）一并抹掉，且日志上完全看不出发生过。
            shutil.copy2(html_path, html_bak)
            _write_hash(html_hash_path, html_content)
            html_content = html_path.read_text(encoding='utf-8')
            print("  [HASH-BROKEN] HTML .hash 缺失或不可解析 → 按当前 HTML 重设基线"
                  "（不恢复 .bak）")
        elif current_hash != stored_hash:
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
            # (2026-08-22 incident: rendered 150s vs declared 160.6s).
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
        if stored_cfg_hash is None:
            # 同 HTML 侧：断链不得走"从 .bak 恢复"。config 是单一权威源，被旧备份
            # 覆盖等于把本轮刚改的字段（时长预算/停顿声明/路径字段…）静默回退。
            shutil.copy2(config_path, config_bak)
            _write_hash(config_hash_path, current_cfg_text)
            print("  [HASH-BROKEN] config .hash 缺失或不可解析 → 按当前 config 重设基线"
                  "（不恢复 .bak）")
        elif current_cfg_hash != stored_cfg_hash:
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

    # 收缩侧基准：CLI > 项目 config 的 shrink_basis > audio_sync_rules.json → shrink.basis
    # 三级里任何一级没声明就落到下一级，缺省 tts_wav ＝ 既有行为逐字不变。
    basis = resolve_shrink_basis(args.shrink_basis, cfg.get('shrink_basis'),
                                 _default_shrink_basis())
    trim_durations = None
    if basis == "speech_end" and args.shrink:
        speech_ends = measure_speech_ends(tts_dir, num_scenes)
        missing = [i + 1 for i in range(num_scenes) if str(i + 1) not in speech_ends]
        if missing:
            print(f"  [SHRINK-BASIS] {len(missing)}/{num_scenes} 场无末句实测"
                  f"（场 {missing}）— 这些场按 wav 基准")
        if not speech_ends:
            print("  [SHRINK-BASIS] 全场取不到末句实测 — 整体回退 tts_wav 基准"
                  "（多为 _timeline_manifest.json 缺失或早于句级记录落盘）")
        else:
            # 可裁基准永不得高于 wav：末句若晚于 wav（异常记录）由 measure_speech_ends 拦掉，
            # 这里再兜一层 min 使"换基准"在结构上不可能把音频裁掉哪怕 0.01s。
            trim_durations = [min(tts_durations[i], speech_ends.get(str(i + 1), tts_durations[i]))
                              for i in range(num_scenes)]
            reclaim = sum(t - m for t, m in zip(tts_durations, trim_durations))
            print(f"  [SHRINK-BASIS] speech_end 生效：可裁尾部静音合计 {reclaim:.2f}s"
                  f"（{len(speech_ends)}/{num_scenes} 场有末句实测）")

    print(f"\nTTS Duration Analysis (shrink={'ON' if args.shrink else 'OFF'}" +
          (f", margin={args.shrink_margin}s" if args.shrink else "") +
          (f", basis={basis}" if args.shrink else "") + "):")
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
        shrink=args.shrink, shrink_margin=args.shrink_margin,
        trim_durations=trim_durations
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
        # 零调整分支也必须走 cue 写入面（2026-10-12 词级 cue 批）。这不是"顺手补一处"：
        # 窗口已收敛的项目走的就是这个提前返回，只在下方的调整分支写块＝
        # 收敛后新加的 cue 声明永远不落盘，而预检那条校验已判它合法——
        # 条文在生效路径上没有载体（A06 同族）。写在 _write_hash 之前，
        # 使哈希基准＝真正落盘的那份字节，否则下一轮把自家写的块读成"手工编辑"。
        html_content, cue_changed, cue_problems = resolve_cue_block(html_content, tts_dir)
        if cue_problems:
            return report_cue_problems(cue_problems)
        if cue_changed:
            html_path.write_text(html_content, encoding='utf-8')
            print(f"  Updated HTML (cue 块，零调整分支): {html_path}")
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
    # 词级 cue 定界块：与零调整分支共用 resolve_cue_block（约束见该函数 docstring，
    # 两处各写一份即两套口径）。
    html_content, cue_changed, cue_problems = resolve_cue_block(html_content, tts_dir)
    if cue_problems:
        return report_cue_problems(cue_problems)
    if cue_changed:
        print(f"  CUE-MAP: 整块再生成（{len(cue_resolve.scan_cue_declarations(html_content))} 条声明）")
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
