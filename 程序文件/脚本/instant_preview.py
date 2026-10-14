#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
瞬时预览引擎 — 渲染前场景缩略图 + 动画密度分析

设计原则：
  把反馈环从 21 分钟（全量渲染）缩短到 5 秒（Chrome 截图）。
  渲染前即可发现视觉问题（留白过长、布局错乱、场景重叠）。

工作流：
  1. 启动 Chrome headless（--remote-debugging-port）
  2. 加载 HTML 文件（file:// URL）
  3. 对每个场景边界时间点：seek GSAP tl → 截图
  4. 拼接场景缩略图网格（Pillow）
  5. 分析 GSAP 时间戳密度，标注"动画荒漠"
  6. 输出预览报告

用法：
  python instant_preview.py --html index.html --config workflow_promo.json
  python instant_preview.py --html index.html --config workflow_promo.json --output preview.png
  python instant_preview.py --html index.html --config workflow_promo.json --density-only

输出：
  - preview_grid.png: 场景缩略图网格（每场景 2 帧：入场完成 + 结束前）
  - preview_report.txt: 动画密度分析 + 问题标注
"""

import base64
import json
import os
import re
import signal
import subprocess
import sys
import io
import time
import argparse
import tempfile
import shutil
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

# Import shared GSAP time resolution utilities (T-block aware)
sys.path.insert(0, str(Path(__file__).parent))
from _gsap_time_utils import parse_t_block, resolve_script_times
from _script_env import ROOT, PREVIEW_TEMP, QUALITY_DIR, resolve_narration_scenes
import _gate_status as gs

# Default Chrome path (same as HyperFrames uses)
DEFAULT_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


class CDPClient:
    """Minimal Chrome DevTools Protocol client over websocket."""

    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(
            ws_url, timeout=30, max_size=20 * 1024 * 1024,
            origin="http://localhost"
        )
        self._msg_id = 0
        self._events = []  # CDP events buffer

    def send(self, method, params=None, timeout=30):
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method}
        if params:
            msg["params"] = params
        self.ws.send(json.dumps(msg))

        # Wait for response with matching id (skip events)
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.5, deadline - time.time())
            try:
                self.ws.settimeout(remaining)
                data = json.loads(self.ws.recv())
                if data.get("id") == self._msg_id:
                    if "error" in data:
                        raise RuntimeError(f"CDP error: {data['error']}")
                    return data.get("result", {})
                # It's an event, buffer it
                self._events.append(data)
            except Exception as e:
                if time.time() >= deadline:
                    raise TimeoutError(f"CDP {method} timed out after {timeout}s") from e
                continue
        raise TimeoutError(f"CDP {method} timed out after {timeout}s")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def launch_chrome(chrome_path, port=9222):
    """Launch Chrome headless with remote debugging port.

    Profiles live under _script_env.PREVIEW_TEMP (project-managed temp area), one
    mkdtemp per launch, whole directory removed on cleanup.
    NOTE (2026-10-11): the previous wording here claimed "Chrome cannot handle
    Chinese characters in user-data-dir or screenshot paths". That was an
    unverified assertion, and the repo's own A/B probe disproved it (ASCII group and
    Chinese group both rc=0 with byte-identical PNG). Do not re-add it.
    """
    preview_temp_dir = str(PREVIEW_TEMP)
    os.makedirs(preview_temp_dir, exist_ok=True)
    user_data_dir = tempfile.mkdtemp(prefix="chrome_pv_", dir=preview_temp_dir)
    args = [
        chrome_path,
        "--headless=new",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--window-size=1920,1080",
        "--disable-gpu",
        "--no-sandbox",
        "--remote-allow-origins=*",
        "--disable-web-security",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-sync",
        "--disable-translate",
        "--mute-audio",
        "--no-first-run",
        "--font-render-hinting=none",
        "--run-all-compositor-stages-before-draw",
        "--disable-features=PaintHolding",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Wait for Chrome to start listening
    for _ in range(30):
        time.sleep(0.5)
        try:
            import urllib.request
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
            if resp.status == 200:
                return proc, user_data_dir
        except Exception:
            continue
    raise RuntimeError("Chrome failed to start within 15 seconds")


def get_ws_url(port=9222):
    """Get websocket URL for the first Chrome tab."""
    import urllib.request
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5)
    tabs = json.loads(resp.read())
    if not tabs:
        # Create a new tab
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/new", timeout=5)
        tab = json.loads(resp.read())
    else:
        tab = tabs[0]
    return tab["webSocketDebuggerUrl"]


def capture_screenshot(cdp, clip=None):
    """Capture a screenshot and return as PNG bytes."""
    params = {"format": "png", "quality": 85}
    if clip:
        params["clip"] = clip
    result = cdp.send("Page.captureScreenshot", params, timeout=30)
    return base64.b64decode(result["data"])


def seek_and_capture(cdp, time_sec, wait_ms=300, skip_seek=False):
    """Seek GSAP timeline to a specific time and capture screenshot.

    Args:
        skip_seek: If True, skip GSAP seek and capture current frame directly.
    """
    if not skip_seek:
        # Seek the GSAP timeline
        js = f"""
        (function() {{
            if (typeof tl !== 'undefined' && tl && typeof tl.seek === 'function') {{
                tl.seek({time_sec}, false);
                // Force a render tick
                if (typeof gsap !== 'undefined') gsap.ticker.tick();
                return 'seeked';
            }}
            return 'no_timeline';
        }})()
        """
        result = cdp.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
        status = result.get("result", {}).get("value", "unknown")
    else:
        status = "skipped"

    # Wait for DOM to settle
    time.sleep(wait_ms / 1000.0)

    return capture_screenshot(cdp), status


def analyze_animation_density(html_content, scenes, cover_duration=3.0):
    """Analyze GSAP animation density across the timeline.

    Returns list of (time_range, event_count, density_rating) tuples.
    """
    # Extract GSAP time positions from tl.to/tl.fromTo/tl.set calls
    script_match = re.search(r'<script>(.*?)</script>', html_content, re.DOTALL)
    if not script_match:
        return []

    script = script_match.group(1)

    # Parse T-block if present (structured timeline), resolve all timestamps
    t_map = parse_t_block(script)
    time_entries = resolve_script_times(script, t_map)
    gsap_times = [t for t, _ in time_entries]

    if not gsap_times:
        return []

    # Analyze density in 30-second windows
    total_dur = max(gsap_times) if gsap_times else 0
    window_size = 30.0
    results = []

    for start in range(0, int(total_dur) + 1, int(window_size)):
        end = start + window_size
        events = [t for t in gsap_times if start <= t < end]
        count = len(events)
        # Density rating
        if count == 0:
            rating = "DESERT"  # No animation in 30s window
        elif count < 3:
            rating = "SPARSE"  # Very few animations
        elif count < 6:
            rating = "OK"
        else:
            rating = "RICH"
        results.append((start, end, count, rating))

    return results


def check_preview_safety(manifest_entries, temp_dir):
    """字幕安全区预检（prefab 复盘决策一，2026-08-07）。

    复用 visual_boundary_check.measure_content_bottom 对 preview 截图测量
    content_bottom，把布局溢出的判定从“渲染完成后”前移到秒级预览：
    prefab-agent-launch 场景3 溢出 34px 曾消耗一次 37 分钟全量渲染才暴露，
    同类问题必须在预览阶段阻断。

    Args:
        manifest_entries: capture_manifest.json 的 scenes 列表
                          （每项含 sceneId / time / file）。
        temp_dir: preview 截图副本所在目录（preview_{file}）。

    Returns:
        violations: [{scene, content_bottom, overflow_px, time}]，每场景取最差样本。
    """
    from visual_boundary_check import (
        measure_content_bottom, frame_height, subtitle_safety_line)

    worst_per_scene = {}
    safety_line = None
    for entry in manifest_entries:
        dst = Path(temp_dir) / f"preview_{entry['file']}"
        if not dst.exists():
            continue
        bottom = measure_content_bottom(str(dst))
        if safety_line is None:
            # 安全区随画布高度等比缩放，沿用横版数值会误判竖版溢出
            safety_line = subtitle_safety_line(frame_height(str(dst)))
        sid = entry["sceneId"]
        if sid not in worst_per_scene or bottom > worst_per_scene[sid][0]:
            worst_per_scene[sid] = (bottom, entry.get("time", 0))

    if not worst_per_scene:
        print("\n  [Safety Zone Pre-check] 无可用预览截图，跳过")
        return []

    print(f"\n  [Safety Zone Pre-check] safety line y={safety_line}")
    violations = []
    for sid, (bottom, t) in sorted(worst_per_scene.items(), key=lambda kv: str(kv[0])):
        if bottom > safety_line:
            overflow = bottom - safety_line
            print(f"    \u2717 Scene {sid}: content_bottom=y{bottom} "
                  f"OVERFLOW {overflow}px @{t:.0f}s")
            violations.append({"scene": sid, "content_bottom": bottom,
                               "overflow_px": overflow, "time": t})
        else:
            print(f"    \u2713 Scene {sid}: content_bottom=y{bottom} "
                  f"gap={safety_line - bottom}px @{t:.0f}s")
    return violations


def _numeric_scene_ids(items, key="scene_id"):
    """场景条目 → 数字 id 集合（兼容 scene_id 为 3 或 "s3" 两种写法）。"""
    ids = set()
    for it in items or []:
        raw = it.get(key)
        if raw is None:
            continue
        m = re.search(r'\d+', str(raw))
        if m:
            ids.add(int(m.group(0)))
    return ids


def compute_capture_gap(node_scenes, manifest_entries, capture_dir):
    """预览截图覆盖率裁定（2026-09-03）。

    Chrome 渲染进程中途崩溃时 capture manifest 只含部分场景，而脚本此前照旧打印
    [PASS] 且退出码 0——"没查"被读成"查了没问题"，与仓库已登记的
    "无记录既不能证真也不能证伪"同一缺陷形态。此处把覆盖缺口显式化。

    计"可实测"以 png 文件真正在位为准（instant_preview 复制到 temp_dir 的前置条件
    同源），而非 manifest 声明的 sceneId 条数。

    Returns: (gap_ids, covered_count, expected_count)
    """
    expected_ids = _numeric_scene_ids(node_scenes)
    measurable = [e for e in manifest_entries or []
                  if (Path(capture_dir) / e["file"]).exists()]
    captured_ids = _numeric_scene_ids(measurable, "sceneId")
    covered = captured_ids & expected_ids
    return sorted(expected_ids - covered), len(covered), len(expected_ids)


# ── 版面塌陷（每行字数）门禁：取数在 preview_capture.js，裁定在此 ──────────
#
# 存在理由：既有三条判据（字幕安全区预检、视觉边界、画面内容终检）管"越界"与"空场"，
# 没有一条管"字框过窄"。消费者副本一份**已交付**的竖屏片正文塌成每行 3 字的竖排列，
# 从预览到终检全程绿灯（读数见 操作日志.md [2026-10-12] 同名条与用例85 docstring）。
#
# 职责切分：JS 侧只报原始行盒读数（含逐元素跨采样最优值），本模块持有全部阈值与裁定——
# 一条判据不在两层各写一份，且"取数没跑成"由本层显式判 UNTESTED，不得由"没发现问题"短路。
TEXT_FIT_REQUIRED_KEYS = ("min_chars_per_line", "min_lines", "min_chars")
TEXT_FIT_RECORD_NAME = "_text_fit_check.json"


def load_text_fit_rules(rules_path=None):
    """读 config/quality/video_quality_rules.json → text_fit 节，返回 (rules, 归因)。

    fail-closed：缺文件／不可解析／缺节／缺键／值非正数一律返回 (None, 点名断在哪条腿)。
    本门禁**刻意不设默认阈值**：给一个回退常量即把"配置断链"读成"按某个数字查过且干净"。
    （与 `enhance_video_audio.pick_subtitle_palette` 的"读不到即回退深档"不同级——那条只
    换配色、不裁定合格与否；本条是门禁。）
    """
    path = Path(rules_path) if rules_path else QUALITY_DIR / "video_quality_rules.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"配置文件缺失：{path}"
    except (OSError, ValueError) as e:
        return None, f"配置文件不可解析：{path.name}（{type(e).__name__}: {e}）"
    section = data.get("text_fit") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return None, f"{path.name} 缺 text_fit 节（阈值断链，不得按猜测值裁定）"
    missing = [k for k in TEXT_FIT_REQUIRED_KEYS if k not in section]
    if missing:
        return None, f"{path.name} → text_fit 缺必需键 {missing}"
    rules = {}
    for key in TEXT_FIT_REQUIRED_KEYS:
        raw = section[key]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None, f"{path.name} → text_fit.{key} 不可解析为数值：{raw!r}"
        if not value > 0:
            return None, f"{path.name} → text_fit.{key} 非正数：{raw!r}"
        rules[key] = value
    return rules, None


def adjudicate_text_fit(fit, rules):
    """四态裁定 → (status, 报告行列表, findings)。

    判塌陷需三条**同时**成立（缺一即不判，2026-10-12 三件实测标定）：
      ① 行盒数 ≥ min_lines（收到 2，覆盖"2 行 × 极低字数"形态；旧探针取 3 会漏）
      ② 实测每行字数 < min_chars_per_line
      ③ 盒容量（getBoundingClientRect 宽／font-size，CJK 字宽≈1em）亦 < min_chars_per_line
    第③条是必需的区分项而非加严：短文本落在宽盒里自然折行不是塌陷。实测竖屏卡片标题
    "❸没发现≠没问题"（8 字／2 行＝每行 4 字、盒容 7.4 字）只按②会被判成缺陷，而它排版正常；
    真病灶那两枚的盒容只有 2.0 字（44px 框／22px 字）。

    测量面对象为 0 → NOT_APPLICABLE，不判 PASS：已交付的 13 场横屏件
    已交付的 13 场横屏知识口播件与竖屏骨架在 1920/1080 画布下正文极少折行，实测候选 0 个，
    此时判 PASS 即把"无对象"读成"查过且干净"（A04 同族）。
    """
    label = f"阈值：每行 ≥ {rules['min_chars_per_line']:g} 字" \
            f"／折行 ≥ {rules['min_lines']:g} 行／盒容 < {rules['min_chars_per_line']:g} 字才判塌陷"
    if fit is None:
        return gs.UNTESTED, [f"  [TEXT-FIT] UNTESTED — 捕获脚本未产出 text_fit 段"
                             f"（脚本版本不符或该步未跑），本项无从裁定；{label}"], [], {}
    if not isinstance(fit, dict):
        return (gs.UNTESTED,
                [f"  [TEXT-FIT] UNTESTED — text_fit 段不是对象（实取 "
                 f"{type(fit).__name__}），本项无从裁定；{label}"], [],
                {"reason": "text_fit_not_mapping"})
    hint = fit.get("status_hint")
    if hint:
        why = {"thresholds_not_passed": "调用方未传阈值 " + str(fit.get("missing")),
               "measure_failed": "取数异常 " + str(fit.get("error")),
               "no_time_source": "页面无 S-block／T-block 且 config 无场景窗口，"
                                 "稳态采样时刻无从落位"}.get(hint, str(hint))
        return gs.UNTESTED, [f"  [TEXT-FIT] UNTESTED — {why}；{label}"], [], dict(fit)

    raw_nodes = fit.get("nodes", [])
    if not isinstance(raw_nodes, list):
        # 取数面结构不符（版本不符或被改坏）→ 无从裁定，不得靠异常炸掉整轮预览
        return (gs.UNTESTED,
                [f"  [TEXT-FIT] UNTESTED — text_fit.nodes 不是列表"
                 f"（实取 {type(raw_nodes).__name__}），本项无从裁定；{label}"],
                [], {"reason": "nodes_not_list"})

    def _num(v):
        """读数必须是有限正数；None/字符串/NaN/Inf（JS 侧 Infinity 经 JSON 即 null）一律不算取到。"""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f == f and f not in (float("inf"), float("-inf")) else None

    nodes, unreadable = [], []
    for n in raw_nodes:
        if not isinstance(n, dict):
            unreadable.append((str(n)[:24], "节点不是对象"))
            continue
        lines_ = _num(n.get("lines"))
        chars_ = _num(n.get("chars"))
        if lines_ is None or chars_ is None:
            # 连"是否够上候选门槛"都无从判定＝同样是取数断链，不得静默把该节点当不存在
            # （若 JS 把 lines 改名，全量节点静默消失会伪装成"测量面无对象"的合法 N/A）
            missing = [k for k in ("lines", "chars") if _num(n.get(k)) is None]
            unreadable.append((str(n.get("key", "?"))[:36], "缺 " + "/".join(missing)))
            continue
        if lines_ >= rules["min_lines"] and chars_ >= rules["min_chars"]:
            cpl_, cap_ = _num(n.get("cpl")), _num(n.get("capacity"))
            if cpl_ is None or cap_ is None:
                # 已过候选门槛却缺核心读数＝取数断链，判未测并点名——
                # 绝不退回"缺即视为达标"的默认值（那会把改名/漏发读成查过且干净）
                missing = [k for k, v in (("cpl", n.get("cpl")),
                                          ("capacity", n.get("capacity"))) if _num(v) is None]
                unreadable.append((str(n.get("key", "?"))[:36], "缺 " + "/".join(missing)))
                continue
            # 数值单独存一对已验证的副本用于比较；原读数按 JS 发来的形态留给报告面
            # （`2` 与 `2.0` 的字面差异属显示面，不得因解析而改动既有报告文案）
            nodes.append(dict(n, _cpl=cpl_, _capacity=cap_))

    findings = [n for n in nodes
                if n["_cpl"] < rules["min_chars_per_line"]
                and n["_capacity"] < rules["min_chars_per_line"]]
    meta = {"time_src": fit.get("time_src"), "samples": fit.get("samples"),
            "measured_nodes": fit.get("measured_nodes"), "candidates": len(nodes),
            "unreadable": len(unreadable)}
    head = (f"  [TEXT-FIT] 时刻源={fit.get('time_src')} 采样 {fit.get('samples')} 次 "
            f"候选节点 {fit.get('measured_nodes')} 个（{label}）")
    if findings:
        lines = [f"  [TEXT-FIT] FAIL — {len(findings)} 个文本节点塌成竖排"] + [head]
        for n in sorted(findings, key=lambda x: x.get("cpl", 0))[:10]:
            lines.append(f"    ✗ {n.get('key')}: 每行 {n.get('cpl')} 字"
                         f"（{n.get('chars')} 字／{n.get('lines')} 行，"
                         f"盒宽 {n.get('box_w')}px／字号 {n.get('font_px')}px＝盒容 "
                         f"{n.get('capacity')} 字）@{n.get('at')}s")
        lines.append("    -> 该框放不下设计下限要求的字数；修向＝加宽容器或缩短文本"
                     "（版面骨架的 min-width:calc(var(--min-cpl)*var(--fs-body)) 即此规则的设计侧实现）")
        return gs.FAIL, lines, findings, meta
    if unreadable:
        # 有节点够上了候选门槛、却没有可用读数＝取数断链。判未测并逐节点点名，
        # 既不得读成 PASS（缺键即视为达标），也不得读成 NOT_APPLICABLE（那是"测量面无对象"）。
        # 位置在 findings 之后、PASS 之前：即便其余节点全部达标，断链面也不被"其余节点"洗白。
        detail = "；".join(f"{k}（{why}）" for k, why in unreadable[:10])
        return (gs.UNTESTED,
                [f"  [TEXT-FIT] UNTESTED — {len(unreadable)} 个候选节点缺核心读数：{detail}"
                 f"（取数面与裁定面字段名不一致，或该值在 JS 侧为 Infinity/NaN）；{label}"],
                [], meta)
    if not nodes:
        return (gs.NOT_APPLICABLE,
                [f"  [TEXT-FIT] NOT_APPLICABLE — 测量面无候选节点"
                 f"（折行 ≥ {rules['min_lines']:g} 行且字符 ≥ {rules['min_chars']:g} 的自持文本节点 0 个），"
                 f"本项不裁定也不计通过"] + [head], [], meta)
    return (gs.PASS,
            [f"  [TEXT-FIT] PASS — {len(nodes)} 个候选节点每行字数均达下限"] + [head], [], meta)


def write_text_fit_record(temp_dir, status, findings, meta, report_lines=None, decision=None):
    """裁定的文件级留痕：落项目 temp 的 _text_fit_check.json。

    "检查点跑过"需要正证据而不是由"没有异常记录"反推（与 duration_budget 通过路径
    留痕同判据），故 PASS 亦写盘；每次预览全量覆写本轮结论，读取侧只信当轮文件。
    """
    record = {
        "status": status,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "findings": findings,
        "thresholds": meta.get("thresholds") or None,
        "time_src": meta.get("time_src"),
        "samples": meta.get("samples"),
        "measured_nodes": meta.get("measured_nodes"),
        "candidates": meta.get("candidates"),
    }
    if report_lines:
        record["report_lines"] = [str(x).strip() for x in report_lines]
    if decision:
        record["decision"] = decision
    path = Path(temp_dir) / TEXT_FIT_RECORD_NAME
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _thumb_size(png_bytes):
    """按截图实测宽高比选缩略图尺寸（竖版 1080×1920 压成 480×270 会失真到不可用）。"""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            w, h = img.size
    except Exception:
        w, h = 16, 9
    return (270, 480) if h > w else (480, 270)


def generate_grid(screenshots, scene_info, output_path, cols=4):
    """Generate a thumbnail grid from screenshots using Pillow.

    Args:
        screenshots: list of (png_bytes, label) tuples
        scene_info: list of scene dicts for labeling
        output_path: output PNG path
        cols: number of columns in grid
    """
    from PIL import Image, ImageDraw, ImageFont

    thumb_w, thumb_h = _thumb_size(screenshots[0][0]) if screenshots else (480, 270)
    label_h = 36  # label height
    cell_h = thumb_h + label_h
    padding = 10

    n = len(screenshots)
    rows = (n + cols - 1) // cols

    grid_w = cols * (thumb_w + padding) + padding
    grid_h = rows * (cell_h + padding) + padding

    grid = Image.new("RGB", (grid_w, grid_h), (15, 23, 42))  # dark background
    draw = ImageDraw.Draw(grid)

    # Try to load a font, fallback to default
    try:
        font = ImageFont.truetype("arial.ttf", 16)
        font_small = ImageFont.truetype("arial.ttf", 13)
    except Exception:
        font = ImageFont.load_default()
        font_small = font

    for i, (png_bytes, label) in enumerate(screenshots):
        col = i % cols
        row = i // cols
        x = padding + col * (thumb_w + padding)
        y = padding + row * (cell_h + padding)

        # Draw label
        draw.text((x, y), label, fill=(226, 232, 240), font=font)

        # Paste thumbnail
        try:
            with Image.open(io.BytesIO(png_bytes)) as img:
                img = img.resize((thumb_w, thumb_h), Image.LANCZOS)
                grid.paste(img, (x, y + label_h))
        except Exception as e:
            # Draw placeholder
            draw.rectangle([x, y + label_h, x + thumb_w, y + cell_h],
                           fill=(30, 41, 59), outline=(100, 116, 139))
            draw.text((x + 10, y + label_h + 10), f"[Error: {str(e)[:30]}]",
                      fill=(248, 113, 113), font=font_small)

    grid.save(str(output_path), "PNG")
    return grid_w, grid_h


def load_config(config_path):
    """Load pipeline config JSON."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def start_http_server(directory, port=8765):
    """Start a simple HTTP server to serve HTML and assets locally.

    Returns (process, base_url).
    """
    import http.server
    import threading

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, format, *args):
            pass  # Suppress logs

    server = http.server.HTTPServer(("127.0.0.1", port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)  # Let server start
    return server, f"http://127.0.0.1:{port}"


def find_local_gsap():
    """Find a local copy of GSAP library."""
    gsap_candidates = [
        # npm cache (installed by instant_preview engine)
        ROOT / "程序文件" / "运行环境" / "npm_cache" / "gsap-3.14.2.min.js",
        # node_modules from npm install
        ROOT / "过程产物" / "临时产物" / "node_modules" / "gsap" / "dist" / "gsap.min.js",
        # HyperFrames project copies
        ROOT / "程序文件" / "源码" / "hyperframes" / "hospital-partition-wall" / "gsap.min.js",
        ROOT / "程序文件" / "源码" / "hyperframes" / "hotel-partition-wall" / "gsap.min.js",
        ROOT / "程序文件" / "源码" / "hyperframes" / "quickstart-demo" / "gsap.min.js",
    ]
    for path in gsap_candidates:
        if path.exists() and path.stat().st_size > 1000:
            return path
    return None


def patch_html_with_local_gsap(html_path, temp_dir):
    """Create a patched copy of HTML with local GSAP injected.

    Returns the path to the patched HTML file.
    """
    gsap_path = find_local_gsap()
    if not gsap_path:
        print("  WARNING: No local GSAP copy found, will rely on CDN")
        return html_path

    print(f"  Using local GSAP: {gsap_path.name} ({gsap_path.stat().st_size // 1024} KB)")
    gsap_code = gsap_path.read_text(encoding='utf-8')

    html_content = html_path.read_text(encoding='utf-8')
    # Find the CDN script tag and replace with inline GSAP
    cdn_pattern = re.compile(
        r'<script\s+src="https?://cdn[^"]*gsap[^"]*"[^>]*></script>',
        re.IGNORECASE
    )
    patched = cdn_pattern.sub(
        lambda m: f'<script>/* Local GSAP injection */\n{gsap_code}\n</script>',
        html_content,
        count=1
    )

    if patched == html_content:
        print("  WARNING: Could not find GSAP CDN tag to patch")
        return html_path

    # Also remove Google Fonts CDN links (they can block rendering in offline/slow-network envs)
    fonts_pattern = re.compile(
        r'<link[^>]*href="https?://fonts\.(googleapis|gstatic)\.com[^"]*"[^>]*>',
        re.IGNORECASE
    )
    patched = fonts_pattern.sub('<!-- fonts removed for preview -->', patched)
    # Add system font fallback
    if '<style>' in patched:
        patched = patched.replace(
            '<style>',
            '<style>\n/* Preview font fallback */\n'
            'body{font-family:"Microsoft YaHei","Segoe UI",system-ui,sans-serif !important;}\n'
            '.mono{font-family:"Consolas","Courier New",monospace !important;}\n'
        )

    # Write patched HTML to temp directory
    patched_path = temp_dir / html_path.name
    patched_path.write_text(patched, encoding='utf-8')
    print(f"  Patched HTML: {patched_path}")
    return patched_path


def main():
    parser = argparse.ArgumentParser(
        description="Instant Preview Engine — pre-render scene thumbnails + animation density")
    parser.add_argument("--html", required=True, help="Path to index.html")
    parser.add_argument("--config", required=True, help="Path to pipeline config JSON")
    parser.add_argument("--output", default=None,
                        help="Output preview grid path (default: <temp_dir>/preview_grid.png)")
    parser.add_argument("--density-only", action="store_true",
                        help="Only run animation density analysis, skip Chrome screenshots")
    parser.add_argument("--chrome", default=DEFAULT_CHROME,
                        help="Path to Chrome executable")
    parser.add_argument("--port", type=int, default=9222,
                        help="Chrome debugging port (default: 9222)")
    parser.add_argument("--serve-port", type=int, default=8765,
                        help="Local HTTP server port for serving HTML (default: 8765)")
    parser.add_argument("--accept-text-fit-untested", action="store_true",
                        help="版面塌陷判据无从裁定时知情放行（默认阻断；放行必须留痕）")
    parser.add_argument("--text-fit-rules", default=None,
                        help="覆盖 text_fit 阈值文件路径（回归取证用；默认读 "
                             "config/quality/video_quality_rules.json 单一权威源）")
    args = parser.parse_args()

    html_path = Path(args.html)
    config_path = Path(args.config)

    if not html_path.exists():
        print(f"ERROR: HTML not found: {html_path}")
        return 1
    if not config_path.exists():
        print(f"ERROR: Config not found: {config_path}")
        return 1

    config = load_config(config_path)
    cover_duration = float(config.get("cover_duration", 0))
    # P0-03 指针模式：narration_source 为场景权威源
    try:
        ptr_scenes, ptr_path = resolve_narration_scenes(config, config_path, cover_duration)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 1
    if ptr_path is not None:
        config["scenes"] = ptr_scenes
        print(f"  Scene source: narration_source ({ptr_path.name})")
    scenes = config.get("scenes", [])
    video_duration = float(config.get("video_duration", 0))

    # Determine output paths
    temp_subdir = config.get("paths", {}).get("temp_subdir", "preview_temp")
    temp_dir = ROOT / "过程产物" / "临时产物" / temp_subdir
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output) if args.output else temp_dir / "preview_grid.png"
    report_path = temp_dir / "preview_report.txt"

    print("=" * 60)
    print("INSTANT PREVIEW ENGINE")
    print("=" * 60)
    print(f"  HTML:   {html_path.name}")
    print(f"  Config: {config_path.name}")
    print(f"  Scenes: {len(scenes)}, Duration: {video_duration}s")
    print()

    # --- Phase 1: Animation Density Analysis (always runs) ---
    print("--- Phase 1: Animation Density Analysis ---")
    html_content = html_path.read_text(encoding='utf-8')
    density = analyze_animation_density(html_content, scenes, cover_duration)

    desert_windows = []
    sparse_windows = []
    for start, end, count, rating in density:
        marker = ""
        if rating == "DESERT":
            marker = " <<< DESERT"
            desert_windows.append((start, end))
        elif rating == "SPARSE":
            marker = " < sparse"
            sparse_windows.append((start, end))
        print(f"  {start:6.0f}s-{end:6.0f}s: {count:>3} events [{rating}]{marker}")

    # --- Phase 2: Chrome Screenshots via puppeteer-core (unless --density-only) ---
    safety_violations = []
    capture_count = 0
    capture_gap = []
    # Phase 2 整体包在 try/except 里（node 崩/截图复制失败都不得把脚本炸掉）。但"吞掉异常"
    # 不等于"检查跑过了"：旧形态下异常发生后 safety_violations 仍是空表，报告面照样印
    # "[PASS] All scenes stay above subtitle safety line"——把"没跑成"印成"查过且干净"，
    # 与本仓四态纪律相悖。phase2_error 让这一态可见、可阻断。
    phase2_error = None
    safety_untested = None
    # 版面塌陷门禁：阈值先读，读不回即整项无从裁定（不得带着猜测值跑取数）
    text_fit_rules, text_fit_rules_reason = load_text_fit_rules(args.text_fit_rules)
    fit_status = None
    fit_lines = []
    fit_findings = []
    fit_meta = {"thresholds": text_fit_rules}
    if text_fit_rules is None:
        fit_status = gs.UNTESTED
        fit_lines = [f"  [TEXT-FIT] UNTESTED — 阈值断链：{text_fit_rules_reason}"]
    elif args.density_only:
        fit_status = gs.NOT_APPLICABLE
        fit_lines = ["  [TEXT-FIT] NOT_APPLICABLE — 本次只跑密度分析"
                     "（--density-only 不启动截图，行盒取数无对象）"]
    if not args.density_only:
        print("\n--- Phase 2: Chrome Screenshots (puppeteer-core) ---")

        if not os.path.exists(args.chrome):
            print(f"ERROR: Chrome not found: {args.chrome}")
            print("  Use --chrome to specify Chrome path, or --density-only to skip screenshots")
            return 1

        # Patch HTML with local GSAP to avoid CDN dependency
        patched_html = patch_html_with_local_gsap(html_path, temp_dir)
        html_needs_restore = False
        if patched_html != html_path:
            # Temporarily replace original HTML with patched version
            backup_html = html_path.with_suffix('.html.bak_preview')
            shutil.copy2(str(html_path), str(backup_html))
            shutil.copy2(str(patched_html), str(html_path))
            html_needs_restore = True

        # Capture output directory (project-managed temp area, inside the scan surface)
        preview_temp_dir = str(PREVIEW_TEMP)
        os.makedirs(preview_temp_dir, exist_ok=True)
        capture_dir = Path(preview_temp_dir) / "preview_captures"
        capture_dir.mkdir(parents=True, exist_ok=True)

        # Node.js script path
        capture_script = ROOT / "程序文件" / "脚本" / "preview_capture.js"
        if not capture_script.exists():
            print(f"ERROR: Capture script not found: {capture_script}")
            return 1

        # Set NODE_PATH to find puppeteer-core from HyperFrames
        node_env = os.environ.copy()
        hf_node_modules = os.path.join(
            os.environ.get('APPDATA', ''),
            'npm', 'node_modules', 'hyperframes', 'node_modules'
        )
        if os.path.exists(hf_node_modules):
            existing = node_env.get('NODE_PATH', '')
            node_env['NODE_PATH'] = f"{hf_node_modules};{existing}" if existing else hf_node_modules

        # Fixed Chrome profile dir inside PREVIEW_TEMP (project temp area; the ASCII
        # requirement behind the old D:-root placement was measured and does not hold)
        chrome_user_data = Path(preview_temp_dir) / "chrome_profile"
        chrome_user_data.mkdir(parents=True, exist_ok=True)
        node_env['CHROME_USER_DATA_DIR'] = str(chrome_user_data)

        # preview_capture.js 读的是磁盘上的 config；指针模式下那份 config 有意不含
        # scenes（单一权威源：场景在 narration.json）。给它一份"指针已解析"的快照，
        # 否则它数到 0 个场景、一张图都不截，安全区预检便在没有像素证据的情况下判 PASS。
        node_config = dict(config)
        if ptr_path is not None:
            # 解析器输出的是绝对时间，node 内部还会再 +cover_duration，
            # 故此处平移回它期望的相对语义。
            node_config["scenes"] = [
                {**s,
                 "start": round(float(s["start"]) - cover_duration, 3),
                 "end": round(float(s["end"]) - cover_duration, 3)}
                for s in scenes
            ]
        node_config_path = temp_dir / "preview_config_resolved.json"
        node_config_path.write_text(
            json.dumps(node_config, ensure_ascii=False, indent=2), encoding='utf-8')

        try:
            # Run Node.js capture script
            cmd = [
                'node', str(capture_script),
                str(html_path), str(node_config_path), str(capture_dir)
            ]
            # 阈值由 Python 侧（权威源读者）下传给取数侧当门槛；取数侧刻意不设缺省值，
            # 缺传即显式回吐 thresholds_not_passed，不静默按某个数字跑一趟。
            if text_fit_rules is not None:
                cmd += [f"--fit-min-cpl={text_fit_rules['min_chars_per_line']:g}",
                        f"--fit-min-lines={text_fit_rules['min_lines']:g}",
                        f"--fit-min-chars={text_fit_rules['min_chars']:g}"]
            print(f"  Running: {' '.join(cmd[:3])} ...")
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                env=node_env, timeout=120
            )

            # Print Node.js output
            for line in result.stdout.strip().split('\n'):
                print(f"  {line}")
            if result.stderr.strip():
                for line in result.stderr.strip().split('\n'):
                    print(f"  [stderr] {line}")

            if result.returncode != 0:
                print(f"  Capture script failed with exit code {result.returncode}")
            else:
                # Read manifest and generate grid
                manifest_path = capture_dir / "capture_manifest.json"
                if manifest_path.exists():
                    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                    capture_count = len(manifest.get("scenes", []))
                    for err in manifest.get("errors", []):
                        print(f"    capture error: scene {err.get('sceneId')}: {err.get('error')}")

                    # 覆盖率裁定：Chrome 渲染进程中途崩溃时 manifest 只含部分场景
                    # （2026-09-03 实测：某商业类项目截到 5/11 后 Target closed），
                    # 脚本此前照样打印 [PASS] 并退出 0——盲区必须成为机器可判的事实。
                    capture_gap, covered_scenes, expected_scenes = compute_capture_gap(
                        node_config.get("scenes", []), manifest.get("scenes", []), capture_dir)
                    print(f"\n  [COVERAGE] 安全区可实测覆盖 {covered_scenes}"
                          f"/{expected_scenes} 场"
                          + (f"，缺: {capture_gap}" if capture_gap else "（完整）"))

                    # 版面塌陷裁定：取数面在 node、判据面在此，两轴各证一件事。
                    # manifest 里没有 text_fit 段属"取数没跑成"，由 adjudicate 判 UNTESTED
                    # （版本不符/该步未跑都会显形在这里，不得读成"查过且干净"）。
                    # 阈值断链时不进入 adjudicate：它自身要读阈值出文案，带 None 进去即
                    # TypeError 把整轮预览炸掉——"已经判完的项"不得再走一遍取数。
                    if text_fit_rules is not None:
                        fit_status, fit_lines, fit_findings, _fit_meta = adjudicate_text_fit(
                            manifest.get("text_fit"), text_fit_rules)
                        fit_meta.update(_fit_meta)
                    for line in fit_lines:
                        print(line)

                    screenshots = []
                    for entry in manifest.get("scenes", []):
                        png_path = capture_dir / entry["file"]
                        if png_path.exists():
                            label = f"S{entry['sceneId']} @{entry['time']:.0f}s [{entry['frame']}]"
                            screenshots.append((png_path.read_bytes(), label))

                    if screenshots:
                        print(f"\n  Generating grid: {len(screenshots)} thumbnails...")
                        gw, gh = generate_grid(screenshots, scenes, output_path)
                        size_kb = output_path.stat().st_size / 1024
                        print(f"  Grid saved: {output_path} ({gw}x{gh}, {size_kb:.0f} KB)")

                        # Copy screenshots to project temp dir
                        for entry in manifest.get("scenes", []):
                            src = capture_dir / entry["file"]
                            dst = temp_dir / f"preview_{entry['file']}"
                            if src.exists():
                                shutil.copy2(str(src), str(dst))

                        # 安全区预检（prefab 复盘决策一）：溢出在此阻断，
                        # 不把布局问题拖到全量渲染后才暴露。
                        try:
                            safety_violations = check_preview_safety(
                                manifest.get("scenes", []), temp_dir)
                        except ImportError as e:
                            # 判据模块装不上（PIL/numpy 缺失）＝这一项没跑成。旧形态只 print
                            # 一行 skipped，随后 Overall 照旧印 "[PASS] All scenes stay above
                            # subtitle safety line"——把"没跑成"印成"查过且干净"。现点名 UNTESTED。
                            # 刻意不阻断（与 phase2_error 不同档）：消费者环境装不上 PIL 时，
                            # 拦下即等于把"本机缺依赖"报成"成片有缺陷"；此处只撤掉伪 PASS 文案。
                            safety_untested = f"字幕安全区判据模块不可用：{e}"
                            print(f"  [Safety Zone Pre-check] UNTESTED — {safety_untested}"
                                  "（不判 PASS）")
                    else:
                        print("  No screenshots to generate grid from")
                else:
                    print("  Capture manifest not found")

        except subprocess.TimeoutExpired:
            phase2_error = "Capture script timed out after 120s"
            print(f"  ERROR: {phase2_error}")
        except Exception as e:
            phase2_error = f"{type(e).__name__}: {e}"
            print(f"  ERROR during preview capture: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Restore original HTML if we patched it
            if html_needs_restore:
                backup_html = html_path.with_suffix('.html.bak_preview')
                if backup_html.exists():
                    shutil.copy2(str(backup_html), str(html_path))
                    backup_html.unlink()
                    print("  Original HTML restored")

            # Clean up Chrome temp data (avoid workspace pollution)
            preview_cleanup_dir = PREVIEW_TEMP
            if preview_cleanup_dir.exists():
                try:
                    shutil.rmtree(str(preview_cleanup_dir), ignore_errors=True)
                    print(f"  Cleaned up: {preview_cleanup_dir}")
                except Exception as cleanup_err:
                    print(f"  Warning: could not clean {preview_cleanup_dir}: {cleanup_err}")

    # 取数面未跑成的兜底：捕获脚本超时/非零返回/整体异常时 manifest 从未被读到，
    # 此时该项不得留成"没有结论也没有点名"——补一条 UNTESTED 并点名成因。
    if fit_status is None:
        fit_status = gs.UNTESTED
        fit_lines = ["  [TEXT-FIT] UNTESTED — 捕获脚本未成功返回，text_fit 取数无落盘证据"]
        print(fit_lines[0])
    fit_decision = ("accepted_untested" if fit_status == gs.UNTESTED
                    and args.accept_text_fit_untested else None)
    try:
        fit_record_path = write_text_fit_record(
            temp_dir, fit_status, fit_findings, fit_meta,
            report_lines=fit_lines, decision=fit_decision)
    except OSError as e:
        fit_record_path = None
        print(f"  [TEXT-FIT] 留痕写入失败（不影响裁定，影响追溯）: {e}")

    # --- Phase 3: Generate Report ---
    print("\n--- Phase 3: Preview Report ---")
    report_lines = []
    report_lines.append(f"Preview Report: {html_path.name}")
    report_lines.append(f"Video Duration: {video_duration}s, Scenes: {len(scenes)}")
    report_lines.append("")

    # Scene window utilization
    report_lines.append("== Scene Window Utilization ==")
    # Read TTS durations if available
    tts_dir = temp_dir / "tts_44k"
    manifest_path = temp_dir / "tts_manifest.json"
    tts_available = tts_dir.exists() and manifest_path.exists()

    if tts_available:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for scene in scenes:
            sid = str(scene["scene_id"])
            entry = manifest.get(sid)
            h = entry.get('hash') if isinstance(entry, dict) else entry
            if h:
                hq_file = tts_dir / f"tts_{h}_hq.wav"
                if hq_file.exists():
                    try:
                        result = subprocess.run(
                            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                             "-of", "json", str(hq_file)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace'
                        )
                        tts_dur = float(json.loads(result.stdout)["format"]["duration"])
                        window = float(scene["end"]) - float(scene["start"])
                        util = tts_dur / window * 100 if window > 0 else 0
                        status = "OK" if util > 85 else ("WARN" if util > 75 else "LOW")
                        report_lines.append(
                            f"  Scene {sid}: TTS={tts_dur:.1f}s / window={window:.1f}s "
                            f"util={util:.0f}% [{status}]"
                        )
                    except Exception:
                        pass
    else:
        report_lines.append("  (TTS data not available — run TTS step first)")

    # Animation density summary
    report_lines.append("")
    report_lines.append("== Animation Density ==")
    for start, end, count, rating in density:
        report_lines.append(f"  {start:.0f}s-{end:.0f}s: {count} events [{rating}]")

    if desert_windows:
        report_lines.append("")
        report_lines.append(f"!! {len(desert_windows)} ANIMATION DESERT(S) DETECTED:")
        for start, end in desert_windows:
            report_lines.append(f"   {start:.0f}s-{end:.0f}s: 30s+ with ZERO animation events")
            report_lines.append("   -> Consider adding continuous animations or reducing scene duration")

    if sparse_windows:
        report_lines.append("")
        report_lines.append(f"! {len(sparse_windows)} sparse window(s):")
        for start, end in sparse_windows:
            report_lines.append(f"   {start:.0f}s-{end:.0f}s: < 3 events in 30s")

    # Text fit（每行字数）——版面塌陷门禁，四态各有一行可读卡点
    report_lines.append("")
    report_lines.append("== Text Fit (每行字数) ==")
    report_lines.extend(fit_lines)
    report_lines.append(f"  状态: {fit_status}"
                        + (f"（已按 --accept-text-fit-untested 放行，留痕 decision="
                           f"{fit_decision}）" if fit_decision else ""))
    if fit_record_path is not None:
        report_lines.append(f"  留痕: {fit_record_path}")

    # Overall assessment
    report_lines.append("")
    report_lines.append("== Overall Assessment ==")
    # 安全区预检结果（prefab 复盘决策一）：溢出是硬阻断，不是建议
    if safety_violations:
        report_lines.append(f"  [FAIL] {len(safety_violations)} scene(s) overflow subtitle safety zone:")
        for v in safety_violations:
            report_lines.append(
                f"         Scene {v['scene']}: content_bottom=y{v['content_bottom']} "
                f"overflow {v['overflow_px']}px @{v['time']:.0f}s")
        report_lines.append("         -> reduce content height / tighten padding-bottom, then re-preview")
    elif not args.density_only:
        if phase2_error:
            report_lines.append(
                f"  [UNTESTED] 字幕安全区本次未跑完（预览阶段异常：{phase2_error}）"
                " —— 不得判 PASS，异常被打印不等于检查通过")
        elif safety_untested:
            report_lines.append(f"  [UNTESTED] 字幕安全区 {safety_untested} —— 不得判 PASS")
        elif capture_gap:
            report_lines.append(
                f"  [FAIL] {len(capture_gap)} 场无预览像素（缺: {capture_gap}），"
                f"安全区预检对这些场无证据 —— 不得判 PASS")
        elif capture_count:
            report_lines.append("  [PASS] All scenes stay above subtitle safety line")
        else:
            report_lines.append(
                "  [FAIL] 未截到任何场景截图，安全区预检无像素证据 —— 不得判 PASS")

    # 版面塌陷四态照常进结论面：N/A 与 UNTESTED 都不得写成通过（四态纪律）
    if fit_status == gs.FAIL:
        report_lines.append(f"  [FAIL] {len(fit_findings)} 个文本节点塌成竖排"
                            "（每行字数低于设计下限，详见 == Text Fit == 节）")
    elif fit_status == gs.UNTESTED:
        report_lines.append("  [UNTESTED] 版面塌陷判据无从裁定"
                            + (f"（decision={fit_decision}，已知情放行）" if fit_decision
                               else "—— 不得读成该项干净"))
    elif fit_status == gs.NOT_APPLICABLE:
        report_lines.append("  [N/A] 版面塌陷判据无测量对象（对象为 0 ≠ 查过且干净）")
    total_deserts = len(desert_windows)
    total_sparse = len(sparse_windows)
    if total_deserts == 0 and total_sparse <= 1:
        report_lines.append("  [PASS] Animation density is adequate")
    elif total_deserts <= 2:
        report_lines.append(f"  [WARN] {total_deserts} desert(s), {total_sparse} sparse — review recommended")
    else:
        report_lines.append(f"  [FAIL] {total_deserts} desert(s), {total_sparse} sparse — significant whitespace risk")

    # Write report
    report_text = "\n".join(report_lines)
    report_path.write_text(report_text, encoding='utf-8')
    print(f"  Report saved: {report_path}")

    # Print summary to stdout
    print()
    print(report_text)

    # 安全区溢出 → 非零退出：pipeline_runner.step_preview 视 preview 失败为 FATAL，
    # render 硬门禁随之拒跑，布局问题不再消耗全量渲染。
    if safety_violations:
        print(f"\nPREVIEW FAILED: {len(safety_violations)} scene(s) overflow the subtitle "
              f"safety zone — fix layout before rendering.")
        return 2
    # 版面塌陷与"越界"同档：都是改完画面才能继续的结构缺陷，放进渲染就是白跑一次全量。
    if fit_status == gs.FAIL:
        print(f"\nPREVIEW FAILED: {len(fit_findings)} 个文本节点塌成竖排"
              "（每行字数低于 video_quality_rules.json → text_fit 的设计下限）—— "
              "加宽容器或缩短文本后重跑预览，禁止带此形态进入全量渲染。")
        return 2

    # 退出码 2 = 真实溢出；3 = 预检存在盲区（零截图或部分场景未截到）。
    # 两者都不得当作 PASS 放行——盲区里溢出多少张图都不会被看见。
    if not args.density_only and phase2_error:
        print(f"\nPREVIEW FAILED: 预览阶段异常（{phase2_error}）—— 字幕安全区与版面塌陷"
              "两项本次没有跑完，异常被打印不等于检查通过。修好取数链后重跑预览，"
              "禁止带着这一盲区进入全量渲染。")
        return 3
    if not args.density_only and capture_count == 0:
        print("\nPREVIEW FAILED: 零截图，字幕安全区未被实测 —— "
              "检查场景发现/Chrome/捕获链路后重跑，禁止带此盲区进入全量渲染。")
        return 3
    if not args.density_only and capture_gap:
        print(f"\nPREVIEW FAILED: {len(capture_gap)} 场未截到预览图（缺: {capture_gap}），"
              "字幕安全区对这些场无证据 —— 常见成因是 Chrome 渲染进程中途崩溃，"
              "重跑预览至覆盖完整后再进入全量渲染。")
        return 3
    # 塌陷判据无从裁定：默认与盲区同档阻断（"没测成"不得读成"没查出问题"）；
    # 知情放行必须走显式开关，且已在 _text_fit_check.json 留 decision=accepted_untested。
    if fit_status == gs.UNTESTED and not args.accept_text_fit_untested:
        print("\nPREVIEW FAILED: 版面塌陷判据无从裁定（阈值断链/取数未跑成）—— "
              "按点名成因修复后重跑；确需带此盲区进入渲染，用 --accept-text-fit-untested "
              "显式放行（会留痕）。")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
