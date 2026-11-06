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

def parse_s_block_array(array_text):
    """把 `var S = [...]` 的数组字面量解析成 dict 列表，返回 `(scenes 或 None, reason)`。

    存在理由（2026-11-06 架构收口②）：本仓 S 块有**两种书写形态**——编译器产的 JSON
    形态（41 份）与手写 HTML 的 JS 字面量形态（裸键、单引号，8 份）。旧实现只认前者，
    且失败即静默：写入侧 `adjust_s_block` 因此 no-op、不打印，而
    `scene_patch_render.extract_scene_times` 的时间源见证按正则扫全脚本的 `end:` 字面量，
    **读得到**这份没被维护的旧数值。同一声明"一边不存在、一边是权威见证"，实测
    某支手写项目每轮因此多付一次全量渲染（末场 end 66.0 对 data-duration 65.6）。

    刻意**只供写入侧与普查/门禁消费，不改 `parse_scene_map` 的 JSON-only 语义**：后者一旦
    开始看见 JS 字面量形态，`adjust_timeline` 的时长推导就从 narration 指针切到
    `s_map['duration'] + total_ext`，而两条公式在 `total_ext`（各场扩展量之和）与
    `extensions[-1]`（末场扩展量）上并不等价——那是音频/字幕对齐层面的行为变更，
    不属于"把静默 no-op 修成有声音"这一步的收益。要动它须单独成批并给等价性实测。

    已知的转换边界（刻意不做更复杂的词法分析）：双引号字符串值里含**撇号**时，
    单引号→双引号互换会把该字符串改坏，于是走 `unparsable` 并交调用方点名——
    这是"读不进就说出来"而非"读错了还改写"，方向安全。

    自校验守卫：裸键加引号与单引号互换都是**可能改坏**的机械变换，故转换后必须
    `json.loads` 成功**且**每项是带数值 start/end 的对象（cover 项除外），否则一律
    返回 reason 交调用方点名——宁可报"维护不了"，不得拿一份读错的表去改写。
    """
    text = (array_text or "").strip()
    if not (text.startswith("[") and text.endswith("]")):
        return None, "数组字面量边界不完整"
    try:
        scenes = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        conv = re.sub(r"([{,]\s*)([A-Za-z_$][\w$-]*)(\s*:)", r'\1"\2"\3', text)
        conv = conv.replace("'", '"')
        conv = re.sub(r",(\s*[}\]])", r"\1", conv)
        try:
            scenes = json.loads(conv)
        except (json.JSONDecodeError, ValueError) as e:
            return None, f"JSON 与 JS 字面量两种形态均解析失败（{type(e).__name__}: {str(e)[:60]}）"
    if not isinstance(scenes, list) or not scenes:
        return None, "解析结果不是非空数组"
    for i, s in enumerate(scenes):
        if not isinstance(s, dict):
            return None, f"第 {i + 1} 项不是对象"
        if s.get("cover"):
            continue
        for k in ("start", "end"):
            if not isinstance(s.get(k), (int, float)):
                return None, f"第 {i + 1} 项缺数值 {k} 键"
    return scenes, None


def classify_s_block_shape(array_text):
    """给 `var S` 的数组字面量定形，返回 `(shape, scenes_or_None, reason)`。

    三种合法形状、一种不可维护（2026-11-06 全仓普查现读，79 份 index.html）：

      - `json`            编译器产，键带引号、数值字面量（41 份）→ 写入侧维护
      - `js_literal`      手写，裸键/单引号、数值字面量（8 份）→ 写入侧维护（本批修复面）
      - `derived_from_t`  值写成 `start: T.s1, dur: T.s2 - T.s1`（2 份）→ **结构上不会与
                          T 块分叉**，写入侧无事可做；把它当"维护不了"报警属误报
      - `unparsable`      以上皆非 → 必须出声，不得静默 no-op

    定形顺序＝**先试解析，解析不进再看是否 T 派生**：`T.s1 - T.s0` 这类表达式两种解析
    路径都读不进，若不辨来源就会被归进 `unparsable` 并触发一条本不该存在的告警。
    （本句此前写的是"刻意先判 T 派生"——那个先后关系实测不承重：两种顺序对四类形状的
    归类结果相同，故按真实因果改写，不保留一条把自己假设写成机制的理由。）
    """
    text = (array_text or "").strip()
    if not (text.startswith("[") and text.endswith("]")):
        return "unparsable", None, "数组字面量边界不完整"
    scenes, reason = parse_s_block_array(text)
    if scenes is None:
        if re.search(r'[:,]\s*"?(?:start|end|dur)"?\s*:\s*T\.', text):
            return "derived_from_t", None, None
        return "unparsable", None, reason
    return ("json" if re.search(r'\{\s*"', text) else "js_literal"), scenes, None


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
