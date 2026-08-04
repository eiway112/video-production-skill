#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Narration / subtitle digit-normalization lint.

Shared by: preflight_check.py, scene_compiler.py (SDL validation),
enhance_video_audio.py (post-generation SRT scan).

Policy (see AGENTS.md → "旁白与字幕数字规范"):
  Numeric quantities in narration and subtitles MUST use Arabic digits
  (30小时, 5%, 20元). Chinese numerals are only allowed inside idioms
  or ordinals.

Detection strategy:
  1. Regex matches "Chinese-digit run + quantitative unit". A 2+ char
     digit run (三十/一百/五千) is unambiguously numeric — hard match.
     A 1-char digit followed by a time/money/percent unit (三小时/五元)
     is also treated as numeric.
  2. Idiom whitelist filters out common non-numeric expressions
     (三心二意, 第三, 独一无二 ...). Projects can extend via
     `narration_digits_whitelist` in config.json.

Return type is a list of Finding tuples so callers can decide how to
render them (preflight uses r.error, SDL validator prints, step7 warns).
"""

import json
import re
from pathlib import Path
from typing import List, NamedTuple, Optional


class DigitFinding(NamedTuple):
    scope: str          # e.g. "scene 7 narration" or "subtitle #14"
    matched: str        # e.g. "三十小时"
    context: str        # surrounding snippet for the message
    suggestion: str     # human-readable replacement hint


class SpeechFinding(NamedTuple):
    scope: str          # e.g. "scene 3 narration"
    kind: str           # "dot_ext" | "semicolon"
    matched: str        # e.g. "narration点json" / "；"
    suggestion: str     # human-readable fix hint


# 念读写法“xxx点扩展名”：为迁就 TTS 发音把“.”写成“点”的文件名表达
_DOT_EXT_RE = re.compile(
    r"[A-Za-z0-9_\-]+点(?:json|html?|md|py|js|ts|srt|mp4|mp3|wav|png|jpe?g|csv|txt|ya?ml|pdf)",
    re.IGNORECASE,
)


def scan_speech_style(cfg: dict) -> List[SpeechFinding]:
    """扫描念读文本与显示文本的配对缺口，以及弱停顿标点。

    规则一（dot_ext）：旁白为迁就 TTS 发音写成“xxx点json”时，必须在
    config 声明 subtitle_display_replacements 把字幕还原为“xxx.json”，
    否则念读文本会原样烧进字幕（用户反馈：字幕出现“narration点json”）。

    规则二（semicolon）：TTS 引擎对分号的停顿显著短于句号，语义独立的
    断句用“；”会导致音频衔接仓促（用户反馈：句间几乎无停顿）。
    属风格建议 → 调用方应降级为警告。
    """
    findings: List[SpeechFinding] = []
    repl = cfg.get("subtitle_display_replacements", {}) or {}
    for s in cfg.get("scenes", []) or []:
        sid = s.get("scene_id", "?")
        text = s.get("narration", "") or ""
        scope = f"scene {sid} narration"
        for m in _DOT_EXT_RE.finditer(text):
            token = m.group(0)
            # 替换键覆盖判定：key 包含 token 或 token 包含 key 即视为已配对
            if any((token in k) or (k in token) for k in repl):
                continue
            display = token.replace("点", ".", 1)
            findings.append(SpeechFinding(
                scope, "dot_ext", token,
                f"『{token}』是 TTS 念读写法，需在 config 声明 "
                f'subtitle_display_replacements {{"{token}": "{display}"}} '
                f"还原字幕显示文本"))
        if "；" in text:
            findings.append(SpeechFinding(
                scope, "semicolon", "；",
                "TTS 对分号停顿过短、听感仓促，语义独立断句建议改用句号"))
    return findings


# ── Configurable rule set (loaded once, cached) ─────────────────────
_RULES_CACHE: Optional[dict] = None


def _default_rules() -> dict:
    return {
        "chinese_digit_chars": "零〇一二三四五六七八九十百千万",
        # 1-char digit is enough when unit is a time/money/percent primary unit
        "primary_units": [
            "小时", "分钟", "秒", "天", "日", "年", "月", "周",
            "元", "万元", "亿元", "角", "块",
            "%", "％",
        ],
        # Any unit — combined with 2+ char digit run triggers a match
        "extended_units": [
            "次", "倍", "个", "项", "条", "份", "位", "名", "人", "家", "款",
            "米", "厘米", "毫米", "公里", "千米", "公斤", "克", "吨",
            "度", "伏", "安", "瓦", "千瓦",
            "kg", "km", "GB", "MB", "TB", "dB",
        ],
        # Common idioms / ordinals / set phrases — matched literally
        "idiom_whitelist": [
            # 成语
            "三心二意", "一心一意", "五湖四海", "三头六臂", "独一无二",
            "一模一样", "百年树人", "千方百计", "万无一失", "三令五申",
            "四通八达", "一目了然", "一步到位", "二话不说", "十全十美",
            "百思不解", "千头万绪", "万紫千红", "一鸣惊人", "百战百胜",
            # 序数
            "第一", "第二", "第三", "第四", "第五", "第六", "第七", "第八", "第九", "第十",
            "第十一", "第十二", "第一次", "第二次",
            "唯一", "第几",
            # 项目内常见弱数字表达（"一人公司/一年一度"等，通常语义模糊而非量化）
            "一人公司", "一人一岗", "一年一度", "一日千里",
            # 项目类容易误伤的短语（非精确量化）
            "百万级", "千万级", "亿万级",
        ],
        # Project config key that appends to idiom_whitelist
        "project_whitelist_override_key": "narration_digits_whitelist",
    }


def load_rules(rules_path: Optional[Path] = None) -> dict:
    """Load rules from config/quality/narration_digits_rules.json.

    Falls back to _default_rules() if the file is missing or invalid —
    the check must never crash the pipeline over a missing config file.
    """
    global _RULES_CACHE
    if _RULES_CACHE is not None and rules_path is None:
        return _RULES_CACHE

    if rules_path is None:
        rules_path = (
            Path(__file__).resolve().parents[1] / "配置" / "config" / "quality"
            / "narration_digits_rules.json"
        )

    rules = _default_rules()
    if rules_path and rules_path.exists():
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # Shallow merge — user-supplied lists REPLACE defaults if set
            for k, v in loaded.items():
                if k.startswith("$"):
                    continue
                rules[k] = v
        except (json.JSONDecodeError, OSError):
            pass  # keep defaults on any read error

    _RULES_CACHE = rules
    return rules


def _build_regex(rules: dict):
    digits = re.escape(rules["chinese_digit_chars"])
    # Primary units: match with 1+ char digit run
    primary = "|".join(re.escape(u) for u in rules["primary_units"])
    # Extended units: match only with 2+ char digit run (compound numerals)
    extended = "|".join(re.escape(u) for u in rules["extended_units"])
    primary_re = re.compile(rf"([{digits}]+)({primary})")
    extended_re = re.compile(rf"([{digits}]{{2,}})({extended})")
    return primary_re, extended_re


def _passes_whitelist(text: str, start: int, end: int, matched: str,
                      whitelist: list) -> bool:
    """Return True if the match is covered by an idiom / ordinal whitelist entry.

    Whitelist entries match by substring around the found span. Also filters
    when preceded by "第" (ordinals like "第三次" that slip past explicit list).
    """
    # Cheap prefix filter: "第" immediately before → ordinal
    if start > 0 and text[start - 1] == "第":
        return True

    # Idiom span check: any whitelist entry that overlaps the match position
    for phrase in whitelist:
        if not phrase:
            continue
        idx = 0
        while True:
            hit = text.find(phrase, idx)
            if hit < 0:
                break
            if hit <= start and hit + len(phrase) >= end:
                return True
            idx = hit + 1
    return False


def scan_text(text: str, scope: str, project_whitelist: Optional[list] = None,
              rules: Optional[dict] = None) -> List[DigitFinding]:
    """Scan a single string for Chinese-digit + unit violations.

    Args:
        text: source string (a narration line or subtitle entry)
        scope: label for the finding location, e.g. "scene 3 narration"
        project_whitelist: extra phrases to whitelist beyond built-in idioms
        rules: pre-loaded rules (optional; loaded on demand otherwise)
    """
    if not text:
        return []

    rules = rules or load_rules()
    whitelist = list(rules.get("idiom_whitelist", []))
    if project_whitelist:
        whitelist.extend(project_whitelist)

    primary_re, extended_re = _build_regex(rules)
    findings: List[DigitFinding] = []
    seen_spans = set()  # dedup overlapping matches from the two regexes

    for regex in (primary_re, extended_re):
        for m in regex.finditer(text):
            span = m.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            matched = m.group(0)
            if _passes_whitelist(text, span[0], span[1], matched, whitelist):
                continue
            ctx_start = max(0, span[0] - 6)
            ctx_end = min(len(text), span[1] + 6)
            context = text[ctx_start:ctx_end]
            suggestion = f"『{matched}』 → 用阿拉伯数字（例: 30小时 / 5% / 20元）"
            findings.append(DigitFinding(scope, matched, context, suggestion))
    return findings


def scan_config_narrations(cfg: dict) -> List[DigitFinding]:
    """Scan every scene's narration field in a HyperFrames config dict."""
    project_wl = cfg.get(_default_rules()["project_whitelist_override_key"], [])
    rules = load_rules()
    findings: List[DigitFinding] = []
    for s in cfg.get("scenes", []) or []:
        sid = s.get("scene_id", "?")
        narration = s.get("narration", "")
        findings.extend(scan_text(
            narration, f"scene {sid} narration",
            project_whitelist=project_wl, rules=rules,
        ))
    return findings


def scan_srt_file(srt_path: Path,
                  project_whitelist: Optional[list] = None) -> List[DigitFinding]:
    """Scan a generated .srt file for the same rule."""
    if not srt_path.exists():
        return []
    content = srt_path.read_text(encoding="utf-8-sig")
    rules = load_rules()
    findings: List[DigitFinding] = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0])
        except ValueError:
            continue
        subtitle = " ".join(lines[2:])
        findings.extend(scan_text(
            subtitle, f"subtitle #{idx}",
            project_whitelist=project_whitelist, rules=rules,
        ))
    return findings


def scan_sdl_scenes(scenes: list) -> List[DigitFinding]:
    """Scan raw SDL scene dicts (used by scene_compiler.py)."""
    rules = load_rules()
    findings: List[DigitFinding] = []
    for i, s in enumerate(scenes or []):
        narration = s.get("narration", "") if isinstance(s, dict) else ""
        findings.extend(scan_text(
            narration, f"scene {i+1} narration", rules=rules,
        ))
    return findings


def format_findings(findings: List[DigitFinding]) -> str:
    """Human-readable multi-line summary of findings."""
    if not findings:
        return ""
    lines = []
    for f in findings:
        lines.append(f"  {f.scope}: 命中 “{f.matched}” — 上下文 “…{f.context}…” — {f.suggestion}")
    return "\n".join(lines)
