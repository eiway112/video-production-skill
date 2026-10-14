#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旁白断词与停顿复核面（零渲染 / 零 TTS / 零 ASR；描述面，不裁定）。

存在理由
--------
2026-10-13 用户对多支成片给出同一条口播反馈："断句停顿的节奏（停顿和标点）把握不准"，
并举了两例（"一条带"被连读、"5 条管"该停未停）。复盘实测后根因分两类，且**都不是停顿阈值的问题**：

1. **作者在需要语义边界处漏了标点**。TTS 拿到的是带标点的整段文本，"红线至少一条带触发条件"
   里"一条"与"带"之间没有任何标点，引擎按词切分时把"条带"读成一个词。这一类机判得着：
   形状＝"数词 + 量词 + 可与量词构成别词的字"，且量词与该字之间无标点。
2. **段内标点不构成可靠控制面**。`audio_sync_rules.json → sentence_pause.description` 自己就写着：
   强制停顿只作用在**字幕段边界**（实测段间 0.40~0.44s），段内标点由 TTS 内部韵律决定，
   可低至 0。实测本项目标点类停顿中位：句号 0.40／分号 0.44／冒号 0.40／逗号 0.34／顿号 0.30／
   无标点 0.00。所以"一长段里塞五个枚举项"这种写法，加再多顿号也不会产生强制停顿——
   要停就得让它成为段边界（句号/分号且不被贪心合并），或者改写到不需要停顿也不歧义。

判据（全部住配置文件，脚本内零字面量）
--------------------------------------
`config/quality/narration_prosody_rules.json`
- merge_risk：量词 × 高危后缀字的封闭表，增字必须附真实口播样本；
- segment_overload：分段结果里"长段 + 多顿号"的形状（用生产分段函数取数，不写第二套切分口径）；
- pause_measurement：音频面读数下限与可听阈。

刻意**不做成门禁**
------------------
本面只打印读数与逐条点名，不产出 PASS/FAIL，不影响任何退出码裁定——与 2026-10-13 同日
`frame_occupancy.py` 的裁定同向（缺的不是判据是观测面；为观感类问题新增门禁只会多一次
"全绿但难看"的错觉）。退出码只有两种：0＝跑过并有读数；2＝取数断链（规则文件缺失/不可解析、
分段函数取不回、旁白文件读不到），断链一律点名断在哪条腿，不得由"没报问题"读成"没有问题"。

音频面是**可选**面：项目 temp 里没有 `_timeline_manifest.json`、或该场 method≠asr_forced、
或该场原文去标点后字符数与对齐字符数不等（含拉丁词/数字展开时常见）时，逐场点名跳过，
绝不按 0 计、也不得因"跳过"把整场读成干净。

用法
----
    python narration_pause_precheck.py --project repo-mesh-governance-promo
    python narration_pause_precheck.py --narration <narration.json> [--manifest <_timeline_manifest.json>]
    python narration_pause_precheck.py --all            # 全仓旁白普查（只裁文本面）
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_env import ROOT, QUALITY_DIR, HTML_BASE, TEMP_BASE  # noqa: E402

RULES_FILE = QUALITY_DIR / "narration_prosody_rules.json"
SYNC_RULES_FILE = QUALITY_DIR / "audio_sync_rules.json"

# 度量时不计入"发音字符"的字符（标点与空白）。数字与拉丁字母算发音字符，
# 与对齐字符数不等时该场音频面会被点名跳过。
NON_SPOKEN = set("，。、；：！？…—－–~～（）()〔〕《》〈〉“”‘’\"' \n\t\r　")


class Broken(Exception):
    """取数断链：本面无从产出读数，必须点名而不是沉默。"""


def load_rules() -> dict:
    if not RULES_FILE.exists():
        raise Broken(f"规则文件缺失：{RULES_FILE}")
    try:
        rules = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # 不可解析＝断链，不得按空规则继续裁
        raise Broken(f"规则文件不可解析：{RULES_FILE} ({e})")
    for section in ("merge_risk", "segment_overload", "pause_measurement"):
        if not isinstance(rules.get(section), dict):
            raise Broken(f"规则缺节或形态不对：{section}")
    mr = rules["merge_risk"]
    for key in ("quantifiers", "suffix_chars", "min_count_before"):
        vals = mr.get(key)
        if not isinstance(vals, list) or not vals:
            raise Broken(f"merge_risk.{key} 缺失或为空（空表＝该腿永不成立）")
    so = rules["segment_overload"]
    for key in ("min_chars", "min_enumeration_marks"):
        try:
            if int(so[key]) <= 0:
                raise Broken(f"segment_overload.{key} 非正数")
        except (KeyError, TypeError, ValueError):
            raise Broken(f"segment_overload.{key} 缺失或不可解析")
    try:
        if float(rules["pause_measurement"]["audible_floor_seconds"]) <= 0:
            raise Broken("pause_measurement.audible_floor_seconds 非正数")
    except (KeyError, TypeError, ValueError):
        raise Broken("pause_measurement.audible_floor_seconds 缺失或不可解析")
    return rules


def production_splitter():
    """取生产分段函数，不做第二套切分口径。"""
    try:
        from enhance_video_audio import _split_text_to_segments  # 延迟导入，理由见函数注释
    except Exception as e:
        raise Broken(f"生产分段函数取不回（enhance_video_audio._split_text_to_segments）：{e}")
    if not callable(_split_text_to_segments):
        raise Broken("生产分段函数不可调用")
    return _split_text_to_segments


def merge_risk_pattern(rules: dict) -> re.Pattern:
    mr = rules["merge_risk"]
    counts = "".join(re.escape(str(c)) for c in mr["min_count_before"])
    quant = "".join(re.escape(str(c)) for c in mr["quantifiers"])
    suffix = "".join(re.escape(str(c)) for c in mr["suffix_chars"])
    return re.compile(rf"[{counts}][{counts}\s]*([{quant}])([{suffix}])")


def text_face(narration: list, rules: dict, split) -> tuple[list, dict]:
    """返回 (点名条目, 计数)。计数住判据自身，不由调用侧另算。"""
    hits: list = []
    counts = {"merge_risk": 0, "segment_overload": 0, "scenes": 0}
    so = rules["segment_overload"]
    min_chars = int(so["min_chars"])
    min_marks = int(so["min_enumeration_marks"])
    pat = merge_risk_pattern(rules)
    for sc in narration:
        sid = sc.get("scene_id")
        text = (sc.get("narration") or "").strip()
        if not text:
            continue
        counts["scenes"] += 1
        for m in pat.finditer(text):
            q, suf = m.group(1), m.group(2)
            a, b = m.span()
            hits.append(
                f"s{sid} 连读风险：『{text[a:b]}』（量词 {q} 与 {suf} 之间无标点）"
                f"…… 修法＝加逗号断开，或把该短语移出枚举成整句")
            counts["merge_risk"] += 1
        for seg in split(text):
            marks = seg.count("、")
            if len(seg) >= min_chars and marks >= min_marks:
                hits.append(
                    f"s{sid} 枚举挤在一段：{len(seg)} 字 / {marks} 个顿号"
                    f"『{seg[:26]}…』…… 段内不插强制停顿；修法＝拆句或减少每项内容"
                    f"（拆句只保证句末自然停顿，强制 0.40s 需该缝真的成为字幕段边界）")
                counts["segment_overload"] += 1
    return hits, counts


def spoken_and_after(text: str):
    spoken, after, buf = [], [], ""
    for ch in text:
        if ch in NON_SPOKEN:
            buf += ch
        else:
            spoken.append(ch)
            after.append(buf)
            buf = ""
    after.append(buf)  # 句尾残留标点
    return spoken, after


def audio_face(narration: list, manifest: dict, rules: dict):
    """返回 (点名条目, 读数摘要, 逐场状态)。"""
    floor = float(rules["pause_measurement"]["audible_floor_seconds"])
    by_scene = {str(sc.get("scene_id")): sc for sc in narration}
    gaps_by_punct: dict = {}
    silent_punct: list = []
    seg_pause = []
    skipped = []
    for entry in manifest.get("scenes", []):
        sid = str(entry.get("scene_id"))
        if entry.get("method") != "asr_forced" or not entry.get("chars"):
            skipped.append(f"s{sid}(未对齐/无字符流)")
            continue
        sc = by_scene.get(sid)
        if sc is None:
            skipped.append(f"s{sid}(旁白里查无该场)")
            continue
        chars = entry["chars"]
        if len(chars) != len(entry.get("norm") or ""):
            skipped.append(f"s{sid}(chars 与 norm 长度不等)")
            continue
        spoken, after = spoken_and_after((sc.get("narration") or "").strip())
        if len(spoken) != len(chars):
            skipped.append(f"s{sid}(原文 {len(spoken)} 字 ≠ 对齐 {len(chars)} 字，疑含拉丁/数字展开)")
            continue
        for i in range(len(chars) - 1):
            g = float(chars[i + 1][0]) - float(chars[i][1])
            # 该静音住在 spoken[i] 与 spoken[i+1] 之间，归属给"紧跟 i+1 之前的那串标点"
            # （after[k] 存的是 spoken[k] **之前**的标点串——归属错一位会把所有停顿读成 0.00，
            #  本函数首版即栽在此处，实测中位全部 0.00 才暴露）
            # 纯空白（数字与量词间的 "171 个"）不构成停顿请求，剥掉后按"无标点"归类，
            # 否则全仓普查会被数十条空格噪声淹没（首版实测 22 条点名里 14 条是空格）。
            punct = after[i + 1].strip()
            key = punct if punct else "无标点"
            gaps_by_punct.setdefault(key, []).append(g)
            if punct and g < floor:
                ctx = "".join(spoken[max(0, i - 4):i + 1]) + punct + "".join(spoken[i + 1:i + 7])
                silent_punct.append(
                    f"s{sid} 标点处实测 {g:.2f}s（<{floor:g}s 可听阈）『…{ctx}…』")
        sents = entry.get("sentences") or []
        for a, b in zip(sents, sents[1:]):
            seg_pause.append(float(b["rel_start"]) - float(a["rel_end"]))
    floor_enforced = enforced_floor()
    # 与 ASR 时间戳粒度（0.02s）同档的容差：强制停顿恰好落在 0.40 时，浮点减法会给出
    # 0.39999999997，严格小于即把"恰好达标"数成"低于下限"（本函数首版实测 4 条，真值 1 条）。
    eps = 0.01
    below = (sum(1 for g in seg_pause if g < floor_enforced - eps)
             if seg_pause and floor_enforced > 0 else None)
    summary = {
        "punct_median": {k: round(statistics.median(v), 3) for k, v in sorted(gaps_by_punct.items())},
        "punct_min": {k: round(min(v), 3) for k, v in sorted(gaps_by_punct.items())},
        "segment_pause_median": round(statistics.median(seg_pause), 3) if seg_pause else None,
        # None＝无从计数（无段间读数，或强制下限值本身取不回）；不得由 None 读成 0
        "segment_pause_below_enforced": below,
        "enforced_floor_seconds": floor_enforced if floor_enforced > 0 else None,
        "measured_scenes": len(manifest.get("scenes", [])) - len(skipped),
    }
    return silent_punct, summary, skipped


def enforced_floor() -> float:
    """段间强制停顿的权威值（audio_sync_rules → sentence_pause.min_seconds）；读不回返回哨兵。"""
    try:
        d = json.loads(SYNC_RULES_FILE.read_text(encoding="utf-8"))
        return float(d["sentence_pause"]["min_seconds"])
    except Exception:
        return -1.0


def load_narration(path: Path) -> list:
    if not path.exists():
        raise Broken(f"旁白文件读不到：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise Broken(f"旁白文件不可解析：{path} ({e})")
    scenes = data.get("scenes") if isinstance(data, dict) else data
    if not isinstance(scenes, list) or not scenes:
        raise Broken(f"旁白文件里没有 scenes 列表：{path}")
    return scenes


def resolve_project(name: str):
    base = HTML_BASE / name
    narration = base / "narration.json"
    temp = None
    for cand in (TEMP_BASE / f"{name}_audio" / "_timeline_manifest.json",
                 base / "temp" / "_timeline_manifest.json"):
        if cand.exists():
            temp = cand
            break
    return narration, temp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="旁白断词与停顿复核面（描述面，不裁定）")
    ap.add_argument("--narration", help="narration.json 路径")
    ap.add_argument("--manifest", help="可选：该项目 _timeline_manifest.json（音频面）")
    ap.add_argument("--project", help="项目名（自动定位 narration 与 manifest）")
    ap.add_argument("--all", action="store_true", help="全仓旁白普查（只裁文本面）")
    ap.add_argument("--quiet", action="store_true", help="只印汇总行")
    a = ap.parse_args(argv)
    try:
        rules = load_rules()
        split = production_splitter()
    except Broken as e:
        print(f"[PROSODY] 无从裁定：{e}")
        return 2

    targets = []
    if a.all:
        for p in sorted(HTML_BASE.glob("*/narration.json")):
            targets.append((p, resolve_project(p.parent.name)[1]))
    elif a.project:
        n, m = resolve_project(a.project)
        targets.append((n, Path(a.manifest) if a.manifest else m))
    elif a.narration:
        targets.append((Path(a.narration), Path(a.manifest) if a.manifest else None))
    else:
        print("[PROSODY] 用法见 --help（--project / --narration / --all）")
        return 2

    grand = {"merge_risk": 0, "segment_overload": 0, "silent_punct": 0, "projects": 0}
    for path, manifest_path in targets:
        name = path.parent.name
        try:
            narration = load_narration(path)
        except Broken as e:
            print(f"[PROSODY] {name} 取数断链：{e}")
            continue
        grand["projects"] += 1
        hits, counts = text_face(narration, rules, split)
        grand["merge_risk"] += counts["merge_risk"]
        grand["segment_overload"] += counts["segment_overload"]
        silent, summary, skipped = ([], {}, [])
        if manifest_path and manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                silent, summary, skipped = audio_face(narration, manifest, rules)
                grand["silent_punct"] += len(silent)
            except Exception as e:
                print(f"[PROSODY] {name} 音频面取数失败（文本面照常）：{e}")
        if not a.quiet or hits or silent:
            print(f"—— {name}（{counts['scenes']} 场）——")
            for h in hits:
                print(f"  · {h}")
            for s in silent:
                print(f"  · {s}")
            if summary.get("punct_median"):
                med = "  ".join(f"{k}:{v:.2f}" for k, v in summary["punct_median"].items())
                lowmin = "  ".join(f"{k}:{v:.2f}" for k, v in summary["punct_min"].items())
                below = summary["segment_pause_below_enforced"]
                print(f"  停顿中位(s)：{med}")
                print(f"  停顿最低值(s)：{lowmin}")
                print(f"  段间实测中位 {summary['segment_pause_median']}s"
                      f"｜强制下限 {summary['enforced_floor_seconds'] if summary['enforced_floor_seconds'] is not None else '取不回'}"
                      f"｜低于强制下限的段间数 {below if below is not None else '无从计数'}"
                      f"｜实测场数 {summary['measured_scenes']}")
            if skipped:
                print(f"  音频面未量（逐场点名）：{'、'.join(skipped)}")
            if not hits and not silent:
                print("  文本面与音频面均无点名对象")
    print(
        f"[PROSODY] 描述面（不裁定）：{grand['projects']} 个项目｜连读风险 {grand['merge_risk']}"
        f"｜枚举挤段 {grand['segment_overload']}｜标点处实测低于可听阈 {grand['silent_punct']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
