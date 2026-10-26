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
    python content_vacuum_precheck.py <index.html> --boundaries
    # 场边界零渲染采样（观测面）：按 T 块每个场起点取窗，逐帧量覆盖，
    # 出现"占用恰为 0"的纯平帧即点名该边界。不设门禁、不新增阈值——
    # 占用量（行占比）没有跨项目一致语义，落到某个阈值就是拍脑袋。

退出码：0=全过，1=存在 VACUUM，2=环境/取数失败或**存在无从裁定的场景**（不得读成通过）。
  --boundaries 模式：0=观测完成（读数不改变退出码），2=无从观测。
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


def scene_starts(html: str):
    """T 块场起点的**唯一解析点** → (ordered_ids, {id: start})。

    两条腿共用：`parse_scenes`（两点采样空窗判据）与 `--boundaries`（场边界采样）。
    各写一份正则＝同一份 T 块有两套读法，改一处口径另一处静默不动。
    顺序：先按场景 div 的 data-scene-id 出现次序取 T 块里有的键，再把 T 块独有的键
    按起点秒数补进来（与旧实现逐字同序，供 parse_scenes 沿用）。
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
    return ordered, starts


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
    ordered, starts = scene_starts(html)
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


# HTML 里的相对资源引用（图片/脚本/样式）。协议头、data URI 与绝对路径（含 //host）
# 不取——它们不依赖工作目录，复制到探针工作区反而无意义。
_RELATIVE_REF_RE = re.compile(
    r'(?:src|href)\s*=\s*["\'](?!https?:|data:|file:|blob:|/)([^"\'#\?]+)', re.I)


def relative_asset_refs(src: str):
    """被测 HTML 引用的相对路径资源，去重保序（探针工作目录需要它们才能在位）。"""
    from urllib.parse import unquote
    out = []
    for ref in _RELATIVE_REF_RE.findall(src):
        ref = unquote(ref).strip().lstrip('./')
        if ref and ref not in out:
            out.append(ref)
    return out


def copy_referenced_assets(html_src: Path, work: Path, refs):
    """把 HTML 引用的相对资源按原层级复制进探针工作目录。

    存在理由：探针 HTML 落在别处的临时区，`./assets/x.png` 相对它解析不到 → 含图
    场次的覆盖率被**系统性低估**（实测同一场 0.223 → 素材在位后 0.489）。旧实现只
    硬编码复制 gsap.min.js，那是"已知资源名逐个补"，图片这类同案资源仍漏；现按
    HTML 实际引用取数（登记面＝真实读取路径）。

    返回缺失清单并点名：引用了但项目目录里没有＝被测件自身缺陷（该场仍会偏低），
    不得由探针静默兜掉，也不得因为"复制不到"就把整场判成空窗。
    """
    missing = []
    project = html_src.parent
    for ref in refs:
        src_path = (project / ref).resolve()
        # 只带项目目录内的引用：指向项目外的引用在探针里同样解析不到，点名而非跟随
        if not str(src_path).startswith(str(project.resolve())) or not src_path.is_file():
            missing.append(ref)
            continue
        dst = work / ref
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst)
    return missing


def make_probe(html_src: Path, work: Path):
    """生成 seek 探针；返回 (probe 路径, 未能带入工作目录的资源引用)。"""
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
      // 驱动源两腿：全局 tl（本仓 69/69 项目都有），取不到则回退渲染器真正驱动的
      // window.__timelines 注册表（唯一条目）。旧实现只认 tl，注册表写法不同名时
      // 探针静默不 seek——两帧同为 t=0 会让比值恒 1、把空窗判成 OK（漏判而非误杀）。
      var driver = (typeof tl !== 'undefined' && tl && tl.pause) ? tl : null;
      if (!driver) {
        var reg = window.__timelines || {};
        var ks = Object.keys(reg).filter(function (k) { return reg[k] && reg[k].pause; });
        if (ks.length === 1) driver = reg[ks[0]];
      }
      if (driver) {
        driver.pause();
        driver.seek(t, false);
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
    missing = copy_referenced_assets(html_src, work, relative_asset_refs(src))
    return probe, missing


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


# ── 场边界零渲染采样（--boundaries，观测面）───────────────────────────
# 窗宽取自 2026-10-26 那轮的实测有效窗口：封面→S1 的纯平空帧落在 t=5.4–5.8s
# （场起点 5.5s）——1fps 汇总看不见，10fps 才现形，故窗＝起点前 0.5s 到起点后 1.3s。
# 判据只有一条，且是"恰为 0"：出现占用（visual_boundary_check 的正文区行占比口径）
# 为 0 的纯平帧即点名该边界。不设门禁、不新增阈值——占用量没有跨项目一致语义，
# 阈值落到某处即拍脑袋；本模式因此只出读数，退出码不随读数变化。
BOUNDARY_PRE = 0.5
BOUNDARY_POST = 1.3
BOUNDARY_STEP = 0.1


def format_boundary_window() -> str:
    """取样窗与判据的唯一复述处：数值由常量插值，文案里不写死秒数。"""
    return (f"窗＝场起点−{BOUNDARY_PRE}s ~ 起点+{BOUNDARY_POST}s，"
            f"步长 {BOUNDARY_STEP}s（{round(1 / BOUNDARY_STEP)}fps）")


def boundary_window(start: float, total=None):
    """某场起点前后的采样时刻表（含两端；下界截到 0，上界不超过组合总时长）。"""
    lo = max(0.0, round(start - BOUNDARY_PRE, 3))
    hi = round(start + BOUNDARY_POST, 3)
    if total is not None:
        hi = min(hi, round(float(total), 3))
    out, i = [], 0
    while True:
        t = round(lo + i * BOUNDARY_STEP, 3)
        if t > hi + 1e-9:
            break
        out.append(t)
        i += 1
    return out


def probe_boundary(chrome, probe, sid, start, work, total, verbose):
    """一场起点的窗内逐帧占用读数 → 该边界行（纯平帧按**原始** cover 判 0，不取舍入值）。"""
    frames = []
    for t in boundary_window(start, total):
        img = work / f"{sid}_b{int(round(t * 1000)):07d}.png"
        shoot(chrome, probe, t, img)
        raw = vbc.measure_content_coverage(img)
        frames.append((round(t, 2), raw))
        if verbose:
            print(f"    t={t:6.2f}s cov={raw:.4f}")
    blanks = [t for t, raw in frames if raw == 0.0]
    row = {"sid": sid, "start": start, "frames": frames, "blanks": blanks}
    if frames:
        t_min, cov_min = min(frames, key=lambda f: f[1])
        row["min_cov"], row["min_t"] = cov_min, t_min
        print(f"  {sid:6s} T={start:6.2f}s  帧 {len(frames):2d}  "
              f"min={cov_min:.4f}@{t_min:.2f}s  纯平帧 {len(blanks)}"
              + ("".join(f" @{t:.2f}s" for t in blanks[:6]) if blanks else ""))
    else:
        row["min_cov"], row["min_t"] = None, None
        print(f"  {sid:6s} T={start:6.2f}s  帧 0（窗长非正或已被总时长截空）")
    return row


def summarize_boundaries(rows):
    """边界观测汇总裁定 → (exit_code, lines)。

    观测面：退出码只回答"观测完成与否"（0/2），不回答"画面好不好"——有纯平帧与
    没有纯平帧同为 0，读数只进结论行与点名清单。无一行采到帧即判 2：
    "没有读数"不得读成"边界干净"。
    """
    if not rows or not any(r["frames"] for r in rows):
        return 2, ["UNTESTED: 没有任何场起点进入窗内采样（无从观测，"
                   "不得由「没有读数」读成「边界干净」）"]
    named = [r for r in rows if r["blanks"]]
    frames_n = sum(len(r["frames"]) for r in rows)
    lines = []
    if named:
        lines.append(f"OBSERVED: {len(rows)} 个场起点 / {frames_n} 帧——{len(named)} 个边界"
                     "出现占用恰为 0 的纯平帧 -> " + ", ".join(r["sid"] for r in named))
        for r in named:
            lines.append(f"  - {r['sid']} 起点 {r['start']:.2f}s：纯平 {len(r['blanks'])} 帧 @ "
                         + ", ".join(f"{t:.2f}s" for t in r["blanks"]))
    else:
        lines.append(f"OBSERVED: {len(rows)} 个场起点 / {frames_n} 帧——全部帧占用 > 0，"
                     "未观测到纯平帧")
    empty = [r["sid"] for r in rows if not r["frames"]]
    if empty:
        lines.append(f"UNTESTED: {len(empty)} 个起点无采样帧（窗长非正或总时长截空）-> "
                     + ", ".join(empty))
    lines.append(f"口径：{format_boundary_window()}；判据＝占用恰为 0 即点名该边界。"
                 "本模式是观测面、不设门禁——退出码不随读数变化（0=观测完成、2=无从观测）；"
                 "占用量无跨项目一致语义，故不给 PASS/FAIL，也不新增阈值。")
    return 0, lines


def run_boundaries(html_src: Path, verbose: bool):
    """--boundaries 入口：取 T 块场起点 → 逐边界采窗 → 观测结论。"""
    try:
        html_text = html_src.read_text(encoding="utf-8")
        ordered, starts = scene_starts(html_text)
    except RuntimeError as e:
        print(f"UNTESTED: {e}", file=sys.stderr)
        return 2
    if not ordered:
        print("UNTESTED: T 块未给出任何场起点，边界采样无对象", file=sys.stderr)
        return 2
    total = composition_duration(html_text)
    work = BASE_DIR / f"bnd_{html_src.parent.name}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        chrome = find_chrome()
        probe, missing = make_probe(html_src, work)
        print(f"场边界零渲染采样（观测面，不设门禁）: {html_src.parent.name}")
        print(f"  {format_boundary_window()}")
        if missing:
            print(f"  ⚠ HTML 引用了但未能带入工作目录的资源 {len(missing)} 项 -> "
                  + ", ".join(missing[:8])
                  + ("…" if len(missing) > 8 else "")
                  + "（该边界占用仍会偏低，属被测件引用问题）")
        rows = [probe_boundary(chrome, probe, sid, starts[sid], work, total, verbose)
                for sid in ordered]
    except (RuntimeError, OSError) as e:
        print(f"UNTESTED: {e}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)
    code, lines = summarize_boundaries(rows)
    for line in lines:
        print(line)
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("html", help="项目 index.html 路径")
    ap.add_argument("scenes", nargs="*",
                    help="sid:start:dur，省略则自动从 HTML 的 T 块推导全部场景")
    ap.add_argument("--quiet", action="store_true", help="不打印点亮行区段")
    ap.add_argument("--boundaries", action="store_true",
                    help="场边界零渲染采样（观测面，不设门禁；忽略场景清单参数）")
    args = ap.parse_args()

    html_src = Path(args.html).resolve()
    if not html_src.is_file():
        print(f"UNTESTED: 找不到 {html_src}", file=sys.stderr)
        return 2

    if args.boundaries:
        return run_boundaries(html_src, not args.quiet)

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
        probe, missing = make_probe(html_src, work)
        print(f"content_vacuum 预判（零渲染，判据与 visual_boundary_check 同源）: {html_src.parent.name}")
        if missing:
            print(f"  ⚠ HTML 引用了但未能带入工作目录的资源 {len(missing)} 项 -> "
                  + ", ".join(missing[:8])
                  + ("…" if len(missing) > 8 else "")
                  + "（该场覆盖率仍会偏低，属被测件引用问题，不因此判空窗）")
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
