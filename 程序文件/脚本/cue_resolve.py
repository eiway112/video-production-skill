# -*- coding: utf-8 -*-
"""词级 cue 解析器 —— 把"说到哪个词"解析成"场内相对第几秒"（2026-10-12 词级 cue 批）。

存在理由
--------
参考片的"说到哪个构件、哪个才亮"需要**词级**时刻，而我们此前只把句级时间戳交出去：
`_forced_align._align_char_times` 内部本就逐发音字符算出 (start, end, matched)，
旧实现切完句即丢弃。本模块是那份数据唯一的消费入口，职责只有一件事——
**声明（词）× 实测（字符流）→ 场内相对秒 + 归因**，不写 HTML、不起动画、不做裁定之外的判断。

四条判据（每条都对应一种会静默出错的形态）
------------------------------------------
1. **只读记录，绝不自跑对齐**。句级与字符级时刻必须取自同一次对齐输出——
   实测（`_取证/20261012/probe_chars_prompt_leg.py`、`probe_jitter_once.py`）：同一份 wav、
   同一份分段，重放与 2026-10-11 记录逐场差 ±0.02~0.42s，且 `OMP_NUM_THREADS=1` 一跑即再变，
   即对齐读数随计算线程环境移动。自跑对齐＝让 cue 时刻脱离已交付字幕的时刻基准，
   动画会在"没说这个词"的时点亮。故本模块**不导入对齐函数**，只吃 manifest 记录。
2. **插值字符不得当实测**。未匹配字符的时间是在左右锚点间线性均分造出来的
   （`_forced_align.py` 的 166-186 行），`matched` 旗标把它标了出来。落在插值字符上的 cue
   一律降级为该 cue 所属句的**句级锚点**并 WARN 点名——句级起口是实测值，宁可让元素早亮，
   也不让它亮在虚构时刻上。
3. **两向 fail-loud**（与 `bgm_source`／`narration_source` 的"指针找不到即报错、禁止静默回退"
   同判据）：①声明的词经归一化后在该场发音字符流里**不存在** → FAIL，归因写"这条 cue
   永远不会亮"；②归一化后为空（纯标点/空白）→ FAIL。这两种都是确定性的声明缺陷，
   不是"没测成"，按 `_gate_status` 的定义属 FAIL 而非 UNTESTED。
   注：开批 brief §2.3 原写"判 UNTESTED"，此处按四态词汇的定义改判 FAIL 并留痕原因——
   UNTESTED 留给"无从裁定"，而"词不在流里"是测出了违规。
4. **旧记录缺字符级节属设计内状态**，判 NOT_APPLICABLE 并走句级锚点，不重绑 30 份历史对齐
   （重对齐会移动句级时刻，见第 1 条实测；那是"改判据＋动字幕"的最贵一类）。

状态词汇沿用 `_gate_status`（PASS/FAIL/UNTESTED/NOT_APPLICABLE），不另立同义名。
一条判据只在这里写一次：HTML 写入侧、preflight 契约校验侧都调本模块，不得各自复述规则。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    from _gate_status import (FAIL, NOT_APPLICABLE, PASS, UNTESTED, verdict)
except ImportError:  # 允许以脚本目录之外的 CWD 直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_status import (FAIL, NOT_APPLICABLE, PASS, UNTESTED, verdict)

import _forced_align as _fa  # 只用它的归一化：cue 词必须与对齐基准同一套字符口径

ANCHOR_CHAR = "char"          # 实测字符起口
ANCHOR_SENTENCE = "sentence"   # 句级起口（插值降级或旧记录）


def normalize(text: str) -> str:
    """与对齐基准逐字同源的归一化（只留发音字符并小写）。

    刻意转发 `_forced_align._normalize_chars` 而不在本模块重写一份：cue 是在
    **那条**字符流里定位，两套归一化即两套索引，偏移一位就会把动画配到别的词上。
    """
    return _fa._normalize_chars(text or "")[0]


def _spans_of_sentences(sentences, norm_len):
    """句级索引区间——转发 `_forced_align.segment_char_offsets`，本模块不维护第二套累加。

    长度对不上即返回 None：记录里的 norm 由当轮分段拼接而成，若分段与字符流已经不同长，
    句级定位就无从成立——此时按 UNTESTED 点名，不得猜一个偏移量继续。
    """
    spans = _fa.segment_char_offsets([s.get("text", "") for s in (sentences or [])])
    if sentences and sum(b - a for a, b in spans) != norm_len:
        return None
    return spans


def resolve_cue(phrase, *, norm=None, chars=None, sentences=None):
    """单条 cue 声明 → {status, anchor, t, 归因…}。

    Args:
        phrase: 作者写在 `data-cue-at` 里的词/短语（原文，不必预先归一化）。
        norm / chars / sentences: **同一场**的 manifest 记录节。
            chars 为 [[rel_start, rel_end, matched01], ...]，与 norm 逐字符同长。
    """
    out = {"cue": phrase, "status": PASS, "anchor": None, "t": None,
           "warnings": [], "occurrences": 0, "span": None,
           "matched_ratio": None, "sentence_index": None, "reason": ""}

    needle = normalize(phrase)
    if not needle:
        out.update(status=FAIL, anchor=ANCHOR_CHAR,
                   reason="cue 声明归一化后没有发音字符（纯标点/空白），无法在任何字符流里定位")
        return out

    has_char_stream = bool(norm) and bool(chars) and len(chars) == len(norm)

    # ── 判据 4：旧记录（v3 时代）没有字符级节 —— 设计内**不适用**，不是损坏。
    #    此时仍能从分段文本判定"词在不在本场旁白"，故 fail-loud ① 照常生效；
    #    但不得报"记录已损坏"，那会把缺键读成半截记录（两种归因要分开）。
    if not has_char_stream:
        per_sent = [(k, normalize((sentences or [])[k].get("text", "")))
                    for k in range(len(sentences or []))]
        hits = [k for k, s in per_sent if s.find(needle) != -1]
        if not hits:
            out.update(status=FAIL,
                       reason="cue 词不在该场旁白里——这条 cue 永远不会亮")
            return out
        if len(hits) > 1:
            out["warnings"].append(
                f"该词在本场 {len(hits)} 个句子出现，取第 {hits[0]+1} 句；"
                "要消歧请写更长的短语，本模块不另设序号语法")
        si = hits[0]
        out.update(status=NOT_APPLICABLE, sentence_index=si, occurrences=len(hits),
                   anchor=ANCHOR_SENTENCE,
                   t=round(float(sentences[si].get("rel_start", 0.0)), 3),
                   reason="本场记录无字符级时间流（旧对齐记录），已降级为句级锚点")
        return out

    spans = _spans_of_sentences(sentences, len(norm or ""))
    if spans is None:
        out.update(status=UNTESTED,
                   reason="分段与字符流长度不匹配，句级锚点无从定位（记录已损坏或跨代混用）")
        return out

    idxs = []
    start = norm.find(needle)
    while start != -1:
        idxs.append(start)
        start = norm.find(needle, start + len(needle))
    out["occurrences"] = len(idxs)
    if not idxs:
        # fail-loud ①：静默跳过＝元素永不亮，而"永不亮"在成片上表现为"少一个动画"，
        # 属只能靠人眼事后发现的缺陷，故在解析期即判违规并点名。
        out.update(status=FAIL,
                   reason="cue 词不在该场旁白的发音字符流里——这条 cue 永远不会亮")
        return out
    if len(idxs) > 1:
        out["warnings"].append(
            f"该词在本场出现 {len(idxs)} 次，取首次（位置 {idxs[0]}）；"
            "要消歧请写更长的短语，本模块不另设序号语法")

    i = idxs[0]
    j = i + len(needle)
    si = next((k for k, (a, b) in enumerate(spans) if a <= i < b), None)
    out["sentence_index"] = si
    out["span"] = [i, j]

    def _sentence_time():
        if si is None or not sentences:
            return None
        return round(float(sentences[si].get("rel_start", 0.0)), 3)

    window = chars[i:j]
    flags = [int(c[2]) for c in window]
    ratio = round(sum(flags) / len(flags), 3) if flags else None
    out["matched_ratio"] = ratio
    if ratio != 1.0:
        un = [i + k for k, f in enumerate(flags) if f == 0]
        out.update(anchor=ANCHOR_SENTENCE, t=_sentence_time(),
                   reason=f"覆盖字符含 {len(un)} 枚插值时刻（索引 {un[:6]}），"
                          "不得用虚构时间驱动画面，已降级句级锚点")
        out["warnings"].append("插值字符降级")
        return out

    t = round(float(window[0][0]), 3)
    # 与句级同真相的自检（真实音频 13/13 场已核，见交接 brief §1b 的 T3）：
    # 句首字符时间与句 rel_start 同源同值，若此处失配说明记录被跨代混写。
    if si is not None and i == spans[si][0] and sentences:
        sent_t = _sentence_time()
        if sent_t is not None and abs(sent_t - t) > 0.001:
            out.update(status=UNTESTED,
                       reason=f"句首字符时刻 {t} 与句级 {sent_t} 不同源，记录疑被混写")
            return out
    out.update(anchor=ANCHOR_CHAR, t=t)
    return out


def resolve_scene(scene_entry, phrases):
    """对一场批量解析；`scene_entry` 是 `_timeline_manifest.json` 的 scenes[] 元素。"""
    return [resolve_cue(p, norm=scene_entry.get("norm"), chars=scene_entry.get("chars"),
                        sentences=scene_entry.get("sentences")) for p in phrases]


def verdict_of(results):
    """整批 cue 的整体裁定（FAIL > UNTESTED > NOT_APPLICABLE 不计通过）。"""
    fails = [r for r in results if r["status"] == FAIL]
    untested = [r for r in results if r["status"] == UNTESTED]
    v = verdict(fails, untested)
    if v == PASS and all(r["status"] == NOT_APPLICABLE for r in results) and results:
        return NOT_APPLICABLE, fails, untested
    return v, fails, untested


def format_cue_line(r):
    """单条 cue 的报告行——唯一生成点，消费方不得另拼一份文案。"""
    bits = [f"cue「{r['cue']}」", f"状态={r['status']}"]
    if r.get("anchor"):
        bits.append(f"锚点={r['anchor']}")
    bits.append(f"t={r['t'] if r['t'] is not None else '—'}")
    if r.get("matched_ratio") is not None:
        bits.append(f"实测字符占比={r['matched_ratio']}")
    if r.get("warnings"):
        bits.append("警告: " + "；".join(r["warnings"]))
    if r.get("reason"):
        bits.append(r["reason"])
    return "  [ " + " | ".join(bits) + " ]"


def format_cue_map(entries):
    """HTML 侧定界块内容——唯一生成点：key → 场内相对秒。

    `entries` 是 [(key, t_rel), ...]；只接受已解析成功（t 非 None）的对象，
    调用方负责把 FAIL/UNTESTED 的声明在写入前拦下（preflight 侧），此处不静默丢。
    """
    lines = ["/* CUE-MAP-START — 由 timeline 步按音频实测再生成，勿手改 */", "var C = {"]
    for key, t in entries:
        lines.append(f"  {json.dumps(str(key), ensure_ascii=False)}: {round(float(t), 3)},")
    lines.append("};")
    lines.append("/* CUE-MAP-END */")
    return "\n".join(lines)


def load_manifest(path):
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {str(s.get("scene_id")): s for s in data.get("scenes", [])}


# ── 声明语法（唯一持有者）────────────────────────────────────────────────
# 词级 cue 声明只有一种写法：场景 div 内的元素属性 `data-cue-at="旁白里的词"`。
# 解析也只有一个函数——preflight 的契约校验与 timeline 的写入侧共用它，
# 两处各写一份正则即两套语法（A06 同族：改了一处，另一处照常"读不到"而不报错）。
_CUE_ATTR_RE = re.compile(r'data-cue-at\s*=\s*"([^"]*)"')
_SCENE_DIV_RE = re.compile(r'data-scene-id\s*=\s*"(s?\d+)"')


def scan_cue_declarations(html_text):
    """返回 [{"scene_id": "s3", "cue": "耐火极限", "at": 偏移}]，按文档顺序。

    场景归属＝该属性之前**最近**的一个 `data-scene-id`（模板里场景 div 平铺，
    元素嵌在其内）。找不到归属即返回 scene_id=None，由调用方按"声明写了但不知属于哪场"
    判 FAIL——不得静默丢。
    """
    out = []
    scene_marks = [(m.start(), m.group(1)) for m in _SCENE_DIV_RE.finditer(html_text)]
    for m in _CUE_ATTR_RE.finditer(html_text):
        owner = None
        for pos, sid in scene_marks:
            if pos < m.start():
                owner = sid
            else:
                break
        cue = m.group(1).strip()
        out.append({"scene_id": owner, "cue": cue, "at": m.start()})
    return out


def format_declaration_error(d):
    """声明面（扫描侧）的失败文案——唯一生成点，preflight 与写入侧不得各拼一份。"""
    if d.get("scene_id") is None:
        return f"cue 声明「{d.get('cue', '')}」不在任何带 data-scene-id 的场景 div 内，无从归属"
    return f"场景 {d['scene_id']} 的 cue 声明「{d.get('cue', '')}」解析失败：{d.get('reason', '')}"


def normalize_scene_id(sid) -> str:
    """场景编号归一为纯数字串（HTML 写 `s3`，manifest/narration 存 `3` 或 3）。

    唯一持有者：写入侧查 manifest、preflight 侧查旁白源都走这里。各处自写
    `lstrip('s')` 就是两套口径——一套漏了 `str()` 时查表静默落空，落空被读成
    "该场没有 cue"而不是"没查到"（A06 同族）。
    """
    return "" if sid is None else str(sid).strip().lstrip("sS")


# ── 写入面（HTML 定界块的唯一改写者）──────────────────────────────────────
_BLOCK_START = "/* CUE-MAP-START"
_BLOCK_END = "/* CUE-MAP-END */"


def _find_script_insert_pos(html_text):
    """GSAP timeline 所在 script 的**体首**位置；找不到返回 None。

    位置必须是"在那个 script 里面、且排在任何 `T.sN + C[...]` 之前"：

    * 排在后面 → 作者写的引用在求值时 C 还是 undefined，动画静默落在 0 秒；
    * 排在 script 标签**外面**（本函数旧形态返回 `m.start()`）→ 块根本不是 JS，
      而是被当成正文文本渲染出来：`var C = {…}` 变成画面上的一行字，同时页面里
      没有任何 C 绑定，引用它的 tween 直接抛 ReferenceError。
      该形态由 2026-10-12 真实副本端到端探针取得（`_取证/20261012/probe_cue_value_e2e.py`
      首跑：dump-dom 里 `window.C` 读不到、块出现在 `</div>` 与 `<script>` 之间），
      夹具面（B8 只验"有没有 script 可放"）看不见它——所以两处都补了断言。

    宁可判未测也不猜一个位置：找不到含 `timeline(` 的 script 即返回 None。
    """
    for m in re.finditer(r'<script\b[^>]*>', html_text):
        tail = html_text[m.end():m.end() + 200000]
        close = tail.find('</script>')
        if close == -1:
            continue
        if 'timeline(' in tail[:close]:
            return m.end()
    return None


def apply_cue_block(html_text, resolved):
    """把解析成功的 cue 写进 HTML 定界块；返回 (new_html, changed, problems)。

    `resolved` = [(scene_id, cue, resolution_dict), ...]（调用方负责逐条解析，本函数不重新解析）。

    三条规则：
      1. **无声明即零动作**：changed=False 且原文逐字节返回——这是"存量项目零字节变化"那条
         硬前置的实现形态；写成"总是输出一个空块"会让每个存量项目的 index.html 变字节，
         进而 render 步判 inputs-changed＝一次全量渲染。
      2. **整块再生成**：已存在的块按定界符整段替换，值直接来自本次解析，不做算术叠加。
      3. 只写解析成功的条目；任何 FAIL/UNTESTED 都由 problems 带回，调用方必须在**写盘之前**
         拦下（写一半＝交付一份 cue 永不亮的成片）。
    """
    problems = []
    entries = []
    seen = {}
    for sid, cue, r in resolved:
        status = r['status']
        if status in (PASS, NOT_APPLICABLE):
            if r.get('t') is None:
                problems.append(f"场景 {sid} 的 cue「{cue}」状态 {status} 但时刻为空，无从写入")
                continue
            key = f"{sid}:{cue}"
            t = float(r['t'])
            if key in seen:
                # 同一场里两个构件声明同一个词是**正常写法**（两处该同时亮）。
                # C 按词建键，故折叠成一条；时刻不同才说明记录被跨代混写。
                if abs(seen[key] - t) > 1e-6:
                    problems.append(f"同一场景的同一 cue「{cue}」解析出两个时刻 "
                                    f"（{seen[key]} / {t}）——记录不同源，无从取舍")
                continue
            seen[key] = t
            entries.append((key, t))
        else:
            # FAIL 与 UNTESTED 同权带回（2026-10-12 写入面探针实测：旧实现写作
            # `elif status != FAIL`，即**只有未测**才报问题，FAIL 被静默摘掉——
            # 表现是"作者写了 cue、块里少一条、元素永不亮、流水线全绿"，
            # 正是本函数 docstring 第 3 条禁止的半截块形态。摘掉一条 FAIL 与
            # 静默回退同判据，不得由"解析侧已返回 FAIL"推定"写入侧会拦"。）
            problems.append(format_declaration_error({**r, 'scene_id': sid, 'cue': cue}))
    if problems:
        return html_text, False, problems
    if not resolved:
        return html_text, False, []        # 零声明 → 零字节动作
    block = format_cue_map(entries)
    if _BLOCK_START in html_text:
        i = html_text.index(_BLOCK_START)
        j = html_text.find(_BLOCK_END, i)
        if j == -1:
            return html_text, False, ["CUE-MAP 起始定界在位而尾定界缺失——半截块无从整块替换"]
        j += len(_BLOCK_END)
        return html_text[:i] + block + html_text[j:], True, []
    pos = _find_script_insert_pos(html_text)
    if pos is None:
        return html_text, False, ["HTML 里找不到构造 GSAP timeline 的 script，cue 块无处安放"
                                  "（放在其后会让 C 在被求值时是 undefined）"]
    return html_text[:pos] + block + "\n" + html_text[pos:], True, []


def resolve_and_apply(html_text, scenes_by_id):
    """声明 → 解析 → 写入的一体化入口（timeline 步与 preflight 共用同一链）。

    `scenes_by_id`：{scene_id → manifest 的 scenes[] 元素}；id 允许带或不带前导 's'
    （HTML 用 `s3`，manifest 用 `3`——两侧命名不同是历史事实，此处归一而不是要求改两侧）。
    返回 (new_html, changed, problems)。
    """
    def _lookup(sid):
        key = str(sid) if sid is not None else ""
        for cand in (normalize_scene_id(sid), key):
            if cand and cand in scenes_by_id:
                return scenes_by_id[cand]
        return None

    decls = scan_cue_declarations(html_text)
    resolved = []
    problems = []
    for d in decls:
        entry = _lookup(d['scene_id'])
        if entry is None:
            # 声明在位但该场没有时间记录：无从裁定，不得静默当"没有 cue"
            problems.append(f"场景 {d['scene_id']} 声明了 cue「{d['cue']}」"
                            "但该场在时间记录里没有条目，解析侧无从取数")
            continue
        r = resolve_cue(d['cue'], norm=entry.get('norm'), chars=entry.get('chars'),
                        sentences=entry.get('sentences'))
        resolved.append((d['scene_id'], d['cue'], r))
    if problems:
        # 查表侧的问题必须在此带回，不得交给 apply_cue_block 顺手发现：那里只看得见
        # `resolved`，而这类声明根本没进 resolved（记录缺失＝无从解析）。旧实现让这一行
        # 缺席，实测形态是"manifest 整份缺失 → 退出码 0、HTML 不写块、全绿"
        # （2026-10-12 写入面探针 q5），即把"没记录"读成"没有 cue"。
        return html_text, False, problems
    return apply_cue_block(html_text, resolved)


# ── 分镜侧预检（零 ASR、零渲染、零 TTS）─────────────────────────────────────
# 存在理由：preflight 的契约校验拿的是**已对齐的记录**，它答的是"这条 cue 能不能定位"；
# 而"这条 cue 值不值得挂"在写完旁白、花 TTS 与渲染钱**之前**就能算——只需要旁白文本本身。
# 实测（2026-10-12 agent-decision-map 首轮）：按"句子"直觉选词时 27 条里有 3 条段内偏移为 0
# （词级时刻＝句级时刻，挂 cue 等于没挂）、6 条预估差值 <1.6s，而按句子校验全部判"可定位"。
# 判据：**词级/句级差值的边界是字幕分段，不是句子**——故本函数用生产同一个分段函数取段。
def _cue_offset_threshold():
    """段内偏移可观测档的取数：权威源 video_quality_rules.json → cue_placement，取不回才兜底。

    2026-10-16 单源收敛：该阈值此前只住本模块常量，而换算率在三处各写一份
    （本行注释 4.0 字/秒、AGENTS.md 记实测回归 4.76 字/秒、踩坑清单 3.87–3.95）。
    阈值入配置后，常量降为"配置断链时的兜底"，且**兜底必须可见**（来源随读数一起
    暴露给 CUE_OFFSET_SOURCE，用例107 锁"值真来自配置文件而非调用点字面量"）。
    本预检面是 WARN 档（不裁定交付），故取不回按"回退＋点名"处理，不比照 text_fit
    那类承载门禁的节点走 fail-closed——两处失效面不同，混为一档会把提示性工具变成阻断。
    """
    fallback = 6
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "配置" / "config" / "quality" / "video_quality_rules.json"
        val = int(json.loads(p.read_text(encoding="utf-8"))["cue_placement"]["min_observable_offset_chars"])
        if val <= 0:
            raise ValueError("非正数")
        return val, "video_quality_rules.json → cue_placement.min_observable_offset_chars"
    except Exception as e:                                  # noqa: BLE001
        return fallback, f"兜底字面量 6（配置取不回：{type(e).__name__}: {str(e)[:60]}）"


MIN_OBSERVABLE_OFFSET_CHARS, CUE_OFFSET_SOURCE = _cue_offset_threshold()


def _production_segmenter():
    """返回生产在用的字幕分段函数；取不回即返回 None（调用方判 UNTESTED，不得另写一套切分）。

    刻意**延迟导入** `enhance_video_audio`：该模块顶层导入 media_qa_gate / _forced_align 等，
    cue_resolve 被 preflight 与 adjust_timeline 导入，模块级导入会把这条链变成全链依赖。
    """
    try:
        from enhance_video_audio import _split_text_to_segments
        return _split_text_to_segments
    except Exception as exc:                       # 依赖缺失/导入炸＝取数断链，不是"没有分段"
        print(f"  [CUE-PREVIEW] 分段函数取不回（{type(exc).__name__}: {exc}）")
        return None


def preview_cue_placement(cue, narration_text, *, segmenter=None):
    """单条 cue 在**旁白文本层**的落点预检 → {status, seg_index, seg_len, offset, occurrences}。

    只吃文本，不吃波形：给出的全是字符偏移与段号，绝不产出秒。秒要等真实对齐后由
    `resolve_cue` 读，本函数若报一个"预估秒"就会被当成解析归因（那是虚构时刻）。
    """
    out = {"cue": cue, "status": PASS, "seg_index": None, "seg_len": 0,
           "offset_in_seg": None, "occurrences": 0, "reason": "", "warnings": []}
    needle = normalize(cue)
    if not needle:
        out.update(status=FAIL, reason="cue 归一化后没有发音字符（纯标点/空白）")
        return out
    if not (narration_text or "").strip():
        out.update(status=FAIL, reason="该场没有旁白（封面/无解说场），cue 无从落点")
        return out

    if segmenter is None:
        segmenter = _production_segmenter()
    if segmenter is None:
        out.update(status=UNTESTED, reason="分段函数取不回，无从判定段内偏移（不得按整场文本猜）")
        return out

    stream = normalize(narration_text)
    idxs, p = [], stream.find(needle)
    while p != -1:
        idxs.append(p)
        p = stream.find(needle, p + len(needle))
    out["occurrences"] = len(idxs)
    if not idxs:
        out.update(status=FAIL, reason="cue 词不在该场旁白里——这条 cue 永远不会亮")
        return out
    if len(idxs) > 1:
        out["warnings"].append(f"该词在本场出现 {len(idxs)} 次，解析侧取首次；请写更长的短语消歧")

    # 段区间按**分段函数的输出**逐段归一化后累加，与 `resolve_cue` 里
    # `_spans_of_sentences`（转发 `_forced_align.segment_char_offsets`）同口径。
    spans, cur = [], 0
    for seg in segmenter(narration_text):
        n = len(normalize(seg))
        spans.append((cur, cur + n))
        cur += n
    if cur != len(stream):
        out.update(status=UNTESTED,
                   reason=f"分段拼接长度 {cur} 与整场字符流 {len(stream)} 不等，段内偏移无从判定")
        return out
    i = idxs[0]
    si = next((k for k, (a, b) in enumerate(spans) if a <= i < b), None)
    if si is None:
        out.update(status=UNTESTED, reason="字符位置落在分段区间之外")
        return out
    off = i - spans[si][0]
    out.update(seg_index=si, seg_len=spans[si][1] - spans[si][0], offset_in_seg=off)
    if off == 0:
        out.update(status=NOT_APPLICABLE,
                   reason="词落在所属分段的段首——词级时刻＝句级时刻，挂 cue 不改变任何时刻")
    elif off < MIN_OBSERVABLE_OFFSET_CHARS:
        out["warnings"].append(f"段内偏移仅 {off} 字（<{MIN_OBSERVABLE_OFFSET_CHARS}），"
                               "词级与句级差值不可观测，请把 cue 移到该段后部")
    return out


def format_placement_line(sid, r):
    """预检报告行——唯一生成点，CLI 与用例不得各拼一份。"""
    bits = [f"s{sid}", f"cue「{r['cue']}」", f"状态={r['status']}"]
    if r.get("seg_index") is not None:
        bits.append(f"段={r['seg_index']}/{r['seg_len']}字")
        bits.append(f"段内偏移={r['offset_in_seg']}")
    if r.get("occurrences"):
        bits.append(f"出现{r['occurrences']}次")
    if r.get("warnings"):
        bits.append("警告: " + "；".join(r["warnings"]))
    if r.get("reason"):
        bits.append(r["reason"])
    return "  [ " + " | ".join(bits) + " ]"


def main(argv=None):
    """作者侧自查 CLI：给定 manifest 与场次与 cue 词，打印解析归因（退出码随裁定）。

    两种取数面，各自答不同的问题，刻意不合并：
      * `--manifest`（默认）＝**解析归因**，拿真实对齐后的时刻，交付级证据；
      * `--narration`（+ 可选 `--html` 取声明）＝**落点预检**，只吃文本，零 ASR 零渲染，
        在花 TTS/渲染钱之前决定词该落在哪一句的哪个位置，输出不得当解析归因用。
    """
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Resolve word-level cues against the timeline manifest")
    ap.add_argument("--manifest", help="_timeline_manifest.json 路径（解析归因面）")
    ap.add_argument("--scene", help="scene_id（如 3）")
    ap.add_argument("--cue", action="append", help="cue 词，可重复")
    ap.add_argument("--narration", help="narration.json 路径（落点预检面，零 ASR 零渲染）")
    ap.add_argument("--html", help="index.html 路径；预检面缺 --cue 时从这里取 data-cue-at 声明")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.narration:
        return _main_placement_preview(a)
    if not (a.manifest and a.scene and a.cue):
        print("[FAIL] 解析归因面需要 --manifest、--scene 与至少一枚 --cue")
        return 2
    rec = load_manifest(a.manifest)
    entry = rec.get(str(a.scene))
    if entry is None:
        print(f"[FAIL] manifest 里没有 scene {a.scene}（现有：{sorted(rec)}）")
        return 2
    res = resolve_scene(entry, a.cue)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for r in res:
            print(format_cue_line(r))
    v, fails, untested = verdict_of(res)
    print(f"[CUE] {v} — FAIL {len(fails)} / UNTESTED {len(untested)} / 共 {len(res)} 条")
    return {PASS: 0, NOT_APPLICABLE: 0, UNTESTED: 2, FAIL: 1}[v]


def _main_placement_preview(a):
    """预检面入口：narration.json ×（HTML 声明或 --cue）→ 逐条落点裁定。"""
    scenes = json.loads(Path(a.narration).read_text(encoding="utf-8"))
    if isinstance(scenes, dict):
        scenes = scenes.get("scenes") or []
    by_id = {normalize_scene_id(s.get("scene_id")): (s.get("narration") or "") for s in scenes}
    seg_fn = _production_segmenter()
    if seg_fn is None:
        print("[CUE-PREVIEW] UNTESTED — 分段函数取不回，落点偏移无从判定")
        return 2
    targets = []
    if a.cue:
        sid = normalize_scene_id(a.scene)
        targets = [(sid, c) for c in a.cue]
    elif a.html:
        for d in scan_cue_declarations(Path(a.html).read_text(encoding="utf-8")):
            targets.append((normalize_scene_id(d["scene_id"]), d["cue"]))
    else:
        print("[FAIL] 预检面需要 --cue（配 --scene）或 --html（从声明取词）")
        return 2
    rows = []
    for sid, cue in targets:
        if sid not in by_id:
            rows.append({"cue": cue, "status": FAIL,
                         "reason": f"场景 {sid} 在 narration.json 里没有条目，无从取旁白"})
            continue
        rows.append(preview_cue_placement(cue, by_id[sid], segmenter=seg_fn))
    fails = [r for r in rows if r["status"] == FAIL]
    untested = [r for r in rows if r["status"] == UNTESTED]
    zero_gain = [r for r in rows if r["status"] == NOT_APPLICABLE]
    thin = [r for r in rows if r["status"] == PASS
            and any("段内偏移仅" in w for w in r.get("warnings") or [])]
    summary = (f"[CUE-PREVIEW] 共 {len(rows)} 条：不可定位 {len(fails)} / 无从判定 {len(untested)} / "
               f"段首零收益 {len(zero_gain)} / 差值偏弱 {len(thin)}")
    if a.json:
        # 机器可读态下 stdout 必须是**纯 JSON**：汇总行带 `[CUE-PREVIEW]` 前缀进 stdout 时，
        # 任何 `json.loads` 取数方都在末行炸 `Extra data`（本批 --json 首跑实测踩到）。
        # 汇总照常打，只是改打 stderr——不删信息面，只换通道。
        print(summary, file=sys.stderr)
        print(json.dumps([{"scene": s, **r} for s, r in zip([t[0] for t in targets], rows)],
                         ensure_ascii=False, indent=2))
    else:
        for sid, r in zip([t[0] for t in targets], rows):
            print(format_placement_line(sid, r))
        print(summary)
    if fails:
        return 1
    if untested:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

