#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Photo preprocessor for HyperFrames video projects.

Crops source photos to target aspect ratios defined in photo-crop-spec.json,
ensuring visual consistency across scenes with different container layouts.

Usage:
  # Preview what would be cropped (dry-run)
  python preprocess_photos.py --project hospital-partition-wall

  # Execute cropping
  python preprocess_photos.py --project hospital-partition-wall --execute

  # Also update HTML to reference cropped versions
  python preprocess_photos.py --project hospital-partition-wall --execute --update-html

  # Force re-crop even if cropped versions exist
  python preprocess_photos.py --project hospital-partition-wall --execute --force
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Fix GBK encoding in PowerShell（import 即生效）+ 公共路径
from _script_env import ROOT, HTML_BASE  # noqa: E402, F401

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow")
    sys.exit(1)

# Crop focus strategies: (horizontal_anchor, vertical_offset_ratio)
# vertical_offset_ratio: 0.0 = top, 0.5 = center, 0.7 = center-bottom, 1.0 = bottom
FOCUS_STRATEGIES = {
    "center":         (0.5, 0.5),
    "center-top":     (0.5, 0.35),
    "center-bottom":  (0.5, 0.65),
    "top":            (0.5, 0.25),
    "bottom":         (0.5, 0.75),
}


def parse_ratio(ratio_str: str) -> float:
    """Parse '4:3' or '16:9' into a float ratio value."""
    if ':' in ratio_str:
        parts = ratio_str.split(':')
        return float(parts[0]) / float(parts[1])
    return float(ratio_str)


def calculate_crop_box(src_w: int, src_h: int, target_ratio: float,
                       focus: str) -> tuple:
    """
    Calculate the crop box (left, upper, right, lower) for smart cropping.

    Strategy:
    - If source is wider than target: crop left/right (horizontal trim)
    - If source is taller than target: crop top/bottom (vertical trim)
    - Focus determines where the crop anchor point is
    """
    src_ratio = src_w / src_h
    h_anchor, v_offset = FOCUS_STRATEGIES.get(focus, FOCUS_STRATEGIES["center"])

    if src_ratio > target_ratio:
        # Source is wider -> crop width, keep full height
        new_h = src_h
        new_w = int(src_h * target_ratio)
    else:
        # Source is taller -> crop height, keep full width
        new_w = src_w
        new_h = int(src_w / target_ratio)

    # Calculate crop center based on focus
    center_x = int(src_w * h_anchor)
    center_y = int(src_h * v_offset)

    # Calculate crop box
    left = center_x - new_w // 2
    upper = center_y - new_h // 2

    # Clamp to image boundaries
    left = max(0, min(left, src_w - new_w))
    upper = max(0, min(upper, src_h - new_h))
    right = left + new_w
    lower = upper + new_h

    return (left, upper, right, lower)


def get_ratio_suffix(ratio_str: str) -> str:
    """Convert '4:3' to '4x3' for filename suffix."""
    return ratio_str.replace(':', 'x')


def process_photos(project_dir: Path, spec: dict, execute: bool = False,
                   force: bool = False) -> list:
    """Process all photos according to crop spec. Returns list of results."""
    photos_dir = project_dir / "photos"
    cropped_dir = photos_dir / "cropped"
    results = []

    if execute and not cropped_dir.exists():
        cropped_dir.mkdir(parents=True)

    for name, crop_info in spec.get("crops", {}).items():
        target_ratio_str = crop_info["target_ratio"]
        target_ratio = parse_ratio(target_ratio_str)
        focus = crop_info.get("focus", "center")
        scene = crop_info.get("scene", 0)

        # Skip entries with crop: false (use original photo, no HTML rewrite)
        if crop_info.get("crop") is False:
            results.append({
                "name": name,
                "scene": scene,
                "status": "SKIPPED",
                "detail": crop_info.get("_skip_reason", "crop disabled")
            })
            continue

        # Find source file (try common extensions)
        src_file = None
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            candidate = photos_dir / f"{name}{ext}"
            if candidate.exists():
                src_file = candidate
                break

        if src_file is None:
            results.append({
                "name": name,
                "scene": scene,
                "status": "MISSING",
                "detail": f"No source file found in {photos_dir}"
            })
            continue

        # Check if cropped version exists
        suffix = get_ratio_suffix(target_ratio_str)
        cropped_name = f"{name}_{suffix}.jpg"
        cropped_path = cropped_dir / cropped_name

        if not force and cropped_path.exists():
            results.append({
                "name": name,
                "scene": scene,
                "status": "CACHED",
                "detail": f"{cropped_name} already exists"
            })
            continue

        # Load and analyze source
        img = Image.open(src_file)
        src_w, src_h = img.size
        src_ratio = src_w / src_h

        # Calculate crop box
        crop_box = calculate_crop_box(src_w, src_h, target_ratio, focus)
        crop_w = crop_box[2] - crop_box[0]
        crop_h = crop_box[3] - crop_box[1]

        result = {
            "name": name,
            "scene": scene,
            "source": src_file.name,
            "source_size": f"{src_w}x{src_h}",
            "source_ratio": f"{src_ratio:.2f}",
            "target_ratio": target_ratio_str,
            "target_ratio_val": f"{target_ratio:.2f}",
            "focus": focus,
            "crop_box": list(crop_box),
            "crop_size": f"{crop_w}x{crop_h}",
        }

        if execute:
            cropped_img = img.crop(crop_box)
            cropped_img.save(cropped_path, "JPEG", quality=95,
                           optimize=True)
            result["status"] = "CROPPED"
            result["output"] = str(cropped_path.relative_to(project_dir))
        else:
            result["status"] = "PREVIEW"

        # Calculate info loss
        crop_area = crop_w * crop_h
        src_area = src_w * src_h
        result["retained_pct"] = f"{crop_area / src_area * 100:.0f}%"

        results.append(result)

    return results


def update_html_references(project_dir: Path, spec: dict) -> list:
    """Update HTML to reference cropped versions and use consistent object-fit."""
    html_path = project_dir / "index.html"
    if not html_path.exists():
        return [{"status": "ERROR", "detail": "index.html not found"}]

    content = html_path.read_text(encoding='utf-8')
    changes = []

    for name, crop_info in spec.get("crops", {}).items():
        # Skip entries with crop: false
        if crop_info.get("crop") is False:
            continue

        target_ratio_str = crop_info["target_ratio"]
        suffix = get_ratio_suffix(target_ratio_str)

        # Find all img src references to this photo
        # Match: src="photos/site_01.jpg" or src="photos/seal_detail.jpg"
        pattern = re.compile(
            r'(src="photos/)' + re.escape(name) + r'(\.\w+")',
            re.IGNORECASE
        )

        def replacer(m):
            return f'{m.group(1)}cropped/{name}_{suffix}.jpg"'

        new_content, count = pattern.subn(replacer, content)
        if count > 0:
            changes.append({
                "photo": name,
                "replacements": count,
                "new_src": f"photos/cropped/{name}_{suffix}.jpg"
            })
            content = new_content

    # Normalize object-fit for scene photos
    # Remove inline object-fit:contain and use default cover
    # (since photos are now pre-cropped to target ratio, cover fills perfectly)
    # Only for entries with crop enabled (default)
    for name, crop_info in spec.get("crops", {}).items():
        if crop_info.get("crop") is False:
            continue
        # Remove object-fit:contain from images that we've cropped
        # Pattern: within img tags referencing our cropped photos
        pass  # Keep existing CSS - cover is the default via .scene-img class

    if changes:
        html_path.write_text(content, encoding='utf-8')

        # Sync .bak file
        bak_path = project_dir / "index.html.bak"
        if bak_path.exists():
            bak_path.write_text(content, encoding='utf-8')
            changes.append({"status": "BAK_SYNCED"})

    return changes


def print_report(results: list, changes: list = None):
    """Print a formatted report of processing results."""
    print("\n" + "=" * 70)
    print("  PHOTO PREPROCESSOR REPORT")
    print("=" * 70)

    # Group by scene
    by_scene = {}
    for r in results:
        scene = r.get("scene", 0)
        by_scene.setdefault(scene, []).append(r)

    for scene in sorted(by_scene.keys()):
        print(f"\n  Scene {scene}:")
        for r in by_scene[scene]:
            status_icon = {
                "CROPPED": "[+]",
                "PREVIEW": "[~]",
                "CACHED": "[=]",
                "SKIPPED": "[-]",
                "MISSING": "[!]",
            }.get(r["status"], "[?]")

            print(f"    {status_icon} {r['name']}")
            if r["status"] in ("CROPPED", "PREVIEW"):
                print(f"        {r['source']} ({r['source_size']}, "
                      f"ratio {r['source_ratio']})")
                print(f"        -> {r['target_ratio']} "
                      f"(ratio {r['target_ratio_val']}) "
                      f"focus={r['focus']}")
                print(f"        crop: {r['crop_size']} "
                      f"(retained {r['retained_pct']})")
            elif r["status"] == "CACHED":
                print(f"        {r['detail']}")
            elif r["status"] == "MISSING":
                print(f"        WARNING: {r['detail']}")
            elif r["status"] == "SKIPPED":
                print(f"        {r['detail']}")

    if changes:
        print(f"\n  HTML Updates:")
        for c in changes:
            if "photo" in c:
                print(f"    -> {c['photo']}: {c['replacements']} reference(s) "
                      f"-> {c['new_src']}")
            elif c.get("status") == "BAK_SYNCED":
                print(f"    -> index.html.bak synced")

    # Summary
    cropped = sum(1 for r in results if r["status"] == "CROPPED")
    preview = sum(1 for r in results if r["status"] == "PREVIEW")
    cached = sum(1 for r in results if r["status"] == "CACHED")
    missing = sum(1 for r in results if r["status"] == "MISSING")

    print(f"\n  Summary: ", end="")
    parts = []
    if cropped:
        parts.append(f"{cropped} cropped")
    if preview:
        parts.append(f"{preview} preview")
    if cached:
        parts.append(f"{cached} cached")
    if missing:
        parts.append(f"{missing} MISSING")
    print(", ".join(parts) if parts else "nothing to do")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Preprocess photos to target aspect ratios"
    )
    parser.add_argument("--project", required=True,
                        help="HyperFrames project folder name")
    parser.add_argument("--execute", action="store_true",
                        help="Actually crop photos (default: dry-run preview)")
    parser.add_argument("--update-html", action="store_true",
                        help="Update HTML to reference cropped photos")
    parser.add_argument("--force", action="store_true",
                        help="Re-crop even if cached versions exist")
    args = parser.parse_args()

    project_dir = HTML_BASE / args.project
    spec_path = project_dir / "photo-crop-spec.json"

    if not spec_path.exists():
        print(f"ERROR: {spec_path} not found.")
        print(f"Create a photo-crop-spec.json in {project_dir} first.")
        sys.exit(1)

    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    print(f"Project: {args.project}")
    print(f"Spec:    {spec_path}")
    print(f"Mode:    {'EXECUTE' if args.execute else 'PREVIEW (dry-run)'}")

    # Process photos
    results = process_photos(project_dir, spec, execute=args.execute,
                           force=args.force)

    # Update HTML if requested
    changes = None
    if args.update_html and args.execute:
        changes = update_html_references(project_dir, spec)

    # Report
    print_report(results, changes)

    if not args.execute:
        print("\n  Tip: Add --execute to actually crop the photos.")
        print("  Tip: Add --update-html to also update HTML references.")


if __name__ == "__main__":
    main()
