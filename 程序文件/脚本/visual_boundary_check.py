"""Visual boundary check — verify rendered content stays above subtitle zone.

Extracts frames from the rendered video at three sample points per scene
(30% / 50% / 70% of scene duration), measures the lowest content pixel
y-coordinate at each point, and fails if the worst-case measurement
extends into the subtitle safety zone.

This is a HARD GATE in the pipeline: it runs AFTER rendering and
BEFORE subtitle burn-in. If content overlaps the subtitle zone,
the pipeline is blocked and the issue is reported with exact
scene numbers and pixel measurements.

Usage:
    python visual_boundary_check.py --video render_raw.mp4 --config pipeline_config.json
    python visual_boundary_check.py --video render_raw.mp4 --scenes-json '[{"start":0,"end":30},...]'
"""

import argparse
import json
import sys
import subprocess
import tempfile
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Force UTF-8 output on Windows GBK consoles
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("ERROR: PIL and numpy required. Install: pip install Pillow numpy")
    sys.exit(1)

import _gate_status as gs


# ── Configuration ──────────────────────────────────────────────
# Subtitle safety line: content must stay ABOVE this y-coordinate.
# 1080 基准：底部预留 220px 字幕带 → y=860（渐变起点，字幕文字约 y=940）。
BASELINE_FRAME_H = 1080
BASELINE_BAND_PX = 220

# 预留带是"占画布高度的比例"，不是固定 px。实测依据（2026-09-02，
# 过程产物/临时产物/wsi-cases_2026_md/probe_subtitle_band.py）：用生产
# ASS 样式（enhance_video_audio.py 的 force_style）在 1920×1080 与
# 1080×1920 各烧 1/2/3 行字幕并量墨迹行——ffmpeg subtitles 滤镜按
# PlayResY 排版，故底隙=0.0917h、行距=0.052h、N 行墨迹顶边
# =(0.8696-0.052(N-1))h 全部与画布高度成正比。h=1080 且 2 行时预留带
# 恰为 220px，即原常量 860 只是这一个特例，竖版沿用它会把安全区算错。
# 已知局限：行数恒取 2（渲染前无法从 SRT 预知实际换行数），故 3 行字幕
# 在 16:9 下墨迹顶边 y=827 仍低于 860 线——此盲区适配前后一致。
SUBTITLE_LINE_PITCH_RATIO = 0.052
SUBTITLE_BAND_1LINE_RATIO = BASELINE_BAND_PX / BASELINE_FRAME_H - SUBTITLE_LINE_PITCH_RATIO

# Minimum bright pixels in a row to count as "content" (avoids noise)
CONTENT_PIXEL_THRESHOLD = 20

# Brightness threshold to distinguish content from dark background
BRIGHTNESS_THRESHOLD = 80  # for R/G channels
BLUE_THRESHOLD = 100       # for B channel (dark blue background)

# ── Content vacuum detection (有声无画门禁) ──────────────────────
# 与 dead-air 门禁对称：那个拦“有画没声”，这个拦“有声没画”。
# 成片反馈：旁白已开始念主体内容，但主视觉元素晚入场 8s+，画面长时间
# 只有一行标题。判定：场景早期内容覆盖率远低于晚期（内容晚到）即拒收。
VACUUM_MIN_SCENE_DUR = 6.0      # 短场景（封面/转场）不检

# 内容覆盖率统计区间顶部，同样按 1080 基准等比导出（覆盖率阈值
# 0.35/0.15 是在 1080 的统计区间形状上标定的，区间形状必须一起缩放）
VACUUM_CONTENT_TOP_RATIO = 120 / BASELINE_FRAME_H
VACUUM_EARLY_RATIO = 0.35       # 早期覆盖率 < 晚期的 35% → 内容晚到
VACUUM_MIN_LATE_COVERAGE = 0.15  # 晚期覆盖率本身很低（极简设计）则不判空窗

# Per-scene sample points (fraction of scene duration). Three-point sampling
# catches entrance (30%), steady-state (50%) and late-appearing (70%) content;
# the WORST measurement (max content_bottom) decides the scene verdict.
SAMPLE_POINTS = (0.3, 0.5, 0.7)


def subtitle_safety_line(frame_h, subtitle_lines=2):
    """该画布高度下内容必须保持在之上的 y 坐标（预留带随高度等比缩放）。"""
    lines = max(int(subtitle_lines), 1)
    band = round(frame_h * (SUBTITLE_BAND_1LINE_RATIO
                            + SUBTITLE_LINE_PITCH_RATIO * (lines - 1)))
    return frame_h - band


def frame_height(image_path):
    """实测帧高——门禁判据一律用实测值，不信 config 声明的 resolution。"""
    with Image.open(image_path) as img:
        return img.size[1]


def measure_content_coverage(image_path, subtitle_lines=2):
    """内容覆盖率：正文区（y∈[区间顶部, 安全线]）含内容像素的行占比。

    只有标题时覆盖率约 0.1，内容铺满时通常 > 0.4，区分度足够。
    """
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    h, w = arr.shape[:2]
    y0 = round(h * VACUUM_CONTENT_TOP_RATIO)
    y1 = min(subtitle_safety_line(h, subtitle_lines), h - 1)
    band = arr[y0:y1, 20:w-20, :]
    r = band[:, :, 0].astype(int)
    g = band[:, :, 1].astype(int)
    b = band[:, :, 2].astype(int)
    bright = (r > BRIGHTNESS_THRESHOLD) | (g > BRIGHTNESS_THRESHOLD) | (b > BLUE_THRESHOLD)
    rows_with_content = (bright.sum(axis=1) > CONTENT_PIXEL_THRESHOLD).sum()
    total_rows = max(y1 - y0, 1)
    return rows_with_content / total_rows


def find_ffmpeg():
    """Locate ffmpeg executable."""
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    # Fallback: check common locations
    for p in [
        Path(__file__).parent.parent / "过程产物" / "临时产物" / "office_pw_audio" / "ffmpeg-bin" / "ffmpeg.exe",
    ]:
        if p.exists():
            return str(p)
    return None


def extract_frame(video_path, timestamp, output_path, ffmpeg_exe="ffmpeg"):
    """Extract a single frame from video at the given timestamp."""
    cmd = [
        ffmpeg_exe, "-ss", str(timestamp),
        "-i", str(video_path),
        "-frames:v", "1",
        "-update", "1",
        "-y", str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    return os.path.exists(output_path)


def measure_content_bottom(image_path):
    """Measure the lowest y-coordinate with visible content.

    Returns:
        int: y-coordinate of lowest content pixel, or 0 if no content found.
    """
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    h, w = arr.shape[:2]

    # Scan from bottom to top, looking for content pixels
    # Skip 20px margins on left/right to avoid edge artifacts
    for y in range(h - 1, 50, -1):
        row = arr[y, 20:w-20, :]
        r, g, b = row[:, 0].astype(int), row[:, 1].astype(int), row[:, 2].astype(int)
        bright = (r > BRIGHTNESS_THRESHOLD) | (g > BRIGHTNESS_THRESHOLD) | (b > BLUE_THRESHOLD)
        if bright.sum() > CONTENT_PIXEL_THRESHOLD:
            return y

    return 0


def load_scenes_from_config(config_path):
    """Load scene boundaries from pipeline config JSON."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # P0-03 指针模式：narration_source 为场景权威源（绝对时间）
    try:
        from _script_env import resolve_narration_scenes
        ptr_scenes, ptr_path = resolve_narration_scenes(
            config, config_path, float(config.get("cover_duration", 0)))
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    if ptr_path is not None:
        config["scenes"] = ptr_scenes
        print(f"  Scene source: narration_source ({ptr_path.name})")
    scenes = config.get("scenes", [])
    if not scenes:
        print("ERROR: No scenes found in config")
        sys.exit(1)

    result = []
    for s in scenes:
        result.append({
            "id": s.get("scene_id", s.get("id", 0)),
            "start": s.get("start", 0),
            "end": s.get("end", 0),
        })
    return result


def probe_frame_height(video_path, scenes, ffmpeg_exe):
    """抽一帧实测画布高度（画布尺寸的唯一可信来源），失败返回 None。"""
    first = scenes[0] if scenes else None
    t = (first["start"] + first["end"]) / 2 if first else 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "size_probe.png")
        if not extract_frame(video_path, t, path, ffmpeg_exe):
            return None
        try:
            return frame_height(path)
        except Exception:
            return None


def run_check(video_path, scenes, ffmpeg_exe="ffmpeg", subtitle_lines=2):
    """Run visual boundary check on all scenes.

    Returns:
        (passed: bool, results: list of dicts)
    """
    frame_h = probe_frame_height(video_path, scenes, ffmpeg_exe)
    measured = frame_h is not None
    if not measured:
        frame_h = BASELINE_FRAME_H
    safety_line = subtitle_safety_line(frame_h, subtitle_lines)

    print("=" * 60)
    print("VISUAL BOUNDARY CHECK")
    print(f"  Video:               {video_path}")
    print(f"  Frame height:        {frame_h}"
          + ("" if measured else "  ⚠ 实测失败，回退 1080 基准"))
    band = frame_h - safety_line
    print(f"  Safety line:         y={safety_line} "
          f"(预留带 {band}px = {band / frame_h:.1%} 画布高)")
    print(f"  Scenes to check:     {len(scenes)}")
    print("=" * 60)

    results = []
    violations = []
    untested = []        # 检查项未能完成 → 整体不得判通过
    not_applicable = []  # 按规则合法不适用 → 不阻断，也不计通过

    with tempfile.TemporaryDirectory() as tmp_dir:
        for scene in scenes:
            sid = scene["id"]
            start = scene["start"]
            end = scene["end"]
            dur = end - start

            if dur < 2:
                results.append({
                    "scene": sid, "status": gs.NOT_APPLICABLE,
                    "reason": f"too short ({dur:.1f}s)",
                    "content_bottom": 0, "gap": None,
                    "safety_line": safety_line
                })
                not_applicable.append(
                    {"scene": sid, "check": "content_boundary", "reason": f"dur {dur:.1f}s < 2s"})
                continue

            # Extract all sample points in parallel (extract_frame is an
            # independent subprocess with its own output file → thread-safe).
            jobs = []  # (ratio, sample_t, frame_path)
            for ratio in SAMPLE_POINTS:
                sample_t = start + dur * ratio
                frame_path = os.path.join(tmp_dir, f"scene_{sid}_p{int(ratio * 100)}.png")
                jobs.append((ratio, sample_t, frame_path))

            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [
                    pool.submit(extract_frame, video_path, t, fp, ffmpeg_exe)
                    for _, t, fp in jobs
                ]
                extracted = [f.result() for f in futures]

            # Measure serially (in SAMPLE_POINTS order) for determinism.
            samples = []
            for (ratio, sample_t, frame_path), ok in zip(jobs, extracted):
                if not ok:
                    continue
                content_bottom = measure_content_bottom(frame_path)
                samples.append({
                    "ratio": ratio,
                    "t": round(sample_t, 1),
                    "content_bottom": content_bottom,
                    "gap": safety_line - content_bottom,
                })

            if not samples:
                results.append({
                    "scene": sid, "status": gs.UNTESTED,
                    "reason": "frame extraction failed",
                    "content_bottom": 0, "gap": None,
                    "safety_line": safety_line
                })
                untested.append({"scene": sid, "check": "content_boundary",
                                 "reason": "all frame extractions failed"})
                print(f"  ? Scene {sid}: UNTESTED — frame extraction failed "
                      f"({len(jobs)} sample points)")
                continue

            if len(samples) < len(jobs):
                # 最坏值取自 3 个采样点，缺点即判据不完整——不得当作已检查
                untested.append({"scene": sid, "check": "content_boundary",
                                 "reason": f"only {len(samples)}/{len(jobs)} samples available"})
                print(f"  ? Scene {sid}: 采样不完整 ({len(samples)}/{len(jobs)} 点)，"
                      f"最坏值判据降级为未测")

            # Worst case = max content_bottom (first occurrence wins on tie).
            worst = max(samples, key=lambda s: s["content_bottom"])
            content_bottom = worst["content_bottom"]
            gap = worst["gap"]
            sample_t = worst["t"]

            status = gs.PASS if content_bottom <= safety_line else gs.FAIL

            result = {
                "scene": sid,
                "status": status,
                "content_bottom": content_bottom,
                "safety_line": safety_line,
                "gap": gap,
                "sample_time": sample_t,
                "samples": samples,
            }
            results.append(result)

            if status == gs.FAIL:
                violations.append(result)
                print(f"  ✗ Scene {sid}: content_bottom=y{content_bottom} | safety=y{safety_line} | OVERFLOW {abs(gap)}px @t={sample_t:.1f}s (worst of {len(samples)} samples)")
            else:
                print(f"  ✓ Scene {sid}: content_bottom=y{content_bottom} | gap={gap}px @t={sample_t:.1f}s (worst of {len(samples)} samples)")

    # Second pass: content vacuum — narration playing over a near-empty frame
    print("\n  [Second pass: content vacuum (early vs late coverage)]")

    def _vacuum_untested(sid, reason):
        """真空窗检查未完成的统一留痕：入未测清单，并在场景记录上标注，
        避免"没测到"在报告里呈现为"测过且正常"。"""
        untested.append({"scene": sid, "check": "content_vacuum", "reason": reason})
        existing = next((r for r in results if r["scene"] == sid), None)
        if existing is not None:
            existing["vacuum_status"] = gs.UNTESTED

    with tempfile.TemporaryDirectory() as tmp_dir:
        for scene in scenes:
            sid = scene["id"]
            start = scene["start"]
            end = scene["end"]
            dur = end - start

            if dur < VACUUM_MIN_SCENE_DUR:
                not_applicable.append(
                    {"scene": sid, "check": "content_vacuum",
                     "reason": f"dur {dur:.1f}s < {VACUUM_MIN_SCENE_DUR}s"})
                continue

            # 早期采样点 = 观众容忍线（约7.5s，短场景取45%处）：
            # 分批入场是正常设计，只拦“主内容迟于容忍线”的真空窗
            early_t = start + min(7.5, dur * 0.45)
            late_t = start + dur * 0.85
            early_path = os.path.join(tmp_dir, f"scene_{sid}_early.png")
            late_path = os.path.join(tmp_dir, f"scene_{sid}_full.png")
            if not extract_frame(video_path, early_t, early_path, ffmpeg_exe):
                _vacuum_untested(sid, "early frame extraction failed")
                print(f"  ? Scene {sid}: UNTESTED — 真空窗检查取早帧失败")
                continue
            if not extract_frame(video_path, late_t, late_path, ffmpeg_exe):
                _vacuum_untested(sid, "late frame extraction failed")
                print(f"  ? Scene {sid}: UNTESTED — 真空窗检查取晚帧失败")
                continue

            # 覆盖率测量异常同样记为未测：静默 continue 会让"没测到"看起来像"测过且正常"
            try:
                early_cov = measure_content_coverage(early_path, subtitle_lines)
                late_cov = measure_content_coverage(late_path, subtitle_lines)
            except Exception as e:
                _vacuum_untested(sid, f"coverage measurement failed: {e}")
                print(f"  ? Scene {sid}: UNTESTED — 覆盖率测量异常({type(e).__name__})")
                continue
            existing = next((r for r in results if r["scene"] == sid), None)
            if existing is not None:
                existing["early_coverage"] = round(early_cov, 3)
                existing["late_coverage"] = round(late_cov, 3)

            if late_cov >= VACUUM_MIN_LATE_COVERAGE and early_cov < late_cov * VACUUM_EARLY_RATIO:
                print(f"  ✗ Scene {sid}: CONTENT VACUUM — coverage {early_cov:.0%} @t={early_t:.1f}s"
                      f" vs {late_cov:.0%} @t={late_t:.1f}s (main content enters too late)")
                vac = {
                    "scene": sid, "status": gs.FAIL, "kind": "content_vacuum",
                    "early_coverage": round(early_cov, 3),
                    "late_coverage": round(late_cov, 3),
                    "sample_time": round(early_t, 1),
                }
                if existing is not None:
                    existing["status"] = gs.FAIL
                    existing["kind"] = "content_vacuum"
                violations.append(vac)
            else:
                print(f"  ✓ Scene {sid}: coverage {early_cov:.0%} → {late_cov:.0%}")

    # 四态裁定：未测项存在时整体不得判通过（"没有违规"与"完成检查"是两件事）
    overall = gs.verdict(violations, untested)
    passed = overall == gs.PASS
    counts = gs.count_statuses(results)
    summary = {
        "verdict": overall,
        "counts": counts,
        "untested": untested,
        "not_applicable": not_applicable,
    }

    print()
    print(f"  Checked: {counts[gs.PASS]} pass / {counts[gs.FAIL]} fail / "
          f"{len(untested)} untested / {len(not_applicable)} not-applicable")
    if overall == gs.PASS:
        print(f"✓ VISUAL BOUNDARY CHECK PASSED — all {len(scenes)} scenes within safe zone")
    elif overall == gs.UNTESTED:
        print(f"✗ VISUAL BOUNDARY CHECK INCOMPLETE — {len(untested)} 项检查未完成，"
              f"不得计为通过：")
        for u in untested:
            print(f"    Scene {u['scene']} [{u['check']}]: {u['reason']}")
        print(f"\n  未测 ≠ 无违规：先修取证链（ffmpeg 抽帧/图像解码），再判定画面合规。")
    else:
        print(f"✗ VISUAL BOUNDARY CHECK FAILED — {len(violations)} scene(s) overflow:")
        for v in violations:
            if v.get("kind") == "content_vacuum":
                print(f"    Scene {v['scene']}: content vacuum — early coverage "
                      f"{v['early_coverage']:.0%} < {VACUUM_EARLY_RATIO:.0%} of late "
                      f"{v['late_coverage']:.0%}; move main content entrance earlier")
            else:
                print(f"    Scene {v['scene']}: content at y={v['content_bottom']} overflows safety line y={safety_line} by {abs(v['gap'])}px")
        print(f"\n  Fix: overflow → reduce content height; vacuum → advance GSAP entrance times.")
        print(f"  Content must stay above y={safety_line} and appear while narration plays.")
        if untested:
            print(f"  另有 {len(untested)} 项检查未完成（见 visual_boundary_report.json:untested）")

    return passed, results, summary


def main():
    parser = argparse.ArgumentParser(description="Visual boundary check for rendered video")
    parser.add_argument("--video", required=True, help="Path to rendered video (render_raw.mp4)")
    parser.add_argument("--config", default=None, help="Pipeline config JSON (for scene boundaries)")
    parser.add_argument("--scenes-json", default=None, help='Inline scenes JSON: \'[{"start":0,"end":30},...]\'')

    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"ERROR: Video not found: {args.video}")
        sys.exit(1)

    # Load scenes
    if args.scenes_json:
        scenes = json.loads(args.scenes_json)
    elif args.config:
        scenes = load_scenes_from_config(args.config)
    else:
        print("ERROR: Either --config or --scenes-json is required")
        sys.exit(1)

    ffmpeg_exe = find_ffmpeg()
    if not ffmpeg_exe:
        print("ERROR: ffmpeg not found")
        sys.exit(1)

    passed, results, summary = run_check(args.video, scenes, ffmpeg_exe)

    # Write results to JSON for pipeline consumption
    report_path = Path(args.video).parent / "visual_boundary_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "passed": passed,
            "verdict": summary["verdict"],
            "counts": summary["counts"],
            "untested": summary["untested"],
            "not_applicable": summary["not_applicable"],
            "safety_line": (results[0].get("safety_line") if results
                            else subtitle_safety_line(BASELINE_FRAME_H)),
            "scenes": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nReport: {report_path}")

    # 退出码分级：0=通过 1=画面越界 2=检查未完成（未测不得计为通过，且与越界可区分）
    if passed:
        sys.exit(0)
    sys.exit(2 if summary["verdict"] == gs.UNTESTED else 1)


if __name__ == "__main__":
    main()
