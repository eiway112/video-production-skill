#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
场景级增量渲染（scene patch render）。

职责（与 pipeline_runner.py 的 render 步骤配合，三道门禁照常执行）：
  1. sidecar  — 全量渲染成功后提取场景指纹，写 temp_dir/scene_fingerprints.json
  2. classify — 白名单制变更分类：仅"≤50% 场景的 div 内容变化，其余全部不变"才允许 PATCH
  3. patch    — 变更场景经 wrapper timeline 时间平移的临时 composition 分段渲染，
                与基线 render_raw 按帧号精确拼接，统一参数重编码，像素级自校验，
                任一环节失败 → exit 3（调用方自动降级 FULL 全量渲染）

关键机制结论（2026-02 小样验证）：
  - HyperFrames root data-start 是"父时间轴放置起点"，不 seek GSAP 时间轴，
    直接用于分段渲染会得到全黑帧 → 必须走 wrapper 路线；
  - wrapper：注入脚本将 window.__timelines.main 替换为
    gsap.timeline().add(main.tweenFromTo(SEG_START, SEG_END)) 并预先 main.seek(SEG_START)
    重建段起点累积状态（set/from/visibility 全部正确重现，已抽帧验证）。

Exit codes: 0 成功；3 需降级 FULL（原因已打印）；1 参数/环境错误。

用法：
  python scene_patch_render.py --config scene-patch-test.json --mode sidecar
  python scene_patch_render.py --config scene-patch-test.json --mode classify
  python scene_patch_render.py --config scene-patch-test.json --mode patch
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import io
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gsap_time_utils import parse_t_block
from _script_env import ROOT

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

CONFIG_DIR = ROOT / "程序文件" / "配置" / "config"
HTML_BASE = ROOT / "程序文件" / "源码" / "hyperframes"
TEMP_BASE = ROOT / "过程产物" / "临时产物"

SIDECAR_NAME = "scene_fingerprints.json"
VERDICT_NAME = "scene_patch_verdict.json"
SIDECAR_VERSION = 1
EXIT_FALLBACK = 3

# 像素自校验阈值（0-255 平均绝对差）：
# 未变场景帧只允许重编码噪声；拼接点边界帧允许略宽（编码器边界块效应）
DIFF_UNCHANGED_MAX = 3.0
DIFF_JUNCTION_MAX = 8.0


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── HTML 结构提取 ──

def extract_scene_blocks(html):
    """按 DOM 顺序提取全部 data-scene-id div 块（含嵌套平衡）。

    返回 [(scene_id, block_text, start_offset, end_offset), ...]
    """
    blocks = []
    for m in re.finditer(r'<div\b[^>]*\bdata-scene-id="([^"]+)"[^>]*>', html):
        scene_id = m.group(1)
        depth = 1
        pos = m.end()
        for tag in re.finditer(r'<div\b[^>]*>|</div>', html[m.end():]):
            depth += 1 if tag.group(0).startswith('<div') else -1
            if depth == 0:
                pos = m.end() + tag.end()
                break
        else:
            raise ValueError(f"unbalanced div for scene {scene_id}")
        blocks.append((scene_id, html[m.start():pos], m.start(), pos))
    return blocks


def extract_root_attrs(html):
    """composition root（data-composition-id 所在 div）的属性字典。"""
    m = re.search(r'<div\b[^>]*\bdata-composition-id="[^"]*"[^>]*>', html)
    if not m:
        return {}
    return dict(re.findall(r'\bdata-([\w-]+)="([^"]*)"', m.group(0)))


def extract_fingerprints(html, config, narration_path):
    """场景指纹 + 白名单外全部内容的指纹。"""
    blocks = extract_scene_blocks(html)
    scene_hashes = {sid: _sha256(text) for sid, text, _, _ in blocks}
    scene_order = [sid for sid, _, _, _ in blocks]

    # 白名单外残余：场景块挖空后的整份 HTML（覆盖 style/script/head/结构等一切）
    residual = html
    for sid, text, _, _ in reversed(blocks):
        residual = residual.replace(text, f"<!--scene:{sid}-->", 1)
    residual_hash = _sha256(residual)

    style_hash = _sha256("\n".join(re.findall(r'<style[^>]*>([\s\S]*?)</style>', html)))
    script_hash = _sha256(
        "\n".join(re.findall(r'<script(?:\s[^>]*)?>([\s\S]*?)</script>', html))
        + "|" + "|".join(re.findall(r'<script\b[^>]*\bsrc="([^"]*)"', html)))

    cfg_render = {k: config.get(k) for k in
                  ("video_duration", "fps", "resolution", "cover_duration", "timeline")}
    config_hash = _sha256(json.dumps(cfg_render, ensure_ascii=False, sort_keys=True))

    narration_hash = _file_sha256(narration_path) if narration_path.exists() else ""

    return {
        "scene_order": scene_order,
        "scene_hashes": scene_hashes,
        "residual_hash": residual_hash,
        "style_hash": style_hash,
        "script_hash": script_hash,
        "config_hash": config_hash,
        "narration_hash": narration_hash,
    }


def extract_scene_times(html, config):
    """T-block + root data-duration → 各场景 [start,end)。

    返回 (times, err)：times = [{"id","start","end"}, ...]；数据矛盾时 err 说明原因。
    """
    script_all = "\n".join(re.findall(r'<script(?:\s[^>]*)?>([\s\S]*?)</script>', html))
    t_map = parse_t_block(script_all)
    if not t_map:
        return None, "no-T-block"

    root = extract_root_attrs(html)
    try:
        duration = float(root.get("duration", 0))
    except ValueError:
        return None, "bad-data-duration"
    if duration <= 0:
        return None, "no-data-duration"

    blocks = extract_scene_blocks(html)
    if len(blocks) != len(t_map):
        return None, f"scene-count-mismatch(T={len(t_map)}, div={len(blocks)})"

    starts = [t_map[k] for k in sorted(t_map)]
    if starts != sorted(starts) or starts[0] != 0.0:
        return None, "T-block-not-monotonic"

    # 时长见证（2026-09-03 R8 重构）：S-block 数值 end 只是见证之一，不是唯一见证。
    # 旧实现把"脚本里没有 end: 字面量"一律判不可信 → 现行手写风格（T-block +
    # root data-duration，不写 S-block end）的基线永久降级 FULL，增量渲染零投产
    # （wsi-hotel-cases 基线实测 scene_times_error=S-block-end-unverifiable）。
    # 重构后：end 字面量**在场即必须自洽**（脏数据仍强制降级，R8 语义保留）；
    # 缺席改由三重见证承接 —— ①root data-duration ②config video_duration 交叉校验
    # ③classify 帧网格 total_frames == round(duration*fps)（取自基线成片实测）。
    ends = [float(v) for v in re.findall(r'\bend:\s*([\d.]+)', script_all)]
    if ends and abs(max(ends) - duration) > 0.05:
        return None, f"S-block-end({max(ends)}) != data-duration({duration})"

    # config 时间源交叉校验
    cfg_dur = float(config.get("video_duration", 0))
    if abs(cfg_dur - duration) > 0.05:
        return None, f"config video_duration({cfg_dur}) != data-duration({duration})"

    # 帧网格可段渲染守卫：每个场景窗口至少占 1 帧，否则段渲染 expect_frames=0
    try:
        fps = float(config.get("fps", 25)) or 25.0
    except (TypeError, ValueError):
        fps = 25.0

    times = []
    for i, (sid, _, _, _) in enumerate(blocks):
        end = starts[i + 1] if i + 1 < len(starts) else duration
        if round(end * fps) - round(starts[i] * fps) < 1:
            return None, f"scene-window-sub-frame({sid}: {starts[i]}-{end} @ {fps}fps)"
        times.append({"id": sid, "start": starts[i], "end": end})
    return times, None


def scene_time_witness(html):
    """时长见证级别，写入 sidecar 供 classify 留痕。

    s-block-end        — 脚本含数值 end 字面量（并与 data-duration 自洽）
    duration-cross-check — end 字面量缺席，靠 data-duration + config + 帧网格见证
    """
    script_all = "\n".join(re.findall(r'<script(?:\s[^>]*)?>([\s\S]*?)</script>', html))
    return "s-block-end" if re.search(r'\bend:\s*[\d.]+', script_all) else "duration-cross-check"


# ── ffmpeg / ffprobe ──

def _ffmpeg():
    return os.environ.get("HYPERFRAMES_FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe():
    return os.environ.get("HYPERFRAMES_FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"


def _segment_timeout_seconds():
    """段渲染硬超时（秒）：复用 render_rules.json watchdog 的 grace + stall。

    该文件自述为"参数语义权威源，禁止任何脚本另设分叉默认值"，故此处读取而非
    自定义；仅在文件不可读时退回其文档化默认值（360 + 300）。超时的语义是
    "降级全量渲染"，不是"判定失败交付"，因此取宽于段长的上界是安全方向。
    """
    rules = ROOT / "程序文件" / "配置" / "config" / "quality" / "render_rules.json"
    try:
        wd = json.loads(rules.read_text(encoding="utf-8")).get("watchdog", {})
        return float(wd.get("grace_seconds", 360)) + float(wd.get("stall_seconds", 300))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 660.0


def _kill_tree(pid):
    """杀渲染进程树，与 pipeline_runner._RenderProgressWatcher._kill 同法。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        else:
            os.kill(pid, 9)
    except Exception as e:
        print(f"  [SCENE-PATCH] kill failed: {e}")


def probe_video(path):
    """返回 {frames, fps, width, height, has_audio} 或 None。"""
    r = subprocess.run(
        [_ffprobe(), "-v", "error", "-count_frames", "-show_entries",
         "stream=codec_type,nb_read_frames,r_frame_rate,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    info, has_audio = None, False
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and info is None:
            num, den = st.get("r_frame_rate", "0/1").split("/")
            info = {"frames": int(st.get("nb_read_frames", 0)),
                    "fps": float(num) / float(den) if float(den) else 0,
                    "width": st.get("width"), "height": st.get("height")}
        elif st.get("codec_type") == "audio":
            has_audio = True
    if info:
        info["has_audio"] = has_audio
    return info


def extract_frame(video, frame_idx, fps, out_png):
    t = frame_idx / fps
    r = subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-ss", f"{t:.6f}", "-i", str(video),
         "-frames:v", "1", str(out_png)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0 and Path(out_png).exists()


def mean_abs_diff(png_a, png_b):
    from PIL import Image, ImageChops
    with Image.open(png_a) as a, Image.open(png_b) as b:
        diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
        hist = diff.histogram()
        total, count = 0, 0
        for ch in range(3):
            for v, n in enumerate(hist[ch * 256:(ch + 1) * 256]):
                total += v * n
                count += n
        return total / count if count else 255.0


# ── PatchRenderer ──

class PatchRenderer:
    def __init__(self, config_path):
        self.config_path = Path(config_path)
        if not self.config_path.is_absolute():
            for subdir in ("", "pipelines", "openmontage", "system"):
                cand = (CONFIG_DIR / subdir / self.config_path) if subdir else (CONFIG_DIR / self.config_path)
                if cand.exists():
                    self.config_path = cand
                    break
        if not self.config_path.exists():
            print(f"ERROR: config not found: {self.config_path}")
            sys.exit(1)
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        paths = self.config.get("paths", {})
        self.html_project = paths.get("html_project", "")
        self.source_dir = HTML_BASE / self.html_project
        self.html_path = self.source_dir / "index.html"
        self.temp_dir = TEMP_BASE / paths.get("temp_subdir", f"{self.html_project}_audio")
        self.render_raw = self.temp_dir / "render_raw.mp4"
        self.sidecar_path = self.temp_dir / SIDECAR_NAME
        self.verdict_path = self.temp_dir / VERDICT_NAME
        self.fps = int(self.config.get("fps", 25))

    # ── sidecar ──

    def write_sidecar(self):
        if not self.render_raw.exists():
            print(f"ERROR: render_raw missing: {self.render_raw}")
            return 1
        html = self.html_path.read_text(encoding="utf-8")
        fp = extract_fingerprints(html, self.config, self.source_dir / "narration.json")
        times, err = extract_scene_times(html, self.config)
        info = probe_video(self.render_raw)
        if info is None:
            print("ERROR: ffprobe failed on render_raw")
            return 1
        sidecar = {
            "version": SIDECAR_VERSION,
            "created": datetime.now().isoformat(),
            "fps": self.fps,
            "width": info["width"], "height": info["height"],
            "total_frames": info["frames"],
            "has_audio": info["has_audio"],
            "scene_times": times,          # None 表示时间源不可信（classify 时强制 FULL）
            "scene_times_error": err,
            "scene_times_witness": scene_time_witness(html) if times else None,
            **fp,
        }
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        with open(self.sidecar_path, "w", encoding="utf-8") as f:
            json.dump(sidecar, f, ensure_ascii=False, indent=2)
        print(f"  Sidecar written: {self.sidecar_path.name} "
              f"({len(fp['scene_order'])} scenes, {info['frames']} frames"
              f"{', WARN time-source: ' + err if err else ''})")
        return 0

    # ── classify ──

    def classify(self):
        """返回 (verdict, changed_scene_ids, reason, ctx)。verdict ∈ {"PATCH","FULL"}"""
        def full(reason):
            return "FULL", [], reason, {}

        if not self.sidecar_path.exists():
            return full("no-sidecar-baseline")
        if not self.render_raw.exists():
            return full("no-render_raw-baseline")
        with open(self.sidecar_path, "r", encoding="utf-8") as f:
            base = json.load(f)
        if base.get("version") != SIDECAR_VERSION:
            return full("sidecar-version-mismatch")
        if base.get("scene_times") is None:
            return full(f"baseline-time-source-untrusted({base.get('scene_times_error')})")
        if base.get("has_audio"):
            return full("baseline-has-audio-stream")
        if base.get("fps") != self.fps:
            return full("fps-changed")

        html = self.html_path.read_text(encoding="utf-8")
        cur = extract_fingerprints(html, self.config, self.source_dir / "narration.json")
        times, err = extract_scene_times(html, self.config)
        if err:
            return full(f"time-source-untrusted({err})")

        if cur["scene_order"] != base.get("scene_order"):
            return full("scene-structure-changed")
        # 白名单外一律全量：style/script/config时间字段/narration/残余HTML
        for key, label in (("residual_hash", "html-outside-scenes"),
                           ("style_hash", "style"), ("script_hash", "script"),
                           ("config_hash", "config-render-fields"),
                           ("narration_hash", "narration")):
            if cur[key] != base.get(key):
                return full(f"{label}-changed")
        if times != base.get("scene_times"):
            return full("scene-timing-changed")

        # 帧网格：总帧数必须与时长×fps吻合（拼接前提）
        duration = times[-1]["end"]
        if base.get("total_frames") != round(duration * self.fps):
            return full(f"frame-grid-mismatch(frames={base.get('total_frames')}, "
                        f"dur*fps={duration * self.fps})")

        changed = [sid for sid in cur["scene_order"]
                   if cur["scene_hashes"][sid] != base["scene_hashes"].get(sid)]
        if not changed:
            return full("no-scene-content-change")
        if len(changed) * 2 > len(cur["scene_order"]):
            return full(f"too-many-scenes-changed({len(changed)}/{len(cur['scene_order'])})")

        ctx = {"base": base, "times": times, "html": html,
               "witness": base.get("scene_times_witness") or "s-block-end"}
        return "PATCH", changed, "", ctx

    # ── patch（段渲染 + 拼接 + 自校验） ──

    def _plan_segments(self, times, changed):
        """帧号对齐的分段计划。返回 [(kind, start_f, end_f, scene_ids)]，kind∈{keep,render}"""
        bounds = [round(t["start"] * self.fps) for t in times] + \
                 [round(times[-1]["end"] * self.fps)]
        segs = []
        for i, t in enumerate(times):
            kind = "render" if t["id"] in changed else "keep"
            if segs and segs[-1][0] == kind:
                segs[-1] = (kind, segs[-1][1], bounds[i + 1], segs[-1][3] + [t["id"]])
            else:
                segs.append((kind, bounds[i], bounds[i + 1], [t["id"]]))
        return segs

    def _make_wrapper(self, html, seg_start_f, seg_end_f, idx):
        """生成 wrapper 临时 composition：root duration 改段长 + 时间平移注入脚本。"""
        seg_start = seg_start_f / self.fps
        seg_len = (seg_end_f - seg_start_f) / self.fps
        root_m = re.search(r'<div\b[^>]*\bdata-composition-id="[^"]*"[^>]*>', html)
        root_tag = root_m.group(0)
        new_tag = re.sub(r'\bdata-duration="[^"]*"', f'data-duration="{seg_len}"', root_tag)
        new_tag = re.sub(r'\s*\bdata-cover-duration="[^"]*"', '', new_tag)
        wrapped = html.replace(root_tag, new_tag, 1)
        inject = f"""
<script>
/* scene-patch wrapper: 原时间轴 [{seg_start}, {seg_start + seg_len}) 平移到本地 [0, {seg_len}) */
(function () {{
  var SEG_START = {seg_start}, SEG_END = {seg_start + seg_len};
  var main = window.__timelines && window.__timelines.main;
  if (!main) return;
  try {{ main.pause(); }} catch (e) {{}}
  var seg = gsap.timeline({{ paused: true }});
  seg.add(main.tweenFromTo(SEG_START, SEG_END, {{ ease: "none" }}), 0);
  main.seek(SEG_START, false); /* 预热：重建段起点前全部累积状态 */
  window.__timelines.main = seg;
  seg.play();
}})();
</script>
</body>"""
        wrapped = wrapped.replace("</body>", inject, 1)
        wrapper_path = self.source_dir / f"_patch_seg_{idx}.html"
        wrapper_path.write_text(wrapped, encoding="utf-8")
        return wrapper_path

    def _render_segment(self, wrapper_path, out_mp4, expect_frames):
        cmd = ["npx.cmd", "hyperframes", "render",
               "-c", wrapper_path.name, "-o", str(out_mp4),
               "--fps", str(self.fps), "--workers", "1",
               "--low-memory-mode", "--protocol-timeout", "600000"]
        # 与 pipeline_runner 全量渲染同条件的 GPU 栈病态规避（2026-08-07）：
        # 缺此条时段渲染会独走 GPU 探测路径，在该环境下可永不返回。
        if os.environ.get("PRODUCER_HEADLESS_SHELL_PATH"):
            cmd.append("--no-browser-gpu")
        limit = _segment_timeout_seconds()
        print(f"  > {' '.join(cmd[:6])}... (hard timeout {limit:.0f}s)")
        proc = subprocess.Popen(cmd, cwd=str(self.source_dir))
        try:
            rc = proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            print(f"  SEGMENT RENDER TIMEOUT > {limit:.0f}s — process tree killed; "
                  f"falling back to FULL")
            return False
        if rc != 0 or not out_mp4.exists():
            print(f"  SEGMENT RENDER FAILED (rc={rc})")
            return False
        info = probe_video(out_mp4)
        if not info or info["frames"] != expect_frames:
            print(f"  SEGMENT FRAME MISMATCH: got {info and info['frames']}, expect {expect_frames}")
            return False
        return True

    def _stitch(self, segs, seg_files, out_mp4):
        """基线未变段（按帧号 trim）+ 新渲染段 concat，统一参数重编码。"""
        inputs = [_x for f in seg_files.values() for _x in ("-i", str(f))]
        input_idx = {k: i + 1 for i, k in enumerate(seg_files)}  # 0 = baseline
        parts, labels = [], []
        for n, (kind, sf, ef, ids) in enumerate(segs):
            lab = f"[v{n}]"
            if kind == "keep":
                parts.append(f"[0:v]trim=start_frame={sf}:end_frame={ef},setpts=PTS-STARTPTS{lab}")
            else:
                parts.append(f"[{input_idx[n]}:v]setpts=PTS-STARTPTS{lab}")
            labels.append(lab)
        fc = ";".join(parts) + f";{''.join(labels)}concat=n={len(segs)}:v=1:a=0[out]"
        cmd = [_ffmpeg(), "-y", "-v", "error", "-i", str(self.render_raw), *inputs,
               "-filter_complex", fc, "-map", "[out]", "-r", str(self.fps),
               "-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-pix_fmt", "yuv420p", str(out_mp4)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"  STITCH FAILED: {r.stderr[-500:]}")
            return False
        return True

    def _self_check(self, patched, segs, times, changed, shots_dir):
        """未变场景抽帧 diff≈0；拼接点未变侧边界帧 diff<阈值。失败返回 False。"""
        shots_dir.mkdir(parents=True, exist_ok=True)
        checks = []  # (frame_idx, max_diff, label)
        for t in times:
            if t["id"] in changed:
                continue
            mid = (round(t["start"] * self.fps) + round(t["end"] * self.fps)) // 2
            checks.append((mid, DIFF_UNCHANGED_MAX, f"unchanged-scene-{t['id']}-mid"))
        for n in range(1, len(segs)):
            junction = segs[n][1]
            if segs[n - 1][0] == "keep":
                checks.append((junction - 1, DIFF_JUNCTION_MAX, f"junction-{junction}-left"))
            if segs[n][0] == "keep":
                checks.append((junction, DIFF_JUNCTION_MAX, f"junction-{junction}-right"))
        ok = True
        for frame_idx, limit, label in checks:
            a = shots_dir / f"{label}_base.png"
            b = shots_dir / f"{label}_patch.png"
            if not (extract_frame(self.render_raw, frame_idx, self.fps, a)
                    and extract_frame(patched, frame_idx, self.fps, b)):
                print(f"  SELF-CHECK EXTRACT FAILED: {label}")
                ok = False
                continue
            d = mean_abs_diff(a, b)
            status = "OK" if d <= limit else "FAIL"
            print(f"  [{status}] {label}: frame {frame_idx} diff={d:.2f} (limit {limit})")
            if d > limit:
                ok = False
        return ok

    def _record_verdict(self, verdict, reason, changed, outcome, witness, t0):
        """裁定留痕（数据而非仅 stdout）：供 pipeline_runner 写入 state 与完工报告。

        2026-09-03：增量渲染长期零投产的原因之一是"没用上"只体现在 stdout，
        state 里没有任何字段能证明分类器跑过、为何降级。写盘失败不改变返回值——
        留痕是观测面，不得反过来影响渲染判定。
        """
        rec = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "verdict": verdict,
            "reason": reason,
            "changed_scenes": changed,
            "time_witness": witness,
            "outcome": outcome,
            "elapsed_seconds": round(time.time() - t0, 1),
        }
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            with open(self.verdict_path, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"  WARNING: verdict record failed: {e}")
        return rec

    def patch(self):
        t0 = time.time()
        verdict, changed, reason, ctx = self.classify()
        if verdict != "PATCH":
            print(f"  CLASSIFY: FULL ({reason})")
            self._record_verdict(verdict, reason, changed, "fallback-full", None, t0)
            return EXIT_FALLBACK
        times = ctx["times"]
        witness = ctx.get("witness")
        print(f"  CLASSIFY: PATCH — changed scenes: {', '.join(changed)} "
              f"(time witness: {witness})")
        rc, outcome = self._patch_execute(ctx, changed, times)
        self._record_verdict(verdict, reason, changed, outcome, witness, t0)
        return rc

    def _patch_execute(self, ctx, changed, times):
        """段渲染 + 拼接 + 自校验。返回 (rc, outcome)。"""
        # wrapper 落在项目源码目录：上次段渲染被超时杀掉时 finally 不会执行，
        # 残留的 _patch_seg_*.html 会让 hyperframes lint 报 multiple_root_compositions，
        # 干扰后续全量渲染。开工前先清掉同前缀残留（只认这一个前缀）。
        for stale in self.source_dir.glob("_patch_seg_*.html"):
            try:
                stale.unlink()
                print(f"  Removed stale wrapper: {stale.name}")
            except OSError:
                pass

        segs = self._plan_segments(times, changed)
        for kind, sf, ef, ids in segs:
            print(f"    [{kind}] frames {sf}-{ef} ({(ef - sf) / self.fps:.1f}s) {'+'.join(ids)}")

        patch_dir = self.temp_dir / "scene_patch"
        patch_dir.mkdir(parents=True, exist_ok=True)
        seg_files, wrappers = {}, []
        try:
            for n, (kind, sf, ef, ids) in enumerate(segs):
                if kind != "render":
                    continue
                wrapper = self._make_wrapper(ctx["html"], sf, ef, n)
                wrappers.append(wrapper)
                out = patch_dir / f"seg_{n}.mp4"
                if not self._render_segment(wrapper, out, ef - sf):
                    return EXIT_FALLBACK, "segment-render-failed"
                seg_files[n] = out

            patched = patch_dir / "render_patched.mp4"
            if not self._stitch(segs, seg_files, patched):
                return EXIT_FALLBACK, "stitch-failed"

            info = probe_video(patched)
            base_frames = ctx["base"]["total_frames"]
            if not info or info["frames"] != base_frames:
                print(f"  FRAME COUNT MISMATCH: patched={info and info['frames']}, "
                      f"baseline={base_frames}")
                return EXIT_FALLBACK, "frame-count-mismatch"
            print(f"  Frame count verified: {info['frames']} == baseline")

            if not self._self_check(patched, segs, times, changed, patch_dir / "shots"):
                print("  SELF-CHECK FAILED — falling back to FULL")
                return EXIT_FALLBACK, "self-check-failed"

            os.replace(patched, self.render_raw)
            print(f"  render_raw.mp4 replaced (patched)")
            if self.write_sidecar() != 0:
                return EXIT_FALLBACK, "sidecar-refresh-failed"
            return 0, "applied"
        finally:
            for w in wrappers:
                try:
                    w.unlink()
                except OSError:
                    pass


def main():
    parser = argparse.ArgumentParser(description="Scene-level incremental render")
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["sidecar", "classify", "patch"], required=True)
    args = parser.parse_args()

    pr = PatchRenderer(args.config)
    if args.mode == "sidecar":
        sys.exit(pr.write_sidecar())
    if args.mode == "classify":
        t0 = time.time()
        verdict, changed, reason, ctx = pr.classify()
        pr._record_verdict(verdict, reason, changed, "classify-only",
                           ctx.get("witness"), t0)
        print(json.dumps({"verdict": verdict, "changed": changed, "reason": reason,
                          "time_witness": ctx.get("witness")}, ensure_ascii=False))
        sys.exit(0 if verdict == "PATCH" else EXIT_FALLBACK)
    sys.exit(pr.patch())


if __name__ == "__main__":
    main()
