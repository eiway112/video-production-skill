#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
统一流水线运行器（HyperFrames + OpenMontage）

工作流意义：一条命令从意图到交付。用户写SDL → 系统编译+渲染+校验 → 交付视频。

根据配置文件中的 engine 字段自动路由到对应引擎：
  - engine=hyperframes: HyperFrames HTML+GSAP 渲染流水线
  - engine=openmontage: OpenMontage Agent-Driven 视频制作流水线

HyperFrames 流水线步骤：
  preflight → tts → timeline → preview → render → verify → visual_check → postprocess

OpenMontage 流水线步骤（按 pipeline 类型不同）：
  animated-explainer: preflight → parse_input → generate_script → plan_scenes → generate_assets → compose → publish
  clip-factory:       preflight → analyze_source → select_clips → extract_clips → enhance → compose → publish
  podcast-repurpose:  preflight → transcribe_audio → plan_segments → generate_visuals → compose → publish

用法：
  # 统一入口（推荐）：SDL → 编译 → 全量流水线 → 交付
  python pipeline_runner.py --sdl project.yaml

  # 传统入口：已有 HTML+Config 时直接使用
  python pipeline_runner.py --config hotel_pw_enhance.json
  python pipeline_runner.py --config hotel_pw_enhance.json --quick-fix
  python pipeline_runner.py --config openmontage_demo.json --engine openmontage
  python pipeline_runner.py --config hotel_pw_enhance.json --resume
  python pipeline_runner.py --config hotel_pw_enhance.json --status
"""

import argparse
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
from _gsap_time_utils import parse_scene_map
from pipeline_state_fingerprint import compute_inputs_fingerprint
from _script_env import ROOT, VENV_PYTHON

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


SCRIPTS = ROOT / "程序文件" / "脚本"
CONFIG_DIR = ROOT / "程序文件" / "配置" / "config"
HTML_BASE = ROOT / "程序文件" / "源码" / "hyperframes"
OUTPUT_DIR = ROOT / "成果文件" / "视频"
TEMP_BASE = ROOT / "过程产物" / "临时产物"

# Step definitions with ordering
STEPS = [
    ("preflight", "静态预检"),
    ("tts", "TTS 预生成"),
    ("timeline", "时间轴调整"),
    ("preview", "瞬时预览（场景缩略图+动画密度）"),
    ("render", "HyperFrames 渲染"),
    ("verify", "渲染后时长验证"),
    ("visual_check", "视觉边界检测"),
    ("postprocess", "音频/字幕后处理"),
]

# Hard gate: which steps MUST have passed before a given step can execute.
# This is the enforcement layer — bypass is not allowed unless --force is set.
# 
# P0-06 整改：完整依赖链确保不遗漏关键校验
# - render 必须依赖 tts（确保有音频产物）
# - render 必须依赖 timeline（确保时间轴已调整）
# - postprocess 必须依赖 visual_check（确保画面边界检查通过）
# - delivery/completion_report 新增（最终交付关卡）
HARD_GATES = {
    "tts":         ["preflight"],                      # TTS 需要场景定义检查通过
    "timeline":    ["tts"],                            # 时间轴调整依赖 TTS 产物存在
    "preview":     ["timeline"],                       # 预览依赖时间轴
    "render":      ["preflight", "tts", "timeline", "preview"],  # 渲染完整依赖链
    "verify":      ["render"],                         # 验证依赖渲染
    "visual_check": ["render"],                        # 视觉检查依赖渲染
    "postprocess": ["verify", "visual_check"],         # 后处理依赖两项检查
    "delivery":    ["postprocess"],                    # 交付依赖后处理完成
    "completion_report": ["delivery"],                 # 完工报告依赖交付完成
}

STEP_NAMES = [s[0] for s in STEPS]


def compile_sdl(sdl_path):
    """Compile SDL(YAML) to HTML+config via scene_compiler.

    Returns (config_path: Path, html_project: str) or None on failure.
    """
    try:
        import yaml
    except ImportError:
        print("ERROR: PyYAML not installed. Run: pip install pyyaml")
        return None

    from scene_compiler import compile_sdl as _compile

    with open(sdl_path, 'r', encoding='utf-8') as f:
        sdl_data = yaml.safe_load(f)

    name = sdl_data.get('video', {}).get('name', 'untitled')
    output_dir = HTML_BASE / name

    result = _compile(sdl_data, output_dir)
    if result is None:
        print(f"ERROR: SDL compilation failed: {sdl_path}")
        return None

    html_path, config_path = result
    html_project = output_dir.name
    print(f"\n=== SDL Compiled: {html_project} ===\n")
    return config_path, html_project


class PipelineState:
    """Track pipeline execution state in a JSON file."""

    def __init__(self, state_path):
        self.path = Path(state_path)
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError) as e:
                # Corrupted state file — attempt recovery by extracting
                # the last valid JSON object (multiple writes may have concatenated)
                print(f"  WARNING: State file corrupted ({e}), attempting recovery...")
                raw = self.path.read_text(encoding='utf-8')
                # Split on "}\n{" boundary and try the last segment
                parts = raw.rsplit('}\n{', 1)
                if len(parts) == 2:
                    candidate = '{' + parts[1]
                    try:
                        data = json.loads(candidate)
                        print(f"  RECOVERED: Using last valid state segment")
                        return data
                    except json.JSONDecodeError:
                        pass
                # If recovery fails, start fresh
                print(f"  RECOVERY FAILED: Starting with fresh state")
                return {"steps": {}, "last_run": None}
        return {"steps": {}, "last_run": None}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def mark_started(self, step):
        self.data["steps"][step] = {
            "status": "running",
            "started": datetime.now().isoformat(),
            "completed": None,
            "error": None,
        }
        self.data["last_run"] = datetime.now().isoformat()
        self.save()

    def mark_completed(self, step, input_fingerprint=None):
        self.data["steps"][step]["status"] = "passed"
        self.data["steps"][step]["completed"] = datetime.now().isoformat()
        # 缓存中毒防御：记录完成时刻的输入指纹，跳步判定必须凭指纹兑现。
        # 无指纹的旧状态（历史格式）一律不予信任。
        if input_fingerprint:
            self.data["steps"][step]["input_fingerprint"] = input_fingerprint
        self.save()

    def mark_failed(self, step, error=""):
        self.data["steps"][step]["status"] = "failed"
        self.data["steps"][step]["error"] = error[:200]
        self.save()

    def mark_skipped(self, step, reason=""):
        self.data["steps"][step] = {
            "status": "skipped",
            "started": None,
            "completed": None,
            "error": None,
            "reason": reason,
        }
        self.save()

    def reset(self, reason=""):
        """清空全部步骤状态（--fresh 真实重跑入口）。"""
        self.data = {"steps": {}, "last_run": None, "reset_reason": reason,
                     "reset_at": datetime.now().isoformat()}
        self.save()

    def is_passed(self, step):
        return self.data.get("steps", {}).get(step, {}).get("status") == "passed"

    def last_failed_step(self):
        for name, info in reversed(list(self.data.get("steps", {}).items())):
            if info.get("status") == "failed":
                return name
        return None

    def print_status(self):
        print("=" * 60)
        print("PIPELINE STATE")
        print("=" * 60)
        if self.data.get("last_run"):
            print(f"  Last run: {self.data['last_run']}")
        print()
        for step_name, step_label in STEPS:
            info = self.data.get("steps", {}).get(step_name, {})
            status = info.get("status", "pending")
            icons = {"passed": "+", "failed": "X", "running": ">", "skipped": "-"}
            icon = icons.get(status, " ")
            line = f"  [{icon}] {step_label}"
            if status == "failed" and info.get("error"):
                line += f"  -- {info['error'][:60]}"
            elif status == "passed" and info.get("completed"):
                line += f"  ({info['completed'][:19]})"
            elif status == "skipped" and info.get("reason"):
                line += f"  ({info['reason']})"
            print(line)
        print()


def _load_audio_sync_rules():
    """Load config/quality/audio_sync_rules.json with default fallback.

    Consumed by step_timeline (shrink defaults) and step7_validate in
    enhance_video_audio.py. Never fails the pipeline on read errors —
    falls back to hard-coded defaults documented in AGENTS.md.
    """
    rules_path = CONFIG_DIR / "quality" / "audio_sync_rules.json"
    defaults = {
        "dead_air": {"max_seconds_per_scene": 3.0, "fail_on_violation": True},
        "shrink": {"enabled_by_default": True, "shrink_margin_seconds": 1.0},
    }
    if not rules_path.exists():
        return defaults
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        for k, v in loaded.items():
            if not k.startswith("$"):
                defaults[k] = v
        return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


class PipelineRunner:
    def __init__(self, config_path, html_project=None, quick_fix=False, force=False, gate_mode="render", shrink=True, fresh=False):
        self.config_path = Path(config_path)
        if not self.config_path.is_absolute():
            # Search subdirectories (pipelines/, openmontage/, system/) then root
            resolved = None
            for subdir in ("", "pipelines", "openmontage", "system"):
                candidate = CONFIG_DIR / subdir / self.config_path if subdir else CONFIG_DIR / self.config_path
                if candidate.exists():
                    resolved = candidate
                    break
            self.config_path = resolved or (CONFIG_DIR / self.config_path)

        if not self.config_path.exists():
            print(f"ERROR: Config not found: {self.config_path}")
            sys.exit(1)

        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        # Resolve paths from config
        paths = self.config.get("paths", {})
        self.html_project = html_project or paths.get("html_project", "")
        if not self.html_project:
            print("ERROR: html_project not specified (via --html or config paths.html_project)")
            sys.exit(1)

        self.source_dir = HTML_BASE / self.html_project
        self.html_path = self.source_dir / "index.html"
        self.temp_dir = TEMP_BASE / paths.get("temp_subdir", f"{self.html_project}_audio")
        self.tts_dir = self.temp_dir / "tts_44k"
        self.render_raw = self.temp_dir / "render_raw.mp4"

        video_name = paths.get("video_name", f"{self.html_project}.mp4")
        self.output_file = OUTPUT_DIR / video_name

        self.state = PipelineState(self.temp_dir / "pipeline_state.json")
        self.quick_fix = quick_fix
        self.force = force
        # --fresh：验证/验收类运行的规定入口。清空旧状态，所有步骤真实重跑，
        # 但保留 HARD_GATES 门禁（区别于 --force 的门禁绕过语义）。
        if fresh:
            prev = self.state.data.get("last_run")
            self.state.reset(reason=f"--fresh (previous last_run: {prev})")
            print(f"  [FRESH] Pipeline state reset (previous last_run: {prev or 'none'}) — all steps will truly re-run")
        self.gate_mode = gate_mode  # "render" or "audit"
        # Audio-sync policy: shrink oversized scene windows so TTS drives
        # the timeline. See AGENTS.md → 渲染纪律 → 音画同步.
        self.audio_sync_rules = _load_audio_sync_rules()
        shrink_default = self.audio_sync_rules.get("shrink", {}).get("enabled_by_default", True)
        # CLI --no-shrink overrides config default; explicit shrink=True/False wins.
        self.shrink = shrink if shrink is not None else shrink_default

    def _run(self, cmd, cwd=None, env=None, desc=""):
        """Run subprocess, return exit code."""
        print(f"  > {' '.join(str(c) for c in cmd[:6])}{'...' if len(cmd) > 6 else ''}")
        # Ensure child Python processes use UTF-8 I/O (avoid GBK codec errors on Windows)
        run_env = env or os.environ.copy()
        run_env.setdefault('PYTHONIOENCODING', 'utf-8')
        result = subprocess.run(
            cmd, cwd=cwd, env=run_env,
            capture_output=False
        )
        return result.returncode

    def _run_with_retry(self, cmd, cwd=None, env=None, desc="", retries=1):
        """Run subprocess with auto-retry on transient failure (network/browser)."""
        rc = self._run(cmd, cwd=cwd, env=env, desc=desc)
        for attempt in range(retries):
            if rc == 0:
                break
            print(f"  [RETRY] {desc} failed (attempt {attempt+1}/{retries}), retrying in 3s...")
            time.sleep(3)
            rc = self._run(cmd, cwd=cwd, env=env, desc=f"{desc} (retry)")
        return rc

    def _setup_env(self):
        """Configure environment for HyperFrames rendering."""
        env = os.environ.copy()

        # Ensure Node.js in PATH
        node_path = r"C:\Program Files\nodejs"
        if node_path not in env.get("PATH", ""):
            env["PATH"] = f"{node_path};{env.get('PATH', '')}"

        # Chrome browser
        browser = env.get("HYPERFRAMES_BROWSER_PATH", "")
        if not browser:
            for p in [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]:
                if os.path.exists(p):
                    browser = p
                    break
            if browser:
                env["HYPERFRAMES_BROWSER_PATH"] = browser

        # FFmpeg + FFprobe (ASCII symlink for Chinese paths)
        ffmpeg_exe = shutil.which("ffmpeg")
        if ffmpeg_exe:
            ffmpeg_dir = str(Path(ffmpeg_exe).parent)
            if any(ord(c) >= 128 for c in ffmpeg_dir):
                link_dir = self.temp_dir / "ffmpeg-bin"
                if not link_dir.exists():
                    link_dir.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["cmd", "/c", "mklink", "/J", str(link_dir), ffmpeg_dir],
                        capture_output=True, text=True, encoding='utf-8', errors='replace'
                    )
                ffmpeg_exe = str(link_dir / "ffmpeg.exe")
            env["HYPERFRAMES_FFMPEG_PATH"] = ffmpeg_exe
            ffprobe_dir = Path(ffmpeg_exe).parent
            ffprobe_exe = str(ffprobe_dir / "ffprobe.exe")
            if os.path.exists(ffprobe_exe):
                env["HYPERFRAMES_FFPROBE_PATH"] = ffprobe_exe

        # Increase Node.js heap for large compositions (4150+ frames)
        existing_opts = env.get('NODE_OPTIONS', '')
        if '--max-old-space-size' not in existing_opts:
            env['NODE_OPTIONS'] = f'--max-old-space-size=8192 {existing_opts}'.strip()

        # Disable streaming encode to avoid V8 Set size limit on long compositions
        env['PRODUCER_ENABLE_STREAMING_ENCODE'] = 'false'
        # Disable static frame dedup (can cause Set size issues on complex timelines)
        env['HF_STATIC_DEDUP'] = 'false'
        # Ensure child Python processes use UTF-8 I/O
        env['PYTHONIOENCODING'] = 'utf-8'

        self.env = env
        self.browser_path = browser
        self.ffmpeg_path = ffmpeg_exe

        # Validate
        issues = []
        if not browser:
            issues.append("Chrome not found (set HYPERFRAMES_BROWSER_PATH)")
        if not ffmpeg_exe:
            issues.append("ffmpeg not found in PATH")
        if not VENV_PYTHON.exists():
            issues.append(f"venv Python not found: {VENV_PYTHON}")

        if issues:
            for issue in issues:
                print(f"  ERROR: {issue}")
            return False

        print(f"  Browser: {browser}")
        print(f"  FFmpeg:  {ffmpeg_exe}")
        print(f"  Node:    {node_path}")
        return True

    # --- Cache-poisoning defense (跳步判定) ---
    # 教训：2026-07-16 的旧 pipeline_state.json 曾让后续运行跳过多数步骤。
    # 规则：跳步必须同时满足 status=passed AND 输入指纹一致；
    #       无指纹的旧状态一律重跑；命中缓存必须显式打印状态日期。

    def _step_inputs(self, step):
        """每个步骤的输入文件清单（用于指纹计算）。"""
        narration = self.source_dir / "narration.json"
        compiled = self.source_dir / "_compiled_scenes.json"
        tts_manifest = self.tts_dir / "tts_manifest.json"
        inputs_map = {
            "preflight":    {"config": self.config_path, "html": self.html_path},
            "tts":          {"config": self.config_path, "narration": narration,
                             "compiled_scenes": compiled},
            "timeline":     {"config": self.config_path, "html": self.html_path,
                             "tts_manifest": tts_manifest},
            "preview":      {"config": self.config_path, "html": self.html_path},
            "render":       {"config": self.config_path, "html": self.html_path},
            "verify":       {"config": self.config_path, "html": self.html_path,
                             "render_raw": self.render_raw},
            "visual_check": {"config": self.config_path, "render_raw": self.render_raw},
            "postprocess":  {"config": self.config_path, "html": self.html_path,
                             "render_raw": self.render_raw, "narration": narration,
                             "tts_manifest": tts_manifest},
        }
        return {k: str(v) for k, v in inputs_map.get(step, {}).items()}

    def _fingerprint(self, step):
        """计算步骤当前输入的联合指纹（步骤完成时记录用）。"""
        return compute_inputs_fingerprint(self._step_inputs(step))

    def _step_dirty_reason(self, step):
        """步骤失效原因的纯判定（无日志副作用），供跳步判定与全链扫描共用。

        返回 None 表示可复用缓存；否则返回失效原因字符串：
          status=<x>     — 未通过/未执行/被失效
          no-fingerprint — 历史状态无指纹，不予信任
          inputs-changed — 输入指纹与完成时刻不一致（上游已变化）
        """
        rec = self.state.data.get("steps", {}).get(step, {})
        if rec.get("status") != "passed":
            return f"status={rec.get('status') or 'pending'}"
        old_fp = rec.get("input_fingerprint", "")
        if not old_fp:
            return "no-fingerprint"
        if old_fp != self._fingerprint(step):
            return "inputs-changed"
        return None

    def _earliest_rerun_step(self):
        """resume 前的全链指纹校验：返回 (最早需重跑步骤, 原因)。

        遍历整条依赖链，任一步骤输入指纹变化即视为失效——因此上游输入
        （如 HTML）被修复后，会自动回退到最早失效步，而非仅从失败步续跑。
        全部命中缓存时返回 (None, None)。
        """
        for step in STEP_NAMES:
            reason = self._step_dirty_reason(step)
            if reason is not None:
                return step, reason
        return None, None

    def _can_skip(self, step):
        """指纹校验的跳步判定。命中缓存时打印状态日期，保证旧状态可见。"""
        if self.force:
            return False
        reason = self._step_dirty_reason(step)
        rec = self.state.data.get("steps", {}).get(step, {})
        completed = (rec.get("completed") or "?")[:19]
        if reason == "no-fingerprint":
            print(f"  [STALE] Step passed at {completed} but has no input fingerprint — not trusted, re-running")
            return False
        if reason == "inputs-changed":
            print(f"  [CHANGED] Inputs changed since {completed} (fingerprint mismatch) — re-running")
            return False
        if reason is not None:
            return False
        old_fp = rec.get("input_fingerprint", "")
        print(f"  [CACHED] Step passed at {completed}, input fingerprint verified ({old_fp[:12]}...) — skipping")
        return True

    # --- Pipeline Steps ---

    def step_preflight(self):
        if self.quick_fix:
            self.state.mark_skipped("preflight", "quick-fix mode")
            return True
        if self._can_skip("preflight"):
            return True

        self.state.mark_started("preflight")
        config_name = self.config_path.name
        rc = self._run(
            [str(VENV_PYTHON), "preflight_check.py",
             "--config", config_name, "--html", self.html_project,
             "--mode", self.gate_mode],
            cwd=str(SCRIPTS),
            desc="preflight check"
        )
        if rc != 0:
            self.state.mark_failed("preflight", "Static validation failed")
            return False

        # Photo preprocessing: auto-crop if photo-crop-spec.json exists
        crop_spec = self.source_dir / "photo-crop-spec.json"
        if crop_spec.exists():
            print("  Photo crop spec found, preprocessing...")
            rc2 = self._run(
                [str(VENV_PYTHON), "preprocess_photos.py",
                 "--project", self.html_project, "--execute", "--update-html"],
                cwd=str(SCRIPTS),
                desc="photo preprocessing"
            )
            if rc2 != 0:
                self.state.mark_failed("preflight", "Photo preprocessing failed")
                return False

        self.state.mark_completed("preflight", self._fingerprint("preflight"))
        return True

    def step_tts(self):
        if self.quick_fix:
            self.state.mark_skipped("tts", "quick-fix mode")
            return True
        if self._can_skip("tts"):
            return True

        self.state.mark_started("tts")
        rc = self._run_with_retry(
            [str(VENV_PYTHON), "enhance_video_audio.py",
             "--config", str(self.config_path), "--tts-only"],
            cwd=str(SCRIPTS),
            desc="TTS generation"
        )
        if rc == 0:
            self.state.mark_completed("tts", self._fingerprint("tts"))
            return True
        self.state.mark_failed("tts", "TTS generation failed")
        return False

    def step_timeline(self):
        if self.quick_fix:
            self.state.mark_skipped("timeline", "quick-fix mode")
            return True
        if self._can_skip("timeline"):
            return True

        # Check if bak exists (required by adjust_timeline.py)
        # Use .html.bak extension (not .bak.html) to avoid HyperFrames
        # lint detecting it as a second composition root.
        bak_html = self.html_path.parent / (self.html_path.name + '.bak')
        if not bak_html.exists():
            print("  Creating backup for timeline adjustment...")
            shutil.copy2(self.html_path, bak_html)

        self.state.mark_started("timeline")

        # Delete stale BGM (video duration may change)
        bgm_file = self.temp_dir / "bgm.wav"
        if bgm_file.exists():
            bgm_file.unlink()
            print("  Deleted stale BGM (will regenerate)")

        cmd = [str(VENV_PYTHON), "adjust_timeline.py",
               "--html", self.html_project,
               "--config", str(self.config_path),
               "--tts-dir", str(self.tts_dir)]
        if self.shrink:
            margin = self.audio_sync_rules.get("shrink", {}).get("shrink_margin_seconds", 1.0)
            cmd.extend(["--shrink", "--shrink-margin", str(margin)])
            print(f"  Auto-shrink enabled (margin {margin}s) — scene windows will collapse to TTS + margin")
        rc = self._run(
            cmd,
            cwd=str(SCRIPTS),
            desc="timeline adjustment"
        )
        if rc == 0:
            # Reload config from disk — adjust_timeline.py may have updated video_duration
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            new_dur = self.config.get('video_duration', '?')
            print(f"  Config reloaded: video_duration = {new_dur}s")
            self.state.mark_completed("timeline", self._fingerprint("timeline"))
            return True
        self.state.mark_failed("timeline", "Timeline adjustment failed")
        return False

    def step_preview(self):
        """Instant preview: scene thumbnails + animation density analysis."""
        if self.quick_fix:
            self.state.mark_skipped("preview", "quick-fix mode")
            return True
        if self._can_skip("preview"):
            return True

        self.state.mark_started("preview")

        # Set NODE_PATH for puppeteer-core
        node_env = self.env.copy()
        hf_node_modules = os.path.join(
            os.environ.get('APPDATA', ''),
            'npm', 'node_modules', 'hyperframes', 'node_modules'
        )
        if os.path.exists(hf_node_modules):
            existing = node_env.get('NODE_PATH', '')
            node_env['NODE_PATH'] = f"{hf_node_modules};{existing}" if existing else hf_node_modules

        rc = self._run_with_retry(
            [str(VENV_PYTHON), "instant_preview.py",
             "--html", str(self.html_path),
             "--config", str(self.config_path)],
            cwd=str(SCRIPTS),
            env=node_env,
            desc="instant preview"
        )
        if rc == 0:
            self.state.mark_completed("preview", self._fingerprint("preview"))
            return True
        # Preview failure is FATAL — visual issues must be resolved before render.
        # This is the hard gate: do not waste 10+ minutes on a full render
        # when static preview has already detected structural problems.
        self.state.mark_failed("preview", "Instant preview failed — fix visual issues before rendering")
        return False

    def step_render(self):
        if self.quick_fix:
            self.state.mark_skipped("render", "quick-fix mode")
            return True
        if self._can_skip("render"):
            # Check render_raw.mp4 exists
            if self.render_raw.exists():
                return True
            print("  render_raw.mp4 missing, re-rendering...")

        # ── HARD GATE: preflight + preview must have passed ──
        # This is the most expensive step (~11 min + post-processing).
        # Refusing to proceed without verified static checks.
        if not self.force:
            for prereq in HARD_GATES.get("render", []):
                if not self.state.is_passed(prereq):
                    label = dict(STEPS).get(prereq, prereq)
                    print(f"  BLOCKED: Step '{prereq}' ({label}) has not passed.")
                    print(f"  RENDER REFUSED. Run preflight and preview first.")
                    print(f"  To override: use --force (not recommended)")
                    self.state.mark_failed("render", f"Hard gate: {prereq} not passed")
                    return False

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.state.mark_started("render")

        print("  Rendering HyperFrames (this takes ~11 minutes)...")
        rc = self._run(
            ["npx.cmd", "hyperframes", "render", "-o", str(self.render_raw), "--workers", "1", "--fps", "25", "--low-memory-mode", "--protocol-timeout", "600000"],
            cwd=str(self.source_dir),
            env=self.env,
            desc="HyperFrames render"
        )
        if rc != 0:
            self.state.mark_failed("render", "HyperFrames render failed")
            return False

        if not self.render_raw.exists():
            self.state.mark_failed("render", "render_raw.mp4 not created")
            return False

        size_mb = self.render_raw.stat().st_size / 1024 / 1024
        print(f"  Rendered: {self.render_raw} ({size_mb:.1f} MB)")

        # Copy to output directory for post-processing
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.render_raw, self.output_file)
        print(f"  Copied to: {self.output_file}")

        self.state.mark_completed("render", self._fingerprint("render"))
        return True

    def step_verify(self):
        if self.quick_fix:
            self.state.mark_skipped("verify", "quick-fix mode")
            return True
        if self._can_skip("verify"):
            return True

        self.state.mark_started("verify")

        # ffprobe duration check
        probe_result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_format", "-of", "json", str(self.render_raw)],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        try:
            probe_data = json.loads(probe_result.stdout)
            rendered_dur = float(probe_data["format"]["duration"])
        except (json.JSONDecodeError, KeyError):
            self.state.mark_failed("verify", "ffprobe returned invalid data")
            return False

        # Read expected duration: prefer S-block (authoritative), fall back to config JSON
        expected_dur = 0
        dur_source = "config"
        s_map = {}
        if self.html_path and self.html_path.exists():
            html_content = self.html_path.read_text(encoding='utf-8')
            script_match = re.search(r'<script>(.*?)</script>', html_content, re.DOTALL)
            if script_match:
                s_map = parse_scene_map(script_match.group(1))
                if s_map:
                    expected_dur = s_map['duration']
                    dur_source = "S-block"
        if not expected_dur:
            expected_dur = self.config.get("video_duration", 0)

        diff = abs(rendered_dur - expected_dur)

        print(f"  Rendered: {rendered_dur:.1f}s  Expected: {expected_dur:.1f}s ({dur_source})  Diff: {diff:.1f}s")

        if diff > 5.0:
            self.state.mark_failed(
                "verify",
                f"Duration mismatch: {rendered_dur:.1f}s vs {expected_dur:.1f}s ({dur_source}, diff {diff:.1f}s > 5s)"
            )
            print("  ERROR: Duration mismatch exceeds 5s. Investigate GSAP timeline.")
            # Scene-by-scene diagnostic: help locate which scene has the drift
            if s_map and s_map.get('scenes'):
                print("\n  Scene breakdown (S-block):")
                for i, sc in enumerate(s_map['scenes']):
                    label = f"cover" if sc.get('cover') else f"scene{i}" if not sc.get('cover') else ""
                    if sc.get('cover'):
                        label = "cover"
                    else:
                        # Count only content scenes
                        content_idx = sum(1 for s in s_map['scenes'][:i] if not s.get('cover'))
                        label = f"scene{content_idx + 1}"
                    print(f"    {label:10s}  {sc['start']:6.1f}s - {sc['end']:6.1f}s  (dur={sc['dur']:.1f}s, type={sc.get('type','?')})")
                print(f"  Total declared: {s_map['duration']:.1f}s  Rendered: {rendered_dur:.1f}s  Gap: {rendered_dur - s_map['duration']:+.1f}s")
            return False
        elif diff > 2.0:
            print("  WARNING: Duration drift detected. Proceeding but review recommended.")
            if s_map and s_map.get('scenes'):
                print("  Scene breakdown (S-block):")
                for i, sc in enumerate(s_map['scenes']):
                    if sc.get('cover'):
                        label = "cover"
                    else:
                        content_idx = sum(1 for s in s_map['scenes'][:i] if not s.get('cover'))
                        label = f"scene{content_idx + 1}"
                    print(f"    {label:10s}  {sc['start']:6.1f}s - {sc['end']:6.1f}s  (dur={sc['dur']:.1f}s)")

        self.state.mark_completed("verify", self._fingerprint("verify"))
        return True

    def step_visual_check(self):
        """Pixel-level content boundary check against subtitle safety zone.

        Extracts frames from render_raw.mp4 at each scene's midpoint and 70%,
        measures the lowest content pixel, fails if any scene overflows y=920.
        """
        if self.quick_fix:
            self.state.mark_skipped("visual_check", "quick-fix mode")
            return True
        if self._can_skip("visual_check"):
            return True

        self.state.mark_started("visual_check")

        cmd = [
            str(VENV_PYTHON), "visual_boundary_check.py",
            "--video", str(self.render_raw),
            "--config", str(self.config_path),
        ]

        rc = self._run(cmd, cwd=str(SCRIPTS), desc="visual boundary check")
        if rc == 0:
            self.state.mark_completed("visual_check", self._fingerprint("visual_check"))
            return True
        self.state.mark_failed("visual_check", "Content overflows subtitle safety zone")
        return False

    def step_postprocess(self):
        if self._can_skip("postprocess"):
            return True

        self.state.mark_started("postprocess")

        cmd = [str(VENV_PYTHON), "enhance_video_audio.py", "--config", str(self.config_path)]
        if self.quick_fix:
            cmd.append("--quick-fix")

        rc = self._run(cmd, cwd=str(SCRIPTS), desc="audio/subtitle post-processing")
        if rc == 0:
            self.state.mark_completed("postprocess", self._fingerprint("postprocess"))
            return True
        self.state.mark_failed("postprocess", "Post-processing failed")
        return False

    def run(self, start_from=None):
        """Execute the pipeline."""
        print("=" * 60)
        mode = "QUICK-FIX" if self.quick_fix else "FULL"
        print(f"PIPELINE [{mode}]: {self.html_project}")
        print("=" * 60)
        print(f"  Config:   {self.config_path.name}")
        print(f"  Source:   {self.source_dir}")
        print(f"  Output:   {self.output_file}")
        print(f"  Temp:     {self.temp_dir}")
        print(f"  Gate:     {self.gate_mode} ({'warn-only' if self.gate_mode == 'audit' else 'hard-block'})")
        print()

        # ── Hard gate prerequisite check when --step is used ──
        # Prevent jumping to render/postprocess without prerequisite steps passing.
        if start_from and not self.force and not self.quick_fix:
            required = HARD_GATES.get(start_from, [])
            missing = [s for s in required if not self.state.is_passed(s)]
            if missing:
                print(f"BLOCKED: Cannot start from '{start_from}'")
                for m in missing:
                    label = dict(STEPS).get(m, m)
                    print(f"  Missing prerequisite: {m} ({label})")
                print(f"\nRun prerequisites first, or use --force to override.")
                sys.exit(1)

        # Setup environment
        print("--- Environment Setup ---")
        if not self._setup_env():
            sys.exit(1)
        print()

        # Auto-derive step functions from STEPS + method naming convention.
        # Single source of truth: adding a step to STEPS requires a matching
        # step_{name}() method — no separate dict to maintain.
        step_funcs = {}
        for step_name, _ in STEPS:
            func = getattr(self, f"step_{step_name}", None)
            if func is None:
                print(f"FATAL: Missing step method: step_{step_name}()")
                sys.exit(1)
            step_funcs[step_name] = func

        start_idx = 0
        if start_from and start_from in STEP_NAMES:
            start_idx = STEP_NAMES.index(start_from)
        elif not self.quick_fix and not self.force:
            # 全链指纹校验：从最早失效步开始（上游输入变化会自动回退，
            # 而非仅从上次失败步续跑。未变化的上游步骤仍会命中缓存快速跳过）
            dirty_step, reason = self._earliest_rerun_step()
            if dirty_step and dirty_step in STEP_NAMES:
                start_idx = STEP_NAMES.index(dirty_step)
                if start_idx > 0:
                    print(f"Resuming from earliest stale/failed step: {dirty_step} ({reason})")
                    print()

        # Execute steps
        for i, (step_name, step_label) in enumerate(STEPS):
            if i < start_idx:
                continue

            print(f"--- Step {i+1}/{len(STEPS)}: {step_label} ---")
            func = step_funcs[step_name]
            ok = func()
            if not ok:
                print(f"\nPIPELINE FAILED at: {step_label}")
                print(f"Fix the issue and re-run with --resume or --quick-fix")
                sys.exit(1)
            print()

        # Success
        print("=" * 60)
        print("PIPELINE COMPLETE")
        print("=" * 60)
        print(f"  Video:    {self.output_file}")

        # Move .noaudio backup out of output dir
        backup_name = self.output_file.stem + ".noaudio.mp4"
        backup_path = self.output_file.parent / backup_name
        if backup_path.exists():
            dest = self.temp_dir / "render_noaudio_backup.mp4"
            shutil.move(str(backup_path), str(dest))
            print(f"  Backup moved to temp: {dest.name}")

        self.state.print_status()

        # Delivery notes reminder
        video_name = self.output_file.stem
        notes_path = self.output_file.parent / f"交付说明_{video_name}.md"
        if not notes_path.exists():
            print("-" * 60)
            print("REMINDER: Create delivery notes")
            print(f"  Template: AI视频制作工作流模板/templates_07_交付说明模板.md")
            print(f"  Output:   成果文件/交付说明_{video_name}.md")
            print("-" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="HyperFrames unified pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 统一入口（推荐）：SDL → 编译 → 全量流水线 → 交付
  python pipeline_runner.py --sdl project.yaml

  # 传统入口：已有 HTML+Config 时直接使用
  python pipeline_runner.py --config hotel_pw_enhance.json
  python pipeline_runner.py --config hotel_pw_enhance.json --quick-fix
  python pipeline_runner.py --config hotel_pw_enhance.json --resume
  python pipeline_runner.py --config hotel_pw_enhance.json --step render
  python pipeline_runner.py --config hotel_pw_enhance.json --status
        """
    )
    parser.add_argument("--config", default=None,
                        help="Config JSON file (e.g. hotel_pw_enhance.json)")
    parser.add_argument("--sdl", default=None, metavar="YAML",
                        help="SDL file (YAML) — auto-compiles to HTML+config, then runs full pipeline. "
                             "This is the ONE-COMMAND entry: intent → delivery.")
    parser.add_argument("--html", default=None,
                        help="HTML project folder (overrides config paths.html_project)")
    parser.add_argument("--quick-fix", action="store_true",
                        help="Skip Steps 0-1, re-run only post-processing")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last failed step")
    parser.add_argument("--step", choices=STEP_NAMES,
                        help="Run from a specific step")
    parser.add_argument("--status", action="store_true",
                        help="Show current pipeline state and exit")
    parser.add_argument("--force", action="store_true",
                        help="Re-run all steps AND bypass hard gates (emergency use). "
                             "For clean verification runs use --fresh instead.")
    parser.add_argument("--fresh", action="store_true",
                        help="Reset pipeline state and truly re-run every step, keeping "
                             "hard gates enforced. REQUIRED for verification/acceptance runs "
                             "(cache-poisoning defense: never trust a stale pipeline_state.json).")
    parser.add_argument("--gate-mode", choices=["audit", "render"],
                        default="render",
                        help="Preflight gate mode: 'audit' (warn-only) or 'render' (hard-block). "
                             "Default: render")
    parser.add_argument("--engine", choices=["hyperframes", "openmontage"],
                        default=None,
                        help="Pipeline engine (auto-detected from config if omitted)")
    parser.add_argument("--no-shrink", action="store_true",
                        help="Disable auto-shrink of oversized scene windows. "
                             "By default, timeline adjustment collapses windows so TTS drives timing "
                             "(prevents audio dead-air between scenes). Use only when the storyboard "
                             "intentionally requires silent breathing space in specific scenes.")

    args = parser.parse_args()

    # ── SDL 统一入口：意图 → 编译 → 流水线 → 交付 ──
    if args.sdl:
        sdl_path = Path(args.sdl)
        if not sdl_path.exists():
            print(f"ERROR: SDL file not found: {sdl_path}")
            sys.exit(1)
        result = compile_sdl(sdl_path)
        if result is None:
            sys.exit(1)
        args.config = str(result[0])
        if not args.html:
            args.html = result[1]

    if not args.config:
        parser.error("Either --config or --sdl is required")

    # Auto-detect engine from config
    engine = args.engine
    if not engine:
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = CONFIG_DIR / config_path
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            engine = cfg.get("engine", "hyperframes")
        else:
            engine = "hyperframes"

    # Route to OpenMontage runner if engine is openmontage
    if engine == "openmontage":
        from openmontage_runner import OpenMontageRunner
        runner = OpenMontageRunner(
            config_path=str(config_path),
            force=args.force,
        )
        if args.status:
            runner.state.print_status()
            return
        start_from = args.step
        if args.resume and not start_from:
            last_fail = runner.state.last_failed_step()
            if last_fail:
                start_from = last_fail
                print(f"Resuming from: {last_fail}")
        success = runner.run(start_from=start_from)
        sys.exit(0 if success else 1)

    runner = PipelineRunner(
        config_path=args.config,
        html_project=args.html,
        quick_fix=args.quick_fix,
        force=args.force,
        gate_mode=args.gate_mode,
        shrink=(not args.no_shrink),
        fresh=args.fresh,
    )

    if args.status:
        runner.state.print_status()
        return

    start_from = None
    if args.step:
        start_from = args.step
    elif args.resume:
        # resume 前先做全链指纹校验，自动回退到最早失效步（而非仅上次失败步）
        dirty_step, reason = runner._earliest_rerun_step()
        if dirty_step:
            start_from = dirty_step
            print(f"Resume: full-chain fingerprint scan -> earliest step needing rerun = "
                  f"{dirty_step} ({reason})")
        else:
            print("Resume: all steps cache-valid, nothing to rerun.")

    runner.run(start_from=start_from)


if __name__ == "__main__":
    main()
