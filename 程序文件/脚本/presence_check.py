"""内容在场判据（渲染前，零全量渲染）：每场"应有内容里整块从未出现"的死块点名。

存在理由（2026-11-06，由一次不合格交付换来）
------------------------------------------------------------------
`_修订01` 六维审计与终检全绿，成片里却有 60 处内容从未出现。7b 形态③
（`preflight_check.check_element_reveal`）补住了其中**一种**写法——「CSS 置 0 ＋ 只有
`tl.from()` 命中」；它判据静态、全仓 0 误杀，因此升为 ERROR。但同类形态是**开放集合**：
`display:none` 从未抬起、揭示语句的锚落在本场窗口之外、类名切换由永不触发的分支承担……
静态面枚举不完。本件量通用面：**把时间轴 seek 到本场各时刻，实测每个承载内容的元素到底
有没有出现过**，不枚举 tween 写法。

分工与既有先例一致（`instant_preview.measureTextFit`／`bar_overflow`）：**取数在页内 JS，
裁定在 Python**。JS 只交回「元素结构（父指针＋是否承载内容）＋该场各采样点的实测最小有效
不透明度」，阈值与死块归类全部在 `adjudicate_scene()` 里算——判据住在 JS 就不可在 CI 上取证
（回归套件只桩 Chrome 腿），判据住在 Python 才谈得上用例与变异。

判据对象＝"死块"，不是"缺席元素数"（这条是分两轮实测出来的，两轮都推翻过初判）
------------------------------------------------------------------
第一轮按「场末单点采样 ＋ 有效不透明度 < 阈」计占比，全仓 49 支可测项目里 6 支有硬缺席、
1 支占比 0.16。看着像缺陷分布，实际会把两支**已交付且用户验收通过**的项目判成病灶：
`acoustic-wall-structure-craft` 的 `#s6-f1/f2` 是「逐步浮现后即淡出」的序列标注，末点
当然不在场——那是设计。据此改两处，各自都是实测而非取舍：

  1. **整场多点采样**（每场 4~14 点，覆盖 [start+LEAD_IN, end−LEAD_OUT]）：元素取各点的
     **最大**有效不透明度——只要某一刻达到可见即算在场，整场从未抬起才算永不在场。
     改后该交付项目 0.155 → 死块 0。
  2. **判据对象收成"极大容器"**：某元素承载 ≥`MIN_BLOCK_CONTENT` 个内容后代、且它们**全部**
     永不在场，才算一块内容没出现。单条冗余标记（步骤行旁的 `✓`）承载数 1，结构上不入判——
     观众看到的是一行字旁少个角标，不是一片空白。这一收把「信息不在场」与「标记不在场」
     分开，且分法是结构性的，不靠枚举类名。
     同批踩到的坑（现由 Python 侧的 `content` 过滤在结构上消除）：JS 初版把非内容节点也按
     初始 `_dead=true` 累加，使一支**零缺席**的真项目实测报出 `div.sc 16/16`。

定档：只点名，不阻断。依据不是感觉而是全仓读数——46 支为 0 死块；3 支各 1 处，其中
其中两支是**已交付**项目（一支已 3 次登记交付）。
一条会当场判红既有验收通过交付的判据不能升为阻断（AGENTS 2026-10-10 常设裁定同向）。
升为 FAIL 的翻转前置只有一条：这 3 处经使用者逐条裁定「确为缺陷、修法不牵连其它场」，
同时 28 支「无 T 块声明」的项目补齐时间源口径（它们现在一律判 UNTESTED，看不见）。

四态与取数纪律
------------------------------------------------------------------
无 T 块／无 `data-duration`／探针无输出／时间轴驱动取不到 → UNTESTED 并逐条点名，
**不得由"没量到"读成"干净"**（A06 同族）；场景根取不到或窗口过窄 → 该场点名不判；
可测内容元素为 0 → NOT_APPLICABLE；其余一律出读数，退出码 0；UNTESTED 退出码 2。
本件**永不返回阻断码**。

登记面：刻意不进 render 步输入指纹（与 `_vacuum_precheck_ok` 同一理由——它不改渲染产物，
登记进去会让"改判据文案"这种操作作废一次全量渲染缓存，代价与收益完全不成比例）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import content_vacuum_precheck as cvp  # noqa: E402  chrome 定位与素材携带腿单源
from _gsap_time_utils import parse_t_block  # noqa: E402  T 块解析单源
import _gate_status as gs  # noqa: E402

# 有效不透明度低于此值即"不可见"。刻意贴近 0 而不是 0.05：全仓普查里 0<o<0.05 共 46 处，
# 全部是 ghost/水印这类刻意压暗的装饰层（`div.ghost o=0.014`），计进缺席即把设计读成缺陷。
INVISIBLE_MAX = 0.0005
# 承载 >=2 个内容元素才算"一块内容"；1 个是标记不是块（题旨见 docstring 第 2 条）。
MIN_BLOCK_CONTENT = 2
# 采样密度 2 点/秒，夹在 [4, 14]：低于 4 会漏掉短暂在场，高于 14 只是线性加时不加分辨力。
SAMPLES_PER_SECOND = 2
MIN_SAMPLES, MAX_SAMPLES = 4, 14
# 采样窗两端内缩：起点 +0.4s 避开入场前的空场帧，终点 −0.3s 避开转场淡出（退场锚在下一场起点）。
LEAD_IN, LEAD_OUT = 0.4, 0.3

PROBE_SCRIPT = r"""
<script>
(function () {
  function effOpaque(el) {
    var o = 1, n = el;
    while (n && n.nodeType === 1) {
      var s = getComputedStyle(n);
      if (s.display === 'none') return 0;
      if (s.visibility === 'hidden' || s.visibility === 'collapse') return 0;
      var v = parseFloat(s.opacity);
      if (isNaN(v)) v = 1;
      o *= v;
      if (o <= 0) return 0;
      n = n.parentElement;
    }
    return o;
  }
  function driver() {
    // 两腿与 content_vacuum_precheck 同形：全局 tl 优先，取不到回退注册表唯一条目。
    // 只认一条腿时，注册表另命名会静默不 seek，整场读数全成"从未出现"。
    var d = (typeof tl !== 'undefined' && tl && tl.seek) ? tl : null;
    if (!d) {
      var reg = window.__timelines || {};
      var ks = Object.keys(reg).filter(function (x) { return reg[x] && reg[x].seek; });
      if (ks.length === 1) d = reg[ks[0]];
    }
    return d;
  }
  function emit(obj) {
    var pre = document.createElement('pre');
    pre.id = '__presence_check';
    pre.textContent = '###PRESENCE###' + JSON.stringify(obj) + '###END###';
    document.documentElement.appendChild(pre);
  }
  function run() {
    var d = driver();
    if (!d) { emit({error: 'no_timeline_driver'}); return; }
    var rows = [];
    for (var i = 0; i < WINDOWS.length; i++) {
      var w = WINDOWS[i];
      var sc = document.querySelector('[data-scene-id="' + w.sid + '"]');
      if (!sc) { rows.push({scene: w.sid, error: 'scene-root-not-found'}); continue; }
      var span = w.end - w.start;
      if (!(span > LEAD_IN + LEAD_OUT)) {
        rows.push({scene: w.sid, error: 'window-too-narrow', span: span});
        continue;
      }
      var els = sc.querySelectorAll('*');
      for (var j = 0; j < els.length; j++) els[j]._pi = j;
      var nodes = [], peaks = [];
      for (var j = 0; j < els.length; j++) {
        var el = els[j], own = false;
        for (var c = 0; c < el.childNodes.length; c++) {
          var cn = el.childNodes[c];
          if (cn.nodeType === 3 && cn.textContent.trim()) { own = true; break; }
        }
        var tag = el.tagName.toLowerCase();
        var content = own || tag === 'img' || tag === 'svg' || tag === 'canvas';
        if (el.closest('[class*="subtitle-safe"]')) content = false;
        var par = el.parentElement;
        var p = (par && par !== sc && par._pi !== undefined) ? par._pi : -1;
        nodes.push({l: el.id ? '#' + el.id : tag + '.' + el.className,
                    c: content ? 1 : 0, p: p});
        peaks.push(0);
      }
      var k = Math.max(MIN_S, Math.min(MAX_S, Math.round(span * HZ)));
      for (var q = 0; q < k; q++) {
        var t = w.start + LEAD_IN + (span - LEAD_IN - LEAD_OUT) * (q / (k - 1));
        d.seek(t, false);
        if (typeof gsap !== 'undefined') gsap.ticker.tick();
        // 取各采样点的**最大**有效不透明度：只要某一刻达到可见即算在场。
        // 取最小会把"先隐后显"的正常揭示全部读成缺席（本批实测的第一次回归即此形态）。
        for (var j2 = 0; j2 < els.length; j2++) {
          var o = effOpaque(els[j2]);
          if (o > peaks[j2]) peaks[j2] = o;
        }
      }
      for (var j3 = 0; j3 < nodes.length; j3++) nodes[j3].o = peaks[j3];
      rows.push({scene: w.sid, samples: k, nodes: nodes});
    }
    emit({rows: rows});
  }
  window.MIN_S = %MINS%; window.MAX_S = %MAXS%; window.HZ = %HZ%;
  window.LEAD_IN = %LEADIN%; window.LEAD_OUT = %LEADOUT%;
  window.WINDOWS = %WINDOWS%;
  try { run(); } catch (e) { emit({error: 'run-throw: ' + (e && e.message)}); }
}
)();
</script>
</body>"""


def scene_windows(html_src: Path) -> Optional[Dict[str, Tuple[float, float]]]:
    """场次窗口取自 HTML T 块；末场终点取根 `data-duration`，封面取 `data-cover-duration`。

    不取 narration.json：全仓 77 份项目里 25 份没有该文件（旧项目在 config 内嵌或指针指向
    别处），窗口腿一断这些项目整体看不见。T 块是 tween 锚点所在坐标系（`T.s1 + 1.6`），
    seek 用的正是它——取数与判定同源。
    """
    src = html_src.read_text(encoding="utf-8", errors="replace")
    t_map: Dict[int, float] = {}
    for block in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S):
        t_map = parse_t_block(block) or t_map
        if t_map:
            break
    if not t_map:
        return None
    keys = sorted(t_map)
    dur = re.search(r'data-duration="([\d.]+)"', src)
    if not dur:
        return None
    wins: Dict[str, Tuple[float, float]] = {}
    for i, k in enumerate(keys):
        end = t_map[keys[i + 1]] if i + 1 < len(keys) else float(dur.group(1))
        wins["s%d" % k] = (t_map[k], end)
    cover = re.search(r'data-cover-duration="([\d.]+)"', src)
    if cover and keys and keys[0] != 0:
        wins["s0"] = (0.0, float(cover.group(1)))
    return wins or None


def sample_count(span: float) -> int:
    return max(MIN_SAMPLES, min(MAX_SAMPLES, round(span * SAMPLES_PER_SECOND)))


def adjudicate_scene(row: Dict[str, Any]) -> Dict[str, Any]:
    """裁定全在这里：缺席＝整场峰值有效不透明度仍不可见；死块＝极大且承载
    >=MIN_BLOCK_CONTENT 个内容后代且它们全部缺席。非内容节点**不得**计入缺席。

    `total`/`absent` 必须在自底向上累加**之前**取叶子计数：累加后的 cc/cd 含祖先重复，
    直接求和会把一份内容数好几遍（实测同一页 162 个元素被报成 726）。
    """
    nodes = row.get("nodes") or []
    content = [bool(n["c"]) for n in nodes]
    # 载荷里的 `o` 是各采样点的最大有效不透明度；它仍不可见 ⇔ 整场每个采样点都不可见。
    invisible = [content[i] and float(nodes[i].get("o", 0)) <= INVISIBLE_MAX
                 for i in range(len(nodes))]
    total_content = sum(1 for c in content if c)
    absent_content = sum(1 for v in invisible if v)
    cc = [1 if c else 0 for c in content]
    cd = [1 if v else 0 for v in invisible]
    # 自底向上聚合：querySelectorAll 是文档序，倒序遍历保证子先于父
    for i in range(len(nodes) - 1, -1, -1):
        p = nodes[i].get("p", -1)
        if p is not None and 0 <= p < len(nodes):
            cc[p] += cc[i]
            cd[p] += cd[i]
    qualified = [i for i in range(len(nodes))
                 if cc[i] >= MIN_BLOCK_CONTENT and cd[i] == cc[i]]
    qset = set(qualified)
    dead = []
    for i in qualified:
        p, maximal = nodes[i].get("p", -1), True
        while p is not None and p >= 0:
            if p in qset:
                maximal = False
                break
            p = nodes[p].get("p", -1)
        if maximal:
            dead.append(i)
    return {"scene": row.get("scene"), "samples": row.get("samples"),
            "total": total_content, "absent": absent_content,
            "absent_labels": [nodes[i]["l"] for i in range(len(nodes))
                              if invisible[i] and cc[i] == 1][:4],
            "dead_blocks": len(dead),
            "dead_labels": ["%s %d/%d" % (nodes[i]["l"], cd[i], cc[i]) for i in dead][:5]}


def adjudicate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenes, untested = [], []
    for r in rows:
        if r.get("error"):
            untested.append("%s: %s" % (r["scene"], r["error"]) if r.get("scene")
                            else str(r["error"]))
            continue
        scenes.append(adjudicate_scene(r))
    res = {"scenes": scenes, "untested": untested,
           "total": sum(s["total"] for s in scenes),
           "absent": sum(s["absent"] for s in scenes),
           "dead_blocks": sum(s["dead_blocks"] for s in scenes)}
    # 取数断链优先于 N/A：一场都没裁成 ⇒ 不是"干净"，是"没量到"（A06/A04 同族）。
    if not scenes and untested:
        res["status"] = gs.UNTESTED
    elif not res["total"]:
        res["status"] = gs.NOT_APPLICABLE
    elif res["dead_blocks"]:
        # 本件不裁定：点名即职责，升格条件见模块 docstring「定档」
        res["status"] = gs.PASS
        res["note"] = "named_not_adjudicated"
    elif untested:
        res["status"] = gs.UNTESTED
    else:
        res["status"] = gs.PASS
    return res


def build_probe(html_src: Path, work: Path, wins: Dict[str, Any]) -> Path:
    src = html_src.read_text(encoding="utf-8", errors="replace")
    arr = [{"sid": k, "start": v[0], "end": v[1]}
           for k, v in sorted(wins.items(), key=lambda x: x[1][0])]
    fragment = (PROBE_SCRIPT
                .replace("%MINS%", str(MIN_SAMPLES))
                .replace("%MAXS%", str(MAX_SAMPLES))
                .replace("%HZ%", str(SAMPLES_PER_SECOND))
                .replace("%LEADIN%", str(LEAD_IN))
                .replace("%LEADOUT%", str(LEAD_OUT))
                .replace("%WINDOWS%", json.dumps(arr)))
    fragment = fragment[fragment.index("<script>"):]
    probe = work / "probe.html"
    probe.write_text(src[:src.rindex("</body>")] + fragment + "</body></html>",
                     encoding="utf-8")
    cvp.copy_referenced_assets(html_src, work, cvp.relative_asset_refs(src))
    return probe


def probe_rows(chrome: Path, probe: Path) -> List[Dict[str, Any]]:
    """Chrome 取数腿（每支项目一次调用；用 --dump-dom 让页内自己遍历各场）。

    不用 CDP `Page.navigate`：实测在全新 tab 上 navigate 一律 `net::ERR_ABORTED`、DOM 为空。
    用例侧桩掉的正是本函数——桩它即可在 CI（无 Chrome）上取证判据。
    """
    cmd = [str(chrome), "--headless=new", "--disable-gpu", "--no-sandbox",
           f"--user-data-dir={probe.parent / 'profile'}", "--window-size=1920,1080",
           "--virtual-time-budget=20000", "--dump-dom", probe.as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    m = re.search(r"###PRESENCE###(\{.*?\})###END###", r.stdout or "", re.S)
    if not m:
        return [{"error": "probe-no-marker"}]
    data = json.loads(m.group(1))
    if data.get("error"):
        return [{"error": str(data["error"])}]
    return data.get("rows") or []


def measure(html_src: Path, work: Optional[Path] = None) -> Dict[str, Any]:
    if not html_src.exists():
        return {"status": gs.UNTESTED, "scenes": [], "untested": ["html-missing"],
                "total": 0, "absent": 0, "dead_blocks": 0}
    wins = scene_windows(html_src)
    if not wins:
        return {"status": gs.UNTESTED, "scenes": [], "total": 0, "absent": 0,
                "dead_blocks": 0,
                "untested": ["no-t-block-or-data-duration（无声明＝没有窗口坐标系可 seek）"]}
    tmp = work or Path(tempfile.mkdtemp(prefix="presence_"))
    try:
        chrome = cvp.find_chrome()
    except Exception as e:  # noqa: BLE001
        return {"status": gs.UNTESTED, "scenes": [], "total": 0, "absent": 0,
                "dead_blocks": 0, "untested": ["chrome-not-found: %s" % str(e)[:60]]}
    res = adjudicate(probe_rows(chrome, build_probe(html_src, tmp, wins)))
    res["html"] = str(html_src)
    return res


def format_lines(res: Dict[str, Any]) -> List[str]:
    """日志行唯一拼装点：runner 与 CLI 都调这里，各拼一份即两份真相（A12 同族）。"""
    lines: List[str] = []
    if res.get("status") == gs.UNTESTED:
        lines.append("[PRESENCE] UNTESTED — 无从观测：%s（不得读成已验证）"
                     % ", ".join(res.get("untested") or ["unknown"]))
        return lines
    if res.get("status") == gs.NOT_APPLICABLE:
        lines.append("[PRESENCE] N/A — 无可测内容元素（纯图形场或场景根取不到）")
    elif res.get("dead_blocks"):
        lines.append("[PRESENCE] WARN — %d 处内容块从未出现（本判据只点名不阻断；"
                     "升格前置见脚本 docstring）" % res["dead_blocks"])
        for s in res["scenes"]:
            if s["dead_blocks"]:
                lines.append("  场 %s: %s" % (s["scene"], ", ".join(s["dead_labels"])))
    else:
        lines.append("[PRESENCE] 死块 0／承载内容元素 %d（各场多点采样，永不阻断）"
                     % res["total"])
    if res.get("absent") and not res.get("dead_blocks"):
        lines.append("  另有 %d 个单元素整场未出现（冗余标记类不入判据，题旨见 docstring）"
                     % res["absent"])
    for u in res.get("untested") or []:
        lines.append("  未判场次：%s" % u)
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="内容在场观测（渲染前，只点名不阻断）")
    ap.add_argument("html", help="项目目录名或 index.html 路径")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = Path(a.html)
    if not p.is_absolute():
        from _script_env import ROOT
        q = ROOT / "程序文件" / "源码" / "hyperframes" / a.html / "index.html"
        p = q if q.exists() else Path(a.html)
    res = measure(p)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        for line in format_lines(res):
            print(line)
    return 2 if res.get("status") == gs.UNTESTED else 0


if __name__ == "__main__":
    sys.exit(main())
