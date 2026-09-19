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
from _script_env import ROOT, PREVIEW_TEMP, resolve_narration_scenes

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

    Uses ASCII-only temp dir on D: drive to avoid Chinese path issues on Windows.
    Chrome cannot handle Chinese characters in user-data-dir or screenshot paths.
    """
    ascii_temp = str(PREVIEW_TEMP)
    os.makedirs(ascii_temp, exist_ok=True)
    user_data_dir = tempfile.mkdtemp(prefix="chrome_pv_", dir=ascii_temp)
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

        # Capture output directory (ASCII path outside workspace to avoid pollution)
        ascii_temp = str(PREVIEW_TEMP)
        os.makedirs(ascii_temp, exist_ok=True)
        capture_dir = Path(ascii_temp) / "preview_captures"
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

        # Provide a Chrome user-data-dir on D: drive (ASCII-only, avoids C: drive)
        chrome_user_data = Path(ascii_temp) / "chrome_profile"
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
                    # （2026-09-03 实测 wsi-commercial 截到 5/11 后 Target closed），
                    # 脚本此前照样打印 [PASS] 并退出 0——盲区必须成为机器可判的事实。
                    capture_gap, covered_scenes, expected_scenes = compute_capture_gap(
                        node_config.get("scenes", []), manifest.get("scenes", []), capture_dir)
                    print(f"\n  [COVERAGE] 安全区可实测覆盖 {covered_scenes}"
                          f"/{expected_scenes} 场"
                          + (f"，缺: {capture_gap}" if capture_gap else "（完整）"))

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
                            print(f"  [Safety Zone Pre-check] skipped ({e})")
                    else:
                        print("  No screenshots to generate grid from")
                else:
                    print("  Capture manifest not found")

        except subprocess.TimeoutExpired:
            print("  ERROR: Capture script timed out after 120s")
        except Exception as e:
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
            ascii_cleanup = PREVIEW_TEMP
            if ascii_cleanup.exists():
                try:
                    shutil.rmtree(str(ascii_cleanup), ignore_errors=True)
                    print(f"  Cleaned up: {ascii_cleanup}")
                except Exception as cleanup_err:
                    print(f"  Warning: could not clean {ascii_cleanup}: {cleanup_err}")

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
        if capture_gap:
            report_lines.append(
                f"  [FAIL] {len(capture_gap)} 场无预览像素（缺: {capture_gap}），"
                f"安全区预检对这些场无证据 —— 不得判 PASS")
        elif capture_count:
            report_lines.append("  [PASS] All scenes stay above subtitle safety line")
        else:
            report_lines.append(
                "  [FAIL] 未截到任何场景截图，安全区预检无像素证据 —— 不得判 PASS")
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
    # 退出码 2 = 真实溢出；3 = 预检存在盲区（零截图或部分场景未截到）。
    # 两者都不得当作 PASS 放行——盲区里溢出多少张图都不会被看见。
    if not args.density_only and capture_count == 0:
        print("\nPREVIEW FAILED: 零截图，字幕安全区未被实测 —— "
              "检查场景发现/Chrome/捕获链路后重跑，禁止带此盲区进入全量渲染。")
        return 3
    if not args.density_only and capture_gap:
        print(f"\nPREVIEW FAILED: {len(capture_gap)} 场未截到预览图（缺: {capture_gap}），"
              "字幕安全区对这些场无证据 —— 常见成因是 Chrome 渲染进程中途崩溃，"
              "重跑预览至覆盖完整后再进入全量渲染。")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
