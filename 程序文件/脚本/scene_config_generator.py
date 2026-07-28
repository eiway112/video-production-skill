#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
场景配置生成器 — TTS驱动的场景窗口自动计算

设计原则：
  场景窗口由 TTS 实际时长自动推导，消除人工估算误差。
  因果方向：写旁白 → 跑TTS → 算窗口 → 输出JSON
  （而非：人工写start/end → 跑TTS → 祈祷窗口合理）

工作流：
  1. 读取旁白文本（文本文件，场景间用 --- 分隔）
  2. 为每段旁白生成 TTS 音频（edge-tts + 44.1kHz 上采样）
  3. 测量每段 TTS 实际时长
  4. 动态计算 margin（短视频宽裕，长视频收紧）
  5. 窗口 = TTS实际时长 + margin
  6. 输出完整的 pipeline JSON，可直接被 pipeline_runner.py 消费

用法：
  python scene_config_generator.py --narrations narrations.txt --output config.json
  python scene_config_generator.py --narrations narrations.txt --voice zh-CN-YunxiNeural --rate "+10%"
  python scene_config_generator.py --narrations narrations.txt --video-name "产品宣传.mp4"

旁白文件格式（narrations.txt）：
  做一条产品介绍视频，你要花多久？...
  ---
  换个思路。企业需要的不是一条视频...
  ---
  先看武器库，三套工具链...
"""

import asyncio
import hashlib
import json
import re
import subprocess
import sys
import os
import io
import argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_env import ROOT
SCRIPTS = ROOT / "程序文件" / "脚本"
TEMP_BASE = ROOT / "过程产物" / "临时产物"
SAMPLE_RATE = 44100

# Default voice parameters (same defaults as enhance_video_audio.py)
DEFAULT_VOICE = "zh-CN-YunxiNeural"
DEFAULT_RATE = "+10%"
DEFAULT_PITCH = "+0Hz"


def get_duration(filepath):
    """Get audio file duration via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "json", filepath],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def tts_cache_key(text, voice, rate, pitch):
    """Compute short hash from narration text + voice params for cache keying."""
    normalized = re.sub(r'\s+', '', text.strip())
    cache_input = f"{normalized}|{voice}|{rate}|{pitch}"
    return hashlib.sha256(cache_input.encode('utf-8')).hexdigest()[:10]


async def generate_tts(text, output_path, voice, rate, pitch):
    """Generate TTS using edge_tts with retry."""
    max_retries = 10
    retry_delays = [5, 10, 15, 20, 30, 30, 30, 30, 30]
    for attempt in range(1, max_retries + 1):
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            await communicate.save(str(output_path))
            if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
                return
            size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            raise Exception(f"edge_tts produced invalid file ({size} bytes)")
        except Exception as e:
            if attempt < max_retries:
                wait = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                print(f"  edge_tts attempt {attempt}/{max_retries} failed: {e}")
                print(f"  Retrying in {wait}s...")
                await asyncio.sleep(wait)
            else:
                raise RuntimeError(
                    f"TTS generation failed after {max_retries} retries. "
                    f"Voice: {voice}, Text: {text[:50]}..."
                ) from e


def load_narrations(filepath):
    """Load narration texts from file, split by --- separator."""
    content = Path(filepath).read_text(encoding='utf-8').strip()
    # Split by --- on its own line
    scenes = re.split(r'\n\s*---\s*\n', content)
    narrations = [s.strip() for s in scenes if s.strip()]
    return narrations


def compute_dynamic_margin(total_tts_duration):
    """Compute per-scene margin based on total video duration.

    Short videos (< 120s): 2.0s margin — surplus is imperceptible
    Medium videos (120-300s): 1.5s margin
    Long videos (> 300s): 1.0s margin — tight to prevent cumulative whitespace

    This makes margin scale-invariant: total surplus = N * margin
    converges rather than diverges as N increases.
    """
    if total_tts_duration > 300:
        return 1.0
    elif total_tts_duration > 120:
        return 1.5
    return 2.0


async def generate_all_tts(narrations, voice, rate, pitch, temp_dir):
    """Generate TTS for all narrations, return list of (duration, hash) tuples."""
    tts_dir = temp_dir / "tts_44k"
    tts_dir.mkdir(parents=True, exist_ok=True)

    manifest = {}
    results = []

    for i, text in enumerate(narrations):
        scene_id = i + 1
        h = tts_cache_key(text, voice, rate, pitch)
        hq_file = tts_dir / f"tts_{h}_hq.wav"

        if hq_file.exists():
            print(f"  Scene {scene_id}: cache hit [hash:{h[:6]}]")
        else:
            raw_file = temp_dir / f"scene_{scene_id}.mp3"
            print(f"  Scene {scene_id}: generating TTS...")
            await generate_tts(text, raw_file, voice, rate, pitch)

            # Upsample to 44.1kHz stereo
            print(f"  Scene {scene_id}: upsampling to 44.1kHz...")
            subprocess.run([
                "ffmpeg", "-y", "-i", str(raw_file),
                "-ar", str(SAMPLE_RATE), "-ac", "2",
                "-sample_fmt", "s16", str(hq_file)
            ], capture_output=True, check=True)

            # Clean up raw mp3
            if raw_file.exists():
                raw_file.unlink()

        dur = get_duration(str(hq_file))
        # Use pipeline-compatible manifest format: {hash, file} dict
        manifest[str(scene_id)] = {
            'hash': h,
            'file': f'tts_44k/tts_{h}_hq.wav'
        }
        results.append((dur, h))
        print(f"  Scene {scene_id}: {dur:.1f}s [hash:{h[:6]}]")

    # Save manifest (format compatible with enhance_video_audio.py / verify_tts_product.py)
    manifest_path = temp_dir / "tts_manifest.json"
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return results


def build_config(narrations, tts_results, voice, rate, pitch,
                 video_name, subtitle_name, temp_subdir, html_project,
                 cover_duration=3.0):
    """Build pipeline JSON with TTS-driven scene windows.

    Args:
        narrations: list of narration texts
        tts_results: list of (duration, hash) from generate_all_tts
        voice, rate, pitch: TTS voice parameters
        video_name: output video filename
        subtitle_name: output subtitle filename
        temp_subdir: temp directory name under 过程产物/临时产物/
        html_project: HyperFrames project directory name
        cover_duration: cover scene duration (default 3.0s)

    Returns:
        dict: complete pipeline JSON configuration
    """
    tts_durations = [dur for dur, _ in tts_results]
    total_tts = sum(tts_durations)
    margin = compute_dynamic_margin(total_tts)

    # Build scenes with cumulative start/end
    scenes = []
    current_time = 0.0
    for i, (text, (dur, _)) in enumerate(zip(narrations, tts_results)):
        scene_id = i + 1
        window = dur + margin
        start = round(current_time, 1)
        end = round(current_time + window, 1)
        scenes.append({
            "scene_id": scene_id,
            "start": start,
            "end": end,
            "narration": text
        })
        current_time = end

    total_duration = round(current_time + cover_duration, 1)

    config = {
        "video_duration": total_duration,
        "voice": voice,
        "rate": rate,
        "pitch": pitch,
        "cover_duration": cover_duration,
        "scenes": scenes,
        "paths": {
            "video_name": video_name,
            "subtitle_name": subtitle_name,
            "temp_subdir": temp_subdir,
            "html_project": html_project
        }
    }

    return config, margin


def main():
    parser = argparse.ArgumentParser(
        description="Scene Config Generator — TTS-driven scene window auto-calculation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example:
  python scene_config_generator.py --narrations narrations.txt --video-name "产品介绍.mp4"

Narration file format (scenes separated by ---):
  First scene narration text...
  ---
  Second scene narration text...
  ---
  Third scene narration text...
        """
    )
    parser.add_argument("--narrations", required=True,
                        help="Text file with narrations (separated by ---)")
    parser.add_argument("--voice", default=DEFAULT_VOICE,
                        help=f"TTS voice (default: {DEFAULT_VOICE})")
    parser.add_argument("--rate", default=DEFAULT_RATE,
                        help=f"TTS rate (default: {DEFAULT_RATE.replace('%', '%%')})")
    parser.add_argument("--pitch", default=DEFAULT_PITCH,
                        help=f"TTS pitch (default: {DEFAULT_PITCH.replace('%', '%%')})")
    parser.add_argument("--output", required=True,
                        help="Output JSON config path")
    parser.add_argument("--video-name", required=True,
                        help="Output video filename (e.g. 产品介绍.mp4)")
    parser.add_argument("--subtitle-name", default=None,
                        help="Output subtitle filename (default: derived from video name)")
    parser.add_argument("--temp-subdir", default=None,
                        help="Temp directory name (default: derived from video name)")
    parser.add_argument("--html-project", default=None,
                        help="HyperFrames project dir name (default: derived from video name)")
    parser.add_argument("--cover-duration", type=float, default=3.0,
                        help="Cover scene duration in seconds (default: 3.0)")
    parser.add_argument("--tts-only", action="store_true",
                        help="Only generate TTS, skip config output (for pre-caching)")
    parser.add_argument("--sync-html", action="store_true",
                        help="Also update HTML data-duration attribute to match computed duration")
    args = parser.parse_args()

    # Load narrations
    narrations = load_narrations(args.narrations)
    if not narrations:
        print("ERROR: No narrations found in file")
        return 1

    print(f"Loaded {len(narrations)} narration(s)")
    print(f"Voice: {args.voice}, Rate: {args.rate}, Pitch: {args.pitch}")
    print()

    # Derive default names from video name
    base_name = Path(args.video_name).stem
    subtitle_name = args.subtitle_name or f"{base_name}.srt"
    temp_subdir = args.temp_subdir or f"{base_name}_audio"
    html_project = args.html_project or base_name.lower().replace(' ', '-')

    # Setup temp directory
    temp_dir = TEMP_BASE / temp_subdir
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Generate TTS for all narrations
    print("=== TTS Generation ===")
    tts_results = asyncio.run(
        generate_all_tts(narrations, args.voice, args.rate, args.pitch, temp_dir)
    )

    if args.tts_only:
        print("\nTTS generation complete (--tts-only mode, skipping config output)")
        return 0

    # Build config with TTS-driven windows
    print("\n=== Config Generation ===")
    config, margin = build_config(
        narrations, tts_results,
        args.voice, args.rate, args.pitch,
        args.video_name, subtitle_name, temp_subdir, html_project,
        args.cover_duration
    )

    # Print summary
    tts_durations = [dur for dur, _ in tts_results]
    total_tts = sum(tts_durations)
    print(f"  Dynamic margin: {margin}s (total TTS: {total_tts:.1f}s)")
    print(f"  Video duration: {config['video_duration']}s (incl. {args.cover_duration}s cover)")
    print(f"  Scenes: {len(narrations)}")
    print(f"\n  {'#':>3}  {'TTS':>6}  {'Window':>6}  {'Margin':>6}  Narration")
    for i, (dur, win) in enumerate(zip(tts_durations, [s['end'] - s['start'] for s in config['scenes']])):
        m = win - dur
        preview = config['scenes'][i]['narration'][:40] + "..."
        print(f"  {i+1:>3}  {dur:>5.1f}s  {win:>5.1f}s  {m:>5.1f}s  {preview}")

    # Write config
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"\n  Config saved: {output_path}")

    # Optionally sync HTML data-duration attribute
    if args.sync_html:
        html_dir = ROOT / "程序文件" / "源码" / "hyperframes" / html_project
        html_path = html_dir / "index.html"
        if html_path.exists():
            html_content = html_path.read_text(encoding='utf-8')
            new_dur = config['video_duration']
            updated = re.sub(r'data-duration="[\d.]+"', f'data-duration="{new_dur}"', html_content)
            if updated != html_content:
                html_path.write_text(updated, encoding='utf-8')
                # Also update .bak if it exists
                bak_path = html_dir / "index.html.bak"
                if bak_path.exists():
                    bak_content = bak_path.read_text(encoding='utf-8')
                    bak_updated = re.sub(r'data-duration="[\d.]+"', f'data-duration="{new_dur}"', bak_content)
                    bak_path.write_text(bak_updated, encoding='utf-8')
                print(f"  HTML data-duration synced: {new_dur}s")
            else:
                print(f"  HTML data-duration already correct: {new_dur}s")
        else:
            print(f"  WARNING: HTML not found at {html_path}, --sync-html skipped")

    print(f"\n  Next step: python pipeline_runner.py --config {output_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
