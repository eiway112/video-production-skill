#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""停顿声明的单一持有者：定位、裁定、翻译成 CosyVoice SSML `<break>`（2026-10-13 停顿可控性接入批）。

存在理由（实测，非推断）
------------------------
2026-10-13 用户反馈多支成片"停顿节奏不准"。仓内既有机制只在**字幕段边界**补静音
（`audio_sync_rules.sentence_pause` + `enhance_video_audio._enforce_sentence_pauses`），
段内标点由引擎内部韵律决定。同一段真实旁白的实测读数证明引擎并不按标点等级给停顿：
句号 0.26s **短于** 顿号 0.44s，同一个逗号在同一句里既给 0.30s 又给 0.86s
（`_取证/20261013/tts_pause_probe/report.json`）。而引擎侧其实有直接控制面：
`<break time>` 单调生效（同位置 无声明 0.36 → 200ms 0.86 → 1500ms 1.70）。本模块把这条
控制面接进来，不换引擎、不换音色、不动时间轴基准。

SSML 在这条路径上是**按包裹触发**的，两种形态的实测后果不同（这一条决定尖括号判据）
--------------------------------------------------------------------------
- 带 `<speak>` 包裹＝进入 SSML 解析，**不需要传 `enable_ssml`**：不带开关的 D 档与带开关的
  C 档落盘 wav 逐字节相同；写入一枚仓内不存在的 `<bogus-tag/>` 被服务端直接
  `400 Invalid SSML label` 拒收——整个调用失败，不是降级。
- 纯文本（无 `<speak>`）**不解析标签**：实测把 `<break time="300ms"/>` 当文字**念了出来**
  （ASR 归一化字符流里 break／time／ms 三词俱在，match_ratio 仍 1.0，而那处间隔 0.0s）。
  裸比较号 `<` 则被静默略过（G 档：调用 OK、间隔 0.0s、无多余词）。
  取证 `../pause_batch/bracket_probe.py`（同一份 wav 上零新增调用复算）。

所以旁白里的尖括号在两种形态下都是病灶：有声明时可能让整次合成报错，无声明时会在成片里
念出英文乱码词——而两种情况下 ASR 对齐与终检都可能照常通过。判据住 `check_angle_brackets`。

为什么声明住在 narration.json 的场景字段而不是旁白文本里的标记串
----------------------------------------------------------------
旁白文本的消费者有六处：字幕分段（`_split_text_to_segments`）、ASR 强制对齐基准
（`_forced_align.align_scene` 的 `segments`）、词级 cue 字符流（`cue_resolve`）、
数字 lint（`_narration_lint`）、交付说明、预检本身。文本内标记要求这六处各自记得剥离，
漏一处就是"字幕印出标记串"或"cue 字符流错位"——正是 A06"取数断链会伪装成裁定"的高危形态。
结构化字段对不认识它的消费者天然惰性，且与词级 cue 同型（锚词定位 + 单一解析器），
那条链已有两支真实业务交付的先例。

档位不是设计出来的，是被标定逼出来的
------------------------------------
`_取证/20261013/pause_batch/calibration.json`：四段真实旁白 × 四档声明值的 Δ（声明后 − 自然）分布

    200ms → Δ ∈ [-0.16, +0.64]   （极差 0.80s，与噪声不可分 → 低于下限不可控）
    500ms → Δ ∈ [+0.70, +0.90]   （极差 0.20s，四段复现 → 本模块的自动插取值）
    800ms → Δ ∈ [+1.08, +1.30]   （极差 0.22s）
   1200ms → Δ ∈ [+1.42, +1.74]   且该档实测间隔达 1.5~2.0s，单场内过长

故 `ms` 的合法区间取 **[min_ms, max_ms] ＝ [400, 1000]**，越界即 ERROR 而非夹取：
夹取等于把"作者以为买了 1.5s 停顿、实际拿到随机值"这一形态洗成通过。
另有一条实测事实决定了"上限按合计计"：SSML 模式下**未声明位置的节奏也会移动**
（同一次调用内，未插 break 的逗号从 0.84s 变 0.26s；另一段句号 0.26 → 0.56），
所以停顿预算是全场资源，不是逐点独立量——`max_total_ms_per_scene` 由此而来。

两处不得误读的边界
------------------
1. **只对 qwen 主引擎成立**。Edge-TTS 有另一套 SSML 语义，本仓未在其上标定；
   声明存在而引擎非 qwen 时一律报错，禁止"换个语法试试看"的静默降级。
2. **自动插缺省关闭**。开启＝机器替作者改朗读文本并加长片长（每处约 +0.8s，
   场景窗口由 TTS 时长驱动，会级联到时长预算与全量渲染）。翻缺省属全仓时间轴基准变更，
   与 2026-10-13 `shrink.basis` 那条同判据：代码落地、缺省不翻，挂在本来就要重跑的那批上。

取数断链的处理
--------------
配置文件缺失/不可解析/缺节/缺键/值非法，以及锚词定位不到、定位有歧义，一律**抛错**，
不返回空计划：调用位点在 TTS 之前，返回空计划等于把"作者声明了停顿"读成"作者没声明"，
静默交出没有停顿的音频且全部下游门禁照常通过。歧义（同词出现多次）取**首次**并 WARN，
与 `cue_resolve` 同判据。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import cue_resolve as _cr
except ImportError as e:  # 归一化口径必须与 cue 同源，取不回即报错，不得自写第二套
    raise RuntimeError(
        f"pause_marks 取数断链：cue_resolve 模块取不回（{e}）——"
        "锚词定位与归一化不得另起一套口径（与词级 cue 批同判据）"
    ) from e

_RULES_REL = Path("config") / "quality" / "audio_sync_rules.json"
_SCRIPTS = Path(__file__).resolve().parent


class PausePlanError(RuntimeError):
    """声明不可执行或配置断链。调用位点在 TTS 之前，宁可不出片也不得静默去掉停顿。"""


def rules_path() -> Path:
    from _script_env import QUALITY_DIR  # 延迟导入：模块级导入会把环境耦合到取数面
    return QUALITY_DIR / "audio_sync_rules.json"


def check_angle_brackets(text: str, *, will_wrap: bool) -> None:
    """旁白含尖括号即抛错——两种形态都实测有害，且都可能被下游读成正常。

    `will_wrap` 决定归因措辞（同一枚尖括号在两条路径上的后果不同，这是实测出来的分岔，
    不是修辞）：

    - 本场景将被 `<speak>` 包裹（有停顿声明）→ 进 SSML 解析，非法标签让整个调用
      `400 Invalid SSML label` 失败（F 档实测）。
    - 走纯文本路径（无声明、机制关闭）→ 标签**不报错**，而是被当文字念出来：
      H 档实测 ASR 归一化流里 break／time／ms 三词俱在、match_ratio 仍 1.0，
      成片里就是一串英文乱码，而所有门禁照常通过。

    刻意不做转义（`&lt;`）：未经标定，不猜。判据只有一条——把该处写成文字
    （"不小于""低于"）或由预检点名后修正旁白。
    """
    bad = [ch for ch in ("<", ">") if ch in (text or "")]
    if not bad:
        return
    if will_wrap:
        cause = ("本场将被 `<speak>` 包裹，引擎会把它当 SSML 解析——"
                 "实测非法标签被服务端 `400 Invalid SSML label` 直接拒收，整个合成调用失败")
    else:
        cause = ("本场走纯文本路径，引擎不解析标签——实测 `<break time=\"300ms\"/>` "
                 "会被当文字念出来（ASR 流里 break／time／ms 三词俱在、match_ratio 仍 1.0），"
                 "成片里即一串英文乱码，且下游门禁读不出异常")
    raise PausePlanError(
        f"旁白含尖括号 {bad!r}：{cause}。请改成文字（如“不低于”“小于”）后重跑。")


def load_rules() -> dict:
    """读 `audio_sync_rules.json → pause_declaration`，形状阈值**引用** prosody 规则。

    fail-closed：文件缺失／不可解析／缺节／缺键／值不可解析／区间倒挂 六形各自点名断在
    哪条腿，不合并成一句"读不到"。刻意**不设内置默认值**（与同文件 `text_fit` 同档，
    而非码率/RMS 那两节的"回退内置默认＋告警"）——本节的数值是档位，有默认值即把
    "配置断了"读成"档位如此"。

    形状阈值（长段字数／顿号数）不住在本节：唯一持有者是
    `narration_prosody_rules.json → segment_overload`（复核面与生效面共用同一形状），
    抄一份即两套阈值，改了配置而另一处照常命中。
    """
    p = rules_path()
    if not p.exists():
        raise PausePlanError(f"停顿档位断链：规则文件缺失 {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise PausePlanError(f"停顿档位断链：规则文件不可解析 {p}（{e}）") from e
    sec = doc.get("pause_declaration") if isinstance(doc, dict) else None
    if not isinstance(sec, dict):
        raise PausePlanError("停顿档位断链：audio_sync_rules.json 缺 pause_declaration 节")
    out = {}
    for key in ("min_ms", "max_ms", "auto_ms", "max_count_per_scene",
                "max_total_ms_per_scene"):
        v = sec.get(key)
        if v is None:
            raise PausePlanError(f"停顿档位断链：pause_declaration 缺键 {key}")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise PausePlanError(f"停顿档位断链：{key} 值不可解析（读到 {v!r}）")
        out[key] = int(v)
    if "enabled_by_default" not in sec:
        raise PausePlanError("停顿档位断链：缺键 enabled_by_default")
    out["enabled_by_default"] = bool(sec["enabled_by_default"])
    eng = sec.get("engines")
    if not isinstance(eng, list) or not eng:
        raise PausePlanError(f"停顿档位断链：engines 缺失或为空（读到 {eng!r}）")
    out["engines"] = [str(e) for e in eng]
    marks = sec.get("mark_chars")
    if not isinstance(marks, str) or not marks:
        raise PausePlanError(f"停顿档位断链：mark_chars 缺失或为空（读到 {marks!r}）")
    out["mark_chars"] = marks

    # 形状阈值：引用 prosody 规则的 segment_overload，不抄值
    from _script_env import QUALITY_DIR
    pr = QUALITY_DIR / "narration_prosody_rules.json"
    if not pr.exists():
        raise PausePlanError(
            f"停顿档位断链：形状阈值的权威源缺失 {pr}"
            "（本节的 auto_insert 引用它的 segment_overload，不自带一份）")
    try:
        pro = json.loads(pr.read_text(encoding="utf-8"))
    except Exception as e:
        raise PausePlanError(f"停顿档位断链：narration_prosody_rules.json 不可解析（{e}）") from e
    shape = (pro.get("segment_overload") or {}) if isinstance(pro, dict) else {}
    for src_key, dst_key in (("min_chars", "min_segment_chars"),
                             ("min_enumeration_marks", "min_marks")):
        v = shape.get(src_key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise PausePlanError(
                f"停顿档位断链：narration_prosody_rules.segment_overload 缺 {src_key}"
                f"（读到 {v!r}）")
        out[dst_key] = int(v)
    if out["min_ms"] <= 0 or out["max_ms"] < out["min_ms"]:
        raise PausePlanError(
            f"停顿档位断链：区间非法 min_ms={out['min_ms']} max_ms={out['max_ms']}")
    if not (out["min_ms"] <= out["auto_ms"] <= out["max_ms"]):
        raise PausePlanError(
            f"停顿档位断链：auto_ms={out['auto_ms']} 落在 [{out['min_ms']}, {out['max_ms']}] 之外")
    return out


def declared_ms(entry) -> int:
    """把声明项的时长读成整数毫秒；形态不对即抛错，不猜。"""
    if not isinstance(entry, dict) or "after" not in entry:
        raise PausePlanError(f"停顿声明形态不对（需含 after 键）：{entry!r}")
    v = entry.get("ms")
    if v is None:
        raise PausePlanError(f"停顿声明缺 ms 键：{entry!r}")
    try:
        ms = int(round(float(v) * 1000)) if isinstance(v, float) and v <= 8 else int(v)
    except (TypeError, ValueError):
        raise PausePlanError(f"停顿声明的 ms 不可解析：{v!r}") from None
    return ms


def anchor_positions(text: str, after: str) -> list:
    """锚词在**发音字符流**里的插入位点（原文字符索引），与 cue 同一归一化口径。

    返回的是"锚词末字符之后"的原文索引；命中 0 次或多次的处理由调用方裁定
    （本函数只给事实，不做取舍——把裁定写在取数面会让 WARN/ERROR 两档无法分别复用）。
    """
    if not after:
        return []
    import _forced_align as _fa
    # 归一化真身＝`_forced_align._normalize_chars`——`cue_resolve.normalize` 转发的正是它，
    # 故本模块与词级 cue 在同一条字符流里定位；在这里重写一份即两套索引。
    norm, index_map = _fa._normalize_chars(text or "")
    needle, _ = _fa._normalize_chars(after)
    if not needle:
        return []
    hits, start = [], 0
    while True:
        j = norm.find(needle, start)
        if j < 0:
            return hits
        hits.append(index_map[j + len(needle) - 1] + 1)   # 锚词末字符的下一个原文位
        start = j + 1


def plan_scene(text: str, declarations, rules: dict, *, engine: str,
               enabled: bool, auto_decls=None) -> dict:
    """裁出一场的停顿计划。返回 {items, engine_text, notes, applied, skipped_reasons}。

    `items` 是按位点升序的 [{at, ms, origin}]，`origin` ∈ author|auto——同位点冲突时
    作者覆盖自动（`auto` 只在作者未在该位点声明时补位），这是"覆盖优先"的实现点，
    不得挪到调用方各判一次。
    """
    notes, skipped = [], []
    if engine not in rules["engines"]:
        if declarations:
            raise PausePlanError(
                f"引擎 {engine!r} 不在停顿声明的支持清单 {rules['engines']!r} 内，"
                f"但本场有 {len(declarations)} 条声明——禁止按另一套 SSML 语义猜测执行")
        return {"items": [], "engine_text": text, "notes": notes, "applied": 0,
                "skipped_reasons": skipped, "cache_tag": ""}

    resolved = []
    for entry in (declarations or []):
        ms = declared_ms(entry)
        if ms < rules["min_ms"] or ms > rules["max_ms"]:
            raise PausePlanError(
                f"停顿声明越界：{entry.get('after')!r} 要 {ms}ms，"
                f"合法区间 [{rules['min_ms']}, {rules['max_ms']}]。"
                "越界档位在本仓标定里不可复现（200ms 档 Δ 极差 0.80s；1200ms 档实测 1.5~2.0s），"
                "夹取会交出'作者以为买了 1.5s、实际拿到随机值'的音频，故按报错处理。")
        hits = anchor_positions(text, entry.get("after") or "")
        if not hits:
            raise PausePlanError(
                f"停顿声明的锚词在本场旁白里定位不到：{entry.get('after')!r}"
                "（归一化口径与词级 cue 同源）。这条停顿永远不会发生，不得静默去掉。")
        if len(hits) > 1:
            notes.append(f"WARN 锚词 {entry.get('after')!r} 命中 {len(hits)} 次，取首次")
        resolved.append({"at": hits[0], "ms": ms, "origin": "author"})

    if enabled:
        for entry in (auto_decls or []):
            hits = anchor_positions(text, entry["after"])
            if not hits:
                continue                      # 自动候选落不了地属正常（形状匹配非精确声明），不抛错
            if any(r["at"] == hits[0] for r in resolved):
                continue                      # 作者已在该位点声明 → 覆盖优先
            resolved.append({"at": hits[0], "ms": entry["ms"], "origin": "auto"})

    if not resolved:
        return {"items": [], "engine_text": text, "notes": notes, "applied": 0,
                "skipped_reasons": skipped, "cache_tag": ""}

    resolved.sort(key=lambda it: it["at"])
    authors = [it for it in resolved if it["origin"] == "author"]
    autos = [it for it in resolved if it["origin"] == "auto"]

    # 不对称判据：作者声明不得被机器静默截断（那等于"写了停顿而拿到的音频没有"，
    # 与 bgm_source/cue 那族"静默降级"同形）；自动补的条数则让位给上限。
    if len(authors) > rules["max_count_per_scene"]:
        raise PausePlanError(
            f"本场作者声明 {len(authors)} 处，超上限 {rules['max_count_per_scene']}——"
            "作者声明不被截断，请减到上限内或改分镜拆句")
    author_total = sum(it["ms"] for it in authors)
    if author_total > rules["max_total_ms_per_scene"]:
        raise PausePlanError(
            f"本场作者声明合计 {author_total}ms 超上限 {rules['max_total_ms_per_scene']}ms——"
            "SSML 模式下未声明处节奏也会移动（实测同场一枚未插 break 的逗号 0.84→0.26s），"
            "停顿预算是全场资源，逐点独立放行会让片长与节奏同时失控")
    resolved = list(authors)
    for it in autos:
        if len(resolved) >= rules["max_count_per_scene"]:
            notes.append(f"自动补位在上限 {rules['max_count_per_scene']} 处之外，让位不插")
            break
        if sum(x["ms"] for x in resolved) + it["ms"] > rules["max_total_ms_per_scene"]:
            notes.append(f"自动补位 {it['ms']}ms 会超全场合计上限 "
                         f"{rules['max_total_ms_per_scene']}ms，让位不插")
            continue
        resolved.append(it)
    if not resolved:
        # 走到这里＝作者无声明、自动候选全部让位（形状命中但超合计/条数上限）。
        # will_wrap 显式传 False：此刻引擎文本就是裸文本（无 `<speak>` 包裹），
        # 尖括号的后果是"被当文字念出"而非"整个调用 400"——归因措辞按实测形态给。
        # 首版漏传该必填 kwarg，本分支一走到即 TypeError（用例95 夹具：
        # max_total_ms_per_scene < auto_ms 时自动候选唯一让位路径）。
        check_angle_brackets(text, will_wrap=False)
        return {"items": [], "engine_text": text, "notes": notes, "applied": 0,
                "skipped_reasons": skipped, "cache_tag": ""}
    resolved.sort(key=lambda it: it["at"])

    return {"items": resolved, "engine_text": build_ssml(text, resolved), "notes": notes,
            "applied": len(resolved), "skipped_reasons": skipped,
            "cache_tag": plan_fingerprint(resolved)}


def auto_candidates(text: str, rules: dict) -> list:
    """按"长枚举挤在一段"的形状给出自动候选位点（不写第二套分段口径）。

    分段一律走生产真身 `_split_text_to_segments`；本函数只在**段内**找标记字符，
    段边界本身由波形层那 0.4s 负责，不在本机制内重复补。
    """
    from enhance_video_audio import _split_text_to_segments  # 延迟导入：反向依赖本模块
    marks = rules["mark_chars"]
    out, seen = [], set()
    for seg in _split_text_to_segments(text) or []:
        body = seg if isinstance(seg, str) else str(seg)
        if len(re.sub(r"\s", "", body)) < rules["min_segment_chars"]:
            continue
        if sum(body.count(c) for c in marks) < rules["min_marks"]:
            continue
        pos = 0
        for c in marks:
            while True:
                i = body.find(c, pos)
                if i < 0:
                    break
                pos = i + 1
                word = _preceding_word(body, i)
                if not word or word in seen:
                    continue
                seen.add(word)
                out.append({"after": word, "ms": rules["auto_ms"]})
    return out


def _preceding_word(body: str, mark_index: int) -> str:
    """取标记字符前那段非空文字作锚词（≤4 字，取尾部）。"""
    left = re.sub(r"\s", "", body[:mark_index])
    return left[-4:] if left else ""


def build_ssml(text: str, items) -> str:
    """把计划翻成引擎实际收到的文本（`<break>` 落在锚词之后、原标点之前）。

    尖括号在此 fail-loud：本仓实测 HTTP 非实时路径**默认解析 SSML**（非法标签直接
    `400 Invalid SSML label`），所以旁白里任何 `<` `>` 都不是"被忽略"而是"被当 markup"，
    最坏形态是整个调用失败。转义（`&lt;`）未经标定，不猜。
    """
    for ch in ("<", ">"):
        if ch in text:
            raise PausePlanError(
                f"旁白含尖括号 {ch!r}：CosyVoice 的 HTTP 非实时路径默认把标签当 SSML 解析"
                "（实测非法标签被 400 拒收），本模块不做未标定的转义。请把该处改成文字"
                "（如“不低于”“小于”），或在预检前修正旁白。")
    cut = sorted({it["at"] for it in items}, reverse=True)
    ms_at = {}
    for it in items:
        ms_at.setdefault(it["at"], 0)
        ms_at[it["at"]] += it["ms"]          # 同位点多条声明合并为一次停顿（求和）
    out = text
    for pos in cut:
        p = max(0, min(len(out), pos))
        out = out[:p] + f'<break time="{ms_at[pos]}ms"/>' + out[p:]
    return "<speak>" + out + "</speak>"


def plan_fingerprint(items) -> str:
    """把计划压成短标签，供 `_tts_cache_key` 参与缓存键。

    这一条是必需的而非加分项：缓存键原式只含文本/引擎/音色/语速/指令，
    **不含停顿**。改了声明而键不变＝命中旧缓存、静默交出没有停顿的音频，
    且全部下游门禁照常通过（A06"登记面＝真实读取路径"同族，2026-10-13 现查发现）。
    无声明时返回空串，调用方拼接后键值逐字不变 → 存量项目零重合成。
    """
    if not items:
        return ""
    canon = ",".join(f"{it['at']}:{it['ms']}:{it['origin'][0]}" for it in items)
    return "p" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:8]


if __name__ == "__main__":
    # 只读自查：给一段旁白 + 声明，打印计划与引擎实际文本（不发起任何 TTS 调用）
    import argparse
    ap = argparse.ArgumentParser(description="停顿声明只读预检")
    ap.add_argument("--text", required=True)
    ap.add_argument("--mark", action="append", default=[],
                    help="锚词=毫秒，如 集成规则=500（可重复）")
    ap.add_argument("--auto", action="store_true", help="同时打印按形状给出的自动候选")
    a = ap.parse_args()
    rs = load_rules()
    decls = []
    for m in a.mark:
        after, _, ms = m.partition("=")
        decls.append({"after": after, "ms": int(ms)})
    r = plan_scene(a.text, decls, rs, engine="qwen", enabled=a.auto,
                   auto_decls=auto_candidates(a.text, rs) if a.auto else None)
    for n in r["notes"]:
        print("  ", n)
    for it in r["items"]:
        print(f"  位点 {it['at']:>3}  {it['ms']:>4}ms  来源 {it['origin']}")
    print("引擎文本:", r["engine_text"])
    sys.exit(0)
