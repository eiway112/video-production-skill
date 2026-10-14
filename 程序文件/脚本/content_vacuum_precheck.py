# -*- coding: utf-8 -*-
"""content_vacuum 零渲染预判器：渲染前秒级裁定场景画面是否会被门禁判空窗。

判据与 visual_boundary_check 同源（行占用覆盖率，非动画节奏），采样时刻复用门禁的
early=start+min(7.5, dur*0.45) / late=start+dur*0.85 两点。instant_preview 每场只取
start+3.0 / end-2.0，覆盖不到这两个时刻，故不能替代本工具。

**空窗判据（本工具不重新定义，只复述）**：某场只有当
`late 覆盖率 ≥ VACUUM_MIN_LATE_COVERAGE` 且 `early/late < VACUUM_EARLY_RATIO` 时才判空窗，
即"画面后来铺满了、但内容晚到"的慢热形态；晚期本身就很低（极简设计）不判。
早期覆盖率单独与 0.35 比较**不是**本工具的规则，代码从未执行过它（2026-10-12 修掉的
一条文案与判据分叉，见 `操作日志.md` 同名条）。

用法：
    python content_vacuum_precheck.py <index.html> [sid:start:dur ...]
    # 省略场景清单时，自动从 HTML 的 `var T = {...}` 与场景 div 推导全部场景窗口
    # 窗口结束＝下一场起点；末场无下一场，取组合自身声明的 data-duration

退出码：0=全过，1=存在 VACUUM，2=环境/取数失败或**存在无从裁定的场景**（不得读成通过）。
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
import _gate_status as gs  # noqa: E402  四态词汇唯一源，禁止另立同义状态名

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


def composition_duration(html: str):
    """组合总时长＝末场窗口结束的权威源（`#root` 的 data-duration）。

    取该属性而非"最后一场没有下一场就当作 0"：全仓 62 份 index.html 实测 62 份都在位
    （2026-10-12 普查），且它是 HyperFrames 渲染器本数读的那份声明，与画面实际长度同源。
    取不到返回 None，由调用方判 UNTESTED 并点名场景，不得静默少测。
    """
    m = re.search(r'data-duration="([0-9.]+)"', html)
    if not m:
        return None
    try:
        total = float(m.group(1))
    except ValueError:
        return None
    return total if total > 0 else None


def parse_scenes(html: str):
    """从 HTML 推导场景窗口 → (measured, not_applicable, untested)。

    本场窗口结束＝下一场起点；**末场无下一场，取 data-duration**（2026-10-12 修：旧实现
    给末场算出 dur=0 并被下面的最短门槛一并丢弃，5 场项目实测只测 4 场且汇总行不点名
    缺测对象——属"覆盖面窄于措辞"，与 A04/A06 同族）。

    三向分态（不得把"没测"与"按规则不测"压成同一个 bool）：
      measured        [(sid, start, dur)]  进入两点采样
      not_applicable  [(sid, reason)]      短场景豁免，与 visual_boundary_check 同名判据同向
      untested        [(sid, reason)]      窗口无从推导（缺 data-duration／窗口非正）
    """
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
    total = composition_duration(html)
    measured, not_applicable, untested = [], [], []
    for idx, sid in enumerate(ordered):
        nxt = starts[ordered[idx + 1]] if idx + 1 < len(ordered) else None
        if nxt is not None:
            end, source = nxt, "下一场起点"
        elif total is not None:
            end, source = total, "data-duration"
        else:
            untested.append((sid, "末场窗口结束无从推导：无下一场起点，且 HTML 未声明可用的 "
                                  "data-duration（不得按 0 时长把该场当成不存在）"))
            continue
        dur = end - starts[sid]
        if dur <= 0:
            untested.append((sid, f"窗口非正（起点 {starts[sid]:.1f}s ≥ 终点 {end:.1f}s，"
                                  f"终点来源 {source}）——声明自相矛盾，无从裁定"))
            continue
        if dur < vbc.VACUUM_MIN_SCENE_DUR:
            not_applicable.append((sid, f"dur {dur:.1f}s < {vbc.VACUUM_MIN_SCENE_DUR}s"))
            continue
        measured.append((sid, starts[sid], dur))
    return measured, not_applicable, untested


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
    # 判据逐字与 visual_boundary_check 的真空窗第二遍同形（同一枚裁定不在两处各写一份）
    vacuum = (cov["late"] >= vbc.VACUUM_MIN_LATE_COVERAGE
              and ratio < vbc.VACUUM_EARLY_RATIO)
    print(f"  {sid:6s} {dur:5.1f}s  early={cov['early']:.3f} late={cov['late']:.3f} "
          f"ratio={ratio:.3f} -> {'VACUUM' if vacuum else 'OK'}")
    return {"sid": sid, "status": gs.FAIL if vacuum else gs.PASS,
            "early": cov["early"], "late": cov["late"], "ratio": ratio}


def format_criterion() -> str:
    """空窗判据的唯一复述处：数值由常量插值，文案里不写死阈值（改常量即改文案）。"""
    return (f"判据＝late ≥ {vbc.VACUUM_MIN_LATE_COVERAGE} 且 early/late < "
            f"{vbc.VACUUM_EARLY_RATIO}（慢热型空窗；早期覆盖率单独与阈值比较不是本工具的规则）")


def summarize(measured, not_applicable, untested, total_scenes):
    """汇总裁定 → (exit_code, lines)。

    三态各证一件事，任一 FAIL 即 1；有 UNTESTED 即 2（"没测成"不得读成通过）；
    实测面为空亦判 2——由"无违规"短路成 PASS 会把"无对象"读成"查过且干净"。
    行数对照（`实测 N/M 场`）写进结论行本身：覆盖面窄于措辞时差额当场可见。
    """
    lines = []
    failed = [r["sid"] for r in measured if r["status"] == gs.FAIL]
    if failed:
        lines.append(f"FAIL: {len(failed)} 个场景预判为空窗 -> {', '.join(failed)}")
        lines.append("修复方向见 新路径实施_踩坑清单与防范检查表.md §4.4（纵向铺展度，不是入场时间）")
        return 1, lines
    if untested:
        lines.append(f"UNTESTED: {len(untested)} 个场景窗口无从推导，未进入测量 -> "
                     + "; ".join(f"{sid}({reason})" for sid, reason in untested))
        lines.append(f"（实测 {len(measured)}/{total_scenes} 场，{format_criterion()}）")
        return 2, lines
    if not measured:
        lines.append(f"UNTESTED: T 块 {total_scenes} 场中无一进入测量（无从裁定，"
                     "不得由\u201c无违规\u201d短路成 PASS）"
                     + (f"；按规则不适用 {len(not_applicable)} 场 -> "
                        + "; ".join(f"{sid}({reason})" for sid, reason in not_applicable)
                        if not_applicable else ""))
        return 2, lines
    lines.append(f"PASS: 实测 {len(measured)}/{total_scenes} 场均不满足空窗判据"
                 f"——{format_criterion()}")
    if not_applicable:
        lines.append(f"NOT_APPLICABLE: 按短场景豁免不测 {len(not_applicable)} 场 -> "
                     + "; ".join(f"{sid}({reason})" for sid, reason in not_applicable))
    return 0, lines


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
        not_applicable, untested = [], []
    else:
        html_text = html_src.read_text(encoding="utf-8")
        try:
            triples, not_applicable, untested = parse_scenes(html_text)
        except RuntimeError as e:
            print(f"UNTESTED: {e}", file=sys.stderr)
            return 2
        if not triples and not not_applicable and not untested:
            print("UNTESTED: T 块未给出任何场景窗口，无从裁定", file=sys.stderr)
            return 2
    total_scenes = len(triples) + len(not_applicable) + len(untested)

    work = BASE_DIR / f"run_{html_src.parent.name}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        chrome = find_chrome()
        probe = make_probe(html_src, work)
        print(f"content_vacuum 预判（零渲染，判据与 visual_boundary_check 同源）: {html_src.parent.name}")
        for sid, reason in untested:
            print(f"  {sid:6s} 无从推导窗口 -> UNTESTED（{reason}）")
        results = [probe_scene(chrome, probe, sid, s, d, work, not args.quiet)
                   for sid, s, d in triples]
    except (RuntimeError, OSError) as e:
        print(f"UNTESTED: {e}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)

    code, lines = summarize(results, not_applicable, untested, total_scenes)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
