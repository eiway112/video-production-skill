#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内容事实台账门禁（内容溯源面，2026-11-07 借鉴批）。

存在理由：本仓门禁全量在"技术正确性"面（指纹、四态、交付槽位、媒体终检），
没有一条问"这句话凭什么这么说"。旁白里的参数、结论、年限只要字幕不溢出、
时长不超预算，就没有任何机算面能发现它无出处。

借鉴自 vibe-knowledge（andyhuo520/vibe-knowledge）的 `engine/scripts/check.py`
33 条内容判据中的**可机算**部分，逐条取舍：
- 保留：来源快照哈希、引文逐字命中、来源等级充分性、弱来源不得标 T1、
  旁白数字必须挂在事实上、非确定状态必须写说明；可选 visual_bindings 逐行对撞 HTML。
- 不借：44 套风格库（与装配式讲解的画风需求错位）、时长偏差 >15%（本仓
  `duration_budget` 是更严的绝对秒数门禁）、概念先修图（本仓无系列课模型）。

**边界（不得读宽）**：本件证明**记账与已声明画面绑定合规**，不证明内容为真——"引文在快照里"
不等于"引文支撑该结论"；visual_bindings 只证作者声明的值/列确实落在对应 HTML 行，不证绑定关系本身正确。

声明面：config 顶层 `content_facts`（相对项目目录的路径），或项目目录下的
`facts.json`。两者都不在位 → NOT_APPLICABLE 并点名，**不得读成检查通过**。
"""

import hashlib
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import _gate_status as gs

RULES_FILE = Path(__file__).resolve().parent.parent / "配置" / "config" / "quality" / "content_facts_rules.json"

# 返回码：0 跑过且干净（含 N/A）／1 违规／2 取数断链（未测）
EXIT_OK, EXIT_FAIL, EXIT_BROKEN = 0, 1, 2


class RulesBroken(RuntimeError):
    """判据文件或台账取数断链——不得按空规则继续裁定。"""


def load_rules(path=None) -> dict:
    p = Path(path) if path else RULES_FILE
    if not p.is_file():
        raise RulesBroken(f"判据文件缺失：{p}")
    try:
        rules = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise RulesBroken(f"判据文件不可解析：{p} ({e})")
    for key in ("tiers", "source_kinds", "fact_types", "statuses",
                "verified_min_sources", "number_claim_field", "visual_binding_field"):
        if key not in rules:
            raise RulesBroken(f"判据缺键：{key}")
    for key in ("tiers", "source_kinds", "fact_types", "statuses"):
        if not isinstance(rules[key], list) or not rules[key]:
            raise RulesBroken(f"判据 {key} 为空表（空表＝该腿永不成立）")
    vms = rules["verified_min_sources"]
    if not isinstance(vms, dict) or not all(
            isinstance(vms.get(k), int) and vms[k] > 0 for k in ("t1", "else_t2")):
        raise RulesBroken("verified_min_sources 缺 t1/else_t2 正整数")
    return rules


def _canon_number(raw) -> str:
    """数值规范化：0.50 / 0.5 / 5.0 / 05 归一到同一形态。不可解析返回原串。"""
    try:
        d = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return str(raw).strip()
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


def scan_numbers(text: str, ordinal_prefix_chars) -> list:
    """旁白里的独立数字（返回 [(原文, 规范化)]）。序数（第N）不是事实断言，排除。"""
    if not text:
        return []
    prefixes = "".join(ordinal_prefix_chars or [])
    out = []
    for m in re.finditer(r"\d+(?:\.\d+)?", text):
        start = m.start()
        if prefixes:
            j = start - 1
            while j >= 0 and text[j].isspace():   # "第 3 关" 仍是序数，空格不改变语义
                j -= 1
            if j >= 0 and text[j] in prefixes:
                continue
        out.append((m.group(0), _canon_number(m.group(0))))
    return out


def _norm_quote(s: str) -> str:
    """引文比对口径：去掉全部空白后逐字包含（换行/缩进差异不算改动）。"""
    return re.sub(r"\s+", "", s or "")


def _resolve_snapshot(facts_dir: Path, rel: str):
    """快照必须是台账目录下的相对路径；越界（.. / 绝对路径）判违规并点名。"""
    p = Path(rel)
    if p.is_absolute():
        return None, f"快照路径必须是台账目录下的相对路径，读到绝对路径：{rel}"
    target = (facts_dir / p).resolve()
    try:
        target.relative_to(facts_dir.resolve())
    except ValueError:
        return None, f"快照路径越出台账目录：{rel}"
    return target, None


def _binding_text_has(text: str, needle: str) -> bool:
    """绑定文本命中：数字按完整 token，中文/带单位串按去空白子串。"""
    spaced = re.sub(r"\s+", " ", str(text or "")).strip()
    compact = re.sub(r"\s+", "", spaced)
    want = re.sub(r"\s+", "", str(needle or ""))
    if not want:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", want):
        return re.search(rf"(?<![\d.]){re.escape(want)}(?![\d.])", spaced) is not None
    return want in compact


def _check_visual_bindings(ledger: dict, html_path, field: str, counts: dict):
    """把事实的可选 visual_bindings 与 HTML DOM 逐行对撞。

    只证“作者声明的画面绑定确实落在对应场景”，不证事实为真。未声明绑定的旧项目
    保持原行为；一旦声明而 HTML 取不回，则判 UNTESTED，不得按没有绑定放过。
    """
    bindings = []
    for fact in ledger.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        value = fact.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            return [f"事实 {fact.get('id', '?')} 的 {field} 必须是列表"], []
        bindings.extend((str(fact.get("id") or "?"), b) for b in value)
    counts["visual_bindings"] = len(bindings)
    if not bindings:
        return [], []

    if html_path is None:
        return [], [f"事实台账声明了 {field}，但预检未提供 HTML 路径，画面绑定无从核对"]
    path = Path(html_path)
    try:
        html = path.read_text(encoding="utf-8")
    except OSError as e:
        return [], [f"画面绑定取数断链：HTML 读不回 {path} ({e})"]

    from html.parser import HTMLParser

    class _Node:
        __slots__ = ("tag", "attrs", "parent", "children", "text")

        def __init__(self, tag, attrs, parent):
            self.tag = tag
            self.attrs = attrs
            self.parent = parent
            self.children = []
            self.text = ""

    class _Parser(HTMLParser):
        VOID = {"img", "br", "hr", "input", "meta", "link", "source", "area",
                "base", "col", "embed", "param", "track", "wbr"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.root = _Node("#root", {}, None)
            self.cur = self.root
            self.nodes = []

        def handle_starttag(self, tag, attrs):
            node = _Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
            self.cur.children.append(node)
            self.nodes.append(node)
            if tag not in self.VOID:
                self.cur = node

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in self.VOID:
                self.cur = self.cur.parent

        def handle_endtag(self, tag):
            node = self.cur
            while node is not self.root:
                if node.tag == tag:
                    self.cur = node.parent
                    return
                node = node.parent

        def handle_data(self, data):
            if self.cur.tag not in ("script", "style"):
                self.cur.text += data

    parser = _Parser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as e:
        return [], [f"画面绑定取数断链：HTML 解析失败 {path.name} ({e})"]

    try:
        from cue_resolve import normalize_scene_id
    except ImportError as e:
        return [], [f"画面绑定取数断链：场景 id 归一无单源 ({e})"]

    def descendants(node):
        for child in node.children:
            yield child
            yield from descendants(child)

    def node_text(node):
        parts = [node.text] + [node_text(child) for child in node.children]
        return " ".join(p for p in parts if p)

    violations = []
    for fid, binding in bindings:
        if not isinstance(binding, dict):
            violations.append(f"事实 {fid} 的画面绑定不是对象")
            continue
        sid = str(binding.get("scene_id") or "").strip()
        rows = binding.get("rows")
        if not sid or not isinstance(rows, list) or not rows:
            violations.append(f"事实 {fid} 的画面绑定缺 scene_id 或非空 rows")
            continue
        wanted = normalize_scene_id(sid)
        roots = [n for n in parser.nodes
                 if "data-scene-id" in n.attrs
                 and normalize_scene_id(n.attrs.get("data-scene-id")) == wanted]
        if len(roots) != 1:
            violations.append(f"事实 {fid} 的画面绑定场景 {sid} 在 HTML 中命中 {len(roots)} 个根")
            continue
        scene = roots[0]
        for row in rows:
            counts["visual_rows"] += 1
            if not isinstance(row, dict):
                violations.append(f"事实 {fid} 的绑定行不是对象")
                continue
            key = str(row.get("key") or "").strip()
            contains = row.get("contains")
            if not key or not isinstance(contains, list) or not contains:
                violations.append(f"事实 {fid} 的绑定行缺 key 或非空 contains")
                continue
            by_id = str(row.get("id") or "").strip()
            container_id = str(row.get("container_id") or "").strip()
            row_class = str(row.get("row_class") or "").strip()
            if bool(by_id) + bool(container_id and row_class) != 1:
                violations.append(f"事实 {fid} 的绑定行须且只须用 id 或 container_id+row_class 定位")
                continue
            if by_id:
                candidates = [n for n in descendants(scene) if n.attrs.get("id") == by_id]
            else:
                containers = [n for n in descendants(scene) if n.attrs.get("id") == container_id]
                candidates = [n for c in containers for n in descendants(c)
                              if row_class in (n.attrs.get("class") or "").split()
                              and _binding_text_has(node_text(n), key)]
            if len(candidates) != 1:
                violations.append(
                    f"事实 {fid} 的绑定行 {key} 在场景 {sid} 中命中 {len(candidates)} 个元素"
                    f"（定位 {by_id or container_id + '.' + row_class}）")
                continue
            text = node_text(candidates[0])
            missing = [str(v) for v in contains if not _binding_text_has(text, str(v))]
            if not _binding_text_has(text, key) or missing:
                violations.append(
                    f"事实 {fid} 的场景 {sid} 绑定行 {key} 与 HTML 不一致"
                    f"（缺 key 或值 {missing or '—'}；实读 {re.sub(chr(10), ' ', text)[:120]}）")
            else:
                counts["visual_rows_verified"] += 1
    return violations, []


def check(facts_path: Path, narration_scenes, rules: dict, html_path=None) -> dict:
    """裁定入口。narration_scenes: [(scene_id, narration_text)]；html_path 供视觉绑定核对。

    返回 {status, violations[], untested[], counts{}}；status 用 _gate_status 四态。
    """
    violations, untested = [], []
    counts = {"sources": 0, "facts": 0, "snapshots_verified": 0,
              "quotes_verified": 0, "numbers_in_narration": 0,
              "numbers_sourced": 0, "visual_bindings": 0,
              "visual_rows": 0, "visual_rows_verified": 0}
    facts_path = Path(facts_path)
    if not facts_path.is_file():
        return {"status": gs.NOT_APPLICABLE, "violations": [],
                "untested": [f"未声明事实台账：{facts_path.name} 不在位"], "counts": counts}

    try:
        ledger = json.loads(facts_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": gs.UNTESTED, "violations": [],
                "untested": [f"台账不可解析：{facts_path} ({e})"], "counts": counts}
    if not isinstance(ledger, dict):
        return {"status": gs.UNTESTED, "violations": [],
                "untested": [f"台账顶层必须是对象：{facts_path}"], "counts": counts}

    facts_dir = facts_path.parent
    sources = ledger.get("sources")
    facts = ledger.get("facts")
    if not isinstance(sources, list) or not sources:
        violations.append("台账 sources 缺失或为空——没有来源就没有可核查的事实")
        sources = []
    if not isinstance(facts, list) or not facts:
        violations.append("台账 facts 缺失或为空")
        facts = []
    counts["sources"], counts["facts"] = len(sources), len(facts)

    # —— 来源：id 唯一 / 等级合法 / 弱来源封顶 / 快照在盘且哈希一致 ——
    src_by_id = {}
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            violations.append(f"sources[{i}] 不是对象")
            continue
        sid = str(s.get("id") or "").strip()
        if not sid:
            violations.append(f"sources[{i}] 缺 id")
            continue
        if sid in src_by_id:
            violations.append(f"来源 id 重复：{sid}")
            continue
        tier = s.get("tier")
        if tier not in rules["tiers"]:
            violations.append(f"来源 {sid} 的 tier={tier!r} 不在 {rules['tiers']} 内")
        kind = s.get("kind")
        if kind not in rules["source_kinds"]:
            violations.append(f"来源 {sid} 的 kind={kind!r} 不在 {rules['source_kinds']} 内")
        cap = (rules.get("kind_max_tier") or {}).get(str(kind))
        if cap and tier in rules["tiers"] and rules["tiers"].index(tier) < rules["tiers"].index(cap):
            violations.append(f"来源 {sid} 是 {kind} 类，不得标 {tier}（上限 {cap}）")
        snap = s.get("snapshot")
        if not isinstance(snap, str) or not snap.strip():
            violations.append(f"来源 {sid} 缺 snapshot（快照是『出处可复查』的唯一实物）")
        else:
            target, err = _resolve_snapshot(facts_dir, snap)
            if err:
                violations.append(f"来源 {sid}：{err}")
            elif not target.is_file():
                violations.append(f"来源 {sid} 的快照不在位：{snap}")
            else:
                raw = target.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                if str(s.get("sha256") or "").strip().lower() != digest:
                    violations.append(
                        f"来源 {sid} 的快照哈希不符（记录 {str(s.get('sha256'))[:12]}…，"
                        f"实测 {digest[:12]}…）——证据被改过或登记错件")
                else:
                    counts["snapshots_verified"] += 1
                    s["_text"] = raw.decode("utf-8", "replace")
        src_by_id[sid] = s

    # —— 事实：字段齐全 / 来源存在 / 等级充分 / 引文命中 / 状态有说明 ——
    numbers_sourced = set()
    for i, f in enumerate(facts):
        if not isinstance(f, dict):
            violations.append(f"facts[{i}] 不是对象")
            continue
        fid = str(f.get("id") or "").strip()
        if not fid:
            violations.append(f"facts[{i}] 缺 id")
            continue
        if not str(f.get("claim") or "").strip():
            violations.append(f"事实 {fid} 缺 claim")
        ftype = f.get("type")
        if ftype not in rules["fact_types"]:
            violations.append(f"事实 {fid} 的 type={ftype!r} 不在 {rules['fact_types']} 内")
        status = f.get("status")
        if status not in rules["statuses"]:
            violations.append(f"事实 {fid} 的 status={status!r} 不在 {rules['statuses']} 内")

        refs = f.get("sources") if "sources" in f else (
            [f["source"]] if f.get("source") else [])
        refs = [str(r) for r in (refs or [])]
        if not refs:
            violations.append(f"事实 {fid} 未挂任何来源")
        used = []
        for rid in refs:
            if rid not in src_by_id:
                violations.append(f"事实 {fid} 引用了不存在的来源：{rid}")
            else:
                used.append(src_by_id[rid])

        # 等级充分性：verified 需 1×T1 或 2×T2；机理类必须有 T1
        if status == "verified" and used:
            t1 = sum(1 for s in used if s.get("tier") == "T1")
            t2 = sum(1 for s in used if s.get("tier") == "T2")
            req = rules["verified_min_sources"]
            if not (t1 >= req["t1"] or t2 >= req["else_t2"]):
                violations.append(
                    f"事实 {fid} 标 verified，但来源等级不足（T1×{t1}、T2×{t2}；"
                    f"需 T1×{req['t1']} 或 T2×{req['else_t2']}）")
            if ftype in rules.get("t1_required_types", []) and t1 < 1:
                violations.append(f"事实 {fid} 是 {ftype} 类断言，verified 必须有 T1 来源")

        if status in rules.get("statuses_requiring_notes", []) \
                and not str(f.get("notes") or "").strip():
            violations.append(f"事实 {fid} 状态为 {status}，必须写 notes 说明边界")

        quote = f.get("quote")
        if quote:
            qn = _norm_quote(str(quote))
            hit = any(qn and qn in _norm_quote(s.get("_text", "")) for s in used)
            if hit:
                counts["quotes_verified"] += 1
            else:
                violations.append(f"事实 {fid} 的引文在其来源快照里找不到：{str(quote)[:40]}…")

        for n in (f.get(rules["number_claim_field"]) or []):
            numbers_sourced.add(_canon_number(n))

    visual_violations, visual_untested = _check_visual_bindings(
        ledger, html_path, rules.get("visual_binding_field") or "visual_bindings", counts)
    violations.extend(visual_violations)
    untested.extend(visual_untested)

    # —— 旁白数字必须挂事实（唯一能自动圈出"这句没人给出处"的腿）——
    prefixes = rules.get("ordinal_prefix_chars") or []
    missing = []
    for scene_id, text in narration_scenes or []:
        for raw, canon in scan_numbers(str(text or ""), prefixes):
            counts["numbers_in_narration"] += 1
            if canon in numbers_sourced:
                counts["numbers_sourced"] += 1
            else:
                missing.append((scene_id, raw))
    for scene_id, raw in missing:
        violations.append(f"场 {scene_id} 的旁白数字 {raw} 没有挂在任何事实上")

    if violations:
        status = gs.FAIL
    elif untested:
        status = gs.UNTESTED
    else:
        status = gs.PASS
    return {"status": status, "violations": violations,
            "untested": untested, "counts": counts}


def _narration_from_config(cfg: dict):
    scenes = (cfg or {}).get("scenes") or []
    return [(str((s or {}).get("scene_id")), str((s or {}).get("narration") or ""))
            for s in scenes if isinstance(s, dict)]


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="内容事实台账门禁")
    ap.add_argument("--facts", required=True, help="facts.json 路径")
    ap.add_argument("--config", default=None, help="项目 config（读 scenes[].narration）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 读数")
    args = ap.parse_args(argv)

    try:
        rules = load_rules()
    except RulesBroken as e:
        print(f"[BROKEN] {e}")
        return EXIT_BROKEN

    scenes = []
    if args.config:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
        scenes = _narration_from_config(cfg)

    res = check(Path(args.facts), scenes, rules)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"[{res['status']}] counts={res['counts']}")
        for v in res["violations"]:
            print(f"  ✗ {v}")
        for u in res["untested"]:
            print(f"  ? {u}")
    return {"PASS": EXIT_OK, "NOT_APPLICABLE": EXIT_OK,
            "FAIL": EXIT_FAIL, "UNTESTED": EXIT_BROKEN}.get(res["status"], EXIT_BROKEN)


if __name__ == "__main__":
    sys.exit(main())
