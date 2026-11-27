# -*- coding: utf-8 -*-
"""场景容器裁切实测（零渲染）：量出内容被 overflow 裁掉多少像素。

**为什么需要它**：像素类门禁（`visual_boundary_check`）量的是"可见内容底边"，
被容器裁切之后反而读成"刚好贴边"——即裁得越狠，边界读数越合规。实测病灶
（acoustic-wall-structure-craft 首版）：`.sc` 容器 `overflow:hidden`，底边固定
y=1486，而内容底落在 1586／1595／1558／1523 → s1 末卡被切 100px、s4 末色带
109px、s2 第3行 72px，肉眼明显截断而全链门禁全绿。2026-11-05 那次的量法是
一次性项目级探针（`过程产物/临时产物/_取证/20261105/scene_overflow_probe.js`），
躺在取证目录里、下个项目不会自动跑；本件是它的常驻化。

**量法为什么必须先归零动画态**：CSS Overflow 规范下，scrollable overflow region
**包含后代盒子的 transform 变换结果**——即 GSAP 入场 `from` 态的 `y:16` 会让
`scrollHeight` 比稳态多 16px。故本件先把主时间轴 seek 到该场末尾（入场落定、
transform 归零）再量；直接量初始态会把动画位移读成裁切（实测 s3/s5 各假报 16px，
恰是 `#s3-note`／`#s5-note` 的入场位移）。取不到驱动源时判无从观测，不读成通过。

**为什么不用 rect 差值**：`getBoundingClientRect().bottom` 含 transform 位移，
在 t=0 同样把动画位移读成真实溢出。scrollHeight 差值虽也含 transform，但先归零
动画态即消除该误差。

**裁切面**：不写死 `.sc` 这个类名——遍历场景根内所有 `overflow-y` 计算值为
`hidden`/`clip` 的元素逐个量，覆盖将来换类名或加嵌套裁切层的情形。

用法：
    python scene_overflow_probe.py <index.html> [--json-out <path>] [--tolerance-px N]

退出码：0=无裁切（各场均在容差内）；1=存在裁切（列出逐场溢出量与最深元素）；
2=无从观测（页面取不到、Chrome 起不来、场景结构读不出等——不得读成通过）。

边界：本件已接入 `pipeline_runner.step_preview` 并登记 preview 指纹；命中只 WARN、
不阻断，UNTESTED 也必须落盘，调用方不得把不存在的 JSON 路径写成已取得证据。
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

import _script_env  # noqa: F401  (GBK 控制台修复)
from _script_env import SCRIPT_DIR

sys.path.insert(0, str(SCRIPT_DIR))
import content_vacuum_precheck as cvp  # noqa: E402  资源复制与 Chrome 定位复用，不另写一份
import _gate_status as gs  # noqa: E402  四态词汇唯一源

# 亚像素取整与 1px 描边的容差：内容底边压线不算"被裁"，多出几像素才算
DEFAULT_TOLERANCE_PX = 2.0

_MEASURE_JS = r"""
<script>
(function () {
  // 场景窗口表：优先取 var S；缺失场次按 var T 的下一场起点补齐，
  // 末场取根 data-duration。两种时间源都取不到该场窗口时，该场判 UNTESTED。
  // scene_id 既有数字也有 "sN" 字符串两种形态：已带 s 前缀时不得再拼一次，
  // 否则键变成 "ss1"，所有场景都取不到时间窗（2026-11-26 实测整件 UNTESTED）。
  function sceneWindows() {
    var map = {};
    // 用 window.S 显式取全局：函数内 `var S` 会 hoist 遮蔽全局同名变量，
    // `typeof S` 于是恒为 'undefined'（局部未赋值）。
    var g = (typeof window.S !== 'undefined' && Array.isArray(window.S)) ? window.S : null;
    if (g) {
      for (var i = 0; i < g.length; i++) {
        var rec = g[i];
        if (rec.cover) { map.s0 = rec.end; }
        else if (rec.scene_id !== undefined) {
          var raw = String(rec.scene_id);
          map[/^s\d+$/.test(raw) ? raw : 's' + raw] = rec.end;
        }
      }
    }
    var t = (typeof window.T !== 'undefined' && window.T && typeof window.T === 'object') ? window.T : null;
    if (t) {
      var roots = Array.prototype.slice.call(document.querySelectorAll('[data-scene-id]'));
      var timed = [];
      for (var r = 0; r < roots.length; r++) {
        var sid = roots[r].getAttribute('data-scene-id');
        var start = t[sid];
        if (typeof start === 'number' && isFinite(start)) timed.push({sid: sid, start: start});
      }
      timed.sort(function (a, b) { return a.start - b.start; });
      var meta = document.getElementById('root') || document.documentElement;
      var total = parseFloat(meta.getAttribute('data-duration'));
      for (var j = 0; j < timed.length; j++) {
        if (map[timed[j].sid]) continue;
        var end = j + 1 < timed.length ? timed[j + 1].start : total;
        if (isFinite(end) && end > timed[j].start) map[timed[j].sid] = end;
      }
    }
    return map;
  }

  // 驱动源两腿：全局 tl；取不到则回退渲染器真正驱动的 window.__timelines
  // 注册表（唯一条目）。旧实现只认 tl，注册表写法不同名时探针静默不 seek——
  // 那样量到的是入场 from 态，位移被读成裁切。
  function pickDriver() {
    if (typeof tl !== 'undefined' && tl && tl.pause && tl.seek) return tl;
    var reg = window.__timelines || {};
    var ks = Object.keys(reg).filter(function (k) {
      return reg[k] && reg[k].pause && reg[k].seek;
    });
    if (ks.length === 1) return reg[ks[0]];
    return null;
  }

  function canvasSize() {
    // 画布基准：项目若在根元素声明固定画布（--cw/--ch），布局不随视口变，
    // 测量与渲染画布一致；未声明则场景根常按视口 inset:0 撑开，
    // 视口与渲染画布不符时读数会失真（须点名，不得当作已核）。
    var cs = window.getComputedStyle(document.documentElement);
    function px(name) {
      var v = cs.getPropertyValue(name);
      var m = /(-?[0-9.]+)px/.exec(v || '');
      return m ? parseFloat(m[1]) : null;
    }
    var w = px('--cw'), h = px('--ch');
    return {w: w, h: h, fixed: (w !== null && h !== null)};
  }

  function measure() {
    var out = {scenes: [], error: null, driver: false,
               viewport: {w: window.innerWidth, h: window.innerHeight},
               canvas: canvasSize()};
    try {
      var driver = pickDriver();
      out.driver = !!driver;
      var wins = sceneWindows();
      var roots = document.querySelectorAll('[data-scene-id]');
      for (var i = 0; i < roots.length; i++) {
        var root = roots[i];
        var sid = root.getAttribute('data-scene-id');
        var rec = {scene_id: sid, clips: [], measured: true, seeked: false};
        var end = wins[sid];
        if (driver && typeof end === 'number') {
          driver.pause();
          driver.seek(end - 0.1, false);
          if (typeof gsap !== 'undefined' && gsap.ticker) gsap.ticker.tick();
          rec.seeked = true;
        }
        var nodes = root.querySelectorAll('*');
        for (var j = 0; j < nodes.length; j++) {
          var el = nodes[j];
          var st = window.getComputedStyle(el);
          var oy = st.overflowY;
          if (oy !== 'hidden' && oy !== 'clip') continue;
          var vo = el.scrollHeight - el.clientHeight;
          var ho = el.scrollWidth - el.clientWidth;
          if (vo <= 0 && ho <= 0) continue;
          rec.clips.push({
            tag: el.tagName.toLowerCase(),
            cls: el.className && typeof el.className === 'string' ? el.className : '',
            id: el.id || '',
            overflow_y_px: vo,
            overflow_x_px: ho
          });
        }
        out.scenes.push(rec);
      }
    } catch (e) {
      out.error = String(e && e.message ? e.message : e);
    }
    return out;
  }

  function publish() {
    var res = measure();
    document.documentElement.setAttribute('data-ovf', encodeURIComponent(JSON.stringify(res)));
  }
  if (document.readyState === 'complete') { publish(); }
  else { window.addEventListener('load', publish); }
})();
</script>
"""


def _dump_dom(chrome: Path, probe: Path, work: Path) -> str:
    """用 headless Chrome dump 执行后的 DOM，取回测量结果。"""
    profile = work / "profile"
    cmd = [
        str(chrome), "--headless=new", "--disable-gpu", "--no-sandbox",
        f"--user-data-dir={profile}",
        "--hide-scrollbars", "--window-size=1080,1920",
        "--virtual-time-budget=8000", "--force-device-scale-factor=1",
        "--dump-dom", probe.as_uri(),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    if "data-ovf" not in (r.stdout or ""):
        raise RuntimeError(
            f"页面未回传测量结果（Chrome 退出码 {r.returncode}）："
            f"{(r.stderr or r.stdout or '')[-400:]}")
    return r.stdout


def _parse(html: str):
    m = re.search(r'data-ovf="([^"]*)"', html)
    if not m:
        raise RuntimeError("DOM 里找不到 data-ovf 属性")
    payload = urllib.parse.unquote(m.group(1))
    return json.loads(payload)


def measure(html_src: Path, tolerance_px: float = DEFAULT_TOLERANCE_PX):
    """逐场量裁切溢出；返回 (逐场记录, 未能带入工作目录的资源引用)。"""
    src = html_src.read_text(encoding="utf-8")
    if "</body>" not in src:
        raise RuntimeError(f"{html_src} 缺少 </body>，无法注入测量脚本")
    with tempfile.TemporaryDirectory(prefix="ovf_probe_") as td:
        work = Path(td)
        probe = work / "probe.html"
        probe.write_text(src[: src.rindex("</body>")] + _MEASURE_JS + "\n</body>\n",
                         encoding="utf-8")
        refs = cvp.relative_asset_refs(src)
        missing = cvp.copy_referenced_assets(html_src, work, refs)
        chrome = cvp.find_chrome()
        dom = _dump_dom(chrome, probe, work)
        data = _parse(dom)
    if data.get("error"):
        raise RuntimeError(f"页面内测量报错：{data['error']}")
    return data, missing


def _fmt_clips(clips, tolerance_px):
    """溢出条按溢出量降序，最多列 5 条（人读用；完整清单见 --json-out）。"""
    ranked = sorted(clips, key=lambda c: max(c["overflow_y_px"], c["overflow_x_px"]),
                    reverse=True)
    return ranked[:5]


def _write_result(path, html_src, tolerance_px, verdict, *, reason=None,
                  scenes=None, missing=None, data=None):
    if not path:
        return True
    payload = {
        "html": str(html_src),
        "tolerance_px": tolerance_px,
        "verdict": verdict,
        "scenes": scenes or [],
        "missing_assets": missing or [],
    }
    if reason:
        payload["reason"] = reason
    if data is not None:
        raw_scenes = data.get("scenes") or []
        payload["diagnostics"] = {
            "viewport": data.get("viewport"),
            "canvas": data.get("canvas"),
            "driver": data.get("driver"),
            "observed_scene_ids": [s.get("scene_id") for s in raw_scenes],
            "unseeked_scene_ids": [s.get("scene_id") for s in raw_scenes
                                   if not s.get("seeked")],
        }
    out = Path(path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        print(f"[ERROR] 结果 JSON 写入失败：{out}（{type(exc).__name__}: {exc}）")
        return False
    print(f"[OK] 结果已写入：{out}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="场景容器裁切实测（零渲染诊断工具）")
    ap.add_argument("html", help="项目 index.html 路径")
    ap.add_argument("--json-out", help="完整结果 JSON 落盘路径")
    ap.add_argument("--tolerance-px", type=float, default=DEFAULT_TOLERANCE_PX,
                    help=f"容差像素（默认 {DEFAULT_TOLERANCE_PX}）")
    args = ap.parse_args()

    html_src = Path(args.html)
    if not html_src.is_file():
        reason = f"找不到 HTML：{html_src}"
        print(f"[ERROR] {reason}")
        _write_result(args.json_out, html_src, args.tolerance_px, gs.UNTESTED,
                      reason=reason)
        return 2

    try:
        data, missing = measure(html_src, args.tolerance_px)
    except Exception as exc:  # 取数断链一律判无从观测，不得读成通过
        reason = f"无从观测：{exc}"
        print(f"[UNTESTED] {reason}")
        _write_result(args.json_out, html_src, args.tolerance_px, gs.UNTESTED,
                      reason=reason)
        return 2
    vp = data.get("viewport", {})
    canvas = data.get("canvas", {})
    print(f"[INFO] 页面视口 {vp.get('w')}×{vp.get('h')}；"
          f"项目声明画布 {canvas.get('w')}×{canvas.get('h')}"
          f"（{'固定 px，测量不受视口影响' if canvas.get('fixed') else '未声明固定画布，读数随视口变，可能失真'}）")
    scenes = data.get("scenes", [])

    if missing:
        print(f"[WARN] {len(missing)} 个引用资源未能带入工作目录（该场读数可能偏低）："
              f"{', '.join(missing[:8])}")

    if not scenes:
        reason = "页面里读不到 [data-scene-id] 场景根，无法观测"
        print(f"[UNTESTED] {reason}")
        _write_result(args.json_out, html_src, args.tolerance_px, gs.UNTESTED,
                      reason=reason, missing=missing, data=data)
        return 2

    # 驱动源缺失＝动画态没能归零，量到的是入场 from 态——不得读成通过
    if not data.get("driver"):
        reason = ("页面里取不到主时间轴驱动源（全局 tl 或 window.__timelines 单一条目），"
                  "入场位移无法归零，本件不出读数（不得读成无裁切）")
        print(f"[UNTESTED] {reason}")
        _write_result(args.json_out, html_src, args.tolerance_px, gs.UNTESTED,
                      reason=reason, missing=missing, data=data)
        return 2
    not_seeked = [s["scene_id"] for s in scenes if not s.get("seeked")]
    if not_seeked:
        reason = (f"以下场景未能定位时间窗（未 seek，动画态未归零）："
                  f"{', '.join(not_seeked)}")
        print(f"[UNTESTED] {reason}")
        _write_result(args.json_out, html_src, args.tolerance_px, gs.UNTESTED,
                      reason=reason, missing=missing, data=data)
        return 2

    rows = []
    failures = []
    for rec in scenes:
        sid = rec["scene_id"]
        clips = rec.get("clips", [])
        worst = max([max(c["overflow_y_px"], c["overflow_x_px"]) for c in clips],
                    default=0.0)
        status = gs.FAIL if worst > args.tolerance_px else gs.PASS
        rows.append({"scene_id": sid, "status": status, "worst_px": worst,
                     "clips": clips})
        if status == gs.FAIL:
            failures.append((sid, worst, clips))

    for row in rows:
        if row["status"] == gs.FAIL:
            print(f"  ✗ {row['scene_id']}: 裁切溢出 {row['worst_px']:.0f}px")
            for c in _fmt_clips(row["clips"], args.tolerance_px):
                name = c["id"] or c["cls"] or c["tag"]
                print(f"      {name}: 纵 {c['overflow_y_px']}px / 横 {c['overflow_x_px']}px")
        else:
            print(f"  ✓ {row['scene_id']}: 无裁切")

    total = len(rows)
    verdict = gs.FAIL if failures else gs.PASS
    if not _write_result(args.json_out, html_src, args.tolerance_px, verdict,
                         scenes=rows, missing=missing, data=data):
        return 2

    if failures:
        print(f"[FAIL] {len(failures)}/{total} 个场景存在容器裁切")
        return 1
    print(f"[PASS] {total} 个场景均无容器裁切（容差 {args.tolerance_px}px）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
