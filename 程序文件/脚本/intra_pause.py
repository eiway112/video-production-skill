#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无标点处多余停顿的**单一判据源**（取数＋裁定，不含波形改写）。

存在理由（2026-11-05，用户口播反馈批）
------------------------------------
用户对多支成片给出同一形态的反馈："强制底线""3 大支柱"这类**完整语义单位被音频打断**，
并说明"以往多个视频项目基本都存在"。实测（`过程产物/临时产物/_取证/20261105/`）：

- 跨 11 支已交付项目、8530 个无标点字符位点：中位 **0.000s**、p99 **0.008s**；
- 其中 **20 处 ≥0.25s 且两侧字符均为实测（matched=1）且区间是真静音谷**
  （谷底 ≤-30 dBFS 且与两侧语音落差 ≥12 dB），分布在 **9 支项目**，单场最多 2 处；
- 有标点位点对照：中位 0.400s、≥0.4s 占一半——即"该停的地方停 0.4s"是本仓既有节奏，
  而"不该停的地方停 0.3s"制造的是**分组歧义**：听众无法区分"词内切开"与"项间换行"，
  于是一条五项目的枚举被听成七项（实测片 装配式装修标准体系_知识科普_修订05 s2：
  评价|验收 0.32s、计量|计价 0.28s，两侧电平 -16.0/-68.2 dB）。

既有两桶为什么抓不到它
------------------
`narration_pause_precheck` 的音频面只在**有标点处**取样（源码里 `if not punct: continue`），
裁的是"标了标点却几乎没停"（连读桶）与"比同场同类标点的正常节奏明显短"（急促桶）。
本形态是它的**镜像**：旁白该位点没有任何停顿请求，波形里却出现了可听静音。
0.000/0.008 那两条读数正是同一份取样里"无标点"一列的统计，此前只进摘要、从不点名。

为什么裁定权在波形层而不是文案层（本轮实测，不是推测）
------------------------------------------------
1. **引擎对给定输入是确定的，跨时间不是**：同文本连合成 3 次 wav 的 sha256 逐字节相同
   （probe_determinism.py），但 2026-10 交付的波形里 制|底=0.30s，2026-11-05 用**逐字相同的整场
   旁白**重合成后该位点 0.00s（probe_whole_scene.py）→ "重跑 TTS"与"改文案"都不能保证下一轮干净。
2. **韵律由整段上下文决定**：把"支撑结论的是 3 大支柱。"单句送进引擎→ 三|大 0.00s；
   按生产粒度（整场 101 字）送→ 同一位点 0.30s 重现。故任何在小句粒度上得到的"改写有效"
   结论都不成立（本轮 A 组曾一度这样误判，改在整场粒度重测后翻转）。
=> 可靠面只有"合成完之后，对**实测波形**动手"，而它必须发生在字幕时刻与场景窗口
   尚未固化之前——即 `_build_timeline_manifest` 内、停顿强制之后（本模块的两个消费方：
   `enhance_video_audio._trim_intrasentence_silence` 与 `narration_pause_precheck` 第三桶）。

单源纪律
--------
判据（阈值、两腿、归属口径）只住本文件与 `audio_sync_rules.json → intra_pause_trim`。
两个消费方**都不得**自己再算一遍间隔——波形侧与点名侧一旦分叉，就是"报告里绿、成片里响"。
字符归属沿用 `narration_pause_precheck.spoken_and_after`（`after[k]`＝spoken[k] **之前**的标点串），
与既有两桶同法；该函数首版曾把归属错一位，读数表现为"所有停顿 0.00"（见其源码注释）。
"""
from __future__ import annotations

import json
from pathlib import Path

RULES_KEY = "intra_pause_trim"

# 取数断链（规则文件缺失/残键）时的裁定：不启用、并逐条点名。
# 不得由"读不到判据"退化成"按内置字面量跑"——那等于把权威源换成脚本。
REQUIRED_KEYS = ("min_seconds", "relative_ratio", "keep_seconds",
                 "valley_floor_db_max", "valley_side_sep_db_min",
                 "enabled_by_default")


class Untested(Exception):
    """判据断链：调用方须点名断在哪条腿，不得静默跳过、也不得按 0 计。"""


def load_rules(rules_path: Path) -> dict:
    """读 intra_pause_trim 节；残键即抛 Untested（fail-closed）。"""
    try:
        data = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise Untested(f"规则文件不可读（{type(e).__name__}）：{rules_path}")
    sec = data.get(RULES_KEY)
    if not isinstance(sec, dict):
        raise Untested(f"规则节缺席或非对象：{RULES_KEY}")
    missing = [k for k in REQUIRED_KEYS if k not in sec]
    if missing:
        raise Untested(f"规则残键 {missing}（{RULES_KEY}）")
    return sec


def interval_gaps(spoken, after, chars):
    """按既有两桶同法逐位点取间隔；返回 [(i, gap, punct_present)]。

    长度不齐（原文字数 ≠ 对齐字符数）属**无从裁定**，由调用方点名跳过，
    不得把差值位点按 0.00 计。
    """
    if len(spoken) != len(chars):
        return None
    out = []
    for i in range(len(chars) - 1):
        gap = float(chars[i + 1][0]) - float(chars[i][1])
        out.append((i, gap, bool(str(after[i + 1] or "").strip())))
    return out


def candidates(intervals, rules):
    """无标点 + 两侧实测 + ≥绝对阈 + （可读时）≥relative_ratio×同场标点中位。

    返回 (points, unmeasurable)。`unmeasurable` 是因**取数面**而未入桶的位点数
    （matched=0 的插值时刻、同场标点样本 <2 无从取中位），调用方必须把它
    与"入桶 0 条"分开呈现——"0 条命中"不等于"干净"。
    """
    floor = float(rules["min_seconds"])
    ratio = float(rules["relative_ratio"])
    punct_vals = [g for _i, g, has in intervals if has]
    cohort = len(punct_vals) >= 2
    med = None
    if cohort:
        import statistics
        med = statistics.median(punct_vals)
    pts, unmeasurable = [], 0
    for i, g, has in intervals:
        if has or g < floor:
            continue
        # 相对腿：同场标点中位取不回（样本 <2）时该位点**不入桶**，计入 unmeasurable，
        # 不得由"无从取中位"读成"不显著"（与既有 relative_unmeasurable 同判据）。
        if not cohort or med is None or med <= 0 or g < ratio * med:
            unmeasurable += 1
            continue
        pts.append({"i": i, "gap": round(g, 3)})
    return pts, unmeasurable


def require_matched_both(chars, i):
    """两侧都须是实测时刻。matched=0 的时间是锚点间线性插值造的，不得驱动波形改写。"""
    try:
        return int(chars[i][2]) == 1 and int(chars[i + 1][2]) == 1
    except (IndexError, TypeError, ValueError):
        return False


def classify_points(spoken, after, chars, rules, audio=None, sr=None):
    """**两个消费方唯一的判据入口**（修剪面与点名面共用，不得各算一遍）。

    返回 {"points": [...], "unmeasurable": int, "no_energy": int, "length_mismatch": bool}。
    每个 point 带 `energy` 三态：True＝真静音谷（可剪）／False＝不是谷（不得剪，
    ASR 词尾早收的时刻偏差）／None＝能量腿没跑（audio 未给或该区间取不到样本）。

    `energy=None` 与 `False` 的分工是本面的要点：
      - 修剪面只动 True 的位点（拿"没量过"当"是谷"去剪波形＝在字上动刀）；
      - 点名面两者都列，但 None 的须印成"未经能量确认"，不得并进命中计数——
        与既有 `relative_unmeasurable` 同判据（"无从裁定"不得读成干净，也不得读成缺陷）。
    """
    intervals = interval_gaps(spoken, after, chars)
    if intervals is None:
        return {"points": [], "unmeasurable": 0, "no_energy": 0, "length_mismatch": True}
    pts, unmeasurable = candidates(intervals, rules)
    out, no_energy = [], 0
    for p in pts:
        i = p["i"]
        if not require_matched_both(chars, i):
            unmeasurable += 1
            continue
        t_lo, t_hi = float(chars[i][1]), float(chars[i + 1][0])
        energy = None
        if audio is not None and sr:
            energy = energy_ok(audio, sr, t_lo, t_hi, rules)
            if energy is None:
                no_energy += 1
        out.append({**p, "t_lo": t_lo, "t_hi": t_hi, "energy": energy,
                    "ctx": ("".join(spoken[max(0, i - 5):i + 1]) + "▏"
                            + "".join(spoken[i + 1:i + 7]))})
    return {"points": out, "unmeasurable": unmeasurable,
            "no_energy": no_energy, "length_mismatch": False}


def audio_is_valley(points):
    """修剪面准入的子集：能量腿明确为 True 的位点。"""
    return [p for p in points if p.get("energy") is True]


def mono_from_wav(path):
    """读 wav → (单声道 float[-1,1], 采样率)。读不回返回 (None, None) 由调用方点名。"""
    import wave
    import numpy as np
    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() != 2:
                return None, None
            n_ch, sr = wf.getnchannels(), wf.getframerate()
            raw = wf.readframes(wf.getnframes())
    except (OSError, ValueError):
        return None, None
    a = np.frombuffer(raw, dtype=np.int16).reshape(-1, n_ch)
    return a.mean(axis=1).astype(np.float64) / 32768.0, sr


def energy_ok(audio, sr, t_lo, t_hi, rules):
    """能量腿：区间确为静音谷（谷底 ≤ floor_max 且与两侧语音落差 ≥ sep_min）。

    `audio` 是 s16 归一化后的单声道 float 数组。区间过短（<0.01s）或越界 → None，
    由调用方按"无从裁定"处理，不得按 False 计（False＝确认不是谷，语义不同）。
    """
    import numpy as np
    i0 = int(round(max(0.0, t_lo) * sr))
    i1 = int(round(min(len(audio) / sr, t_hi) * sr))
    if i1 - i0 < int(0.01 * sr):
        return None

    def _rms(a, b):
        seg = audio[a:b]
        if seg.size < 160:
            return None
        v = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
        return -180.0 if v <= 0 else 20 * np.log10(v + 1e-12)

    pad = int(round(0.15 * sr))
    floor = _rms(i0, i1)
    side_l = _rms(max(0, i0 - pad), i0)
    side_r = _rms(i1, min(len(audio), i1 + pad))
    if floor is None or side_l is None or side_r is None:
        return None
    side = max(side_l, side_r)
    return bool(floor <= float(rules["valley_floor_db_max"])
                and side - floor >= float(rules["valley_side_sep_db_min"]))
