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
- pause_measurement：音频面**三桶**——连读桶（绝对阈 `audible_floor_seconds`）、
  急促桶（`relative_ratio` × 同场同类标点中位、且 < `relative_ceiling_seconds`）与
  **第三桶**（2026-11-05 用户口播反馈批：无标点处**出现了**可听停顿，前两桶的镜像形态）。
  三桶并列不合并的依据是 2026-10-28 的修前真值标定，读数与推理过程住该文件
  `pause_measurement._标定依据`，此处不复述。第三桶的阈值权威源**不在此文件**，
  住 `audio_sync_rules.json → intra_pause_trim`，判据实现住 `intra_pause.py`
  （修剪面与点名面共用同一入口，两处各算即两份真相）。

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

# 度量时不计入"发音字符"的字符（标点与空白）。数字与拉丁字母算发音字符。
NON_SPOKEN = set("，。、；：！？…—－–~～（）()〔〕《》〈〉“”‘’\"' \n\t\r　")

# 发音承载字符＝强制对齐侧的同一口径（`_forced_align._normalize_chars` 从这里取，
# 单源）：汉字/拉丁字母/数字。小数点、百分号、中点这类符号既不是发音字符，也不是
# 停顿请求——把它们算成发音字符会让发音流比对齐流长，整场被判"取数断链"跳过；
# 反过来把它们算成停顿标点，又会把刀插进 "2.03" / "8.4%" 里面。两半都不成立。
SPOKEN_KEEP_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


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
    # 大气口推荐值 fail-closed：缺键不得回退 auto_ms——那正是本次收口掉的第二真相源
    # （同一位点在语义层级行与声明建议里报两个不同的 ms）。
    try:
        beat_ms = int(rules["pause_measurement"]["beat_ms"])
    except (KeyError, TypeError, ValueError):
        raise Broken("pause_measurement.beat_ms 缺失或不可解析（大气口候选无从给声明值）")
    if beat_ms <= 0:
        raise Broken("pause_measurement.beat_ms 非正数")
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
    """返回 (spoken, after)：spoken 只含发音承载字符（`SPOKEN_KEEP_RE`，与强制对齐同源），
    after[i] 是 spoken[i] **之前**的那串停顿标点。

    非发音、非停顿的符号（`.` `%` `·` `×` 等）两侧都不计：计入 spoken 会与对齐字符流
    不等长而整场跳过（数字密集项目实测 7/10 场）；计入停顿请求则会把静音插进
    "2.03""8.4%" 里面——两种读法都会把该补的停顿放掉或把刀落进数字里。
    """
    spoken, after, buf = [], [], ""
    for ch in text:
        if SPOKEN_KEEP_RE.match(ch):
            spoken.append(ch)
            after.append(buf)
            buf = ""
        elif ch in NON_SPOKEN:
            buf += ch
    after.append(buf)  # 句尾残留标点
    return spoken, after


def punctuation_gap_rows(chars, spoken, after):
    """相邻发音字符之间的静音位点——三桶点名与波形强制面**共用的唯一取数入口**。

    存在理由（2026-11-06 机制批）：句内标点的停顿强制此前不在生效路径上，而它要判的
    "这个位点低于下限"与点名侧判的"这个位点低于可听阈"是同一件事。两处各算一遍即两份
    真相（与 `intra_pause.classify_points` 那条同族：修剪侧与复核第三桶必须共用入口），
    会出现"报告里绿、波形里没补"或反之。取数因此只写这一份，两侧都消费它。

    归属判据：静音住在 `spoken[i]` 与 `spoken[i+1]` 之间，归给紧跟 i+1 之前的那串标点
    （`after[k]` 存的是 `spoken[k]` **之前**的标点串——归属错一位会把所有停顿读成 0.00，
    audio_face 首版即栽在此处，实测中位全部 0.00 才暴露）。纯空白（数字与量词间的
    "171 个"）不构成停顿请求，剥掉后按"无标点"归类。

    `measured` ＝ 两侧字符的 ASR matched 旗标均为 1。旗标为假时 gap 是插值造出的时间：
    **点名侧照常入桶**（它报的是"这里读起来赶"，插值时刻不影响该结论的方向），
    **执行侧必须按 measured 拒绝动刀**（在虚构时刻插静音等于把刀落进字里，与
    `_enforce_sentence_pauses` 的"下刀点须落字间隙"同判据）。

    `anchor` ＝ 该标点前**同一小句内**的尾 ≤4 个发音字。刻意不跨上一个标点取字：跨标点的
    锚虽仍能定到位，但它把两句粘成一个串，歧义命中面变大、作者也难核对。
    """
    rows = []
    for i in range(len(chars) - 1):
        gap = float(chars[i + 1][0]) - float(chars[i][1])
        punct = after[i + 1].strip()
        start = i
        while start > 0 and not after[start].strip():
            start -= 1
        rows.append({
            "index": i + 1,
            "punct": punct,
            "gap": gap,
            "ctx": "".join(spoken[max(0, i - 4):i + 1]) + punct + "".join(spoken[i + 1:i + 7]),
            "anchor": "".join(spoken[start:i + 1])[-4:],
            "measured": bool(int(chars[i][2] or 0)) and bool(int(chars[i + 1][2] or 0)),
        })
    return rows


_PAUSE_CLASS_RANGES = {
    # 档位不是设计出来的，是 6 支项目 435 个标点位点的实测分布 + 语义层级定的：
    #   引擎中位：句末 0.40／分号 0.42／冒号 0.42／逗号 0.32／顿号 0.30，
    #   但重叠很大（顿号 p90 0.42 > 句末 p10 0.21），所以档位用于**判断该不该有气口**，
    #   不用于「把某处调到某个值」——实测同一位置换 comma/period/colon/dash 只差 0.05s。
    "end":       (0.30, 0.70, "句末"),
    "semicolon": (0.28, 0.65, "分号"),
    "colon":     (0.22, 0.55, "冒号"),
    "clause":    (0.20, 0.45, "分句逗号"),
    "minor":     (0.08, 0.32, "轻逗号"),
    "enum":      (0.06, 0.35, "顿号"),
    "beat":      (0.30, 0.90, "破折/省略"),
}

# 分句逗号低于它＝语义上是两个词组单元、引擎却几乎没停 → 大气口候选（SSML 声明）。
_BEAT_MIN_CLAUSE_GAP = 0.15


def _classify_boundary(punct: str, prev_len: int, next_len: int) -> str:
    """按标点等级 + 两侧词组长度给边界分档（语义档，不是字符类）。

    逗号再分两档：两侧发音字合计 >=8 视为两个词组单元（分句逗号），否则是修饰语粘连
    （轻逗号）。这条分界被语料逼出来——「翻图集，要两天」两侧合计 10 字，
    「现在，10 分钟」只有 6 字；前者需要气口，后者不需要。
    """
    p = punct or ""
    if "。" in p or "？" in p or "！" in p or "." in p or "?" in p or "!" in p:
        return "end"
    if "；" in p or ";" in p:
        return "semicolon"
    if "：" in p or ":" in p:
        return "colon"
    if "—" in p or "…" in p:
        return "beat"
    if "、" in p:
        return "enum"
    if "，" in p or "," in p:
        return "clause" if (prev_len + next_len) >= 8 else "minor"
    return "minor"


def _build_prosody_summary(rows, beat_ms):
    """从精停构成行里再派生一层语义读数（只透出、不进四态裁定）。

    分档用的量是 `effective_gap`，它的取法记载了 2026-11-07 的一次判据更正：

    - **旧口径**用「ASR 间隙 − 精确零」当"引擎原声"，理由是补出来的零不算引擎的
      韵律。该理由在**引入 SSML 之前**成立——那时精确零只有流水线一个生产者。
    - **引入 `pause_marks` 之后**，引擎的 `<break>` 在原始合成件里就是精确零
      （实测 0.471／0.750／0.458s），与补零字节同形。旧口径于是把引擎的停顿减到
      0.11s 量级，把已拿到约 1.0s 停顿的位点重新列成"大气口候选"。
    - **现口径**：波形分不出生产者，故生产者由**记录**回答——`declared`（本场
      `pause_marks` 声明）为真时，那段零是引擎按作者意图生成的，交付间隙即生效量；
      无声明时扣掉精确零（此时它只可能来自流水线补零或引擎的无意短零）。

    这条更正的边界：无声明位点的"精确零＝补零"仍是**推断**（引擎也有短零段），故
    它只影响描述面的分档，不进任何裁定；真正的账本读数由
    `pause_composition.pipeline_booked_*` 单独给出。

    `beat_ms` 由调用方从 rules 传入（唯一持有者 narration_prosody_rules.json →
    pause_measurement.beat_ms），候选行携带它一路到 `emit_declarations`；
    两处各自取值就会同一位点报两个 ms。
    """
    import statistics

    by_cls = {}
    for r in rows:
        by_cls.setdefault(r["cls"], []).append(r["effective_gap"])
    classes = {}
    for c, v in sorted(by_cls.items()):
        v = sorted(v)
        classes[c] = {
            "label": _PAUSE_CLASS_RANGES.get(c, (0, 0, c))[2],
            "n": len(v),
            "p10": round(v[max(0, int((len(v) - 1) * 0.1))], 3),
            "median": round(statistics.median(v), 3),
            "p90": round(v[max(0, int((len(v) - 1) * 0.9))], 3),
            "range": list(_PAUSE_CLASS_RANGES.get(c, (None, None, c))[:2]),
        }
    short = [r for r in rows
             if r["effective_gap"] < _PAUSE_CLASS_RANGES.get(r["cls"], (0, 99))[0]]
    long_ = [r for r in rows
             if r["effective_gap"] > _PAUSE_CLASS_RANGES.get(r["cls"], (0, 99))[1]]
    by_scene = {}
    for r in rows:
        by_scene.setdefault(str(r["scene_id"]), []).append(r)
    viol = []
    for sid, rs in by_scene.items():
        med = {}
        for c in ("end", "clause", "enum"):
            v = [x["effective_gap"] for x in rs if x["cls"] == c]
            if v:
                med[c] = statistics.median(v)
        if len(med) == 3 and not (med["end"] > med["clause"] >= med["enum"] - 0.03):
            viol.append({"scene_id": sid,
                         "end": round(med["end"], 3),
                         "clause": round(med["clause"], 3),
                         "enum": round(med["enum"], 3)})
    beats = [
        {"scene_id": r["scene_id"], "ctx": r["ctx"], "anchor": r["anchor"],
         "gap": r["effective_gap"], "ms": beat_ms,
         "why": "分句逗号两侧合计 >=8 字、生效间隙 <0.15s（语义要气口而这里没停够）"}
        for r in rows if r["cls"] == "clause" and r["effective_gap"] < _BEAT_MIN_CLAUSE_GAP
    ]
    enum_slow = [
        {"scene_id": r["scene_id"], "ctx": r["ctx"], "gap": r["effective_gap"]}
        for r in rows if r["cls"] == "enum"
        and r["effective_gap"] > _PAUSE_CLASS_RANGES["enum"][1]
    ]
    parts = []
    for c in ("end", "clause", "enum"):
        if c in classes:
            parts.append(f"{classes[c]['label']} {classes[c]['median']:.2f}s")
    line = ("／".join(parts) + "（生效间隙中位）" if parts else "无可用档位读数")
    line += (f"｜低于档位 {len(short)} 处｜高于档位 {len(long_)} 处"
             f"｜层级倒挂 {len(viol)} 场｜大气口候选 {len(beats)} 处")
    return {
        "classes": classes,
        "below_range": [{"scene_id": r["scene_id"], "cls": r["cls"], "gap": r["effective_gap"],
                         "ctx": r["ctx"]} for r in short],
        "above_range": [{"scene_id": r["scene_id"], "cls": r["cls"], "gap": r["effective_gap"],
                         "ctx": r["ctx"]} for r in long_],
        "hierarchy_violations": viol,
        "beat_candidates": beats,
        "enum_slow": enum_slow,
        "summary_line": line,
    }


_SHORT_PAUSE_GAP_SECONDS = 0.10   # 描述面切点：生效间隙低于它＝基本没停


def _zero_silence_runs(audio, sr, min_seconds=0.02):
    """样本**精确为 0**的连续段（秒区间）。

    **本函数只量"精确零静音"，不裁定它的生产者**——这条边界是 2026-11-07 由实测
    逼出来的：引入 `pause_marks` 之后，引擎的 SSML `<break>` 在原始合成件里就已是
    样本精确为 0 的连续段（本项目实测 0.471／0.750／0.458s），与流水线
    `_splice_silence` 补出来的数字零**字节同形**。旧文档写"整段 0 只可能来自数字零
    插入"，那句话在 SSML 之前成立，现在不成立；据它把整段归给流水线，会把引擎的
    停顿记到流水线头上（本项目完工报告印 2.19s，而账本实为 0.19s）。

    生产者归因改由**记录**回答（`_declared_punct_indices` 读 `pause_marks` 声明；
    账本读 manifest 的 `pause_intra_inserted`），不再由波形猜。

    判据依据（仍然成立的部分）：自然语音的衰减、呼吸与房间底噪不会落到 s16 的绝对零，
    故"精确零"确实是人工插入的痕迹，只是插入者有两位。**描述面用，不参与任何裁定。**
    """
    import numpy as np
    hop = max(1, int(round(0.01 * sr)))
    n = len(audio) // hop
    if n <= 0:
        return []
    rms = np.sqrt((audio[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    runs, st = [], None
    for i, v in enumerate(rms):
        z = v == 0.0
        if z and st is None:
            st = i
        elif not z and st is not None:
            if (i - st) * hop / sr >= min_seconds:
                runs.append((st * hop / sr, i * hop / sr))
            st = None
    if st is not None and (n - st) * hop / sr >= min_seconds:
        runs.append((st * hop / sr, n * hop / sr))
    return runs


def _declared_punct_indices(scene):
    """该场 `pause_marks` 声明落在哪些标点位点上（发音字符流索引集合）。

    这是本面**唯一的生产者事实源**：波形分不出引擎 break 与流水线补零，而
    "作者在这里声明过停顿"是一条记录，不是推断。定位口径复用
    `pause_marks.anchor_positions`（与 cue、与合成侧同一条归一化字符流），
    不另写一份索引。

    返回 `(索引集合, 断链点名)`。断链时按"无声明"继续，但必须点名——
    把"解析不到"当成"没声明"，就会把引擎的停顿重新记成流水线补零，
    正是本次要修的那个形态。
    """
    marks = scene.get("pause_marks") or []
    text = (scene.get("narration") or "").strip()
    if not marks or not text:
        return set(), []
    try:
        import pause_marks as _pm
    except Exception as e:  # 取数断链不得静默降级成"没声明"
        return set(), [f"pause_marks 模块取不回（{e}）"]
    out, broken = set(), []
    for m in marks:
        anchor = str((m or {}).get("after") or "")
        try:
            hits = _pm.anchor_positions(text, anchor)
        except Exception as e:
            broken.append(f"锚词 {anchor!r} 定位失败（{e}）")
            continue
        if len(hits) != 1:
            broken.append(f"锚词 {anchor!r} 命中 {len(hits)} 次（须恰 1 次）")
            continue
        # 锚词末字符的下一原文位 → 其前有多少个发音字符 ⇒ 该标点位点的流索引
        out.add(sum(1 for ch in text[:hits[0]] if ch not in NON_SPOKEN))
    return out, broken


def audio_face(narration: list, manifest: dict, rules: dict, wav_dir=None):
    """返回 (连读桶点名, 急促桶点名, 读数摘要, 逐场状态, 连读桶结构化行)。

    第五元 `absolute_rows`（2026-11-06 机制批）与第一元的字符串点名同源，供
    `emit_declarations` 把**绝对阈命中**也转成候选声明——旧形态只把急促桶转候选，
    于是"0.10s 的顿号"被机器点名后没有任何可执行出口，正是"机器看见了、没人接手"
    的第三处断链。点名侧与候选侧因此仍走同一份取数（`punctuation_gap_rows`）。

    两桶**并列不合并**：连读＝绝对阈（该处几乎没停，听感是词被吞）；急促＝相对该场
    该类标点中位明显偏短（听感是用户口播点名的"这里该停一下"）。判据依据与标定读数
    住 narration_prosody_rules.json → pause_measurement._标定依据，此处不复述。

    2026-11-05 增第三桶（`summary["intra"]`）：无标点处**出现了**可听停顿——它是前两桶的
    镜像（前两桶量"标了标点却没停"，本桶量"没标标点却停了"），既有两桶的取样条件
    `if not punct: continue` 结构上抓不到它。判据、阈值与波形修剪面**同源**
    （`intra_pause.classify_points` + `audio_sync_rules.json → intra_pause_trim`）：
    点名侧与修剪侧各算一遍即两份真相，会出现"报告里绿、成片里响"。
    `wav_dir` 给出时（= 该项目 temp/tts_44k）能量腿照常跑；不给则位点以
    `energy=None` 列出并计入 `unconfirmed`，**不得**并进 `hits`。
    """
    pm = rules["pause_measurement"]
    floor = float(pm["audible_floor_seconds"])
    ratio = float(pm["relative_ratio"])
    ceiling = float(pm["relative_ceiling_seconds"])
    by_scene = {str(sc.get("scene_id")): sc for sc in narration}
    rows: list = []          # (sid, punct, gap, ctx) —— 相对桶要按 cohort 二次过，先留底
    gaps_by_punct: dict = {}
    silent_punct: list = []
    silent_rows: list = []
    seg_pause = []
    skipped = []
    intra_rows: list = []
    intra_unmeasurable = 0
    intra_no_energy = 0
    intra_broken: list = []
    comp_rows: list = []       # 标点位点的精停构成（交付间隙／精确零／带噪／声明／账本）
    comp_unmeasured: list = []
    comp_decl_broken: list = []
    # 执行侧「主动不补」的位点（manifest.pause_intra.declined_idx）：裁定侧要把
    # "闸门量到语音而拒插"与"补了没治好"分开，故这两条腿由**执行侧落盘的读数**回答。
    # 旧 manifest 无该键＝当时没有这条裁定语义，按"无记录"计数，不得按 0 条拒插读。
    declined_points: list = []
    declined_scenes_without_record = 0
    declined_scenes: set = set()
    _booked_by_scene: dict = {}   # 场 → 本轮账本补入秒数（缺键＝未记账，不是 0）
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
        # 精停构成（2026-11-06 建、2026-11-07 修归因）：波形里样本精确为 0 的段
        # 是「人工插入的静音」——但插入者可能是**引擎的 SSML break**，也可能是
        # 流水线的补零，两者字节同形。故本面只量"精确零"这一事实，生产者由
        # 声明记录（本场 pause_marks）与账本（manifest 的本轮补入量）回答。
        _bidx = [k for k in range(len(spoken)) if after[k].strip()]
        _declared, _decl_broken = _declared_punct_indices(sc)
        comp_decl_broken += _decl_broken
        _booked = entry.get("pause_intra_inserted")
        if _booked is not None:
            _booked_by_scene[sid] = float(_booked)
        zr = None
        if wav_dir and entry.get("tts_file"):
            import intra_pause as _ip
            _a, _sr = _ip.mono_from_wav(Path(wav_dir) / str(entry["tts_file"]))
            if _a is None:
                comp_unmeasured.append(f"s{sid}(波形读不回)")
            else:
                zr = _zero_silence_runs(_a, _sr)
        elif not wav_dir:
            comp_unmeasured.append(f"s{sid}(未给波形目录)")
        for row in punctuation_gap_rows(chars, spoken, after):
            g, punct = row["gap"], row["punct"]
            gaps_by_punct.setdefault(punct if punct else "无标点", []).append(g)
            if not punct:
                continue
            rows.append((sid, punct, g, row["ctx"], row["anchor"]))
            if zr is not None:
                _lo = float(chars[row["index"] - 1][1]) if row["index"] > 0 else 0.0
                _hi = float(chars[row["index"]][0])
                _zs = sum(max(0.0, min(_hi, b) - max(_lo, a)) for a, b in zr)
                _prev_b = max([b for b in _bidx if b < row["index"]], default=0)
                _next_b = min([b for b in _bidx if b > row["index"]],
                              default=len(spoken))
                _decl = row["index"] in _declared
                _sig = max(0.0, _hi - _lo - _zs)
                comp_rows.append({
                    "scene_id": sid, "punct": punct, "ctx": row["ctx"],
                    "anchor": row["anchor"],
                    "cls": _classify_boundary(punct, row["index"] - _prev_b,
                                              _next_b - row["index"]),
                    "asr_gap": round(_hi - _lo, 3),
                    "zero_silence": round(_zs, 3),
                    "signal_gap": round(_sig, 3),
                    "declared": _decl,
                    # 分档用的量：有声明＝这段零是引擎的 break，交付间隙就是引擎给的；
                    # 无声明＝扣掉可能来自流水线补零的那部分。见 _build_prosody_summary。
                    "effective_gap": round(_hi - _lo if _decl else _sig, 3),
                    "pipeline_booked": _booked,
                })
            if g < floor:
                silent_punct.append(
                    f"s{sid} 标点处实测 {g:.2f}s（<{floor:g}s 可听阈）『…{row['ctx']}…』")
                silent_rows.append({**row, "scene_id": sid})
        # 第三桶：无标点处出现的可听停顿（判据与修剪面同源，见 _intra_bucket_rows）
        res, err = _intra_bucket_rows(sid, spoken, after, chars, entry, wav_dir)
        _pi = entry.get("pause_intra")
        if isinstance(_pi, dict) and "points" in _pi:
            # 本场插入了面跑过：有 declined_idx＝本批之后落盘的读数；缺键＝旧记录。
            if "declined_idx" in _pi:
                for _idx in _pi.get("declined_idx") or []:
                    declined_points.append({"scene_id": sid, "index": int(_idx)})
                    declined_scenes.add(sid)
            else:
                declined_scenes_without_record += 1
        if err:
            intra_broken.append(f"s{sid}：{err}")
        elif res:
            intra_rows += res["rows"]
            intra_unmeasurable += res["unmeasurable"]
            intra_no_energy += res["no_energy"]
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
        # ---- 第三桶（2026-11-05）：无标点处出现了可听停顿 ----
        "intra": {
            "threshold_seconds": _trim_threshold_or_none(),
            "hits": sum(1 for r in intra_rows if r["energy"] is True),
            # energy=None＝能量腿没跑（未给 wav_dir 或该区间取不到样本）：照常点名，
            # 但**不入 hits**——把"没量过"数成"确认是谷"即虚报，反之数成"没问题"即漏判。
            "unconfirmed": sum(1 for r in intra_rows if r["energy"] is None),
            "not_valley": sum(1 for r in intra_rows if r["energy"] is False),
            "points": intra_rows,
            "unmeasurable": intra_unmeasurable,
            "no_energy": intra_no_energy,
            "broken": intra_broken,
        },
        # ---- 精停构成（2026-11-06 建、2026-11-07 修归因，描述面）----
        # 逗号/顿号处看似「达标」的间隙里，有多少是样本精确为 0 的人工静音。
        # **不区分生产者**（引擎 break 与流水线补零同形）；生产者分别由
        # `declared`（声明记录）与 `pipeline_booked`（账本）给出，两者都可能缺席，
        # 缺席时宁可报"未记账"也不按 0 计。只透出、不改变任何裁定。
        "pause_composition": {
            "points_measured": len(comp_rows),
            "dead_floor_seconds": _SHORT_PAUSE_GAP_SECONDS,
            "short_pause": sum(1 for r in comp_rows
                               if r["effective_gap"] < _SHORT_PAUSE_GAP_SECONDS),
            "zero_silence_total_seconds": round(
                sum(r["zero_silence"] for r in comp_rows), 3),
            "declared_positions": sum(1 for r in comp_rows if r["declared"]),
            "pipeline_booked_total_seconds": round(sum(_booked_by_scene.values()), 3),
            "pipeline_booked_scenes": len(_booked_by_scene),
            "rows": comp_rows,
            "unmeasured": comp_unmeasured,
            "declaration_broken": comp_decl_broken,
        },
        # ---- 拒插位点（2026-11-08 真实项目反馈批）----
        # 执行侧幂等闸门量到"切点是说话声"时**主动不插**（插进去＝成片里多一个断口/杂音，
        # 用户点名的"额外杂音"即此）。这类位点不是"没测"也不是"补了没治好"，
        # 而是**没有安全的自动修法**；裁定侧据此把它们归未测并逐条点名。
        # 三类读数分列：执行侧落盘的位点 / 跑过但无该键的场（旧 manifest）/
        # 完全没跑的面（不在本键内，由裁定侧按 FAIL 保底）。
        "insert_declined": {
            "points": declined_points,
            "scenes": len(declined_scenes),
            "scenes_without_record": declined_scenes_without_record,
        },
        # ---- 语义停顿分层（2026-11-06 用户口播反馈批，描述面）----
        # 用途是回答「该不该有气口」，**不是**「把某处调到多少秒」：实测同一位置换
        # comma/period/colon/dash 只差 0.05s，标点等级不驱动引擎停顿。
        "prosody": _build_prosody_summary(
            comp_rows, int(pm["beat_ms"])),
    }
    return silent_punct, rushed, summary, skipped, silent_rows


_TRIM_RULES_MEMO: dict = {}


def load_trim_rules():
    """`audio_sync_rules.json → intra_pause_trim`（修剪面与第三桶共用的阈值权威源）。

    读不回即返回 None 并让调用方点名——**不得**回退到脚本内字面量：那会把权威源
    悄悄换成第二份真相（与 `enforced_floor()` 读不回返回哨兵 -1 同一族设计）。
    """
    try:
        d = json.loads(SYNC_RULES_FILE.read_text(encoding="utf-8"))
        sec = d["intra_pause_trim"]
        return sec if isinstance(sec, dict) else None
    except Exception:
        return None


def _trim_threshold_or_none():
    """第三桶的绝对阈（供报告透出）；读不回即 None 并照常点名，不得回退成脚本内字面量。"""
    r = load_trim_rules()
    try:
        return float(r["min_seconds"])
    except (TypeError, KeyError, ValueError):
        return None


def _intra_bucket_rows(sid, spoken, after, chars, entry, wav_dir):
    """第三桶逐场取数：判据走 intra_pause.classify_points（单源，见其 docstring）。"""
    import intra_pause as _ip
    if "rules" not in _TRIM_RULES_MEMO:      # 只缓存一次；读不回也要缓存（None 是结论不是缺键）
        _TRIM_RULES_MEMO["rules"] = load_trim_rules()
    rules = _TRIM_RULES_MEMO["rules"]
    if rules is None:
        return None, "修剪面判据断链（audio_sync_rules → intra_pause_trim 读不回）"
    audio = sr = None
    if wav_dir:
        cand = Path(wav_dir) / str(entry.get("tts_file") or "")
        audio, sr = _ip.mono_from_wav(cand) if entry.get("tts_file") else (None, None)
    verdict = _ip.classify_points(spoken, after, chars, rules, audio=audio, sr=sr)
    if verdict["length_mismatch"]:
        return None, f"原文 {len(spoken)} 字 ≠ 对齐 {len(chars)} 字，该场未量"
    rows = [{"scene_id": sid, "gap": p["gap"], "ctx": p["ctx"], "energy": p["energy"]}
            for p in verdict["points"]]
    return {"rows": rows, "unmeasurable": verdict["unmeasurable"],
            "no_energy": verdict["no_energy"]}, None


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

    返回 {project, absolute, rushed, summary, skipped, absolute_rows}；取数断链一律抛 Broken，
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
    absolute, rushed, summary, skipped, absolute_rows = audio_face(
        narration, manifest, rules,
        wav_dir=Path(manifest_path).parent / "tts_44k")
    html = Path(narration_path).parent / "index.html"
    html_text = ""
    if html.exists():
        try:
            html_text = html.read_text(encoding="utf-8")
        except OSError:
            html_text = ""      # 取不到 HTML 只影响"画面联动"这一条提示，不影响两桶读数
    return {"project": project, "narration": narration, "absolute": absolute,
            "rushed": rushed, "summary": summary, "skipped": skipped,
            "absolute_rows": absolute_rows, "html": html_text}


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


def emit_declarations(narration: list, rushed: list, html_text: str = "",
                      absolute_rows: list = None, beat_rows: list = None) -> list:
    """把两桶命中转成可直接并入 narration.json 的 pause_marks 片段（只打印，不落盘）。

    2026-11-06 机制批：入参旧形态只收**急促桶**，于是"0.10s 的顿号"这类绝对阈命中
    被点名后没有任何可执行出口。现两桶一并收，按 (场, 上下文) 去重（同一位点常同时
    入两桶，重复声明会双倍占用停顿预算）。

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
    cands = list(rushed)
    seen = {(str(r.get("scene_id")), r.get("ctx")) for r in cands}
    # 语义档候选（2026-11-06）：分句逗号「该有气口而引擎没给」的位点。
    # 与两桶同形，去重同样按（场, 上下文），不另起一套口径。
    for r in (beat_rows or []):
        key = (str(r.get("scene_id")), r.get("ctx"))
        if key in seen:
            continue
        seen.add(key)
        cands.append(r)
    for r in (absolute_rows or []):
        key = (str(r.get("scene_id")), r.get("ctx"))
        if key in seen:
            continue
        seen.add(key)
        cands.append(r)
    for r in cands:
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
            # 2026-11-06：锚词原本取标点前 ≤4 字，会切在词中（「会翻图集」）。
            # 声明是给人核对并入 narration.json 的，缩到**最短唯一后缀**（≥2 字）即可；
            # 不唯一时才保留原锚并进拒绝清单，不猜更长的词。
            if len(anchor) > 2:
                for _n in range(2, len(anchor) + 1):
                    if len(pm.anchor_positions(text, anchor[-_n:])) == 1:
                        anchor = anchor[-_n:]
                        break
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
            # 候选自带的 ms 优先（大气口候选由 pause_measurement.beat_ms 给值）；
            # 两桶行不带 ms，退回 auto_ms。一律取 auto_ms 会让同一位点在
            # 「语义层级」行与本建议里报两个不同的值（第二真相源）。
            ms = int(c.get("ms") or rules["auto_ms"])
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
             "rushed_punct": 0, "projects": 0, "audio_unaudited": 0,
             "intra_hits": 0, "intra_unconfirmed": 0}
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
        silent, rushed, summary, skipped, absolute_rows = [], [], {}, [], []
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
                    silent, rushed, summary, skipped, absolute_rows = audio_face(
                        narration, manifest, rules,
                        wav_dir=Path(manifest_path).parent / "tts_44k")
                    audio_ran = True
                    grand["silent_punct"] += len(silent)
                    grand["rushed_punct"] += len(rushed)
                    _intra = summary.get("intra") or {}
                    grand["intra_hits"] += int(_intra.get("hits") or 0)
                    grand["intra_unconfirmed"] += int(_intra.get("unconfirmed") or 0)
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
            _intra = summary.get("intra") or {}
            if _intra:
                for r in _intra.get("points") or []:
                    tag = {True: "真静音谷", False: "非谷（ASR 词尾早收）",
                           None: "未经能量确认"}[r["energy"]]
                    print(f"  · s{r['scene_id']} 无标点位点实测 {r['gap']:.2f}s"
                          f"（≥{_intra['threshold_seconds'] if _intra['threshold_seconds'] is not None else '?'}s）"
                          f"『…{r['ctx']}…』〔{tag}〕")
                for b in _intra.get("broken") or []:
                    print(f"  · 第三桶取数断链：{b}")
                print(f"  第三桶｜无标点且 ≥{_intra['threshold_seconds']}s 位点 {len(_intra.get('points') or [])} 处"
                      f"＝确认谷 {_intra['hits']}＋未经能量确认 {_intra['unconfirmed']}"
                      f"＋非谷 {_intra['not_valley']}"
                      f"｜插值/样本<2 未裁定 {_intra['unmeasurable']} 个"
                      f"｜区间取不到 {_intra['no_energy']} 个"
                      "（修剪面只动「确认谷」，本面只点名不剪）")
            if skipped:
                print(f"  音频面未量（逐场点名）：{'、'.join(skipped)}")
            if not hits and not silent and not rushed and not (_intra.get("points") or []):
                print("  文本面与音频面均无点名对象"
                      + ("（音频面未跑，此句不代表停顿已核）" if not audio_ran else ""))
        _beat_rows = (summary.get("prosody") or {}).get("beat_candidates") or []
        if a.emit_declarations and (rushed or absolute_rows or _beat_rows):
            print(f"—— {name}：可并入 narration.json 的 pause_marks 建议（只打印，不落盘）——")
            html_file = path.parent / "index.html"
            html_text = ""
            if html_file.exists():
                try:
                    html_text = html_file.read_text(encoding="utf-8")
                except OSError as e:
                    print(f"  画面联动未核（HTML 读不回，两桶建议照常）：{e}")
            for line in emit_declarations(narration, rushed, html_text,
                                          absolute_rows=absolute_rows,
                                          beat_rows=_beat_rows):
                print(f"  {line}")
    print(
        f"[PROSODY] 描述面（不裁定）：{grand['projects']} 个项目｜连读风险 {grand['merge_risk']}"
        f"｜枚举挤段 {grand['segment_overload']}"
        f"｜标点处实测低于可听阈 {grand['silent_punct']}"
        f"｜急促（相对同场同类中位）{grand['rushed_punct']}"
        f"｜无标点处可听停顿 {grand['intra_hits']}"
        + (f"（另有 {grand['intra_unconfirmed']} 处未经能量确认）"
           if grand["intra_unconfirmed"] else "")
        + (f"｜音频面未跑 {grand['audio_unaudited']} 个（该计数>0 时上列两项读数不完整）"
           if grand["audio_unaudited"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
