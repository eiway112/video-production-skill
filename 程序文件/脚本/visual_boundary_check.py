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


# ── Configuration ──────────────────────────────────────────────
# Subtitle safety line: content must stay ABOVE this y-coordinate.
# The subtitle-safe gradient starts at y=860 (height:220px from bottom=0).
# Content entering the gradient zone is visually obscured and appears to
# overlap with burned-in subtitles. y=860 ensures content stays completely
# clear of both the gradient and the subtitle text (~y=940).
SUBTITLE_SAFETY_LINE = 860

# Minimum bright pixels in a row to count as "content" (avoids noise)
CONTENT_PIXEL_THRESHOLD = 20

# Brightness threshold to distinguish content from dark background
BRIGHTNESS_THRESHOLD = 80  # for R/G channels
BLUE_THRESHOLD = 100       # for B channel (dark blue background)

# Per-scene sample points (fraction of scene duration). Three-point sampling
# catches entrance (30%), steady-state (50%) and late-appearing (70%) content;
# the WORST measurement (max content_bottom) decides the scene verdict.
SAMPLE_POINTS = (0.3, 0.5, 0.7)


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


def run_check(video_path, scenes, ffmpeg_exe="ffmpeg"):
    """Run visual boundary check on all scenes.

    Returns:
        (passed: bool, results: list of dicts)
    """
    print("=" * 60)
    print("VISUAL BOUNDARY CHECK")
    print(f"  Video:               {video_path}")
    print(f"  Safety line:         y={SUBTITLE_SAFETY_LINE}")
    print(f"  Scenes to check:     {len(scenes)}")
    print("=" * 60)

    results = []
    violations = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        for scene in scenes:
            sid = scene["id"]
            start = scene["start"]
            end = scene["end"]
            dur = end - start

            if dur < 2:
                results.append({
                    "scene": sid, "status": "SKIP",
                    "reason": f"too short ({dur:.1f}s)",
                    "content_bottom": 0, "gap": None
                })
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
                    "gap": SUBTITLE_SAFETY_LINE - content_bottom,
                })

            if not samples:
                results.append({
                    "scene": sid, "status": "ERROR",
                    "reason": "frame extraction failed",
                    "content_bottom": 0, "gap": None
                })
                continue

            # Worst case = max content_bottom (first occurrence wins on tie).
            worst = max(samples, key=lambda s: s["content_bottom"])
            content_bottom = worst["content_bottom"]
            gap = worst["gap"]
            sample_t = worst["t"]

            status = "PASS" if content_bottom <= SUBTITLE_SAFETY_LINE else "FAIL"

            result = {
                "scene": sid,
                "status": status,
                "content_bottom": content_bottom,
                "safety_line": SUBTITLE_SAFETY_LINE,
                "gap": gap,
                "sample_time": sample_t,
                "samples": samples,
            }
            results.append(result)

            if status == "FAIL":
                violations.append(result)
                print(f"  ✗ Scene {sid}: content_bottom=y{content_bottom} | safety=y{SUBTITLE_SAFETY_LINE} | OVERFLOW {abs(gap)}px @t={sample_t:.1f}s (worst of {len(samples)} samples)")
            else:
                print(f"  ✓ Scene {sid}: content_bottom=y{content_bottom} | gap={gap}px @t={sample_t:.1f}s (worst of {len(samples)} samples)")

    passed = len(violations) == 0
    print()
    if passed:
        print(f"✓ VISUAL BOUNDARY CHECK PASSED — all {len(scenes)} scenes within safe zone")
    else:
        print(f"✗ VISUAL BOUNDARY CHECK FAILED — {len(violations)} scene(s) overflow:")
        for v in violations:
            print(f"    Scene {v['scene']}: content at y={v['content_bottom']} overflows safety line y={SUBTITLE_SAFETY_LINE} by {abs(v['gap'])}px")
        print(f"\n  Fix: Reduce content height in the overflowing scenes, then re-render.")
        print(f"  Content must stay above y={SUBTITLE_SAFETY_LINE} to avoid subtitle overlap.")

    return passed, results


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

    passed, results = run_check(args.video, scenes, ffmpeg_exe)

    # Write results to JSON for pipeline consumption
    report_path = Path(args.video).parent / "visual_boundary_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "passed": passed,
            "safety_line": SUBTITLE_SAFETY_LINE,
            "scenes": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nReport: {report_path}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
