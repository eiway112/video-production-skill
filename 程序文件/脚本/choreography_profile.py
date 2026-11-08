# -*- coding: utf-8 -*-
"""动画编排度量（描述面，2026-11-05 第二支外部参考片实测批）。

**存在理由**：同日实测一支外部竖屏片（可见变化 3.2 次/秒）对本仓成片（0.6-1.2 次/秒），
差距真实但机制不在"有没有写动画"上——73 个项目里 64 个有 yoyo、每场中位 12 路动效、
`ease` 用 none/linear 的占比中位仅 0.85%、单条时长 0.70s 也正常。病灶在**时间分配**：
把无限循环剔掉后，"会走完的动效"只覆盖该场窗口的约 23%（p50），
**88.9% 的场景存在超过 3 秒的无推进段**（中位长度 7.9 秒）。

**为什么另起一面而不复用 `frame_occupancy.motion`**：那面量的是**产出**（成片像素逐帧差分），
本面量的是**声明**（HTML 里动效区间的时间并集）。两者关系恰如"写了什么"与"渲出什么"，
2026-11-05 四支成片同片两面对撞过：含循环的覆盖率算出 1.000（满分）的两支，
像素面在动占比只有 30.7%／25.5%——**声明面用循环能把纸面填满，产出面不认**。
本面的独立价值在于**渲染前就有数**：纯静态解析，零 ffmpeg、零渲染、零扣费，
而像素面要等一次全量渲完才看得见。

**刻意不裁定**：只出数，不判 PASS/FAIL，不进 issues、不进任何退出码路径——
与 `frame_occupancy` 同一取舍（2026-10-10 常设裁定：不为观感问题新增代码门禁）。
`STILL_RUN_SECONDS` 只用于"占比"这一描述性统计，不作裁定门槛；条款本体住
`新路径实施_踩坑清单与防范检查表.md` §八 场景时间设计规范第 3 条，
**改那条条款须同批改此处并在此登记**（本面与条款是两个载体，数值一致靠这条注释维持，
仓内暂无机器校验——升格为机器校验属下一批，须先有 ≥3 支同画布向已验收样本）。

**翻转前置（何时才该考虑定档）**：同画布向、用户逐支点过观感合格的项目 ≥3 支的
本面同口径分布，并同时回答四态给法与"会错杀哪些既有交付"。

**三条取数边界（不得读成"没有缺陷"）**：
  ① 相对锚（`'<'`／`'<<'`／`'+=0.5'`）、标签锚、以及 living 在辅助函数里的变量锚
     （`onAt`／`offAt - 0.45`：同一语句跑 N 次）解析不出 → 计入 `unparsed` 并随读数透出。
     它们紧跟前一条/由数据驱动，计入只会**抬高**覆盖率，故本面覆盖率是**下界**、无推进段是**上界**。
  ③ `cue('sN','词',fallback)` 型锚（2026-11-08，词级 cue 驱动时间轴）按 HTML 里的
     `CUE-MAP`（timeline 步生成、值为场内相对秒）解析成 `T.sN + C['sN:词']`；
     **键缺时回退作者写的固定秒**并单独计入 `tweens_cue_fallback`——回退值是撰写侧估算，
     不是实测时刻，故它算"解析成功"但可能在音频上偏移；CUE-MAP 整块缺席时一律按
     `unparsed` 处理（不得按 0 计，否则把"没解析"洗成"从场首开始"）。
  ④ **节拍子面**（`cue_beats`）：同一场内相邻 cue 时刻之差的中位与最大——即"画面多久换
     一次焦点"。它答的是**换焦点的频率**，不答"两次之间画面有没有在动"（后者看
     `frame_occupancy` 的活跃度子面），两者不得互替。2026-11-08 观感批的病灶就是
     每场 4~6 个 cue 摊在 30~46s 上、最长空档 13.5s。
  ② 锚点越界（如 `T.s5+40` 落在 s5 窗口外）夹到窗口后为空 → 既不计入覆盖**也不计入分母**，
     单独计数 `out_of_window` 点名。首版未夹时算出过 `coverage_min = -2.188` 的负覆盖，
     负覆盖是取数缺陷不是业务量（该错误留在裁定文档 §14.1/§14.2 不回改）。
"""
import re

DEFAULT_DURATION = 0.5          # GSAP 未声明 duration 时的引擎缺省值，非本件自定
STILL_RUN_SECONDS = 3.0         # 描述性占比门槛，与踩坑清单 §八 第 3 条同源；不裁定
LAST_SCENE_FALLBACK = 8.0       # 末场取不到 root data-duration 时的兜底窗口长度

CALIBER = {"default_duration": DEFAULT_DURATION,
           "still_run_seconds": STILL_RUN_SECONDS,
           "last_scene_fallback": LAST_SCENE_FALLBACK,
           "loop_treatment": "repeat:-1 计入含循环覆盖，剔除出推进型覆盖",
           "instant_treatment": "tl.set／tl.call 按 GSAP 语义零时长，"
                                "计入 tweens_instant_statements，不入任一腿",
           "cue_treatment": "cue('sN','词',fallback) 按 CUE-MAP 解析为场内相对秒；"
                            "键缺回退作者固定秒并单计，CUE-MAP 缺席一律 unparsed"}

_TBLOCK = re.compile(r'(?:const|var|let)\s+T\s*=\s*\{([^}]*)\}', re.S)
_TVALS = re.compile(r'(\w+)\s*:\s*([0-9.]+)')
_CUE_MAP = re.compile(r'(?:var|let|const)\s+C\s*=\s*\{([^}]*)\}', re.S)
# 键必须含 ':'（`sN:锚词` 形态），故不会把同名变量的其它数值表读成 CUE-MAP
_CUE_ENTRY = re.compile(r'["\']([^"\']*:[^"\']*)["\']\s*:\s*([0-9.]+)')
_CALL = re.compile(r'\btl\.(to|from|fromTo|set|call|add|fromToOnce)\s*\(', re.I)
_ANCHOR = re.compile(r'^T\.(\w+)(?:\s*([+-])\s*([0-9.]+))?$')
# `T.s1 + cue('s1','使用极限状态',12.2)`，可带尾随偏移（`+ 1.30`）
_CUE_ANCHOR = re.compile(
    r"^T\.(\w+)\s*\+\s*cue\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]"
    r"\s*(?:,\s*([0-9.]+)\s*)?\)\s*(?:([+-])\s*([0-9.]+))?$")
# 辅助函数内的变量锚（`onAt`／`offAt - 0.45`）：同一语句按数据执行 N 次，静态面看不见其覆盖
_VAR_ANCHOR = re.compile(r'^[A-Za-z_$][\w$]*(?:\s*[+-]\s*[0-9.]+)?$')
_BARE = re.compile(r'^([0-9.]+)$')
_DUR_DECL = re.compile(r'duration\s*:\s*([0-9.]+)')
_DUR_EXPR = re.compile(r'duration\s*:\s*\(')
_LOOP = re.compile(r'repeat\s*:\s*-1')
_ROOT_DUR = re.compile(r'data-duration\s*=\s*["\']([0-9.]+)["\']', re.I)


def _match_call(text, open_idx):
    """从 '(' 起做括号配平，返回整段调用文本"""
    depth, i = 0, open_idx
    while i < len(text):
        c = text[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
        i += 1
    return None


def _split_args(seg):
    """按顶层逗号切参数（字符串内与嵌套括号内的逗号不算分隔）"""
    args, depth, cur, in_s, qc = [], 0, [], False, ''
    for ch in seg:
        if in_s:
            cur.append(ch)
            if ch == qc:
                in_s = False
            continue
        if ch in '"\'':
            in_s, qc = True, ch
            cur.append(ch)
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        args.append("".join(cur).strip())
    return args


def _scene_windows(T, root_dur):
    """按 T 块键的**取值**排序切场窗口；末场窗口右界取 root data-duration，取不到用兜底长度"""
    ordered = sorted(T.items(), key=lambda kv: kv[1])
    win = {}
    for i, (k, v) in enumerate(ordered):
        if i + 1 < len(ordered):
            end = ordered[i + 1][1]
        else:
            end = root_dur if (root_dur and root_dur > v) else v + LAST_SCENE_FALLBACK
        win[k] = (float(v), max(float(v) + 0.5, float(end)))
    return win


def _merge(intervals):
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _cue_beats(cue_map):
    """词级 cue 的**节拍间隔**（场内相对秒）：同一场景内相邻 cue 时刻之差。

    这是"画面多久换一次焦点"的渲染前读数——2026-11-08 观感批的病灶正是它：
    每场 4~6 个 cue 分布在 30~46s 上，最长空档 13.5s，观众看到"TTS 在进行、画面停住"。
    取数只认 CUE-MAP（timeline 按实测字符时刻生成），不猜作者写的 fallback。
    边界：本面量的是**声明的节拍**；焦点保持（呼吸脉冲）由辅助函数按数据执行，
    静态面看不见其覆盖——所以本数只答"多久换一次焦点"，不答"中间有没有在动"，
    后者看 `frame_occupancy` 的活跃度子面。
    """
    from collections import defaultdict
    by = defaultdict(list)
    for k, v in cue_map.items():
        by[str(k).split(":")[0]].append(float(v))
    iv = []
    for _sid, vals in by.items():
        ts = sorted(set(vals))
        iv.extend(b - a for a, b in zip(ts, ts[1:]))
    if not iv:
        return {"count": len(cue_map), "scenes": len(by),
                "interval_median": None, "interval_max": None}
    import statistics as st
    return {"count": len(cue_map), "scenes": len(by),
            "interval_median": round(float(st.median(iv)), 2),
            "interval_max": round(float(max(iv)), 2)}


def _coverage(intervals, win):
    """返回 (覆盖率, 最长无覆盖段秒数)。区间先夹到窗口内，夹空者由调用方计数不入分母。"""
    merged = _merge(intervals)
    covered = sum(b - a for a, b in merged)
    w = win[1] - win[0]
    ratio = covered / w if w > 0 else 1.0
    gaps, prev = [], win[0]
    for a, b in merged:
        if a - prev > 0:
            gaps.append(a - prev)
        prev = max(prev, b)
    if win[1] - prev > 0:
        gaps.append(win[1] - prev)
    return ratio, (max(gaps) if gaps else 0.0)


def measure(html_path):
    """编排面读数（描述面，**不裁定**）。永不抛异常——取不到就点名为什么取不到。

    返回含：per_scene 逐场两腿覆盖率与无推进段、全片分布、四条取数计数
    （parsed / unparsed / out_of_window / default_duration）。
    """
    try:
        from pathlib import Path
        p = Path(html_path)
        if not p.is_file():
            return {"untested": f"HTML 不在位（{p}）——编排面无对象可解析，不得读成「编排已检查且干净」"}
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:                              # noqa: BLE001
        return {"untested": f"HTML 读取失败（{type(e).__name__}）"}

    # 常驻锚子面在 T 块之前取数：它读的是**结构**（场景容器与契约属性），与时间轴无关。
    # 若挂在时间轴取数之后，一支无 T 块的项目会整面静默缺席——那正是用例93 B6 立的形态
    # （"没接这个子面"与"接了但取不到数"都写成不出现，等于把摘掉子面藏起来）。
    # 内层 catch 保住主面：子面崩了只点名子面，不得把已有读数吞成一句 untested。
    try:
        persist = measure_persistence(txt)
    except BaseException as e:                          # noqa: BLE001
        persist = {"untested": f"常驻锚子面异常（{type(e).__name__}）"}

    tb = _TBLOCK.search(txt)
    if not tb:
        return {"untested": "无 T-block 声明——本件不猜时间轴，故时间轴腿无读数",
                "persist": persist}
    T = {k: float(v) for k, v in _TVALS.findall(tb.group(1))}
    if len(T) < 2:
        return {"untested": f"T-block 只有 {len(T)} 个键（<2），无从切场窗口",
                "persist": persist}

    m = _ROOT_DUR.search(txt)
    root_dur = float(m.group(1)) if m else None
    win = _scene_windows(T, root_dur)

    cue_map = {}
    _cm = _CUE_MAP.search(txt)
    if _cm:
        cue_map = {k: float(v) for k, v in _CUE_ENTRY.findall(_cm.group(1))}
    beats = _cue_beats(cue_map)

    per = {k: [] for k in win}
    per_noloop = {k: [] for k in win}
    unparsed = out_of_window = default_dur = total = instant = 0
    cue_resolved = cue_fallback = variable_anchor = 0
    for mo in _CALL.finditer(txt):
        seg = _match_call(txt, mo.end() - 1)
        if seg is None:
            unparsed += 1
            continue
        total += 1
        args = _split_args(seg[1:-1])
        anchor = args[-1] if args else ''
        g, b = _ANCHOR.match(anchor), _BARE.match(anchor)
        cg = _CUE_ANCHOR.match(anchor)
        if g and g.group(1) in T:
            key, a = g.group(1), T[g.group(1)]
            if g.group(2):
                a += float(g.group(3)) * (1 if g.group(2) == '+' else -1)
        elif cg and cg.group(1) in T:
            key = cg.group(1)
            _ck = f"{cg.group(2)}:{cg.group(3)}"
            if _ck in cue_map:
                a = T[key] + cue_map[_ck]
                cue_resolved += 1
            elif cg.group(4) is not None:
                a = T[key] + float(cg.group(4))
                cue_fallback += 1
            else:
                # cue() 两处都取不到（键缺且无 fallback）＝无从定位，不得按场首计
                unparsed += 1
                continue
            if cg.group(5):
                a += float(cg.group(6)) * (1 if cg.group(5) == '+' else -1)
        elif b:
            v = float(b.group(1))
            key = next((k for k, (s0, s1) in win.items() if s0 <= v < s1), None)
            a = v
            if key is None:
                out_of_window += 1
                continue
        else:
            # 相对锚（'<'/'+=x'）、标签锚、或时间参数缺位——一律点名，不按 0 覆盖计
            if _VAR_ANCHOR.match(anchor):
                variable_anchor += 1
            unparsed += 1
            continue
        s0, s1 = win[key]
        # `set`／`call` 是瞬时赋值（GSAP 里零时长），给它任何宽度都是**虚高**——而"纸面填满
        # 但画面没在动"正是本面要显形的形态（编号 24）。2026-11-05 复核批实测：全仓 49/50 份
        # 含 tl.set、共 728 条，旧形态按缺省 0.5s 计入推进腿，使 28/50 片的推进覆盖率虚高
        # 最多 +0.028（中位 0.211→0.202）。
        # 位点必须在窗口包含判据**同层**：越界的 set 归 out_of_window、在窗内的归 instant，
        # 两个计数器互斥（首版把跳过放在判窗之前，越界的 set 被记成瞬时＝把锚的问题
        # 换成语句类型，K3/K3b 抓到）。锚解析不出的仍走原路计 unparsed。
        if mo.group(1).lower() in ("set", "call"):
            if s0 <= a < s1:
                instant += 1
            else:
                out_of_window += 1
            continue
        # vars 段可能有**两个**对象：`fromTo(sel, fromVars, toVars, anchor)`。只取第一个
        # 会读到 from 段——那里通常没有 `duration`（于是 0.6s 的动效被记成缺省 0.5s），
        # 更会把写在 toVars 里的 `repeat:-1` 漏掉，使循环混进"推进型"那一腿（方向是**虚高**，
        # 与本件其余低估方向相反）。故遍历全部对象段、取第一个命中（与 `_取证/20261105`
        # 探针 `duration_of` 逐字同法；常驻件读数要与已存档的探针读数可比，口径必须一致）。
        objs = [x for x in args if x.startswith('{') or x.startswith('[')]
        is_loop = any(_LOOP.search(x) for x in objs)
        d = None
        for x in objs:
            md = _DUR_DECL.search(x)
            if md:
                d = float(md.group(1))
                break
            if _DUR_EXPR.search(x):
                d = s1 - a          # duration 写成表达式（多为整场窗口）→ 覆盖到场末
                break
        if d is None:
            d = DEFAULT_DURATION
            default_dur += 1
        if is_loop:
            d = s1 - a              # 无限循环视为覆盖到场末（含循环那一腿）
        iv = (max(a, s0), min(a + d, s1))
        if iv[1] - iv[0] <= 0:
            out_of_window += 1
            continue
        per[key].append(iv)
        if not is_loop:
            per_noloop[key].append(iv)

    n_parsed = sum(len(v) for v in per.values())
    if n_parsed < 3:
        return {"untested": f"可解析动效语句仅 {n_parsed} 条（<3），无从给分布"
                            f"（未解析 {unparsed} 条／越界 {out_of_window} 条）",
                "persist": persist}

    ratios, ratios_nl, gaps, gaps_nl, scenes = [], [], [], [], []
    for k, w in win.items():
        r, gp = _coverage(per[k], w)
        rn, gpn = _coverage(per_noloop[k], w)
        ratios.append(r)
        ratios_nl.append(rn)
        gaps.append(gp)
        gaps_nl.append(gpn)
        scenes.append({"scene": k, "window_s": round(w[1] - w[0], 2),
                       "coverage_loop": round(r, 3), "coverage_advance": round(rn, 3),
                       "advance_gap_max_s": round(gpn, 2)})
    import statistics as st
    out = {
        "caliber": dict(CALIBER),
        "scenes": len(win),
        "scene_window_p50": round(float(st.median([w[1] - w[0] for w in win.values()])), 2),
        "tweens_total": total,
        "tweens_parsed": n_parsed,
        "tweens_unparsed": unparsed,
        "tweens_cue_resolved": cue_resolved,
        "tweens_cue_fallback": cue_fallback,
        "tweens_variable_anchor": variable_anchor,
        "cue_map_entries": len(cue_map),
        "cue_beats": beats,
        "tweens_out_of_window": out_of_window,
        "tweens_default_duration": default_dur,
        "tweens_instant_statements": instant,
        "coverage_loop_p50": round(float(st.median(ratios)), 3),
        "coverage_advance_p50": round(float(st.median(ratios_nl)), 3),
        "coverage_advance_min": round(float(min(ratios_nl)), 3),
        "advance_gap_p50": round(float(st.median(gaps_nl)), 2),
        "advance_gap_max": round(float(max(gaps_nl)), 2),
        "share_scenes_gap_over_still_run": round(
            float(sum(1 for g in gaps_nl if g > STILL_RUN_SECONDS) / len(gaps_nl)), 3),
        "lower_bound": True,
        "per_scene": scenes,
        "persist": persist,
    }
    return out


def format_line(m):
    """人类可读那一行的**唯一生成点**（文案住这里，报告侧不得另抄一句描述）。

    刻意不含"合格/不合格"字样：本面不裁定（理由见模块 docstring）。
    下界性质必须逐字随行——覆盖率不含相对锚，把它当精确值用即高估缺陷、低估产出。
    """
    # 判"键在不在"而非"值真假"，且 None／空 dict／缺读数键三种都得走这一支：
    # `not m` 短路进来后再对 m 取属性即 AttributeError（None 无 get），
    # 而 `{}` 没有 untested 键会掉进取数分支并 KeyError——
    # 两条都由用例122 B5/B6 当场抓到（与用例93 B3 同族：描述面把"崩了"读成"没有这一节"是最坏的形态）。
    if not isinstance(m, dict) or "untested" in m:
        _why = m.get("untested") if isinstance(m, dict) else None
        # 主面未测而子面取到数时，子面读数**照常随行**：时间轴取不到不等于结构也取不到，
        # 把两件事写成一句 untested 就是把已有读数洗掉（frame_occupancy 的 motion 子面同判据）。
        _sub = (format_persist_line(m.get("persist"))
                if isinstance(m, dict) and m.get("persist") is not None else "")
        return f"[CHOREOGRAPHY] UNTESTED — {_why or '未附归因（取数面未点名原因）'}" + _sub
    if "coverage_advance_p50" not in m:
        return (f"[CHOREOGRAPHY] UNTESTED — 读数键缺失（现有键 {sorted(m)[:6]}…），"
                "不得按空读数印成已测")
    c = m.get("caliber") or {}
    # 子面缺席必须**可见**：把"未接常驻锚子面"和"接了但取不到数"都写成静默省略，
    # 就等于让"摘掉子面"这种改动不留任何读数差（用例93 B5 同族）。
    mo = m.get("persist")
    if mo is None:
        persist_txt = "｜常驻锚 未取数（子面未接入，非「取不到」）"
    else:
        persist_txt = format_persist_line(mo)
    # 节拍子面：读的是"多久换一次焦点"。缺席与无间隔分开写——把两者混成一句，
    # 摘掉子面与"这片每场只有一个 cue"读起来一样（用例122 N 段锁住）。
    _b = m.get("cue_beats")
    if not isinstance(_b, dict):
        beat_txt = "｜cue 节拍 未取数（键缺席，须重跑本面）"
    elif _b.get("interval_max") is None:
        beat_txt = (f"｜cue 节拍 无间隔可算（{_b.get('count', 0)} 个 cue／"
                    f"{_b.get('scenes', 0)} 场，每场不足 2 个）")
    else:
        beat_txt = (f"｜cue 节拍 中位间隔 {_b['interval_median']}s／最大 {_b['interval_max']}s"
                    f"（{_b['count']} 个 cue／{_b['scenes']} 场）")
    return (f"[CHOREOGRAPHY] 描述面（不裁定，**下界读数**）：推进型覆盖率 中位 {m['coverage_advance_p50']} "
            f"最低 {m['coverage_advance_min']}（含循环口径 {m['coverage_loop_p50']}，循环不计入推进）"
            f"｜最长无推进段 中位 {m['advance_gap_p50']}s／全片最大 {m['advance_gap_max']}s"
            f"｜>{c['still_run_seconds']}s 无推进的场景占比 {m['share_scenes_gap_over_still_run']}"
            f"（{m['scenes']} 场，场窗中位 {m['scene_window_p50']}s）"
            f"｜动效语句 解析 {m['tweens_parsed']}/{m['tweens_total']}"
            f"，未解析 {m['tweens_unparsed']}（相对锚/标签锚/变量锚，计入会抬高覆盖率；"
            f"其中变量锚 {m.get('tweens_variable_anchor', '缺键')}＝辅助函数内数据驱动，"
            f"静态面看不见其覆盖，须以产出面复核）"
            f"，cue 锚 {m.get('tweens_cue_resolved', '缺键')} 条（回退作者固定秒 "
            f"{m.get('tweens_cue_fallback', '缺键')}，CUE-MAP {m.get('cue_map_entries', '缺键')} 项）"
            f"，越界 {m['tweens_out_of_window']}，缺省时长 {m['tweens_default_duration']}"
            f"，瞬时(set/call) {m.get('tweens_instant_statements', '缺键')}（零时长，不入覆盖）" + beat_txt +
            f"｜口径 静态解析 T-block＋tl 语句时间并集，零渲染" + persist_txt)


# ── 跨场常驻主图（锚＋生长）子面（2026-11-05 编号 26；描述面，不裁定）────────
PERSIST_CONTRACT = "data-anchor"      # 作者契约属性：同一名值＝跨场同一张主图
PERSIST_GEOMETRY_KEYS = ("left", "top", "right", "bottom", "width", "height", "inset")

PERSIST_CALIBER = {"contract_attr": PERSIST_CONTRACT,
                   "scene_container": '带 data-scene-id 或 id="sceneN" 的元素',
                   "geometry_source": "仅内联 style 的定位尺寸（CSS 类规则不参与——无层叠求解器，猜即造数）",
                   "adjudicates": False}

_PERSIST_VOID = {"img", "br", "input", "meta", "link", "source", "area", "base",
                 "col", "embed", "hr", "param", "track", "wbr"}


def _inline_geometry(style):
    if not style:
        return {}
    out = {}
    for part in re.split(r"[;\n]", style):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip().lower()
        if k in PERSIST_GEOMETRY_KEYS:
            out[k] = v.strip().lower().replace(" ", "")
    return out



def _inline_hidden(style):
    """内联样式里请求了初始隐藏（opacity:0 或 display:none）。

    只看内联，与几何同口径（CSS 类规则不参与）。这条不是多余谨慎：
    2026-11-05 复核批实测——两处基形带 `opacity:0` 且**没有任何入场语句**时，
    `check_element_reveal` 判 0（它只认"容器隐藏·入场只打子元素"那种形态）、
    `check_scene_visibility` 也判 0（它管的是场景根），而 `persist` 当时报
    "声明 2 处／覆盖 2 场"。渲染里那两处**全程不存在**（2026-10-13 的"6 块内容永久
    不可见"即同一病灶），读数却像已就位。描述面不裁定，但也不能把没交付读成交付。
    """
    if not style:
        return False
    for part in re.split(r"[;\n]", style):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip().lower()
        v = v.strip().lower().replace(" ", "")
        if k == "display" and v == "none":
            return True
        if k == "opacity" and v in ("0", "0.0", ".0", "0.00"):
            return True
    return False


def _statement_target_selectors(txt):
    """所有 tl 语句的**目标串**（首实参字面量），供"这条隐藏有没有对应入场"用。

    刻意取宽：只要选择器串里出现过该元素的 `#id` 或 `.class` 记号，就算"有入场候选"。
    取宽的方向是**少报**隐藏（宁可漏报不可误报成缺陷），与几何那条"宁可多报"相反——
    因为这里误报的代价是让人去查一处不存在的病灶，而漏报的代价是同一处病灶本来就有
    别的载体（渲染后抽帧）能看见。
    """
    sels = []
    for mo in _CALL.finditer(txt):
        seg = _match_call(txt, mo.end() - 1)
        if not seg:
            continue
        args = _split_args(seg[1:-1])
        if not args:
            continue
        m = re.fullmatch(r'\s*(["\'])(.*?)\1\s*', args[0], re.S)
        if m:
            sels.append(m.group(2))
    return sels


def _scan_anchors(txt):
    """一遍浅栈扫描 → (scenes, instances)。

    只建「当前在哪个场景容器里」这一层状态，不做完整 DOM：本面要的两问（这个元素在哪一场、
    它的内联几何是什么）都不需要层叠与脚本。`<body>` 处清栈——场景 div 一律是 body 的子树，
    不清则 head 里的 meta/style 标签配对错乱会把后续元素归到错误容器。
    """
    from html.parser import HTMLParser

    scenes, instances, covers = [], [], []

    class _P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []                      # [(tag, 所在场景 id 或 None)]

        @staticmethod
        def _scene_of(d):
            sid = (d.get("data-scene-id") or "").strip()
            if sid:
                return sid
            ident = (d.get("id") or "").strip()
            return ident if re.fullmatch(r"scene\d+", ident) else None

        def _visit(self, tag, attrs):
            d = dict(attrs)
            own = self._scene_of(d)
            inherited = self.stack[-1][1] if self.stack else None
            container = own if own is not None else inherited
            if own is not None and own not in scenes:
                scenes.append(own)
                if (d.get("data-scene-entry") or "").strip().lower() == "cover":
                    covers.append(own)
            anchor = (d.get(PERSIST_CONTRACT) or "").strip()
            if anchor:
                instances.append({"anchor": anchor, "scene": container,
                                  "geometry": _inline_geometry(d.get("style")),
                                  "id": (d.get("id") or "").strip(),
                                  "classes": (d.get("class") or "").split(),
                                  "hidden": _inline_hidden(d.get("style"))})
            if tag == "body":
                del self.stack[:]
                return
            if tag not in _PERSIST_VOID:
                self.stack.append((tag, container))

        def handle_starttag(self, tag, attrs):
            self._visit(tag, attrs)

        def handle_startendtag(self, tag, attrs):
            d = dict(attrs)
            own = self._scene_of(d)
            if own and own not in scenes:
                scenes.append(own)
                if (d.get("data-scene-entry") or "").strip().lower() == "cover":
                    covers.append(own)
            anchor = (d.get(PERSIST_CONTRACT) or "").strip()
            if anchor:
                instances.append({"anchor": anchor,
                                  "scene": own or (self.stack[-1][1] if self.stack else None),
                                  "geometry": _inline_geometry(d.get("style")),
                                  "id": (d.get("id") or "").strip(),
                                  "classes": (d.get("class") or "").split(),
                                  "hidden": _inline_hidden(d.get("style"))})

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break

    _P().feed(txt)
    return scenes, instances, covers


def measure_persistence(txt):
    """跨场常驻主图声明面：**不裁定**，永不抛异常，取不到即点名原因。

    存在理由（实测而非推演，2026-11-05 编号 26 落地前量的两条拓扑）：
      · 路线 A（常驻层放场景 div **之外**；仓内唯一先例是章级标识 `#chapter`）——
        `scene_patch_render.resolve_selector` 对它判 `selector-outside-scenes`，
        实证 `standard-system-explainer` 的 `scene_patch = {verdict: FULL,
        reason: html-outside-scenes-changed, attempts: 5, hits: 0}`（该项目累计 6 次全量渲染）。
      · 路线 B（同一基形在每场各画一份、位置一致，内容在其上生长）——同一选择器解析到
        **多场集合且无 cause**；在 `dual-cavity-wet-tile` 上注入三场基形后四条静态门禁
        （element_reveal／scene_insert／chapter_bar／gsap_timing）前后读数逐字一致＝零误杀。
    **所以"常驻"本身不贵，贵的是放错拓扑**；本面量的是写法有没有真兑现。

    几何按**内联字面**比较，不做等价求解：`inset:150px 96px` 与 `left:96px;top:150px` 会判成
    不一致。取舍是偏保守（宁可多报一处"去看看"，也不把两种写法猜成同一几何）。

    `declared_instances = 0` 是**合法读数**（这片没有常驻主图），与"解析不出"（`untested`）
    两向不得互混。`largest_anchor_scene_share` 只报事实（某锚覆盖了多少比例的场次）；
    增量资格那条规则住 `scene_patch_render.py:895`，本面不复述也不裁定。
    """
    try:
        scenes, instances, covers = _scan_anchors(txt)
    except BaseException as e:                          # noqa: BLE001
        return {"untested": f"常驻锚扫描异常（{type(e).__name__}: {str(e)[:60]}）"}
    # 分母含封面容器（封面也是场景 div，只是通常没有 T 条目）。2026-11-05 复核批实测：
    # 74 份里 31 份的 DOM 场数＝T 块场数＋1，即主面那句「9 场」与本面「/10 场」本就差一枚封面。
    # 不做条件剔除（"有封面标记才剔"＝一个键两种口径），改为把分母写进读数与文案：
    # 两腿分母不同必须可见，但口径只有一条。
    if not scenes:
        return {"untested": "未识别到任何场景容器（既无 data-scene-id 也无 id=\"sceneN\"）——"
                            "本面不猜结构，不得把「没识别出」读成「没有常驻锚」"}
    inside = [i for i in instances if i["scene"] in scenes]
    outside = [i for i in instances if i["scene"] not in scenes]
    by_anchor = {}
    for i in inside:
        by_anchor.setdefault(i["anchor"], []).append(i)

    divergent, unchecked = [], []
    for anchor, lst in sorted(by_anchor.items()):
        geoms = [x["geometry"] for x in lst]
        # 任一实例缺内联几何 → **整锚判无从判定**，不得拿在场的那几份算一致性：
        # 旧写法用 `if g` 把空几何滤掉后再比集合，缺位者既不进 divergent 也不进 unchecked，
        # 等于"取数不齐按 0 计"（narration_pause_precheck 同判据：不得由跳过读成干净）。
        if any(not g for g in geoms):
            unchecked.append(anchor)
            continue
        if len({tuple(sorted(g.items())) for g in geoms}) > 1:
            divergent.append({"anchor": anchor, "scenes": [x["scene"] for x in lst],
                              "variants": len({tuple(sorted(g.items())) for g in geoms})})

    order = {sid: i for i, sid in enumerate(scenes)}
    longest_run = 0
    for lst in by_anchor.values():
        seq = sorted({x["scene"] for x in lst}, key=lambda s: order[s])
        run = best = 1 if seq else 0
        for a, b in zip(seq, seq[1:]):
            run = run + 1 if order[b] == order[a] + 1 else 1
            best = max(best, run)
        longest_run = max(longest_run, best)

    covered = {x["scene"] for x in inside}
    # 隐藏计数两条：raw＝内联请求了初始隐藏；unrevealed＝且任何语句目标串里都找不到它的记号。
    hidden = [x for x in inside if x["hidden"]]
    sels = _statement_target_selectors(txt)
    joined = "\n".join(sels)
    unrevealed = [x for x in hidden
                  if not (x["id"] and ("#" + x["id"]) in joined)
                  and not any(("#" + c) in joined or ("." + c) in joined for c in x["classes"])]
    n = float(len(scenes))
    out = {"caliber": dict(PERSIST_CALIBER),
           "scene_elements": len(scenes),
           "cover_containers": len(covers),
           "declared_instances": len(instances),
           "anchors_distinct": len(by_anchor),
           "scenes_with_anchor": len(covered),
           "share_scenes_with_anchor": round(len(covered) / n, 3),
           "outside_scene_instances": len(outside),
           "largest_anchor_scene_share": round(
               max((len({x["scene"] for x in lst}) for lst in by_anchor.values()), default=0) / n, 3),
           "max_consecutive_scenes_with_same_anchor": longest_run,
           "hidden_instances": len(hidden),
           "hidden_instances_without_entrance_target": len(unrevealed),
           "hidden_instances_detail": [{"anchor": x["anchor"], "scene": x["scene"],
                                        "id": x["id"] or None} for x in unrevealed],
           "anchors_geometry_divergent": divergent,
           "anchors_geometry_unchecked": unchecked}
    if unrevealed:
        out["hidden_note"] = (f"{len(unrevealed)} 处基形内联请求初始隐藏且找不到任何入场语句目标＝"
                              "渲染里全程不可见（2026-10-13「内容永久不可见」同病灶）；"
                              "本面只点名，判据归 `preflight_check.check_element_reveal` 与渲染后抽帧")
    if outside:
        out["outside_scene_note"] = (f"{len(outside)} 个锚实例在场景容器之外＝路线 A 拓扑，"
                                     "按 resolve_selector 判据该类语句必判 FULL（只点名不裁定）")
    return out


def format_persist_line(p):
    """常驻锚子面那一行的**唯一生成点**（报告侧不得另抄一句）。"""
    if not isinstance(p, dict) or "untested" in p:
        _why = p.get("untested") if isinstance(p, dict) else None
        return f"｜常驻锚 UNTESTED — {_why or '未附归因（子面未点名原因）'}"
    return (f"｜常驻锚 声明 {p['declared_instances']} 处／{p['anchors_distinct']} 个锚名，"
            f"覆盖 {p['scenes_with_anchor']}/{p['scene_elements']} 场"
            f"（分母含封面容器 {p.get('cover_containers', '缺键')} 枚）"
            f"（占比 {p['share_scenes_with_anchor']}，单锚最大占比 {p['largest_anchor_scene_share']}，"
            f"同锚最长连续 {p['max_consecutive_scenes_with_same_anchor']} 场）"
            f"｜场景外实例 {p['outside_scene_instances']}｜基形初始隐藏 "
            f"{p.get('hidden_instances', '缺键')}（无任何入场目标 {p.get('hidden_instances_without_entrance_target', '缺键')}）｜几何不一致锚 "
            f"{len(p['anchors_geometry_divergent'])}、无从判定 {len(p['anchors_geometry_unchecked'])}")
