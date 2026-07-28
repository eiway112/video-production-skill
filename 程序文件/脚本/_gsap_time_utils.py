#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GSAP Time Resolution & Scene Metadata Utilities.

Shared by: preflight_check, instant_preview, enhance_video_audio, adjust_timeline.

Provides two structured declarations readers:
  - T-block: `var T = { s1: 3.0, s2: 14.0, ... }` — GSAP timeline position mapping
  - S-block: `var S = [{start, end, dur, type, cover}, ...]` — authoritative scene metadata

These replace the old "regex guessing" pattern where consumers reconstructed
information from scattered HTML attributes and GSAP code patterns.

Import pattern:
  import sys
  sys.path.insert(0, str(Path(__file__).parent))
  from _gsap_time_utils import parse_t_block, parse_scene_map
"""

import json
import re


# ── T-block: GSAP timeline position mapping ──

def parse_t_block(script):
    """Parse T-block (var T = { s1: N, s2: N, ... }) from script content.

    Returns {1: 3.0, 2: 14.0, ...} mapping scene_index -> absolute start time.
    Returns empty dict if no T-block found.
    """
    t_match = re.search(r'var\s+T\s*=\s*\{([^}]+)\}', script)
    if not t_match:
        return {}
    mapping = {}
    for entry in re.finditer(r's(\d+)\s*:\s*([\d.]+)', t_match.group(1)):
        mapping[int(entry.group(1))] = float(entry.group(2))
    return mapping


def resolve_script_times(script, t_map):
    """Extract all GSAP time values from script, resolving both numeric literals
    and T-block expressions (T.sN + offset) to absolute float values.

    Returns a sorted list of (time_value, raw_expression) tuples.
    """
    times = []

    # 1. Numeric literals: }, NUMBER)
    for m in re.finditer(r'\},\s*(\d+\.?\d*)\s*\)', script):
        times.append((float(m.group(1)), m.group(1)))

    # 2. T-block expressions: T.sN + offset or T.sN - offset
    if t_map:
        for m in re.finditer(r'T\.s(\d+)\s*([+\-])\s*([\d.]+)', script):
            scene_idx = int(m.group(1))
            sign = 1 if m.group(2) == '+' else -1
            offset = float(m.group(3))
            base = t_map.get(scene_idx, 0)
            resolved = base + sign * offset
            times.append((resolved, m.group(0)))

        # 3. Bare T.sN references (no offset)
        for m in re.finditer(r'T\.s(\d+)(?!\s*[+\-])', script):
            scene_idx = int(m.group(1))
            base = t_map.get(scene_idx, 0)
            times.append((base, m.group(0)))

    times.sort(key=lambda x: x[0])
    return times


def resolve_line_time(line, t_map):
    """Extract the GSAP time position from a single JS line.

    Handles both numeric literals (}, 12.5)) and T-block expressions (T.s1 + 0.3).
    Returns float or None if no time position found.
    """
    if t_map:
        t_expr = re.search(r'T\.s(\d+)\s*([+\-])\s*([\d.]+)', line)
        if t_expr:
            idx = int(t_expr.group(1))
            sign = 1 if t_expr.group(2) == '+' else -1
            offset = float(t_expr.group(3))
            return t_map.get(idx, 0) + sign * offset
        t_bare = re.search(r'T\.s(\d+)(?!\s*[+\-])', line)
        if t_bare:
            return t_map.get(int(t_bare.group(1)), 0)

    nums = re.findall(r',\s*([\d.]+)\s*\)\s*;?', line)
    if nums:
        return float(nums[-1])
    return None


# ── S-block: authoritative scene metadata ──

def parse_scene_map(script):
    """Parse S-block (var S = [...]) from script content.

    Returns a dict with:
      - 'scenes': list of {start, end, dur, type, cover} dicts
      - 'duration': total video duration (float)
      - 'cover_duration': cover scene duration (float)
      - 'scene_count': number of content scenes (int, excludes cover)
    Returns empty dict if no S-block found.
    """
    s_match = re.search(r'var\s+S\s*=\s*(\[[\s\S]*?\])\s*;', script)
    if not s_match:
        return {}
    try:
        scenes = json.loads(s_match.group(1))
    except (json.JSONDecodeError, ValueError):
        return {}

    cover_dur = 0.0
    content_count = 0
    for s in scenes:
        if s.get('cover'):
            cover_dur = s.get('dur', 0.0)
        else:
            content_count += 1

    duration = max((s.get('end', 0) for s in scenes), default=0.0)

    return {
        'scenes': scenes,
        'duration': duration,
        'cover_duration': cover_dur,
        'scene_count': content_count,
    }
