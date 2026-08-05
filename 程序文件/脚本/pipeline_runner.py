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
  → delivery → completion_report

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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
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
# P0 整改（2026-08-04 prefab 复盘）：子进程输出统一落盘——透明度是复盘得出的
# 第一教训（当时 postprocess 失败无任何日志，排障只能靠状态文件反推）。
LOG_DIR = ROOT / "过程产物" / "日志"

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
    ("delivery", "交付验收与清理审计"),
    ("completion_report", "完工报告"),
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

# 结构化错误码：mark_failed 的 error_code 取值集合。
# 语义分类固定，供完工报告/排障工具按码路由，避免解析自由文本 error。
GATE_BLOCKED = "GATE_BLOCKED"            # 硬门禁拦截（前置步骤未通过）
SUBPROCESS_FAILED = "SUBPROCESS_FAILED"  # 子进程非零退出
OUTPUT_MISSING = "OUTPUT_MISSING"        # 产物文件缺失/为空
VERIFY_FAILED = "VERIFY_FAILED"          # 验证/质检类失败
QUICKFIX_BLOCKED = "QUICKFIX_BLOCKED"    # quick-fix 产物交付拦截
UNKNOWN = "UNKNOWN"                      # 未分类失败


def _coerce_bool(value, default=True):
    """配置布尔的安全解析：JSON 布尔直接用；字符串 "false"/"0"/"no" 视为 False。

    防御 bool("false") is True 的经典陷阱（手写配置可能把布尔写成字符串）。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


def _cache_age_str(completed_iso):
    """缓存年龄展示：ISO 时间 → '(12.3h ago)'；解析失败/缺失 → '(? ago)'，绝不抛异常。"""
    try:
        done = datetime.fromisoformat(completed_iso)
        hours = (datetime.now() - done).total_seconds() / 3600.0
        return f"({hours:.1f}h ago)"
    except (TypeError, ValueError):
        return "(? ago)"


class _RenderProgressWatcher:
    """render 阶段旁路进度观察者（P2 优化项3）。

    daemon 线程每 10s 轮询 hyperframes 工作目录（work-*/captured-frames/frame_*.jpg）
    的已捕获帧数并打印进度心跳。纯只读旁路：任何内部异常静默停止轮询，
    绝不向外抛出、绝不影响渲染子进程与 step_render 返回值。整体可移除。

    降级链：captured-frames 帧计数 → render_raw.mp4 文件大小 → 纯耗时心跳。
    只统计本次渲染启动后有更新的目录/文件（mtime 门槛），避免旧渲染残留误报。
    """

    POLL_INTERVAL = 10  # 秒

    def __init__(self, watch_dirs, render_raw, total_frames=0):
        self.watch_dirs = [Path(d) for d in watch_dirs]
        self.render_raw = Path(render_raw)
        self.total_frames = total_frames  # <=0 表示时长未知，不显示百分比
        self._stop_event = threading.Event()
        self._thread = None
        self._start_time = time.time()

    def start(self):
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._loop, name="render-progress-watcher", daemon=True)
        self._thread.start()

    def stop(self, timeout=15):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self):
        try:
            while not self._stop_event.wait(self.POLL_INTERVAL):
                self._report()
        except Exception:
            pass  # 静默停止：观察者故障绝不干扰渲染

    def _elapsed_str(self):
        secs = int(time.time() - self._start_time)
        return f"{secs // 60}m{secs % 60:02d}s"

    def _count_frames(self):
        """返回本次渲染活跃 work 目录的帧数；找不到返回 None。"""
        best = None
        for root in self.watch_dirs:
            if not root.is_dir():
                continue
            for cap in root.glob("work-*/captured-frames"):
                if cap.stat().st_mtime < self._start_time:
                    continue  # 旧渲染残留目录，跳过
                n = sum(1 for _ in cap.glob("frame_*.jpg"))
                if best is None or cap.stat().st_mtime > best[0]:
                    best = (cap.stat().st_mtime, n)
        return None if best is None else best[1]

    def _report(self):
        frames = self._count_frames()
        elapsed = self._elapsed_str()
        if frames is not None:
            if self.total_frames > 0:
                pct = min(100, frames * 100 // self.total_frames)
                print(f"  [RENDER] ~{pct}% ({frames}/{self.total_frames} frames, "
                      f"elapsed {elapsed})", flush=True)
            else:
                print(f"  [RENDER] {frames} frames captured, elapsed {elapsed}", flush=True)
        elif (self.render_raw.exists()
              and self.render_raw.stat().st_mtime >= self._start_time):
            size_mb = self.render_raw.stat().st_size / 1024 / 1024
            print(f"  [RENDER] elapsed {elapsed} (render_raw.mp4: {size_mb:.1f}MB)", flush=True)
        else:
            print(f"  [RENDER] elapsed {elapsed}", flush=True)


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
        # P0（2026-07-29）：状态文件缺失不再完全静默——显式提示从零开始，
        # 避免"看似有历史、实际无状态"的误判（eiway-122-wall 逃逸教训）。
        print(f"  [STATE] No pipeline state file: {self.path} — starting fresh (all steps will run)")
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

    def mark_failed(self, step, error="", error_code=None, extra=None):
        self.data["steps"][step]["status"] = "failed"
        self.data["steps"][step]["error"] = error[:200]
        # 结构化错误码：未指定时归为 UNKNOWN；旧 state 缺此字段的读取方均用 .get 兼容
        self.data["steps"][step]["error_code"] = error_code or UNKNOWN
        # P0 整改（2026-08-04 prefab 复盘）：透传附加诊断字段（如 log_path），
        # 完工报告会将其显示在 issues 中，用户不再面对"黑盒失败"。
        if extra:
            for k, v in extra.items():
                self.data["steps"][step][k] = v
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

    def set_verification(self, key, data):
        """记录某项验证/质检的结构化结果（供完工报告追溯）。

        单一权威源：验证明细由对应脚本落盘、此处只做合并，state 是唯一读出口。
        """
        self.data.setdefault("verifications", {})[key] = data
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
        # 回退值必须与 audio_sync_rules.json 的 documented 值一致（1.5s）。
        # prefab 复盘：此处曾回退 1.0s，与权威配置分叉造成时间轴三连漂移。
        "shrink": {"enabled_by_default": True, "shrink_margin_seconds": 1.5},
        "duration_consistency": {"max_diff_seconds": 1.0},
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


def _load_delivery_gate_rules():
    """Load config/quality/delivery_gate_rules.json with default fallback.

    Consumed by PipelineRunner._fresh_guard (--fresh 受限阈值)。读取失败回退
    documented 默认值，绝不让门禁静默旁路。
    """
    rules_path = CONFIG_DIR / "quality" / "delivery_gate_rules.json"
    defaults = {
        "fresh_guard": {"confirm_required_min_duration_seconds": 300},
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
    def __init__(self, config_path, html_project=None, quick_fix=False, force=False, gate_mode="render", shrink=True, fresh=False, scene_patch=False, confirm_fresh=False, accept_over_budget=False):
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

        # P0 类型体系（2026-07-29）：video_type 声明视频类型；tts_enabled=false
        # 表示纯BGM无旁白路径——tts/timeline 走类型化跳过（typed skip），
        # reason 以 "tts_disabled" 开头写入状态文件，与 quick-fix 跳过可区分。
        # 默认 tts_enabled=true，既有配置行为零变化。
        self.video_type = str(self.config.get("video_type", "") or "")
        self.tts_enabled = _coerce_bool(self.config.get("tts_enabled", True))

        self.state = PipelineState(self.temp_dir / "pipeline_state.json")
        self.quick_fix = quick_fix
        self.force = force
        # 时长预算 soft 超限的显式放行开关（wp 批次复盘 P0，见 _budget_gate_ok）
        self.accept_over_budget = accept_over_budget
        self.last_log_path = None      # 最近一次 _run 的落盘日志（_fail 透传用）
        self._log_seq = 0
        self._current_step = None      # run() 循环每步前设置，日志命名用
        # --fresh：验证/验收类运行的规定入口。清空旧状态，所有步骤真实重跑，
        # 但保留 HARD_GATES 门禁（区别于 --force 的门禁绕过语义）。
        # P1 整改（prefab 复盘根因2）：高成本重置需 --confirm-fresh 显式确认。
        if fresh:
            self._fresh_guard(confirm_fresh)
            prev = self.state.data.get("last_run")
            self.state.reset(reason=f"--fresh (previous last_run: {prev})")
            print(f"  [FRESH] Pipeline state reset (previous last_run: {prev or 'none'}) — all steps will truly re-run")
        self.gate_mode = gate_mode  # "render" or "audit"
        # --scene-patch（opt-in）：render 步骤先尝试场景级增量渲染，白名单外
        # 或任一自校验失败自动降级全量。verify/visual_check/postprocess 门禁照常。
        self.scene_patch = scene_patch
        # Audio-sync policy: shrink oversized scene windows so TTS drives
        # the timeline. See AGENTS.md → 渲染纪律 → 音画同步.
        self.audio_sync_rules = _load_audio_sync_rules()
        shrink_default = self.audio_sync_rules.get("shrink", {}).get("enabled_by_default", True)
        # CLI --no-shrink overrides config default; explicit shrink=True/False wins.
        self.shrink = shrink if shrink is not None else shrink_default

    def _next_log_path(self, desc):
        """本次子进程调用的落盘日志路径：过程产物/日志/{config}_{step}_{序号}_{时刻}.log"""
        self._log_seq = getattr(self, "_log_seq", 0) + 1
        step = getattr(self, "_current_step", None) or "pipeline"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^\w\-.]+", "_", desc)[:30].strip("_") or "cmd"
        return LOG_DIR / f"{self.config_path.stem}_{step}_{self._log_seq:02d}_{slug}_{ts}.log"

    def _run(self, cmd, cwd=None, env=None, desc=""):
        """Run subprocess, return exit code.

        P0 整改（2026-08-04 prefab 复盘）：stdout/stderr 合并后同时回显与落盘
        （过程产物/日志/）。教训：prefab 项目 postprocess 失败时 stderr 未落盘，
        排障只能靠状态文件反推。失败时打印日志路径；log_path 经 _fail() 写入
        pipeline_state，完工报告透传给用户。
        """
        print(f"  > {' '.join(str(c) for c in cmd[:6])}{'...' if len(cmd) > 6 else ''}")
        # Ensure child Python processes use UTF-8 I/O (avoid GBK codec errors on Windows)
        run_env = env or os.environ.copy()
        run_env.setdefault('PYTHONIOENCODING', 'utf-8')

        log_path = self._next_log_path(desc)
        self.last_log_path = log_path
        rc = -1
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w", encoding="utf-8", errors="replace") as log:
                log.write(f"$ {' '.join(str(c) for c in cmd)}\n")
                log.write(f"# cwd: {cwd or os.getcwd()}\n")
                log.write(f"# started: {datetime.now().isoformat()}\n")
                log.write("=" * 72 + "\n")
                log.flush()
                proc = subprocess.Popen(
                    cmd, cwd=cwd, env=run_env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace'
                )
                completed_normally = False
                try:
                    for line in proc.stdout:
                        print(line, end="", flush=True)
                        log.write(line)
                    rc = proc.wait()
                    completed_normally = True
                finally:
                    # 中断可见性（wp 批次复盘 P1）：尾行必写。教训：
                    # wp-select-basics 22:09 的 TTS 运行被外部中断后只留命令头，
                    # 无尾行无状态记录，排障全靠猜。现在即使 Ctrl-C/异常也写
                    # exit_code: INTERRUPTED，配合启动扫描可定位中断运行。
                    try:
                        if proc.poll() is None:
                            proc.kill()
                    except OSError:
                        pass
                    code = rc if completed_normally else "INTERRUPTED"
                    log.write("=" * 72 + "\n")
                    log.write(f"# finished: {datetime.now().isoformat()}  exit_code: {code}\n")
        except OSError as e:
            print(f"  ERROR: subprocess launch failed: {e}")
            rc = -1

        if rc != 0:
            print(f"  [LOG] Step failed (exit {rc}) — full log: {log_path}")
        return rc

    def _fail(self, step, error, error_code):
        """失败登记统一入口：自动附带最近一次子进程日志路径（若有）。"""
        extra = {}
        log_path = getattr(self, "last_log_path", None)
        if log_path and Path(log_path).exists():
            extra["log_path"] = str(log_path)
        self.state.mark_failed(step, error, error_code=error_code, extra=extra or None)

    def _fresh_guard(self, confirm_fresh):
        """--fresh 受限门禁（P1 整改，2026-08-04 prefab 复盘根因2）。

        教训：15:28 用 --fresh 全量重置替代定点修复，直接导致合格成片被后续
        裸渲染覆盖、3 次 40 分钟级重渲、9 小时失败收尾。现在：长视频或已有
        渲染/交付产物时，必须 --confirm-fresh 显式确认，否则打印破坏清单后
        拒绝执行，并给出低成本替代路径（--resume / --quick-fix / --scene-patch）。
        """
        rules = _load_delivery_gate_rules().get("fresh_guard", {})
        min_dur = float(rules.get("confirm_required_min_duration_seconds", 300))
        try:
            dur = float(self.config.get("video_duration", 0) or 0)
        except (TypeError, ValueError):
            dur = 0.0

        long_video = dur >= min_dur
        inventory = []
        if self.render_raw.exists() and self.render_raw.stat().st_size > 0:
            inventory.append(f"  [FILE] {self.render_raw} "
                             f"({self.render_raw.stat().st_size / 1024 / 1024:.1f} MB) — 将被重新渲染覆盖")
        if self.output_file.exists() and self.output_file.stat().st_size > 0:
            inventory.append(f"  [FILE] {self.output_file} — 成果目录现有文件将被新渲染覆盖")
        if self.tts_dir.exists():
            inventory.append(f"  [DIR ] {self.tts_dir} — TTS 产物将重新生成")
        if self.temp_dir.exists():
            for wd in sorted(self.temp_dir.glob("work-*")):
                inventory.append(f"  [DIR ] {wd} — 渲染中间目录")

        if not long_video and not inventory:
            return  # 无可保护产物且非长视频：放行（测试/新项目常规路径）

        print("  [FRESH-GUARD] --fresh 将全量重置并真实重跑所有步骤（含长时渲染）。受影响产物清单：")
        if inventory:
            for line in inventory:
                print(line)
        else:
            print(f"  (无既有渲染/交付产物，但 {dur:.0f}s 视频的全量重渲染成本极高)")
        if long_video:
            print(f"  [FRESH-GUARD] 长视频判定：video_duration {dur:.0f}s ≥ 阈值 {min_dur:.0f}s")

        if confirm_fresh:
            print("  [FRESH-GUARD] 已提供 --confirm-fresh，继续执行。")
            return

        print("  REFUSED: 高成本重置需要显式确认（prefab 复盘：--fresh 曾被误用于定点修复场景）。")
        print("  低成本替代路径（按优先级）：")
        print("    1. --resume       从最早失效步续跑（指纹缓存跳过未变步骤）")
        print("    2. --quick-fix    仅音画/字幕问题时免重渲染定点修复")
        print("    3. --scene-patch  仅场景 div 内容变化时场景级增量渲染")
        print("  确需全量重置：追加 --confirm-fresh 重新运行。")
        sys.exit(1)

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
        """每个步骤的输入文件清单（用于指纹计算）。

        质量阈值表与主执行脚本纳入指纹：改阈值/改脚本后对应步骤缓存
        自动失效重跑。克制原则：只登记每步真实消费的配置与脚本，不做全量覆盖。
        """
        narration = self.source_dir / "narration.json"
        compiled = self.source_dir / "_compiled_scenes.json"
        tts_manifest = self.tts_dir / "tts_manifest.json"
        quality_dir = CONFIG_DIR / "quality"
        audio_rules = quality_dir / "audio_sync_rules.json"
        enhance_script = SCRIPTS / "enhance_video_audio.py"
        inputs_map = {
            "preflight":    {"config": self.config_path, "html": self.html_path},
            "tts":          {"config": self.config_path, "narration": narration,
                             "compiled_scenes": compiled,
                             "script": enhance_script},
            "timeline":     {"config": self.config_path, "html": self.html_path,
                             "tts_manifest": tts_manifest,
                             "audio_sync_rules": audio_rules,
                             "script": SCRIPTS / "adjust_timeline.py"},
            "preview":      {"config": self.config_path, "html": self.html_path},
            "render":       {"config": self.config_path, "html": self.html_path},
            "verify":       {"config": self.config_path, "html": self.html_path,
                             "render_raw": self.render_raw},
            "visual_check": {"config": self.config_path, "render_raw": self.render_raw,
                             "script": SCRIPTS / "visual_boundary_check.py"},
            "postprocess":  {"config": self.config_path, "html": self.html_path,
                             "render_raw": self.render_raw, "narration": narration,
                             "tts_manifest": tts_manifest,
                             "audio_sync_rules": audio_rules,
                             "subtitle_term_rules": quality_dir / "subtitle_term_rules.json",
                             "narration_digits_rules": quality_dir / "narration_digits_rules.json",
                             "video_quality_rules": quality_dir / "video_quality_rules.json",
                             "script": enhance_script},
            "delivery":     {"config": self.config_path, "video": self._delivery_paths()[0],
                             "srt": self._delivery_paths()[1]},
            # completion_report 消费 state 内容，但不能把 pipeline_state.json 整文件
            # 纳入指纹——本步骤完成时 mark_completed 会写回该文件（自引用回路，
            # 指纹永远漂移、缓存永不可命中）。改用稳定摘要：排除自写与易变字段。
            "completion_report": {"config": self.config_path,
                             "video": self._delivery_paths()[0],
                             "srt": self._delivery_paths()[1],
                             "state_digest": "digest:" + self._stable_state_digest()},
        }
        return {k: str(v) for k, v in inputs_map.get(step, {}).items()}

    def _fingerprint(self, step):
        """计算步骤当前输入的联合指纹（步骤完成时记录用）。"""
        return compute_inputs_fingerprint(self._step_inputs(step))

    def _stable_state_digest(self):
        """state 内容的稳定摘要（completion_report 指纹专用）。

        排除字段（均由本步骤自身或每次运行必然写入，属自引用来源）：
          - last_run：每次 mark_started 都会更新
          - steps.completion_report：本步骤自身记录（status/completed/input_fingerprint）
        其余内容（其他步骤的记录等）全部参与摘要——任一上游步骤重跑/状态变化
        都会改变摘要，报告缓存随之失效。
        """
        stable = {k: v for k, v in self.state.data.items() if k != "last_run"}
        stable["steps"] = {k: v for k, v in stable.get("steps", {}).items()
                           if k != "completion_report"}
        blob = json.dumps(stable, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _delivery_paths(self):
        """交付物路径解析：config.delivery 为权威源，缺省按交付惯例推导默认。

        返回 (video_path, srt_path)。config 中声明的相对路径以仓根 ROOT 为基准；
        既有配置惯例 delivery.output_video 只声明成片文件名，落到 成果文件/视频/。
        缺省默认：成果文件/视频/{video_name}、成果文件/字幕/{html_project}.srt。
        """
        delivery_cfg = self.config.get("delivery", {}) if isinstance(self.config, dict) else {}
        if delivery_cfg.get("video"):
            video_path = Path(delivery_cfg["video"])
            if not video_path.is_absolute():
                video_path = ROOT / video_path
        elif delivery_cfg.get("output_video"):
            video_path = OUTPUT_DIR / delivery_cfg["output_video"]
        else:
            video_path = self.output_file
        if delivery_cfg.get("subtitle"):
            srt_path = Path(delivery_cfg["subtitle"])
            if not srt_path.is_absolute():
                srt_path = ROOT / srt_path
        else:
            srt_name = self.config.get("paths", {}).get("subtitle_name", f"{self.html_project}.srt")
            srt_path = ROOT / "成果文件" / "字幕" / srt_name
        return video_path, srt_path

    def _delivery_blocked_steps(self):
        """返回阻断交付的步骤清单（未满足门禁的渲染链步骤）。

        门禁判定走 _gate_satisfied：类型化跳过（tts_enabled=false 声明的
        tts/timeline，reason 以 tts_disabled 开头）是合法路径、不阻断交付；
        quick-fix 跳过（reason='quick-fix mode'）与未通过步骤全部阻断——
        确保 quick-fix 产物不流入交付。
        """
        render_chain = ["preflight", "tts", "timeline", "preview", "render",
                        "verify", "visual_check", "postprocess"]
        blocked = []
        for step in render_chain:
            if self._gate_satisfied(step):
                continue
            rec = self.state.data.get("steps", {}).get(step, {})
            status = rec.get("status", "pending")
            if status == "skipped":
                blocked.append(f"{step} (skipped: {rec.get('reason', 'quick-fix')})")
            else:
                blocked.append(f"{step} (status={status})")
        return blocked

    def _merge_verification_file(self, key, result_path):
        """读取子进程落盘的验证结果 JSON，合并进 state['verifications'][key]。

        文件缺失/损坏时静默跳过（向后兼容：旧流程无此文件，
        完工报告端会标注不可追溯而非崩溃）。
        """
        try:
            p = Path(result_path)
            if not p.exists():
                return
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.state.set_verification(key, data)
            passed = data.get("passed")
            print(f"  [VERIFY] {key} result merged into pipeline_state (passed={passed})")
        except (json.JSONDecodeError, OSError):
            pass

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

    def _typed_skip_reason(self):
        """类型化跳过的 reason 字符串（落入 pipeline_state.json，可审计）。"""
        return f"tts_disabled(video_type={self.video_type or 'unspecified'})"

    def _gate_satisfied(self, step):
        """门禁判定：passed 满足；类型化跳过（reason 以 tts_disabled 开头）
        也满足——这是配置声明的合法路径，不是 quick-fix 式的门禁绕过。
        quick-fix 跳过（reason='quick-fix mode'）不满足门禁，语义不变。"""
        rec = self.state.data.get("steps", {}).get(step, {})
        if rec.get("status") == "passed":
            return True
        return (rec.get("status") == "skipped"
                and str(rec.get("reason", "")).startswith("tts_disabled"))

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
        # 键名与 mark_completed 写入端一致（"completed"），缓存年龄仅作展示
        age = _cache_age_str(rec.get("completed"))
        print(f"  [CACHED] Step passed at {completed}, input fingerprint verified ({old_fp[:12]}...) — skipping {age}")
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
            self._fail("preflight", "Static validation failed", VERIFY_FAILED)
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
                self._fail("preflight", "Photo preprocessing failed", SUBPROCESS_FAILED)
                return False

        self.state.mark_completed("preflight", self._fingerprint("preflight"))
        return True

    def step_tts(self):
        if not self.tts_enabled:
            reason = self._typed_skip_reason()
            self.state.mark_skipped("tts", reason)
            print(f"  [TYPE-SKIP] tts_enabled=false — TTS generation skipped ({reason})")
            return True
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
        # 合并 TTS 产物验证结果（step1 落盘）→ pipeline_state，供完工报告追溯
        self._merge_verification_file("tts_product", self.temp_dir / "tts_verify_result.json")
        if rc == 0:
            self.state.mark_completed("tts", self._fingerprint("tts"))
            return True
        self._fail("tts", "TTS generation failed", SUBPROCESS_FAILED)
        return False

    def step_timeline(self):
        if not self.tts_enabled:
            # 无 TTS 基准时"时间轴向 TTS 收敛"不适用——HTML S-block 即权威时长
            reason = self._typed_skip_reason()
            self.state.mark_skipped("timeline", reason)
            print(f"  [TYPE-SKIP] tts_enabled=false — timeline adjustment skipped ({reason})")
            return True
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
            self._record_timeline_run(new_dur)
            # 时长预算门禁（wp 批次复盘 P0）：渲染前的最后拦截点
            if not self._budget_gate_ok():
                return False
            self.state.mark_completed("timeline", self._fingerprint("timeline"))
            return True
        self._fail("timeline", "Timeline adjustment failed", SUBPROCESS_FAILED)
        return False

    def _record_timeline_run(self, new_dur):
        """时间轴运行历史登记 + 非收敛漂移告警（P1 整改，prefab 复盘根因3）。

        教训：prefab 项目总时长三连漂移 618.9→621.7→623.8s（margin 参数分叉 +
        人工干预），每次漂移触发 30-40 分钟级全量重渲染。现连续两次时间轴结果
        相差 >0.5s 即显式告警，提醒先确认参数权威源与变更来源再继续。
        历史落在 pipeline_state.timeline_runs，完工报告可追溯。
        """
        try:
            new_dur = float(new_dur)
        except (TypeError, ValueError):
            return
        history = self.state.data.setdefault("timeline_runs", [])
        if history:
            try:
                prev_dur = float(history[-1].get("duration", 0))
            except (TypeError, ValueError):
                prev_dur = 0.0
            if prev_dur > 0:
                delta = new_dur - prev_dur
                if abs(delta) > 0.5:
                    print(f"  [DRIFT-WARNING] 时间轴不收敛：{prev_dur:.1f}s → {new_dur:.1f}s ({delta:+.1f}s)")
                    print("    每次漂移都会触发下游 render 全量重渲（30-40分钟级成本）。")
                    print("    请确认：shrink-margin 是否来自同一权威源（audio_sync_rules.json）、")
                    print("    narration/HTML 是否确有变更、config 是否被手工修改过。")
        history.append({"at": datetime.now().isoformat(), "duration": round(new_dur, 1)})
        self.state.save()

    def _budget_gate_ok(self):
        """时长预算门禁（2026-08-05 wp 批次复盘 P0）。

        教训：wp 批次 2/4 视频超 240s 预算（253.0s/248.9s），因预算约束未进入
        门禁声明面，完工报告盖章 VALIDATED 13/13 后才被人工发现，各付出一次
        约 25 分钟全链返工。现在：config 声明 duration_budget（{seconds,
        enforcement: soft|hard}）即启用；未声明 = 门禁不生效（零副作用）。
        检查点设在 timeline 步末尾——权威时长刚确定、渲染尚未开始，拦截成本<1s，
        挽回的是单次 ~13 分钟渲染 + 交付后返工。
        soft（默认）：超限阻断，需 --accept-over-budget 显式放行并在 state 留痕
        （超预算≠不合格，处置是业务决策）；hard：超限直接 FAIL。
        参数语义权威源：audio_sync_rules.duration_budget。
        """
        if self.quick_fix:
            return True
        spec = self.config.get("duration_budget") or {}
        try:
            budget = float(spec.get("seconds", 0) or 0)
        except (TypeError, ValueError):
            budget = 0.0
        if budget <= 0:
            return True
        try:
            actual = float(self.config.get("video_duration", 0) or 0)
        except (TypeError, ValueError):
            return True
        if actual <= 0 or actual <= budget:
            return True

        over = actual - budget
        msg = (f"Duration budget exceeded: actual {actual:.1f}s > "
               f"budget {budget:.0f}s (+{over:.1f}s)")
        enforcement = str(spec.get("enforcement", "soft")).lower()
        if enforcement == "hard":
            print(f"  ERROR: {msg} [enforcement=hard]")
            print("  定点修复指引：从最长场景精简旁白后重跑流水线（tts/timeline 自动重算）。")
            self.state.mark_failed("timeline", msg, error_code=VERIFY_FAILED)
            return False
        if self.accept_over_budget:
            self.state.data["budget_decision"] = {
                "decision": "accepted",
                "actual_seconds": round(actual, 1),
                "budget_seconds": budget,
                "at": datetime.now().isoformat(),
            }
            self.state.save()
            print(f"  [BUDGET] {msg} — --accept-over-budget 显式放行，决策已记录")
            return True
        print(f"  BLOCKED: {msg} [enforcement=soft]")
        print("  超预算≠不合格，请做显式决策：")
        print("    1. 接受当前时长 → 追加 --accept-over-budget 重跑（上游步骤命中缓存）")
        print("    2. 精简至预算内 → 修改旁白后正常重跑")
        self.state.mark_failed("timeline", msg, error_code=VERIFY_FAILED)
        return False

    def _warn_previous_interrupted_runs(self):
        """启动扫描：历史日志缺尾行（疑似外部中断）时显式告警。

        教训（wp 批次复盘 P1）：wp-select-basics 22:09 的 TTS 运行被外部中断，
        日志只有命令头、pipeline_state 无任何失败记录，运行历史不可见。
        现在按 config 名前缀扫描日志尾部，缺 '# finished:' 即告警（只告警不阻断）。
        """
        if not LOG_DIR.exists():
            return
        prefix = self.config_path.stem + "_"
        for f in sorted(LOG_DIR.glob(f"{prefix}*.log")):
            try:
                with open(f, "rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    fh.seek(max(0, size - 512))
                    tail = fh.read().decode("utf-8", errors="replace")
            except OSError:
                continue
            if "# finished:" not in tail:
                print(f"  [WARN] 历史日志缺少尾行（该次运行疑似被外部中断）：{f.name}")
                print("         若怀疑状态不可信，用 --fresh 全链重跑（长视频需 --confirm-fresh）。")

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
        self._fail("preview", "Instant preview failed — fix visual issues before rendering",
                   VERIFY_FAILED)
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
                if not self._gate_satisfied(prereq):
                    label = dict(STEPS).get(prereq, prereq)
                    print(f"  BLOCKED: Step '{prereq}' ({label}) has not passed.")
                    print(f"  RENDER REFUSED. Run preflight and preview first.")
                    print(f"  To override: use --force (not recommended)")
                    self.state.mark_failed("render", f"Hard gate: {prereq} not passed",
                                           error_code=GATE_BLOCKED)
                    return False

        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # ── 交付物保护（P0 整改，prefab 复盘根因2）：渲染前拦截，绝不让 30-40 分钟
        #    渲染白跑后才发现要覆盖已交付成片（AGENTS.md：已交付文件不得覆盖）──
        if (self.output_file.exists() and self.output_file.stat().st_size > 0
                and self._output_was_delivered() and not self.force):
            self.state.mark_started("render")
            print(f"  BLOCKED: 交付目标已是上一轮成功交付的成片：{self.output_file}")
            print("  AGENTS.md 交付纪律：已交付文件不得覆盖，需修订时加后缀。")
            print(f"  处理：config paths.video_name 改为 '{self.output_file.stem}_修订01.mp4' 后重跑；")
            print("        或将旧成片移出成果目录；紧急情况用 --force 越过（不推荐）。")
            self.state.mark_failed(
                "render",
                f"Output {self.output_file.name} is a delivered artifact — use revision suffix",
                error_code=VERIFY_FAILED)
            return False

        self.state.mark_started("render")

        # ── Scene-patch (opt-in): 分类器判 PATCH 则段渲染+拼接产出 render_raw，
        #    任何白名单外变更/自校验失败 → exit 3 → 自动降级 FULL 全量渲染 ──
        patched = False
        if self.scene_patch:
            print("  [SCENE-PATCH] Trying scene-level incremental render...")
            rc = self._run(
                [str(VENV_PYTHON), "scene_patch_render.py",
                 "--config", str(self.config_path), "--mode", "patch"],
                cwd=str(SCRIPTS), env=self.env, desc="scene patch render"
            )
            if rc == 0:
                patched = True
                print("  [SCENE-PATCH] Patch applied — full render skipped")
            else:
                print(f"  [SCENE-PATCH] Falling back to FULL render (rc={rc})")

        if not patched:
            # ── 渲染中断恢复（P1 整改，prefab 复盘根因5）：全量重渲前备份上一版
            #    render_raw——prefab 两次渲染中止于 87%/53% 后毫无恢复路径，只能
            #    从零再来。备份在手：新渲染失败/中断时可回退基线走 --quick-fix。
            if self.render_raw.exists() and self.render_raw.stat().st_size > 0:
                prev_backup = self.render_raw.with_name("render_raw.prev.mp4")
                shutil.copy2(self.render_raw, prev_backup)
                print(f"  Previous render backed up: {prev_backup.name} "
                      f"({prev_backup.stat().st_size / 1024 / 1024:.1f} MB)")
            print("  Rendering HyperFrames (this takes ~11 minutes)...")
            # 旁路进度观察者（P2 优化项3）：CLI 无原生进度参数（--json 仅限 --batch），
            # 只读轮询帧数目录打印心跳。总帧数 = video_duration × 25fps（与渲染命令 --fps 一致）。
            # hyperframes 的 work-*/captured-frames 实测生成在 temp_dir（render_raw 同级），
            # 兼顾 source_dir 兕底。观察者失败不影响渲染判定，整体可移除。
            try:
                _total_frames = int(round(float(self.config.get("video_duration", 0)) * 25))
            except (TypeError, ValueError):
                _total_frames = 0
            watcher = _RenderProgressWatcher(
                [self.temp_dir, self.source_dir], self.render_raw, _total_frames)
            watcher.start()
            try:
                rc = self._run(
                    ["npx.cmd", "hyperframes", "render", "-o", str(self.render_raw), "--workers", "1", "--fps", "25", "--low-memory-mode", "--protocol-timeout", "600000"],
                    cwd=str(self.source_dir),
                    env=self.env,
                    desc="HyperFrames render"
                )
            finally:
                watcher.stop()
            if rc != 0:
                # P1 整改（prefab 复盘）：渲染中断/失败不再等于归零——
                # render_raw.prev.mp4（若有）保留了上一版可用渲染，给出定点恢复路径
                hint = ""
                prev = self.render_raw.with_name("render_raw.prev.mp4")
                if prev.exists() and prev.stat().st_size > 0:
                    hint = (f" — previous render preserved at {prev.name}; "
                            f"if timeline unchanged, restore it + align config + --quick-fix "
                            f"instead of a full re-render")
                self._fail("render", f"HyperFrames render failed{hint}", SUBPROCESS_FAILED)
                return False

        if not self.render_raw.exists():
            self._fail("render", "render_raw.mp4 not created", OUTPUT_MISSING)
            return False

        size_mb = self.render_raw.stat().st_size / 1024 / 1024
        print(f"  Rendered: {self.render_raw} ({size_mb:.1f} MB)")

        # Copy to output directory for post-processing
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        if self.output_file.exists() and self.output_file.stat().st_size > 0:
            # 交付物保护第二道防线：已交付产物已在渲染前拦截；此处覆盖的是
            # 非交付态旧产物（如上次失败运行残留），仍先备份保证物理不丢失。
            pre_backup = self.temp_dir / f"{self.output_file.stem}.pre_render_backup.mp4"
            shutil.copy2(self.output_file, pre_backup)
            print(f"  Existing output backed up before overwrite: {pre_backup.name}")
        shutil.copy2(self.render_raw, self.output_file)
        print(f"  Copied to: {self.output_file}")

        # 场景指纹 sidecar：供后续 --scene-patch 增量渲染做基线（尽力而为，
        # 失败不阻塞交付；patch 成功路径已在脚本内部自行刷新 sidecar）
        if not patched:
            rc_sc = self._run(
                [str(VENV_PYTHON), "scene_patch_render.py",
                 "--config", str(self.config_path), "--mode", "sidecar"],
                cwd=str(SCRIPTS), env=self.env, desc="scene fingerprints sidecar"
            )
            if rc_sc != 0:
                print("  WARNING: sidecar write failed (scene-patch baseline unavailable)")

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
            self.state.mark_failed("verify", "ffprobe returned invalid data",
                                   error_code=VERIFY_FAILED)
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
                f"Duration mismatch: {rendered_dur:.1f}s vs {expected_dur:.1f}s ({dur_source}, diff {diff:.1f}s > 5s)",
                error_code=VERIFY_FAILED
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
        self._fail("visual_check", "Content overflows subtitle safety zone", VERIFY_FAILED)
        return False

    def _output_was_delivered(self):
        """判定 output_file 是否为上一轮成功交付的成片（交付物保护依据）。

        依据：temp_dir/completion_report.json 状态为 VALIDATED*（完工报告全部
        检查通过）且报告引用的视频与当前目标同名。prefab 复盘：上午已交付的
        合格成片被下午的裸渲染直接覆盖丢失——交付态必须可识别、可拦截。
        """
        report_path = self.temp_dir / "completion_report.json"
        if not report_path.exists():
            return False
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                rep = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        if not str(rep.get("status", "")).startswith("VALIDATED"):
            return False
        rep_video = str(rep.get("data_sources", {}).get("video_file", {}).get("path", "") or "")
        # 报告未记录路径（旧格式）→ 保守视为已交付；记录了则必须同名才拦截
        return (not rep_video) or Path(rep_video).name == self.output_file.name

    def _probe_duration(self, path):
        """ffprobe 实测媒体时长；失败返回 None（调用方决定是否放行）。"""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_format", "-of", "json", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30)
            return float(json.loads(r.stdout)["format"]["duration"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def _duration_consistency_ok(self):
        """config↔渲染产物时长一致性早失败门禁（P1 整改，prefab 复盘根因4）。

        教训：config 617.8s vs render_raw 621.72s 的漂移拖到 enhance step3 混音
        门禁（±1.0s）才暴露，白白消耗一轮 TTS/BGM 再生成 + 后续 40 分钟重渲染。
        现在在 runner 阶段早拒并给出定点修复指引。权威阈值：
        audio_sync_rules.duration_consistency.max_diff_seconds。
        quick-fix 模式无 render_raw，检查对象为成果目录文件（enhance 的输入源）。
        """
        target = self.output_file if self.quick_fix else self.render_raw
        if not target.exists():
            return True  # 产物缺失由后续步骤拦截，此处不重复报错
        actual = self._probe_duration(target)
        if actual is None:
            return True  # ffprobe 不可用时不阻断，交由 enhance 内部门禁判定
        try:
            expected = float(self.config.get("video_duration", 0) or 0)
        except (TypeError, ValueError):
            return True
        if expected <= 0:
            return True
        max_diff = float(self.audio_sync_rules.get("duration_consistency", {})
                         .get("max_diff_seconds", 1.0))
        diff = abs(actual - expected)
        if diff <= max_diff:
            print(f"  Duration consistency OK: config {expected:.1f}s vs actual {actual:.1f}s (diff {diff:.2f}s)")
            return True
        msg = (f"Duration drift: config {expected:.1f}s vs actual {actual:.1f}s "
               f"(diff {diff:.1f}s > {max_diff:.1f}s)")
        print(f"  ERROR: {msg}")
        print("  定点修复指引（勿直接全量重渲）：")
        print("    1. 时间轴确需变化 → 重跑 pipeline_runner（timeline 步骤会同步 config）后渲染")
        print(f"    2. 渲染产物正确 → 手工对齐 config.video_duration={actual:.1f} 后 --quick-fix")
        self.state.mark_failed("postprocess", msg, error_code=VERIFY_FAILED)
        return False

    def step_postprocess(self):
        if self._can_skip("postprocess"):
            return True

        self.state.mark_started("postprocess")

        # ── 早失败检查：长耗时后处理启动前先验证时长一致性（prefab 复盘根因4）──
        if not self._duration_consistency_ok():
            return False

        cmd = [str(VENV_PYTHON), "enhance_video_audio.py", "--config", str(self.config_path)]
        if self.quick_fix:
            cmd.append("--quick-fix")

        rc = self._run(cmd, cwd=str(SCRIPTS), desc="audio/subtitle post-processing")
        # 合并媒体质检结果（step7 落盘）→ pipeline_state，供完工报告追溯
        self._merge_verification_file("media_quality", self.temp_dir / "media_quality_result.json")
        if rc == 0:
            self.state.mark_completed("postprocess", self._fingerprint("postprocess"))
            return True
        self._fail("postprocess", "Post-processing failed", SUBPROCESS_FAILED)
        return False

    def step_delivery(self):
        """交付验收与清理审计（最终交付关卡）。

        1. quick-fix/门禁拦截：渲染链任一步骤未满足 _gate_satisfied → 拒收
           （类型化跳过 tts_disabled 是配置声明的合法路径，不拦截）；
        2. 成果文件校验：视频存在且非零；tts_enabled=false 的纯BGM项目豁免字幕；
        3. 清理审计：列出待清理中间产物（默认 dry-run，不实际删除）。

        注意：delivery 是最终交付关卡，quick-fix 产物必须被拦截；因此不设
        quick-fix 跳过分支，且门禁检查先于缓存判定（防止被缓存命中绕过）。
        """
        # ── 门禁 1：quick-fix / 前置步骤拦截（先于缓存判定）──
        blocked = self._delivery_blocked_steps()
        if blocked:
            self.state.mark_started("delivery")
            print("  BLOCKED: 以下前置步骤被跳过或未通过，禁止交付：")
            for b in blocked:
                print(f"    X {b}")
            print("  quick-fix 产物不得流入交付。请运行完整流水线（不带 --quick-fix）后再交付。")
            self.state.mark_failed("delivery", f"Delivery blocked: {len(blocked)} step(s) skipped/not-passed",
                                   error_code=QUICKFIX_BLOCKED)
            return False

        # 门禁通过后方可考虑缓存复用
        if self._can_skip("delivery"):
            return True

        self.state.mark_started("delivery")

        # ── 门禁 2：成果文件存在且非零（纯BGM项目豁免字幕）──
        video_path, srt_path = self._delivery_paths()
        checks = [("视频", video_path)]
        if self.tts_enabled:
            checks.append(("字幕", srt_path))
        else:
            print(f"  [TYPE-SKIP] tts_enabled=false — 纯BGM项目豁免字幕交付要求 ({self._typed_skip_reason()})")
        missing = []
        for label, p in checks:
            if not p.exists():
                missing.append(f"{label}不存在: {p}")
            elif p.stat().st_size == 0:
                missing.append(f"{label}为空文件: {p}")
        if missing:
            print("  BLOCKED: 成果文件校验失败：")
            for m in missing:
                print(f"    X {m}")
            self.state.mark_failed("delivery", f"Delivery artifacts invalid: {'; '.join(missing)}",
                                   error_code=OUTPUT_MISSING)
            return False

        print(f"  成果视频: {video_path} ({video_path.stat().st_size / 1024 / 1024:.1f} MB)")
        if self.tts_enabled:
            print(f"  成果字幕: {srt_path} ({srt_path.stat().st_size} bytes)")

        # ── 清理审计（dry-run）：列出待清理中间产物，不实际删除 ──
        # 破坏性清理必须由用户显式执行 project_cleanup.py --execute（AGENTS.md 破坏性操作审核）
        print("  清理审计（dry-run，仅列出，不删除）：")
        cleanup_targets = [self.render_raw, self.tts_dir]
        if self.temp_dir.exists():
            cleanup_targets.extend(sorted(self.temp_dir.glob("work-*")))
        listed = 0
        for t in cleanup_targets:
            if t.exists():
                listed += 1
                kind = "DIR " if t.is_dir() else "FILE"
                print(f"    [{kind}] {t}")
        if listed == 0:
            print("    (无待清理中间产物)")
        else:
            print(f"  共 {listed} 项待清理。执行清理请运行：project_cleanup.py --execute")

        self.state.mark_completed("delivery", self._fingerprint("delivery"))
        return True

    def step_completion_report(self):
        """完工报告：调用 generate_completion_report.py，状态源自其真实测量。

        报告脚本 FAILED → 非零退出码（EXIT_CODE.GATE_FAILURE=4）→ 本步骤失败；
        runner 不硬编码任何成功字样。纯BGM项目（tts_enabled=false）无字幕产物，
        不传 --subtitle-file（报告端 typed-skip 豁免 tts/timeline 步骤检查）。
        """
        if self._can_skip("completion_report"):
            return True

        self.state.mark_started("completion_report")

        video_path, srt_path = self._delivery_paths()
        report_out = self.temp_dir / "completion_report.json"
        cmd = [str(VENV_PYTHON), "generate_completion_report.py",
               "--project-name", self.html_project,
               "--video-file", str(video_path),
               "--state-file", str(self.state.path),
               "--output-file", str(report_out)]
        if self.tts_enabled:
            cmd.extend(["--subtitle-file", str(srt_path)])
        rc = self._run(cmd, cwd=str(SCRIPTS), desc="completion report")
        # 报告状态源自脚本真实测量：FAILED 时脚本退出非零（GATE_FAILURE），
        # VALIDATED / VALIDATED_WITH_ISSUES 退出 0。此处不硬编码任何成功判定。
        if rc != 0:
            self._fail("completion_report", "Completion report validation FAILED (see report)",
                       VERIFY_FAILED)
            return False
        print(f"  完工报告已生成: {report_out}")
        self.state.mark_completed("completion_report", self._fingerprint("completion_report"))
        return True

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

        # 中断可见性：上次运行若被外部中断（日志缺尾行），在此显式告警
        self._warn_previous_interrupted_runs()

        # ── Hard gate prerequisite check when --step is used ──
        # Prevent jumping to render/postprocess without prerequisite steps passing.
        if start_from and not self.force and not self.quick_fix:
            required = HARD_GATES.get(start_from, [])
            missing = [s for s in required if not self._gate_satisfied(s)]
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
            self._current_step = step_name  # 子进程日志命名用
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
                             "(cache-poisoning defense: never trust a stale pipeline_state.json). "
                             "For long videos (>=300s) or when render/delivery artifacts exist, "
                             "--confirm-fresh is additionally required (prefab postmortem: a "
                             "misused --fresh caused 9h of wasted re-renders).")
    parser.add_argument("--confirm-fresh", action="store_true",
                        help="Explicit confirmation for a high-cost --fresh reset. Prints the "
                             "destruction inventory and proceeds. Without it, --fresh is refused "
                             "when existing artifacts or long renders would be destroyed, with "
                             "low-cost alternatives suggested (--resume/--quick-fix/--scene-patch).")
    parser.add_argument("--gate-mode", choices=["audit", "render"],
                        default="render",
                        help="Preflight gate mode: 'audit' (warn-only) or 'render' (hard-block). "
                             "Default: render")
    parser.add_argument("--engine", choices=["hyperframes", "openmontage"],
                        default=None,
                        help="Pipeline engine (auto-detected from config if omitted)")
    parser.add_argument("--scene-patch", action="store_true",
                        help="Opt-in scene-level incremental render: when only scene div "
                             "content changed (whitelist classifier), re-render changed scenes "
                             "only and stitch with the baseline render_raw. Any non-whitelisted "
                             "change or self-check failure falls back to FULL render automatically.")
    parser.add_argument("--no-shrink", action="store_true",
                        help="Disable auto-shrink of oversized scene windows. "
                             "By default, timeline adjustment collapses windows so TTS drives timing "
                             "(prevents audio dead-air between scenes). Use only when the storyboard "
                             "intentionally requires silent breathing space in specific scenes.")
    parser.add_argument("--accept-over-budget", action="store_true",
                        help="Explicitly accept a video that exceeds its declared duration_budget "
                             "(soft enforcement). The decision is recorded in "
                             "pipeline_state.budget_decision for audit. Without this flag an "
                             "over-budget timeline is blocked before the expensive render step.")

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
        scene_patch=args.scene_patch,
        confirm_fresh=args.confirm_fresh,
        accept_over_budget=args.accept_over_budget,
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
