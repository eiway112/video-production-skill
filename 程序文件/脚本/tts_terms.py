"""送引擎文本的术语修法单源（2026-11-07 ElevenLabs 接入批）。

存在理由（实测形态，非推测）：ElevenLabs 会把「装配式」的「装配」读错，而同一段
文本在 CosyVoice 上读得对。修法只能落在"送进合成的那一份文本"上——旁白原文有六个
消费者（字幕分段、ASR 强制对齐、词级 cue、数字 lint、停顿锚词、duration 预算），
把修法写回原文会同时污染这六处。

判据与形态住本模块 + `配置/config/quality/tts_pronunciation.json`：
  - 表里只有**已由真人听感确认过**的词条，禁止预填猜测项；
  - 修法用**同音字替换**（装配式 → 装佩式）。零宽空格（U+200B）方案已证伪：
    2026-11-11 复测，零宽放在「装|配」或「配|式」都不改变读音，只是让 ASR 按
    上下文猜回正确字面，人耳照旧听到 pī。同音字只换字形、不换音素（拼音全同），
    所以 ASR 强制对齐与字幕时间戳不受影响；常规空格会产生 0.4s 断口，不采用；
  - 表按引擎生效（`engines`），qwen 不读本表——它本来就读得对，读了只会白击穿缓存；
  - 调用的唯一入口是 `enhance_video_audio._pause_engine_text`，本模块不自己找调用点。

缓存纪律：`fingerprint()` 进 elevenlabs 的 TTS 缓存键。改表即换键，旧音频不会被
静默复用（与 pause_marks.cache_tag 同族：改了影响音频的输入而键不变＝交出旧产物）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_ZWSP = "\u200b"


class TermRulesError(RuntimeError):
    """术语表不可用。调用方不得吞掉——那是"修法没生效但音频照样产出"。"""


def rules_path() -> Path:
    return (Path(__file__).resolve().parent.parent
            / "配置" / "config" / "quality" / "tts_pronunciation.json")


def load_rules() -> dict:
    p = rules_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            rules = json.load(f)
    except OSError as e:
        raise TermRulesError(f"术语表读取失败：{p}（{e}）") from e
    except json.JSONDecodeError as e:
        raise TermRulesError(f"术语表不是合法 JSON：{p}（{e}）") from e
    if not isinstance(rules.get("terms"), list):
        raise TermRulesError(f"术语表缺 terms 数组：{p}")
    return rules


def fingerprint() -> str:
    """术语表字节指纹，供 TTS 缓存键使用。表不在位时返回 none（不猜）。"""
    try:
        return hashlib.sha256(rules_path().read_bytes()).hexdigest()[:12]
    except OSError:
        return "none"


def apply(text: str, engine: str) -> tuple[str, list[tuple[str, str]]]:
    """按引擎把术语表应用到**送引擎的文本**上，返回 (新文本, 已应用条目)。

    引擎不在 `engines` 清单内一律原样返回——这是"表按引擎生效"的唯一实现点，
    调用方不得再判一次（两处各判一次＝两份真相）。
    """
    rules = load_rules()
    if str(engine).lower() not in (rules.get("engines") or []):
        return text, []

    applied: list[tuple[str, str]] = []
    for entry in rules["terms"]:
        if not isinstance(entry, dict):
            continue
        word = str(entry.get("word") or "")
        repl = str(entry.get("engine_text") or "")
        if not word or not repl or word == repl:
            continue
        if word not in text:
            continue
        text = text.replace(word, repl)
        applied.append((word, repl))
    return text, applied


def describe_replacement(repl: str) -> str:
    """把不可见字符显形，供日志与交付说明引用。"""
    return repl.replace(_ZWSP, "<ZWSP>")
