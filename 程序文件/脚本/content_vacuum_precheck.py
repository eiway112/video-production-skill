# -*- coding: utf-8 -*-
"""content_vacuum 零渲染预判器：渲染前秒级裁定场景画面是否会被门禁判空窗。

判据与 visual_boundary_check 同源（行占用覆盖率，非动画节奏），采样时刻复用门禁的
early=start+min(7.5, dur*0.45) / late=start+dur*0.85 两点。instant_preview 每场只取
start+3.0 / end-2.0，覆盖不到这两个时刻，故不能替代本工具。

用法：
    python content_vacuum_precheck.py <index.html> [sid:start:dur ...]
    # 省略场景清单时，自动从 HTML 的 `var T = {...}` 与场景 div 推导全部场景窗口

退出码：0=全过，1=存在 VACUUM，2=环境/取数失败（不得读成通过）。
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import _script_env  # noqa: F401  (GBK 控制台修复)
from _script_env import SCRIPT_DIR

sys.path.insert(0, str(SCRIPT_DIR))
import visual_boundary_check as vbc  # noqa: E402

def _preview_scratch_dir() -> Path:
    """零渲染留白预判的工作目录：落【项目内受管临时区】（TEMP_BASE 下子目录）。

    2026-10-11 改，两条理由各独立：
    ① 旧实现返回盘根 `{drive}/vw_vacuum_precheck`，依据是同一条"Chrome 在中文路径上
      不可靠"的成文断言——本仓 A/B 实测已证伪（ASCII 组与中文组同为 rc=0、截图 17331
      字节逐位相同；读数与复现命令见 `操作日志.md` [2026-10-11] 预览落点 条）。
    ② 盘根落点不在任何扫描面内，也不在 `_工具/盘根基线.json` 里：中断残留会被盘根
      越界哨兵报成基线外新增，即"生产者与判据互不知情"。现落项目内，随
      `过程产物/临时产物/` 的龄期巡检可见（该面只报不删，属既有覆盖，不假装已自动清理），
      且永不在工作区外造条目。
    """
    return _script_env.TEMP_BASE / "vacuum_precheck"


BASE_DIR = _preview_scratch_dir()
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome() -> Path:
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return Path(p)
    raise RuntimeError("未找到 Chrome/Edge 可执行文件，无法截图预判")


def parse_scenes(html: str):
    """从 HTML 推导 (sid, start, dur)：T 块起点 + data-scene-id 的先后顺序给出窗口。"""
    m = re.search(r"var\s+T\s*=\s*\{([^}]*)\}", html)
    if not m:
        raise RuntimeError("HTML 中未找到 `var T = {...}` 时间块，无法推导场景窗口")
    starts = {k: float(v) for k, v in re.findall(r"(\w+)\s*:\s*([0-9.]+)", m.group(1))}
    ids = re.findall(r'data-scene-id="([^"]+)"', html)
    ordered = [i for i in ids if i in starts]
    for extra in starts:
        if extra not in ordered:
            ordered.append(extra)
    ordered.sort(key=lambda k: starts[k])
    scenes = []
    for idx, sid in enumerate(ordered):
        nxt = starts[ordered[idx + 1]] if idx + 1 < len(ordered) else None
        dur = (nxt - starts[sid]) if nxt else 0.0
        if dur >= vbc.VACUUM_MIN_SCENE_DUR:
            scenes.append((sid, starts[sid], dur))
    return scenes


def make_probe(html_src: Path, work: Path) -> Path:
    src = html_src.read_text(encoding="utf-8")
    if "</body>" not in src:
        raise RuntimeError(f"{html_src} 缺少 </body>，无法注入 seek 探针")
    injected = src[: src.rindex("</body>")] + """
<script>
(function () {
  var m = location.search.match(/t=([0-9.]+)/);
  var t = m ? parseFloat(m[1]) : 0;
  window.__ready = false;
  requestAnimationFrame(function () {
    requestAnimationFrame(function () {
      if (typeof tl !== 'undefined' && tl && tl.pause) {
        tl.pause();
        tl.seek(t, false);
        if (typeof gsap !== 'undefined') gsap.ticker.tick();
      }
      window.__ready = true;
    });
  });
})();
</script>
</body>"""
    probe = work / "probe.html"
    probe.write_text(injected, encoding="utf-8")
    gsap = html_src.parent / "gsap.min.js"
    if gsap.exists():
        shutil.copy2(gsap, work / "gsap.min.js")
    return probe


def shoot(chrome: Path, probe: Path, t: float, out: Path):
    if out.exists():
        out.unlink()
    cmd = [
        str(chrome), "--headless=new", "--disable-gpu", "--no-sandbox",
        f"--user-data-dir={out.parent / 'profile'}",
        "--hide-scrollbars", "--window-size=1920,1080",
        "--virtual-time-budget=8000", "--force-device-scale-factor=1",
        f"--screenshot={out}",
        probe.as_uri() + f"?t={t}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    if not out.exists():
        raise RuntimeError(
            f"截图失败 t={t}（退出码 {r.returncode}）：{(r.stderr or r.stdout)[-500:]}")


def lit_bands(image_path, h):
    """点亮行的纵向区段（调试用：判断内容铺在画布哪一段）。"""
    import numpy as np
    from PIL import Image
    arr = np.array(Image.open(image_path).convert("RGB"))
    y0 = round(h * vbc.VACUUM_CONTENT_TOP_RATIO)
    y1 = min(vbc.subtitle_safety_line(h), h - 1)
    band = arr[y0:y1, 20:arr.shape[1] - 20, :]
    bright = (band[:, :, 0].astype(int) > vbc.BRIGHTNESS_THRESHOLD) | \
             (band[:, :, 1].astype(int) > vbc.BRIGHTNESS_THRESHOLD) | \
             (band[:, :, 2].astype(int) > vbc.BLUE_THRESHOLD)
    rows = bright.sum(axis=1) > vbc.CONTENT_PIXEL_THRESHOLD
    out, s = [], None
    for i, v in enumerate(rows):
        if v and s is None:
            s = i
        if not v and s is not None:
            out.append((s + y0, i - 1 + y0))
            s = None
    if s is not None:
        out.append((s + y0, len(rows) - 1 + y0))
    return out


def probe_scene(chrome, probe, sid, start, dur, work, verbose):
    early_t = start + min(7.5, dur * 0.45)
    late_t = start + dur * 0.85
    cov = {}
    for label, t in (("early", early_t), ("late", late_t)):
        img = work / f"{sid}_{label}.png"
        shoot(chrome, probe, t, img)
        cov[label] = vbc.measure_content_coverage(img)
        if verbose:
            print(f"    {label} @{t - start:.1f}s cov={cov[label]:.3f} "
                  f"bands={lit_bands(img, vbc.frame_height(img))}")
    ratio = cov["early"] / max(cov["late"], 1e-9)
    vacuum = (cov["late"] >= vbc.VACUUM_MIN_LATE_COVERAGE
              and ratio < vbc.VACUUM_EARLY_RATIO)
    print(f"  {sid:6s} {dur:5.1f}s  early={cov['early']:.3f} late={cov['late']:.3f} "
          f"ratio={ratio:.3f} -> {'VACUUM' if vacuum else 'OK'}")
    return not vacuum


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("html", help="项目 index.html 路径")
    ap.add_argument("scenes", nargs="*",
                    help="sid:start:dur，省略则自动从 HTML 的 T 块推导全部场景")
    ap.add_argument("--quiet", action="store_true", help="不打印点亮行区段")
    args = ap.parse_args()

    html_src = Path(args.html).resolve()
    if not html_src.is_file():
        print(f"UNTESTED: 找不到 {html_src}", file=sys.stderr)
        return 2

    if args.scenes:
        triples = []
        for spec in args.scenes:
            parts = spec.rsplit(":", 2)
            if len(parts) != 3:
                print(f"UNTESTED: 场景参数格式应为 sid:start:dur，收到 {spec}", file=sys.stderr)
                return 2
            triples.append((parts[0], float(parts[1]), float(parts[2])))
    else:
        triples = parse_scenes(html_src.read_text(encoding="utf-8"))
        if not triples:
            print("UNTESTED: 未推导出任何时长达标的场景，无从裁定", file=sys.stderr)
            return 2

    work = BASE_DIR / f"run_{html_src.parent.name}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        chrome = find_chrome()
        probe = make_probe(html_src, work)
        print(f"content_vacuum 预判（零渲染，判据与 visual_boundary_check 同源）: {html_src.parent.name}")
        results = [probe_scene(chrome, probe, sid, s, d, work, not args.quiet)
                   for sid, s, d in triples]
    except (RuntimeError, OSError) as e:
        print(f"UNTESTED: {e}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)

    failed = [t[0] for t, ok in zip(triples, results) if not ok]
    if failed:
        print(f"FAIL: {len(failed)} 个场景预判为空窗 -> {', '.join(failed)}")
        print("修复方向见 新路径实施_踩坑清单与防范检查表.md §4.4（纵向铺展度，不是入场时间）")
        return 1
    print(f"PASS: {len(triples)} 个场景早期覆盖率均不低于门禁阈值 "
          f"{vbc.VACUUM_EARLY_RATIO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
