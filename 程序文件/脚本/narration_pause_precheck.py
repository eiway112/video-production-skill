#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旁白断词与停顿复核面（零渲染 / 零 TTS / 零 ASR；描述面，不裁定）。

消费面（2026-10-28 起）：`pipeline_runner.step_timeline` 末调用 `run_project_audit()`，
在 **render 之前**把两桶读数打印并落 `temp/_pause_audit.json`，结论写
`state.pause_audit` 并随完工报告 `data_sources.pause_audit` 透出。
只报不拦（不新增阻断门禁），且刻意**不登记进任何步骤指纹面**——改本文件不使存量项目重跑
（判据与取证见回归用例94 F 段）。此前它只是手动入口，于是"该停未停"每轮都由人耳在成片
上重新发现，每轮代价＝一次全量渲染（实测 1512.8s）。

两条不得误读的边界：
1. **本读数不是版本化留痕**。`temp/`、流水线日志与 `completion_report.json` 全在
   `.gitignore` 的 `过程产物/` 之下——交付后能回到 git 的只有两处：作者把两桶计数抄进
   交付说明「零渲染前置复核」表（模板 `templates_07` 已列该节），以及最终并入
   `narration.json` 的 `pause_marks` 声明本身。
2. **timeline 步缓存命中时本审计不重跑**（该步整体跳过）。这在语义上是对的：缓存命中
   ⇒ 旁白与波形未变 ⇒ 停顿读数与上一轮相同；此时 `state.pause_audit` 保留的是上一轮
   读数，其 `at` 字段即该读数的时刻，不得被读成"本轮量过"。

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
- pause_measurement：音频面**两桶**——连读桶（绝对阈 `audible_floor_seconds`）与
  急促桶（`relative_ratio` × 同场同类标点中位、且 < `relative_ceiling_seconds`）。
  两桶并列不合并的依据是 2026-10-28 的修前真值标定，读数与推理过程住该文件
  `pause_measurement._标定依据`，此处不复述。

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
    python narration_pause_precheck.py --project X --emit-declarations
        # 把急促桶候选转成可并入 narration.json 的 pause_marks 片段（只打印，不落盘）
        # 候选在此处已过 pause_marks 全部校验（锚点唯一、[min_ms,max_ms]、每场 ≤2 处、
        # 合计 ≤1600ms），超预算逐条点名拒绝——写盘动作留给作者，机器不替作者改朗读节奏。

`--manifest` 传了路径而文件不在位＝**取数断链**，会点名并计入汇总行"音频面未跑"，
不得由"低于可听阈 0"读成"停顿已核"（2026-10-28 实测病灶：相对路径写错一层时旧形态静默
跳过音频面，汇总行照常印 0，而同一份数据直调模块得到 31 条）。
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
    # 相对桶两键 fail-closed：缺键不得静默退化成"只有绝对桶"——那会让急促桶从读数里
    # 消失而汇总行照常打印，正是本模块 docstring 点名的"取数断链伪装成裁定"。
    for key in ("relative_ratio", "relative_ceiling_seconds"):
        try:
            val = float(rules["pause_measurement"][key])
        except (KeyError, TypeError, ValueError):
            raise Broken(f"pause_measurement.{key} 缺失或不可解析（相对桶无从产出，不得按 0 计）")
        if val <= 0:
            raise Broken(f"pause_measurement.{key} 非正数")
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
    """返回 (连读桶点名, 急促桶点名, 读数摘要, 逐场状态)。

    两桶**并列不合并**：连读＝绝对阈（该处几乎没停，听感是词被吞）；急促＝相对该场
    该类标点中位明显偏短（听感是用户口播点名的"这里该停一下"）。判据依据与标定读数
    住 narration_prosody_rules.json → pause_measurement._标定依据，此处不复述。
    """
    pm = rules["pause_measurement"]
    floor = float(pm["audible_floor_seconds"])
    ratio = float(pm["relative_ratio"])
    ceiling = float(pm["relative_ceiling_seconds"])
    by_scene = {str(sc.get("scene_id")): sc for sc in narration}
    rows: list = []          # (sid, punct, gap, ctx) —— 相对桶要按 cohort 二次过，先留底
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
            if not punct:
                continue
            ctx = "".join(spoken[max(0, i - 4):i + 1]) + punct + "".join(spoken[i + 1:i + 7])
            # 锚词＝该标点前**同一小句内**的尾 ≤4 个发音字。刻意不跨上一个标点取字：
            # 跨标点的锚（如「实测名称」实为「…现场实测。名称，」）虽仍能定到位（插入点在
            # 锚尾之后），但它把两句粘成一个串，歧义命中面变大、作者也难核对。
            start = i
            while start > 0 and not after[start].strip():
                start -= 1
            anchor = "".join(spoken[start:i + 1])[-4:]
            rows.append((sid, punct, g, ctx, anchor))
            if g < floor:
                silent_punct.append(
                    f"s{sid} 标点处实测 {g:.2f}s（<{floor:g}s 可听阈）『…{ctx}…』")
        sents = entry.get("sentences") or []
        for a, b in zip(sents, sents[1:]):
            seg_pause.append(float(b["rel_start"]) - float(a["rel_end"]))

    # 相对桶：cohort＝同场同标点类。样本 <2 的 cohort 无从取中位 → 不入桶，单独计数点名。
    cohort: dict = {}
    for sid, punct, g, _ctx, _anchor in rows:
        cohort.setdefault((sid, punct), []).append(g)
    rushed: list = []
    unmeasurable = 0
    for sid, punct, g, ctx, anchor in rows:
        vals = cohort[(sid, punct)]
        if len(vals) < 2:
            unmeasurable += 1
            continue
        med = statistics.median(vals)
        if med > 0 and g < ratio * med and g < ceiling:
            rushed.append({
                "scene_id": sid, "punct": punct, "gap": round(g, 3),
                "cohort_median": round(med, 3), "ratio": round(g / med, 3),
                "anchor": anchor, "ctx": ctx,
                "note": (f"s{sid} 标点处实测 {g:.2f}s（同场『{punct}』中位 {med:.2f}s 的 "
                         f"{g / med:.2f} 倍；判据＝<{ratio:g}×中位 且 <{ceiling:g}s）『…{ctx}…』"),
            })

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
        # 两桶计数与判据值一并落摘要：消费方（pipeline_runner／完工报告）不得另算一遍
        "punct_points_measured": len(rows),
        "absolute_floor_seconds": floor,
        "absolute_hits": len(silent_punct),
        "relative_ratio": ratio,
        "relative_ceiling_seconds": ceiling,
        "relative_hits": len(rushed),
        # 同场同类样本 <2 的位点＝**未被裁定**，不得由"急促桶 0 条"读成干净
        "relative_unmeasurable": unmeasurable if rows else None,
    }
    return silent_punct, rushed, summary, skipped


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


def run_project_audit(project: str) -> dict:
    """流水线与命令行共用的单一入口（取数路径解析不得各写一份）。

    返回 {project, absolute, rushed, summary, skipped}；取数断链一律抛 Broken，
    调用方（pipeline_runner）据此打印"无从审计"并点名断在哪条腿，不得读成 0 条。
    """
    narration_path, manifest_path = resolve_project(project)
    rules = load_rules()
    narration = load_narration(narration_path)
    if not manifest_path or not Path(manifest_path).exists():
        raise Broken(f"音频面取数断链：_timeline_manifest.json 不在位（该场尚未跑过 TTS 对齐）")
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception as e:
        raise Broken(f"音频面取数断链：_timeline_manifest.json 不可解析（{e}）")
    absolute, rushed, summary, skipped = audio_face(narration, manifest, rules)
    html = Path(narration_path).parent / "index.html"
    html_text = ""
    if html.exists():
        try:
            html_text = html.read_text(encoding="utf-8")
        except OSError:
            html_text = ""      # 取不到 HTML 只影响"画面联动"这一条提示，不影响两桶读数
    return {"project": project, "narration": narration, "absolute": absolute,
            "rushed": rushed, "summary": summary, "skipped": skipped, "html": html_text}


_CUE_OFFSET_RE = re.compile(r'C\[\s*"([^"]+)"\s*\]\s*\+\s*(0?\.\d+|\d+\.\d+)')
_CUE_CONCAT_RE = re.compile(r'C\[\s*"([A-Za-z0-9_\-]+):\s*"\s*\+')


def cue_offset_refs(html_text: str):
    """从 HTML 取「cue 词 + 固定偏移」的引用，按场归组。

    存在理由（2026-10-28 生产实证）：插入一处句内停顿会把该场后续所有 cue 的实测时刻
    整体推后，而 CUE-MAP 由 timeline 按新波形自动重算——**唯独作者手调的固定偏移不会**
    （本项目 S6 答复单字段值 `C["s6:结论固定"] + 0.30` 就得手工抬到 `+0.90` 才不与新停顿
    撞车）。只给停顿清单而不点名这类偏移，等于给出一份会让音画错位更严重的清单。
    拼接式引用（`C["s5:" + 段名]`）静态取不到词名，单独计数点名，不得读成"无联动"。
    """
    named: dict = {}
    concat: dict = {}
    for m in _CUE_OFFSET_RE.finditer(html_text or ""):
        sid, _, word = m.group(1).partition(":")
        try:
            off = float(m.group(2))
        except (TypeError, ValueError):
            continue
        if off > 0:
            named.setdefault(sid.strip().lower(), []).append((word.strip(), off))
    for m in _CUE_CONCAT_RE.finditer(html_text or ""):
        sid = m.group(1).strip().lower()
        concat[sid] = concat.get(sid, 0) + 1
    return named, concat


def emit_declarations(narration: list, rushed: list, html_text: str = "") -> list:
    """把急促桶候选转成可直接并入 narration.json 的 pause_marks 片段（只打印，不落盘）。

    刻意不做成自动写盘：写停顿＝改朗读节奏＋加长片长，裁定权在作者（与
    `pause_declaration.enabled_by_default=false` 同判据）。本函数把"作者逐条手抄"
    降级为"作者逐条确认"，并在此处跑一遍 pause_marks 的全部校验
    （锚点唯一、[min_ms,max_ms]、每场 ≤max_count_per_scene、合计 ≤max_total_ms_per_scene），
    超预算的场**点名拒绝**而非静默截断——静默截断等于"给了清单而清单不可执行"。
    """
    import pause_marks as pm
    rules = pm.load_rules()
    named, concat = cue_offset_refs(html_text)
    by_scene = {str(s.get("scene_id")): s for s in narration}
    grouped: dict = {}
    for r in rushed:
        grouped.setdefault(str(r["scene_id"]), []).append(r)
    out = []
    for sid, cands in sorted(grouped.items(), key=lambda kv: _scene_sort_key(kv[0])):
        sc = by_scene.get(sid)
        if sc is None:
            out.append(f"s{sid}: 旁白里查无该场，候选 {len(cands)} 条不产出")
            continue
        text = (sc.get("narration") or "").strip()
        existing = list(sc.get("pause_marks") or [])
        merged = [dict(e) for e in existing]
        accepted = []
        rejected = []
        declared = {p for e in merged
                    for p in pm.anchor_positions(text, str(e.get("after") or ""))}
        for c in sorted(cands, key=lambda x: x["gap"]):
            anchor = c["anchor"]
            hits = pm.anchor_positions(text, anchor)
            if len(hits) != 1:
                rejected.append(f"『{anchor}』锚点命中 {len(hits)} 次（须恰 1 次，不产出；"
                                f"加长锚词至唯一后可用）")
                continue
            # 去重判据＝**标点位点**，不是锚词字面重叠。锚词只取标点前 ≤4 字，与既有声明
            # 的锚词常互为子串而位点不同（2026-10-28 真实读数：候选『名称』与既有「只有名称」
            # 互为子串、分处两个句子，旧字面判据把"可声明"报成"已有声明"并当场丢弃候选）。
            pos = hits[0]
            if pos in declared:
                rejected.append(f"『{anchor}』该标点位已有声明（原文位点 {pos}）")
                continue
            ms = int(rules["auto_ms"])
            if len(merged) + 1 > int(rules["max_count_per_scene"]):
                rejected.append(f"『{anchor}』超每场上限 {rules['max_count_per_scene']} 处")
                continue
            if sum(int(e.get("ms") or 0) for e in merged) + ms > int(rules["max_total_ms_per_scene"]):
                rejected.append(f"『{anchor}』超全场合计 {rules['max_total_ms_per_scene']}ms")
                continue
            merged.append({"after": anchor, "ms": ms})
            declared.add(pos)
            accepted.append(anchor)
        out.append(f"s{sid}（现有 {len(existing)} 条 → 建议 {len(merged)} 条）："
                   + json.dumps(merged, ensure_ascii=False))
        for r in rejected:
            out.append(f"    拒绝：{r}")
        # 画面联动点名：只对**被接受**的候选报，且按锚词与 cue 词的字面包含关系匹配
        # （cue 词是旁白子串，锚词取的是标点前 ≤4 字，两者同源于同一归一化口径）。
        skey = ("s" + sid) if not sid.lower().startswith("s") else sid.lower()
        refs = named.get(skey, [])
        for anchor in accepted:
            for word, off in refs:
                if word and (word in anchor or anchor in word):
                    out.append(f"    画面联动：C[\"{skey}:{word}\"] 带手调固定偏移 +{off:g}s"
                               f"（按无停顿标定）——插入停顿后该偏移须复核，"
                               f"否则值会填在停顿之前、音画错位照旧")
        if accepted and concat.get(skey):
            out.append(f"    画面联动：该场另有 {concat[skey]} 处**拼接式** cue 引用"
                       f"（C[\"{skey}:\" + 变量]），静态取不到词名——须人工核对偏移是否让位")
    return out


def _scene_sort_key(sid: str):
    m = re.search(r"(\d+)", str(sid))
    return (0, int(m.group(1))) if m else (1, 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="旁白断词与停顿复核面（描述面，不裁定）")
    ap.add_argument("--narration", help="narration.json 路径")
    ap.add_argument("--manifest", help="可选：该项目 _timeline_manifest.json（音频面）")
    ap.add_argument("--project", help="项目名（自动定位 narration 与 manifest）")
    ap.add_argument("--all", action="store_true", help="全仓旁白普查（只裁文本面）")
    ap.add_argument("--emit-declarations", action="store_true",
                    help="把急促桶候选转成可并入 narration.json 的 pause_marks 片段（只打印，不落盘）")
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

    grand = {"merge_risk": 0, "segment_overload": 0, "silent_punct": 0,
             "rushed_punct": 0, "projects": 0, "audio_unaudited": 0}
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
        silent, rushed, summary, skipped = [], [], {}, []
        audio_ran = False
        if manifest_path is not None:
            # 传了路径却不在位＝取数断链，必须点名——2026-10-28 实测：相对路径写错一层
            # 时旧形态静默跳过音频面，汇总行照印"低于可听阈 0"，把"没量"读成"没问题"。
            if not Path(manifest_path).exists():
                print(f"[PROSODY] {name} 音频面取数断链：manifest 不在位 {manifest_path}"
                      f"（这不等于「没有急促位点」）")
                grand["audio_unaudited"] += 1
            else:
                try:
                    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
                    silent, rushed, summary, skipped = audio_face(narration, manifest, rules)
                    audio_ran = True
                    grand["silent_punct"] += len(silent)
                    grand["rushed_punct"] += len(rushed)
                except Exception as e:
                    print(f"[PROSODY] {name} 音频面取数失败（文本面照常）：{e}")
                    grand["audio_unaudited"] += 1
        else:
            grand["audio_unaudited"] += 1
        if not a.quiet or hits or silent or rushed:
            print(f"—— {name}（{counts['scenes']} 场）——")
            for h in hits:
                print(f"  · {h}")
            for s in silent:
                print(f"  · {s}")
            for r in rushed:
                print(f"  · {r['note']}")
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
                print(f"  标点位点 {summary['punct_points_measured']} 个"
                      f"｜连读桶（<{summary['absolute_floor_seconds']:g}s）{summary['absolute_hits']} 条"
                      f"｜急促桶（<{summary['relative_ratio']:g}×同场同类中位 且 "
                      f"<{summary['relative_ceiling_seconds']:g}s）{summary['relative_hits']} 条"
                      f"｜cohort 样本<2 未裁定 {summary['relative_unmeasurable'] if summary['relative_unmeasurable'] is not None else '无从计数'} 个")
            if skipped:
                print(f"  音频面未量（逐场点名）：{'、'.join(skipped)}")
            if not hits and not silent and not rushed:
                print("  文本面与音频面均无点名对象"
                      + ("（音频面未跑，此句不代表停顿已核）" if not audio_ran else ""))
        if a.emit_declarations and rushed:
            print(f"—— {name}：可并入 narration.json 的 pause_marks 建议（只打印，不落盘）——")
            html_file = path.parent / "index.html"
            html_text = ""
            if html_file.exists():
                try:
                    html_text = html_file.read_text(encoding="utf-8")
                except OSError as e:
                    print(f"  画面联动未核（HTML 读不回，两桶建议照常）：{e}")
            for line in emit_declarations(narration, rushed, html_text):
                print(f"  {line}")
    print(
        f"[PROSODY] 描述面（不裁定）：{grand['projects']} 个项目｜连读风险 {grand['merge_risk']}"
        f"｜枚举挤段 {grand['segment_overload']}"
        f"｜标点处实测低于可听阈 {grand['silent_punct']}"
        f"｜急促（相对同场同类中位）{grand['rushed_punct']}"
        + (f"｜音频面未跑 {grand['audio_unaudited']} 个（该计数>0 时上列两项读数不完整）"
           if grand["audio_unaudited"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
