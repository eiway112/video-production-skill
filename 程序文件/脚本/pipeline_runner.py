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
from typing import Any, Dict
from pathlib import Path
from _gsap_time_utils import parse_scene_map
from pipeline_state_fingerprint import compute_inputs_fingerprint
from _script_env import ROOT, VENV_PYTHON
from script_interface import atomic_write_json
from media_qa_gate import inspect_frame_content
import _gate_status as gs

# 换包装时必须持住旧流对象：旧 TextIOWrapper 一旦被 GC，其析构会关掉共享的底层
# buffer，宿主进程（import 本模块的测试/工具）之后任何打印都报 "I/O operation on
# closed file"。CLI 独立运行时这两个引用无副作用。
_std_streams_prev = (sys.stdout, sys.stderr)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


SCRIPTS = ROOT / "程序文件" / "脚本"
# visual_boundary 报告落点由调用方声明（--report-out），合并端复用同一变量。
# 旧实现是两条独立推导——写端按"被测视频的父目录"、读端按 temp，二者仅在 render_raw
# 恰好住在 temp 时相等；--video 是公开 CLI 入口，一旦指向交付槽位复检，报告便落进
# 成果目录且合并端静默扑空＝门禁证据凭空消失（2026-09-21 收口，同 A06
# "登记面＝真实读取路径"族）。
VISUAL_REPORT_NAME = "visual_boundary_report.json"
CONFIG_DIR = ROOT / "程序文件" / "配置" / "config"
HTML_BASE = ROOT / "程序文件" / "源码" / "hyperframes"
OUTPUT_DIR = ROOT / "成果文件" / "视频"
# 交付登记表（git 追踪）：mp4 成片按资产策略不入 git，"已交付"若只能从 temp 里的
# 完工报告反推，则 temp 归档/清理后判据即失效，且交付清单在任何地方都不可核。
# 本文件是交付态的权威登记，由 step_completion_report 成功后写入。
DELIVERY_REGISTRY = ROOT / "成果文件" / "交付登记.json"
TEMP_BASE = ROOT / "过程产物" / "临时产物"
# 增量渲染（scene_patch_render.py）的两个数据面：基线指纹 sidecar 与裁定留痕。
# 名称必须与 scene_patch_render.SIDECAR_NAME / VERDICT_NAME 一致。
SCENE_PATCH_SIDECAR = "scene_fingerprints.json"
SCENE_PATCH_VERDICT = "scene_patch_verdict.json"
SCENE_PATCH_EXIT_FALLBACK = 3
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

# 指纹登记项中由流水线自己写盘的运行时产物：路径写错即永久空转（见 _fingerprint）。
# 与源文件（narration.json 等）区分——后者按项目形态可缺，缺失属正常。
_RUNTIME_ARTIFACT_INPUTS = frozenset({"tts_manifest", "render_raw"})

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


def _render_content_status(render_raw):
    """返回 render_raw 的四态内容检查结果，抽帧故障不得被缓存掩盖。"""
    try:
        return inspect_frame_content(str(render_raw))
    except Exception as exc:
        return gs.UNTESTED, f"Frame-content check: UNTESTED — {exc}"


class _RenderProgressWatcher:
    """render 阶段旁路进度观察者（P2 优化项3）。

    daemon 线程每 10s 轮询进度并打印心跳。纯只读旁路：任何内部异常静默停止轮询，
    绝不向外抛出、绝不影响渲染子进程与 step_render 返回值。整体可移除。

    进度源降级链（2026-09-25 结构化加固，单一权威源＝渲染子进程自己的 stdout）：
      ① stdout 进度条 'NN%  Capturing frame X/Y' / trace framesCompleted
      ② work-*/captured-frames 帧计数（旧版 HyperFrames 落盘布局）
      ③ render_raw.mp4 文件大小
      ④ 0（"什么都没发生"本身即进度信号）
    只统计本次渲染启动后有更新的目录/文件（mtime 门槛），避免旧渲染残留误报。

    教训（WorkBuddy 竖屏 hotel-partition-promo，2026-09-25）：screenshot 模式下
    HyperFrames 把帧写进事务目录（.render_raw.hf-transaction-*）、encode 完成后才
    原子落盘 render_raw.mp4，于是 ②③ 两条文件系统腿同时失明 → 进度恒 0 → 退化成
    纯耗时模式 → capture 明明已跑到 2760/2760 帧（stdout 进度条 70%）仍在 grace
    360s 被误杀（日志 hotel-partition-promo_resume_render_20260925_155733.log:1186）。
    ① 消费 stdout 后，进度条与 framesCompleted 在 dev 与 WorkBuddy 两个 HyperFrames
    版本上都稳定推进（实测两版 bar 映射逐字一致），看门狗不再依赖落盘布局。
    """

    POLL_INTERVAL = 10  # 秒
    # stdout 进度条百分比 → 阶段映射（dev 与 WorkBuddy 两个 HyperFrames 版本实测一致）：
    # capture 25→70%、Encoding video 75%、Assembling 90%、Render complete 100%。
    # ≥75% 即 capture 已完成、进入有界收尾（实测 2760 帧 encode 17.4s + assemble 0.3s）。
    POST_CAPTURE_PCT = 75
    _CAPTURE_FRAME_RE = re.compile(r"Capturing frame\s+(\d+)\s*/\s*(\d+)")
    _TRACE_FRAMES_RE = re.compile(r'"framesCompleted"\s*:\s*(\d+)')
    _BAR_PCT_RE = re.compile(r"(\d{1,3})\s*%")
    _BAR_BLOCK_CHARS = "\u2588\u2591"  # █ ░ —— 进度条独有，避免误匹配其它含 % 的行

    def __init__(self, watch_dirs, render_raw, total_frames=0, watchdog=None):
        self.watch_dirs = [Path(d) for d in watch_dirs]
        self.render_raw = Path(render_raw)
        self.total_frames = total_frames  # <=0 表示时长未知，不显示百分比
        # 卡死看门狗（prefab 复盘决策二，2026-08-07）：阈值来自
        # config/quality/render_rules.json，见 _load_render_rules()。
        self.watchdog = watchdog or {}
        self.stalled = False      # 看门狗判定卡死并杀进程树后置位
        self._proc = None         # attach_process() 注入的渲染子进程
        self._last_progress_val = None
        self._last_progress_ts = time.time()
        self._stop_event = threading.Event()
        self._thread = None
        self._start_time = time.time()
        # stdout 进度（主线程 observe_stdout 写、看门狗线程读；单属性绑定 GIL 原子，
        # 与既有跨线程读 self._proc 同风格，不另加锁）。None=尚未见到任何进度条。
        self._stdout_progress = None  # (pct:int, frames:int) 单调非减
        self._stood_down = False      # capture 完成后看门狗站立放行（见 _check_watchdog）

    def attach_process(self, proc):
        """_run 的 Popen 成功后调用：看门狗需要进程句柄才能在卡死时杀进程树。"""
        self._proc = proc

    def observe_stdout(self, line):
        """消费渲染子进程的一行 stdout，提取版本无关的权威进度（_run 行循环内调用）。

        HyperFrames 两个版本都打印 'NN%  Capturing frame X/Y' 进度条；较新版本另在
        [Render:trace] JSON 里带 framesCompleted。任一推进即抬高单调进度，使看门狗
        不再依赖 work-*/captured-frames 落盘布局（screenshot+事务目录会让其失明）。
        纯只读旁路：解析异常一律吞掉，绝不干扰渲染子进程。"""
        try:
            frames = None
            m = self._CAPTURE_FRAME_RE.search(line)
            if m:
                frames = int(m.group(1))
            else:
                m = self._TRACE_FRAMES_RE.search(line)
                if m:
                    frames = int(m.group(1))
            pct = None
            # 百分比只认进度条行（含 █/░ 块字符），避免误匹配 coverage 等其它含 % 的行
            if any(c in line for c in self._BAR_BLOCK_CHARS):
                m = self._BAR_PCT_RE.search(line)
                if m:
                    pct = int(m.group(1))
            if pct is None and frames is None:
                return
            cur_pct, cur_frames = self._stdout_progress or (0, 0)
            new = (max(cur_pct, pct or 0), max(cur_frames, frames or 0))
            if new != (cur_pct, cur_frames):
                self._stdout_progress = new
        except Exception:
            pass  # 观察者故障绝不干扰渲染

    def start(self):
        self._start_time = time.time()
        self._last_progress_ts = time.time()
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

    def _progress_value(self):
        """看门狗进度度量（单调非减）：stdout 进度 → 帧数 → render_raw 大小降级链。
        全部皆无时返回 0——"什么都没发生"本身就是进度（prefab 渲染 #1
        卡 0 帧 12 分钟正是这种形态）。

        stdout 腿优先：复合量 pct*1e7+frames 让百分比跨阶段推进（capture 25→70、
        encode 75、assemble 90、complete 100）且帧计数在 capture 阶段内提供细粒度推进，
        任一前进即视为有进度——这条腿与文件系统布局无关，是 screenshot/事务目录模式下
        唯一不会失明的信号（旧实现只有文件系统两腿，该模式下同时失明致误杀）。"""
        sp = self._stdout_progress
        if sp is not None:
            return sp[0] * 10_000_000 + sp[1]
        frames = self._count_frames()
        if frames is not None:
            return frames
        if (self.render_raw.exists()
                and self.render_raw.stat().st_mtime >= self._start_time):
            return self.render_raw.stat().st_size
        return 0

    def _kill_process_tree(self, pid):
        """杀渲染进程树。独立方法便于回归测试替换。"""
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=30)
            else:
                os.kill(pid, 9)
        except Exception as e:
            print(f"  [WATCHDOG] kill failed: {e}", flush=True)

    def _check_watchdog(self):
        """卡死判定：进度连续 stall_seconds 零增长且总耗时已过 grace_seconds
        → 判定卡死，杀进程树 fail-fast。教训：prefab-agent-launch 渲染 #1
        卡 0%（0/5305 帧）12 分钟无任何诊断，只能干等崩溃。

        后捕获站立放行（2026-09-25）：stdout 进度条一旦到 'Encoding video'(≥75%)，
        说明历史上唯一会卡死的 capture 阶段（prefab #1：0/5305 帧）已完整跑完，
        其后 encode/assemble 是有界收尾（实测 2760 帧 encode 17.4s + assemble 0.3s）
        且 HyperFrames 不再打印增量进度——此时按"进度恒 0"杀进程只会丢弃一个已捕获
        完成的渲染。残留取舍：真正的 encode 卡死不再由本看门狗拦截，由渲染命令自带的
        --protocol-timeout 600000（600s）兜底；capture 卡死（看门狗设立初衷）不受影响，
        因其百分比恒 ≤70%、永不触发站立放行。"""
        wd = self.watchdog
        if not wd.get("enabled") or self.stalled or self._proc is None:
            return
        sp = self._stdout_progress
        if sp is not None and sp[0] >= self.POST_CAPTURE_PCT:
            if not self._stood_down:
                self._stood_down = True
                print(f"\n  [WATCHDOG] Capture complete ({sp[0]}%); standing down for "
                      f"bounded encode/assemble phase (no incremental stdout).", flush=True)
            return
        now = time.time()
        val = self._progress_value()
        if val != self._last_progress_val:
            self._last_progress_val = val
            self._last_progress_ts = now
        stall = now - self._last_progress_ts
        total = now - self._start_time
        if stall >= wd.get("stall_seconds", 300) and total >= wd.get("grace_seconds", 360):
            self.stalled = True
            print(f"\n  [WATCHDOG] Render stalled: zero progress for {int(stall)}s "
                  f"(elapsed {self._elapsed_str()}) — killing render process tree "
                  f"(fail-fast). Thresholds: render_rules.json", flush=True)
            try:
                pid = self._proc.pid
            except Exception:
                pid = None
            if pid:
                self._kill_process_tree(pid)

    def _report(self):
        elapsed = self._elapsed_str()
        sp = self._stdout_progress
        frames = self._count_frames()
        if sp is not None and (sp[0] > 0 or sp[1] > 0):
            # stdout 进度条优先：screenshot/事务目录模式下 _count_frames 失明，
            # 但子进程自己的进度条仍在推进，是唯一可显示的真实进度。
            bar_pct, bar_frames = sp
            if bar_frames > 0 and self.total_frames > 0:
                cap_pct = min(100, bar_frames * 100 // self.total_frames)
                print(f"  [RENDER] ~{cap_pct}% ({bar_frames}/{self.total_frames} frames, "
                      f"bar {bar_pct}%, elapsed {elapsed})", flush=True)
            else:
                print(f"  [RENDER] {bar_pct}% (bar), elapsed {elapsed}", flush=True)
        elif frames is not None:
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
        self._check_watchdog()


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

    @staticmethod
    def _atomic_write(path, payload):
        """状态/登记表落盘：临时文件 + os.replace（实现见 script_interface.atomic_write_json）。

        原地 `open(path,'w')` 先截断再写，异常/断电时留下半截文件；交付登记表若被
        截断，读侧会把损坏读成空表，交付物保护门禁静默 fail-open（A10，2026-09-19）。
        """
        atomic_write_json(path, payload)

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
        # 避免"看似有历史、实际无状态"的误判（某项目逃逸教训）。
        print(f"  [STATE] No pipeline state file: {self.path} — starting fresh (all steps will run)")
        return {"steps": {}, "last_run": None}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.path, self.data)

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

    def expect_verification(self, key):
        """登记"本轮该检查点已执行、必须留下证据"。

        与 set_verification 刻意分写：前者出自执行位点，后者只在证据真读回来时才有，
        两者的差集＝"门禁跑了但证据凭空消失"。
        """
        pending = self.data.setdefault("verifications_expected", [])
        if key not in pending:
            pending.append(key)
            self.save()

    def reset(self, reason=""):
        """清空全部步骤状态（--fresh 真实重跑入口）。

        `render_metrics` 是唯一跨 reset 保留的顶层节：它是累计渲染成本台账，
        预算门禁（`render_budget.max_full_renders`）以 `full_render_attempts` 为
        计数源。旧实现把 self.data 整表换成 4 键新 dict，等于 --fresh 顺带清账——
        "fresh → 重渲 → fresh → 重渲"可无限绕开门禁，而 --fresh 恰是被推荐给
        "怀疑状态不可信"一方的恢复路径（A10，2026-09-19）。其余顶层节
        （verifications/verifications_expected/duration_budget_check/duration_check/
        scene_patch/…）属上一轮
        运行的证据，全量重置后即为陈旧，随步骤状态一并作废。
        """
        ledger = self.data.get("render_metrics")
        self.data = {"steps": {}, "last_run": None, "reset_reason": reason,
                     "reset_at": datetime.now().isoformat()}
        if isinstance(ledger, dict) and ledger:
            self.data["render_metrics"] = ledger
            print(f"  [STATE] Ledger carried across reset: full_render_attempts="
                  f"{ledger.get('full_render_attempts', 0)}, "
                  f"total={ledger.get('full_render_total_seconds', 0)}s "
                  f"(render budget keeps counting through --fresh)")
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


def _load_render_rules():
    """Load config/quality/render_rules.json with default fallback.

    Consumed by step_render (渲染卡死看门狗，prefab 复盘决策二)。读取失败
    回退 documented 默认值，绝不让看门狗静默旁路。
    """
    rules_path = CONFIG_DIR / "quality" / "render_rules.json"
    defaults = {
        "watchdog": {"enabled": True, "grace_seconds": 360, "stall_seconds": 300},
        "render_budget": {"max_full_renders": 3, "enforcement": "soft"},
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
    def __init__(self, config_path, html_project=None, quick_fix=False, force=False, gate_mode="render", shrink=True, fresh=False, no_scene_patch=False, confirm_fresh=False, accept_over_budget=False, accept_over_render=False, accept_media_untested=False):
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
        self.sidecar_path = self.temp_dir / SCENE_PATCH_SIDECAR
        self.patch_verdict_path = self.temp_dir / SCENE_PATCH_VERDICT

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
        # 渲染成本预算 soft 超限的显式放行开关（2026-08-19，见 _render_budget_gate_ok）
        self.accept_over_render = accept_over_render
        # 成片媒体终检 UNTESTED 的显式放行开关（2026-09-18 A04，见 _final_media_qa）
        self.accept_media_untested = accept_media_untested
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
        # --force 绕过留痕（交付审计维度2数据基础，2026-08-19）：门禁被 --force
        # 绕过属于可审计事实，写入 state 供 generate_completion_report --audit
        # 的 gate_integrity 维度裁定；非 force 运行全链成功完成时清除（见 run() 收尾）。
        if self.force:
            self.state.data["forced_run"] = {
                "at": datetime.now().isoformat(),
                "note": "--force bypassed gate cache/HARD_GATES for this run",
            }
            self.state.save()
        # 增量渲染（2026-09-03 触发权内化）：基线齐备（render_raw + sidecar）即自动
        # 尝试场景级增量渲染，PATCH/FULL 由 scene_patch_render 的白名单分类器裁定，
        # 段渲染/拼接/像素自校验任一失败自动降级全量。verify/visual_check/postprocess
        # 三道门禁照常执行。旧实现为 --scene-patch opt-in：跳过零成本且不留痕，
        # 29 份 pipeline_state 实测 scene_patch_attempts 全为 0——能力在位却零投产。
        # --no-scene-patch 为显式退出通道（怀疑增量路径时使用）。
        self.no_scene_patch = no_scene_patch
        # Audio-sync policy: shrink oversized scene windows so TTS drives
        # the timeline. See AGENTS.md → 渲染纪律 → 音画同步.
        self.audio_sync_rules = _load_audio_sync_rules()
        # 渲染纪律：看门狗阈值权威源（config/quality/render_rules.json）。
        self.render_rules = _load_render_rules()
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

    def _run(self, cmd, cwd=None, env=None, desc="", attach_watchdog=None):
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
                # 看门狗注入（prefab 复盘决策二）：卡死时杀的是这个进程树。
                if attach_watchdog is not None:
                    attach_watchdog.attach_process(proc)
                completed_normally = False
                try:
                    for line in proc.stdout:
                        print(line, end="", flush=True)
                        log.write(line)
                        # 进度权威源接线：把渲染子进程自己的进度条/trace 行喂给看门狗，
                        # 使其不再依赖 work-*/captured-frames 落盘布局（screenshot 模式失明）。
                        if attach_watchdog is not None:
                            attach_watchdog.observe_stdout(line)
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
        拒绝执行，并给出低成本替代路径（--resume / --quick-fix / 自动增量渲染）。
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
            inventory.append(f"  [FILE] {self.output_file} — 成果目录现有文件将在后处理成功时被新成片替换")
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
        print("    3. 场景级增量渲染  基线齐备时 render 步自动尝试（无需标志；--no-scene-patch 可退出）")
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

        # 2026-08-07 GPU/显示驱动栈病态规避（Todesk 虚拟显示 + Chrome shutdown 内核态挂起）：
        # 现象：渲染卡在 file_server 之后的 GPU 探测（probeHardwareWebGlInfo 的 browser.close() 永不返回，0/5305 帧）；
        # headless-shell 在该环境下还会 0xC0000005 崩溃。
        # 规避：① PRODUCER_HEADLESS_SHELL_PATH 指向 puppeteer 缓存的完整 chrome.exe（自动发现）；
        #       ② 渲染命令追加 --no-browser-gpu 跳过 GPU 探测（software 模式，画质不受影响，截图捕获路径）。
        # 机器重启可清除该病态（昨日同命令成功即证据），届时此规避无副作用，保留即可。
        if not env.get("PRODUCER_HEADLESS_SHELL_PATH"):
            _chrome_cache = Path.home() / ".cache" / "puppeteer" / "chrome"
            if _chrome_cache.exists():
                for _exe in sorted(_chrome_cache.rglob("chrome-win64/chrome.exe"), reverse=True):
                    env["PRODUCER_HEADLESS_SHELL_PATH"] = str(_exe)
                    break

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
        # tts_manifest 落盘位置是 temp 根，不是 temp/tts_44k/：写入方
        # enhance_video_audio._save_tts_manifest、消费方 instant_preview:736 同源。
        # 旧指纹登记指向 tts_44k 下的同名文件——全仓实测该路径 0 个存在
        # （temp 根 31 个、tts_44k 内 0 个，2026-09-18 find 计数），
        # compute_inputs_fingerprint 对不存在路径退化成常量 "notfound:<路径>"，
        # 于是这项输入永久空转、旁白音频换了也不会让 timeline/postprocess 失效
        # （A06-③，2026-09-18 审核）。
        tts_manifest = self.temp_dir / "tts_manifest.json"
        quality_dir = CONFIG_DIR / "quality"
        audio_rules = quality_dir / "audio_sync_rules.json"
        enhance_script = SCRIPTS / "enhance_video_audio.py"
        inputs_map = {
            # preflight 的结论同时取决于 gate_mode：audit 模式下 8 类
            # RENDER_CRITICAL_CHECKS 降级为警告（preflight_check.py:60-69/98-100），
            # 同一份 HTML 在 audit 里 passed 绝不等于在 render 里 passed。旧指纹不含
            # gate_mode，故 audit 跑过的结果被严格模式直接复用（A06-②）。
            # 非文件输入用 "mode:" 前缀字面量，与 completion_report 的 "digest:" 同一惯例。
            "preflight":    {"config": self.config_path, "html": self.html_path,
                             "narration": narration,
                             "asset_signoff_sheet": self.source_dir / "素材确认单.json",
                             "narration_digits_rules": quality_dir / "narration_digits_rules.json",
                             "gate_mode": "mode:" + str(self.gate_mode),
                             "script": SCRIPTS / "preflight_check.py"},
            "tts":          {"config": self.config_path, "narration": narration,
                             "compiled_scenes": compiled,
                             "script": enhance_script},
            "timeline":     {"config": self.config_path, "html": self.html_path,
                             "tts_manifest": tts_manifest,
                             "audio_sync_rules": audio_rules,
                             "script": SCRIPTS / "adjust_timeline.py"},
            # preview 实测消费 HTML/config 之外还有 tts_manifest（instant_preview.py:736
            # 按旁白时长决定各场采样时刻）与脚本自身；旧指纹只登记 config+html，
            # 换 TTS 音频后预览缓存照常命中，安全区预检跑的是旧时长（A06-②）。
            "preview":      {"config": self.config_path, "html": self.html_path,
                             "tts_manifest": tts_manifest,
                             "script": SCRIPTS / "instant_preview.py"},
            "render":       {"config": self.config_path, "html": self.html_path,
                             "render_rules": quality_dir / "render_rules.json"},
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
                             # 成片媒体终检（_final_media_qa）真实读取脚本本体与其
                             # 阈值表（audio/video 规则已在上方登记）；quick-fix 与
                             # full 的裁定面不同（视觉检查项 NA vs PASS），入指纹。
                             "media_qa_script": SCRIPTS / "media_qa_gate.py",
                             "mode": "mode:" + ("quick-fix" if self.quick_fix else "full"),
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
        """计算步骤当前输入的联合指纹（步骤完成时记录用）。

        流水线自产物的登记路径若不存在，点名打印。动机：
        compute_inputs_fingerprint 对缺失路径退化为常量 "notfound:<路径>"——即该项
        对指纹零贡献，与"登记了一个从不变化的输入"效果等同。A06-③ 的 tts_manifest
        死路径正以此形态静默空转（无报错、无日志、缓存照命中）。

        打印范围限定为流水线自己写盘的产物（_RUNTIME_ARTIFACT_INPUTS），不含源文件：
        narration.json / 素材确认单.json 按项目形态本就可缺（实测 60 份 config 中
        33 份的项目目录无 narration.json），全量点名会让每次运行都刷常驻警告——
        信号疲劳会淹没真问题。源文件绑定的正确性由回归用例50 逐条锁定。
        """
        inputs = self._step_inputs(step)
        missing = [f"{name}={path}" for name, path in sorted(inputs.items())
                   if name in _RUNTIME_ARTIFACT_INPUTS
                   and not Path(str(path)).exists()]
        if missing:
            print(f"  [FP-MISS] {step}: 登记的运行时产物不存在，对指纹零贡献 — "
                  f"{', '.join(missing)}")
        return compute_inputs_fingerprint(inputs)

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

        进入本函数即代表"该检查点本轮真跑了"，先记 `verifications_expected` 再取文件：
        审计 d2 用 expected − present 的差集判"跑了但证据没落盘"，旧 state 无该键不裁定
        （2026-09-21，只对当轮新交付生效，历史交付结论不回改）。
        缺失/损坏只点名不阻断——裁定权在退出码与各检查点，本函数的职责是把
        "静默空跑的登记面"变成可见证据（同 `[FP-MISS]` 口径）。
        """
        self.state.expect_verification(key)
        p = Path(result_path)
        if not p.exists():
            print(f"  [VERIFY-MISS] {key}: 结果文件未落盘 → 完工报告与审计收不到该门禁 "
                  f"({p})")
            return False
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [VERIFY-MISS] {key}: 结果文件不可解析 ({type(e).__name__}) → "
                  f"该门禁证据视为缺失 ({p})")
            return False
        self.state.set_verification(key, data)
        passed = data.get("passed")
        print(f"  [VERIFY] {key} result merged into pipeline_state (passed={passed})")
        return True

    def _step_dirty_reason(self, step):
        """步骤失效原因的纯判定（无日志副作用），供跳步判定与全链扫描共用。

        返回 None 表示可复用缓存；否则返回失效原因字符串：
          status=<x>     — 未通过/未执行/被失效
          no-fingerprint — 历史状态无指纹，不予信任
          inputs-changed — 输入指纹与完成时刻不一致（上游已变化）
          content-invalid — render_raw 未通过内容检查，缓存不得复用
        """
        rec = self.state.data.get("steps", {}).get(step, {})
        if rec.get("status") != "passed":
            return f"status={rec.get('status') or 'pending'}"
        old_fp = rec.get("input_fingerprint", "")
        if not old_fp:
            return "no-fingerprint"
        if old_fp != self._fingerprint(step):
            return "inputs-changed"
        if step == "render":
            status, _ = _render_content_status(self.render_raw)
            if status not in (gs.PASS, gs.NOT_APPLICABLE):
                return "content-invalid"
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

    def _prereq_stale_reason(self, step):
        """--step 起跑的前置步有效性判定：None 表示该前置结论仍对应当前输入。

        _gate_satisfied 只看 status，而历史 passed 可能属于已经变过的输入
        （A06-①，2026-09-18 审核）：改完 HTML 直接 --step render 时，"preflight
        曾通过"仍成立，于是那条本应校验新 HTML 的门禁被静默复用成免检。
        --resume 路径有 _earliest_rerun_step 做全链指纹扫描，显式 --step 路径此前
        没有等价校验。判定与 _step_dirty_reason 同源，不另立第二套指纹口径。
        类型化跳过（status=skipped）无指纹语义，交由 _gate_satisfied 裁定。
        """
        if not self._gate_satisfied(step):
            return "not-passed"
        rec = self.state.data.get("steps", {}).get(step, {})
        if rec.get("status") != "passed":
            return None
        return self._step_dirty_reason(step)

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
        if reason == "content-invalid":
            status, message = _render_content_status(self.render_raw)
            print(f"  [STALE] Render cache rejected ({status}): {message} — re-running")
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

        留痕分工：`budget_decision` 只在**显式放行**时写入（决策事实，一条）；
        `duration_budget_check` 记录**检查点是否真的评估过**及其结论（within_budget /
        not_adjudicated / over_budget_accepted），使门禁可证真。放行分支两键同轮
        写入（2026-09-21 Lint② 补写后不再互斥：前者答"谁放行了"，后者答"门禁跑过
        且结论为何"）。未声明预算与 quick_fix 保持零副作用。
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
            actual = 0.0
        enforcement = str(spec.get("enforcement", "soft")).lower()
        # 通过路径同样留痕：否则"state 无记录"既不能证真也不能证伪（2026-09-01 盘查）
        if actual <= 0:
            self._record_budget_check("not_adjudicated", 0.0, budget, enforcement)
            print(f"  [BUDGET] 无法裁定：config video_duration 缺失或为 0，"
                  f"未与预算 {budget:.0f}s 比较 [enforcement={enforcement}]")
            return True
        if actual <= budget:
            self._record_budget_check("within_budget", actual, budget, enforcement)
            print(f"  [BUDGET] OK: actual {actual:.1f}s ≤ budget {budget:.0f}s "
                  f"[enforcement={enforcement}]")
            return True

        over = actual - budget
        msg = (f"Duration budget exceeded: actual {actual:.1f}s > "
               f"budget {budget:.0f}s (+{over:.1f}s)")
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
            # Lint②（2026-09-21）：放行分支同样完成了评估动作，结论键必须留痕——
            # 与 _duration_consistency_ok"全出口单点留痕"同口径，改注释即反向豁免。
            # 不另起 save()：_record_budget_check 落盘时连带 budget_decision 一并写入。
            self._record_budget_check("over_budget_accepted", actual, budget, enforcement)
            print(f"  [BUDGET] {msg} — --accept-over-budget 显式放行，决策已记录")
            return True
        print(f"  BLOCKED: {msg} [enforcement=soft]")
        print("  超预算≠不合格，请做显式决策：")
        print("    1. 接受当前时长 → 追加 --accept-over-budget 重跑（上游步骤命中缓存）")
        print("    2. 精简至预算内 → 修改旁白后正常重跑")
        self.state.mark_failed("timeline", msg, error_code=VERIFY_FAILED)
        return False

    def _record_budget_check(self, decision, actual, budget, enforcement):
        """时长预算检查点的执行留痕（使"门禁跑过"成为可核证据，而非仅凭无记录反推）"""
        self.state.data["duration_budget_check"] = {
            "decision": decision,
            "actual_seconds": round(actual, 1) if actual > 0 else None,
            "budget_seconds": budget,
            "enforcement": enforcement,
            "at": datetime.now().isoformat(),
        }
        self.state.save()

    def _render_metrics(self) -> Dict[str, Any]:
        """渲染成本度量节（render_metrics）的读写入口，度量先行（2026-08-19）。

        字段：full_render_attempts（全量渲染尝试次数，含失败/中断）、
        full_render_total_seconds（累计成功+失败的全量渲染墙钟耗时）、
        scene_patch_attempts / scene_patch_hits、watchdog_kills。
        数据同时服务预算门禁判定与完工报告成本透出；真实基线校准阈值靠它积累。
        """
        metrics = self.state.data.get("render_metrics")
        if not isinstance(metrics, dict):
            metrics = {
                "full_render_attempts": 0,
                "full_render_total_seconds": 0.0,
                "scene_patch_attempts": 0,
                "scene_patch_hits": 0,
                "watchdog_kills": 0,
            }
            self.state.data["render_metrics"] = metrics
        return metrics

    def _scene_patch_route(self):
        """增量渲染触发裁定 → (attempt, verdict, reason)。

        2026-09-03 触发权内化：不再由 CLI opt-in 决定是否尝试。两条路径：
          ATTEMPT — 基线齐备（render_raw + scene_fingerprints.json），交白名单
                    分类器裁定 PATCH/FULL，失败自动降级全量；
          SKIPPED — --no-scene-patch 显式退出，或基线不齐（首渲/基线被清理）。
        SKIPPED 同样打印并写 state：旧实现跳过时零输出零留痕，于是"能力在位却
        零投产"在 29 份 pipeline_state 里表现为无记录，既不能证真也不能证伪。
        """
        if getattr(self, "no_scene_patch", False):
            return False, "SKIPPED", "opt-out(--no-scene-patch)"
        missing = [p.name for p in (self.render_raw, self.sidecar_path) if not p.exists()]
        if missing:
            return False, "SKIPPED", f"no-baseline({'+'.join(missing)})"
        return True, "ATTEMPT", "baseline-present"

    def _read_patch_verdict(self):
        """读取分类器写盘的裁定（scene_patch_verdict.json）。

        以文件为数据源而非解析 stdout：调用前已删除旧文件，故在场即本次产物。
        读不到（脚本早退/写盘失败）返回 None，调用方退回退出码语义。
        """
        try:
            with open(self.patch_verdict_path, "r", encoding="utf-8") as f:
                rec = json.load(f)
            return rec if isinstance(rec, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _record_scene_patch(self, verdict, reason, outcome=None):
        """裁定留痕：state.scene_patch（本次）+ render_metrics 计数（累计）。

        计数口径：attempts 仅在真实调用分类器时自增——SKIPPED 不计入，否则
        "基线不齐"会把命中率稀释成无意义数字；hits 为 PATCH 成功落地次数。
        FULL 不是失败：变更落在白名单外（改样式/改时间轴/改旁白）时全量渲染
        才是正确裁定，故留痕的目的是让裁定分布可解释，而非追求命中率。
        """
        metrics = self._render_metrics()
        record = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "verdict": verdict,
            "reason": reason,
            "outcome": outcome,
            "attempts": int(metrics.get("scene_patch_attempts", 0)),
            "hits": int(metrics.get("scene_patch_hits", 0)),
        }
        self.state.data["scene_patch"] = record
        self.state.save()
        return record

    def _pre_render_duration_consistent(self) -> bool:
        """渲染前时长一致性预检（2026-08-23 复盘 🟢）。

        教训：run2 的 adjust_timeline 把 HTML 静默回退基线（160.6→150s）后判定
        "零调整"，HTML 与 config 永久分叉，渲染后时长验证才发现，浪费 45 分钟
        全量渲染。渲染预算门禁是最后防线而非预防线——本预检 O(1) 完成，在昂贵
        渲染启动前拦截分叉。检查项：HTML data-duration == config video_duration
        （容差 0.1s）；--force 绕过（语义同 HARD_GATES）。
        """
        if self.force:
            return True
        try:
            html_text = (self.source_dir / "index.html").read_text(encoding="utf-8")
        except OSError:
            return True  # HTML 缺失由 preflight 覆盖，此处不重复裁定
        m = re.search(r'data-duration="([\d.]+)"', html_text)
        if not m:
            return True
        try:
            html_dur = float(m.group(1))
            cfg_dur = float(self.config.get("video_duration") or 0)
        except (TypeError, ValueError):
            return True
        if cfg_dur > 0 and abs(html_dur - cfg_dur) >= 0.1:
            msg = (f"Pre-render duration divergence: HTML data-duration={html_dur}s vs "
                   f"config video_duration={cfg_dur}s — blocked before expensive render")
            self.state.mark_failed("render", msg, error_code=VERIFY_FAILED)
            print(f"  BLOCKED: HTML 总时长 ({html_dur}s) ≠ config video_duration ({cfg_dur}s)")
            print("  渲染是终点验证手段不是调试工具——分叉的时间轴不值得一次全量渲染。")
            print("  恢复：--resume 重跑 timeline 步；若仍报零调整，删除项目目录的")
            print(f"  index.html.bak 与 index.html.hash 后重跑。")
            return False
        return True

    def _render_budget_gate_ok(self) -> bool:
        """渲染成本预算门禁（2026-08-19，P1 渲染成本治理）。

        检查点设在全量渲染启动前——单项目生命周期内全量渲染尝试次数达到
        render_rules.render_budget.max_full_renders 后拦截。阈值实证来源：
        prefab 批次复盘"3 次 40 分钟级重渲 + 9 小时失败收尾"——第 3 次之后
        问题已不是渲染能解决的，必须停下来定点诊断。
        soft（默认）：阻断，需 --accept-over-render 显式放行并留痕
        state.render_budget_decision；hard：直接 FAIL。
        参数语义权威源：render_rules.render_budget。scene-patch/quick-fix 不计入。
        """
        budget = self.render_rules.get("render_budget")
        if not isinstance(budget, dict):
            return True
        max_renders = budget.get("max_full_renders")
        if not isinstance(max_renders, int) or max_renders < 1:
            return True
        attempts = int(self._render_metrics().get("full_render_attempts", 0))
        if attempts < max_renders:
            return True

        enforcement = str(budget.get("enforcement", "soft")).strip().lower()
        msg = (f"Render budget exhausted: {attempts}/{max_renders} full-render attempts "
               f"({self._render_metrics().get('full_render_total_seconds', 0) / 60:.0f} min accumulated)")
        if self.force:
            print(f"  WARNING: {msg} — bypassed via --force (留痕 forced_run)")
            return True
        if enforcement == "hard":
            self.state.mark_failed("render", f"{msg} [enforcement=hard]",
                                   error_code=VERIFY_FAILED)
            return False
        decision = self.state.data.get("render_budget_decision")
        if self.accept_over_render and isinstance(decision, dict) \
                and decision.get("accepted") and decision.get("attempts") == attempts:
            return True
        if self.accept_over_render:
            self.state.data["render_budget_decision"] = {
                "accepted": True,
                "attempts": attempts,
                "reason": "explicit --accept-over-render",
                "at": datetime.now().isoformat(),
            }
            self.state.save()
            print(f"  [BUDGET] {msg} — --accept-over-render 显式放行，决策已记录")
            return True
        print(f"  BLOCKED: {msg} [enforcement=soft]")
        print("  渲染次数超限通常意味着问题不在渲染本身——先定点诊断再决定：")
        print("    1. 诊断既往失败（日志见 过程产物/日志/，state 含 log_path）")
        print("    2. 确需继续 → 追加 --accept-over-render 重跑（决策留痕）")
        print("    3. 局部修订 → 优先 --quick-fix（不计入预算）；场景 div 内容级修订由增量渲染自动承接（同样不计入预算）")
        self.state.mark_failed("render", msg, error_code=VERIFY_FAILED)
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
        if not self._delivery_slot_guard("render"):
            return False

        # ── 成果槽位无背书残留告警（2026-08-31 复盘）：槽位里有成片
        #    但没有任何交付背书时，按定义"不是交付物"，上面的保护门禁会放过——而人
        #    在成果目录里看到它就会当成已交付成片取用。本次 150s 旧基线正是以此形态
        #    占了规范交付名 9 天。不阻断（覆盖残留是合法诉求），只点名实测值 + 留痕。──
        if (self.output_file.exists() and self.output_file.stat().st_size > 0
                and not self._output_was_delivered()):
            _measured = self._probe_duration(self.output_file)
            try:
                _declared = float(self.config.get("video_duration", 0) or 0)
            except (TypeError, ValueError):
                _declared = 0.0
            if _measured is None or _declared <= 0:
                _verdict = ""
                _detail = (f"实测 {_measured if _measured is not None else '未知'}s"
                           f" / config 声明 {_declared or '未声明'}s（数值不可比，不做裁定）")
            else:
                _tol = float(self.audio_sync_rules.get("duration_consistency", {})
                             .get("max_diff_seconds", 1.0))
                _diff = abs(_measured - _declared)
                _verdict = "不一致" if _diff > _tol else "一致"
                _detail = (f"实测 {_measured:.1f}s vs config 声明 {_declared:.1f}s"
                           f"（差 {_diff:.1f}s，阈值 {_tol:.1f}s）→ 与当前声明{_verdict}")
            print(f"  [ORPHAN-SLOT] 成果槽位已存在文件，但无交付背书（既无登记表条目，"
                  f"也无 VALIDATED 完工报告）：{self.output_file.name}")
            print(f"    {_detail}")
            print("    本次运行若后处理成功，该文件会被新成片替换（旧文件由后处理备份进"
                  " temp 的 .prev.mp4）。若它其实是一份交付物（交付证据曾被清理/归档"
                  "导致判据失效），请先移出成果目录或改用修订后缀，再继续。")
            self.state.data["orphan_slot_warning"] = {
                "at": datetime.now().isoformat(timespec="seconds"),
                "file": self.output_file.name,
                "measured_seconds": _measured,
                "declared_seconds": _declared or None,
                "verdict": _verdict or None,
            }
            self.state.save()

        self.state.mark_started("render")

        # ── 渲染前时长一致性预检（2026-08-23 复盘 🟢）：
        #    O(1) 拦截 HTML/config 时长分叉，防 timeline 静默回退直达全量渲染
        #    （见 _pre_render_duration_consistent，45 分钟渲染浪费事故）──
        if not self._pre_render_duration_consistent():
            return False

        # ── 场景级增量渲染（2026-09-03 起默认尝试，--no-scene-patch 退出）：
        #    基线齐备即交白名单分类器裁定；判 PATCH 则段渲染+拼接产出 render_raw，
        #    白名单外变更/段渲染/拼接/像素自校验任一失败 → exit 3 → 降级 FULL ──
        patched = False
        attempt, verdict, reason = self._scene_patch_route()
        outcome = None
        if attempt:
            _metrics = self._render_metrics()
            _metrics["scene_patch_attempts"] = int(_metrics.get("scene_patch_attempts", 0)) + 1
            self.state.save()
            # 裁定留痕以文件为数据源：先删旧文件，在场即本次产物
            try:
                self.patch_verdict_path.unlink(missing_ok=True)
            except OSError:
                pass
            print("  [SCENE-PATCH] Baseline present — trying scene-level incremental render...")
            rc = self._run(
                [str(VENV_PYTHON), "scene_patch_render.py",
                 "--config", str(self.config_path), "--mode", "patch"],
                cwd=str(SCRIPTS), env=self.env, desc="scene patch render"
            )
            rec = self._read_patch_verdict() or {}
            if rc == 0:
                patched = True
                _metrics = self._render_metrics()
                _metrics["scene_patch_hits"] = int(_metrics.get("scene_patch_hits", 0)) + 1
                self.state.save()
                verdict = "PATCH"
                outcome = rec.get("outcome") or "applied"
                print(f"  [SCENE-PATCH] Patch applied — full render skipped "
                      f"(changed: {', '.join(rec.get('changed_scenes') or [])}"
                      f"{'; time witness: ' + str(rec['time_witness']) if rec.get('time_witness') else ''})")
            else:
                verdict = "FULL"
                reason = rec.get("reason") or (
                    "classifier-fallback" if rc == SCENE_PATCH_EXIT_FALLBACK
                    else f"patch-failed(rc={rc})")
                outcome = rec.get("outcome") or f"rc={rc}"
                print(f"  [SCENE-PATCH] Falling back to FULL render (rc={rc}, reason={reason})")
        else:
            print(f"  [SCENE-PATCH] Not attempted — {reason}")
        self._record_scene_patch(verdict, reason, outcome)

        if not patched:
            # ── 渲染成本预算门禁（P1，2026-08-19）：全量渲染启动前拦截，
            #    超预算需 --accept-over-render 显式放行（见 _render_budget_gate_ok）──
            if not self._render_budget_gate_ok():
                return False
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
                [self.temp_dir, self.source_dir], self.render_raw, _total_frames,
                watchdog=self.render_rules.get("watchdog", {}))
            # 成本度量：尝试次数在启动前计入（含失败/中断），耗时含 compile+probe
            _metrics = self._render_metrics()
            _metrics["full_render_attempts"] = int(_metrics.get("full_render_attempts", 0)) + 1
            self.state.save()
            _render_t0 = time.monotonic()
            watcher.start()
            try:
                render_cmd = ["npx.cmd", "hyperframes", "render", "-o", str(self.render_raw), "--workers", "1", "--fps", "25", "--low-memory-mode", "--protocol-timeout", "600000"]
                # 2026-08-07 GPU 栈病态规避：software 模式跳过探测浏览器（见 _setup_env 注释）
                if self.env.get("PRODUCER_HEADLESS_SHELL_PATH"):
                    render_cmd.append("--no-browser-gpu")
                rc = self._run(
                    render_cmd,
                    cwd=str(self.source_dir),
                    env=self.env,
                    desc="HyperFrames render",
                    attach_watchdog=watcher
                )
            finally:
                watcher.stop()
            _metrics = self._render_metrics()
            _metrics["full_render_total_seconds"] = round(
                float(_metrics.get("full_render_total_seconds", 0.0))
                + (time.monotonic() - _render_t0), 1)
            if watcher.stalled:
                _metrics["watchdog_kills"] = int(_metrics.get("watchdog_kills", 0)) + 1
            self.state.save()
            if rc != 0:
                # P1 整改（prefab 复盘）：渲染中断/失败不再等于归零——
                # render_raw.prev.mp4（若有）保留了上一版可用渲染，给出定点恢复路径
                hint = ""
                prev = self.render_raw.with_name("render_raw.prev.mp4")
                if prev.exists() and prev.stat().st_size > 0:
                    hint = (f" — previous render preserved at {prev.name}; "
                            f"if timeline unchanged, restore it + align config + --quick-fix "
                            f"instead of a full re-render")
                if watcher.stalled:
                    wd = self.render_rules.get("watchdog", {})
                    reason = ("Render stalled — watchdog killed the process tree (fail-fast); "
                              f"thresholds stall={wd.get('stall_seconds')}s/"
                              f"grace={wd.get('grace_seconds')}s (render_rules.json)")
                else:
                    reason = "HyperFrames render failed"
                self._fail("render", f"{reason}{hint}", SUBPROCESS_FAILED)
                return False

        if not self.render_raw.exists():
            self._fail("render", "render_raw.mp4 not created", OUTPUT_MISSING)
            return False

        content_status, content_message = _render_content_status(self.render_raw)
        if content_status not in (gs.PASS, gs.NOT_APPLICABLE):
            self._fail("render", content_message, VERIFY_FAILED)
            return False

        size_mb = self.render_raw.stat().st_size / 1024 / 1024
        print(f"  Rendered: {self.render_raw} ({size_mb:.1f} MB)")
        # 交付槽位不在此写入（2026-09-03 结构性修复）：render 产物只落 temp，
        # 成果文件/视频/{name}.mp4 由 postprocess 的混音与烧字幕在成功路径上写。
        # 旧实现这里 copy2(render_raw → 槽位)，一旦 postprocess 失败（如 BGM 未
        # 落盘），无声裸片就留在交付目录冒充成片——2026-09-02 一批 4 条即此
        # 形态，且 [ORPHAN-SLOT] 告警在 postprocess 之后才跑，拦不住。
        # enhance_video_audio.get_paths 现直接以 render_raw.mp4 为输入源；槽位旧
        # 文件的物理保护由 step3 的 {stem}.prev.mp4（落 temp）承担。
        print(f"  Delivery slot untouched: {self.output_file.name} "
              f"is written only by postprocess")

        # 场景指纹 sidecar：下一次渲染自动尝试增量渲染时的比对基线（尽力而为，
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

        visual_report = self.temp_dir / VISUAL_REPORT_NAME
        cmd = [
            str(VENV_PYTHON), "visual_boundary_check.py",
            "--video", str(self.render_raw),
            "--config", str(self.config_path),
            "--report-out", str(visual_report),
        ]

        rc = self._run(cmd, cwd=str(SCRIPTS), desc="visual boundary check")
        # 四态报告入 state：未测/合法不适用与"通过"分开留痕，追溯时不靠控制台
        self._merge_verification_file("visual_boundary", visual_report)
        if rc == 0:
            self.state.mark_completed("visual_check", self._fingerprint("visual_check"))
            return True
        if rc == 2:
            # 抽帧/测量未完成：无违规 ≠ 已检查，不得放行到烧字幕
            self._fail("visual_check",
                       "Visual check incomplete: frame extraction/measurement failed "
                       "(UNTESTED is not a pass) — see visual_boundary_report.json",
                       VERIFY_FAILED)
            return False
        self._fail("visual_check", "Content overflows subtitle safety zone", VERIFY_FAILED)
        return False

    REGISTRY_OK = "ok"
    REGISTRY_MISSING = "missing"
    REGISTRY_CORRUPT = "corrupt"

    @staticmethod
    def _registry_empty():
        return {
            "$description": "已交付成片登记表——本仓唯一版本化的交付台账（mp4 成片不入 git）。"
                            "由 pipeline_runner 完工报告成功后自动写入，禁止手工编辑；"
                            "删除条目不会删除成片，但会让交付物保护门禁失去该成片的交付依据。"
                            "覆盖边界：本表自 2026-08-31 起登记，此前历史成片不入表，"
                            "其交付判据回退至 VALIDATED 完工报告。",
            "deliveries": [],
        }

    @classmethod
    def _registry_load(cls, registry_path=None):
        """→ (data, status)，status ∈ ok / missing / corrupt。

        "缺失"与"读不动"必须可区分：登记表是本仓唯一版本化交付台账，损坏时按空表
        返回会让 `_output_was_delivered` 把所有已交付成片读成未登记，交付物保护
        门禁静默 fail-open（A10，2026-09-19）。
        """
        path = Path(registry_path) if registry_path else DELIVERY_REGISTRY
        if not path.exists():
            return cls._registry_empty(), cls.REGISTRY_MISSING
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return cls._registry_empty(), cls.REGISTRY_CORRUPT
        if not isinstance(data, dict) or not isinstance(data.get("deliveries"), list):
            return cls._registry_empty(), cls.REGISTRY_CORRUPT
        return data, cls.REGISTRY_OK

    @classmethod
    def _registry_read(cls, registry_path=None):
        """读取交付登记表；文件缺失/损坏时返回空登记表（登记缺失不致命，由调用方裁定）。"""
        return cls._registry_load(registry_path)[0]

    def _registry_entry(self, video_name, registry_path=None):
        """按成片文件名查交付登记条目；未登记返回 None。"""
        for entry in self._registry_read(registry_path).get("deliveries", []):
            if isinstance(entry, dict) and entry.get("video") == video_name:
                return entry
        return None

    def _record_delivery(self, video_path, srt_path, report_status):
        """交付登记：完工报告裁定后把成片实测值登记入 git 追踪的交付台账。

        落点在 completion_report 之后而非 delivery 步——"已交付"的成立以完工报告
        裁定为准，delivery 步通过但报告 FAILED 的产物不该获得交付身份。
        登记值一律来自实测（ffprobe 时长 + 文件字节数），不写任何自述结论。
        """
        entry = {
            "video": Path(video_path).name,
            "subtitle": Path(srt_path).name if srt_path else None,
            "project": self.html_project,
            "config": Path(self.config_path).name,
            "duration_seconds": self._probe_duration(video_path),
            "bytes": Path(video_path).stat().st_size if Path(video_path).exists() else None,
            "report_status": report_status,
            "delivered_at": datetime.now().isoformat(timespec="seconds"),
        }
        data, status = self._registry_load()
        if status == self.REGISTRY_CORRUPT:
            # 读成空表再写回 = 用本轮 1 条覆盖整本台账。git 里有上一版可恢复，
            # 但就地覆盖会让恢复动作从"git checkout"变成"记得去翻历史"。
            raise RuntimeError(
                f"{DELIVERY_REGISTRY.name} 无法解析，拒绝在损坏表上追加登记"
                "（先 git checkout 恢复该文件再重跑）")
        # 同名成片按登记时间保留最新一条（修订版走 _修订NN 名，不产生同名覆盖）
        deliveries = [e for e in data["deliveries"]
                      if not (isinstance(e, dict) and e.get("video") == entry["video"])]
        deliveries.append(entry)
        deliveries.sort(key=lambda e: str(e.get("delivered_at") or ""))
        data["deliveries"] = deliveries
        DELIVERY_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        PipelineState._atomic_write(DELIVERY_REGISTRY, data)
        return entry

    def _output_was_delivered(self):
        """判定 output_file 是否为上一轮成功交付的成片（交付物保护依据）。

        判据优先级：①交付登记表命中（权威、git 追踪）；②登记表损坏时按"已交付"
        裁定（fail-closed：无法证明未交付时不得放行覆盖，可用 --force 越过）；
        ③temp_dir/completion_report.json 状态为 VALIDATED* 且报告引用的视频与当前
        目标同名——作为登记表启用前的历史回退。
        prefab 复盘：上午已交付的合格成片被下午的裸渲染直接覆盖丢失——交付态必须
        可识别、可拦截。
        """
        _, _reg_status = self._registry_load()
        if _reg_status == self.REGISTRY_CORRUPT:
            self.state.data["delivery_registry_unreadable"] = {
                "at": datetime.now().isoformat(timespec="seconds"),
                "path": str(DELIVERY_REGISTRY),
                "decision": "treated_as_delivered_fail_closed",
            }
            self.state.save()
            print(f"  WARNING: 交付登记表无法解析（{DELIVERY_REGISTRY.name}），无法证明目标未交付 → "
                  "按已交付拦截覆盖。恢复路径：git checkout 该文件，或确认要覆盖时用 --force。")
            return True
        if self._registry_entry(self.output_file.name) is not None:
            return True
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

    def _delivery_slot_guard(self, step_name):
        """交付槽位保护（AGENTS.md：已交付文件不得覆盖）。放行返回 True。

        2026-09-03 结构性修复后，槽位的真实写入点在 postprocess 内部，render 步
        不再往 成果文件/视频/ 复制任何东西；2026-09-20 时序修复后该写入点收归
        enhance_video_audio._commit_delivery_slot()（全部下游门禁通过后的单次
        原子落槽）。两处都要拦：
        render 前置为省成本（别让 30-40 分钟渲染白跑），postprocess 前置为写入点
        把关——quick-fix 模式整段跳过 render，只靠 render 前置的话，quick-fix 会
        直接把已交付成片换掉。
        """
        if not (self.output_file.exists() and self.output_file.stat().st_size > 0):
            return True
        if self.force or not self._output_was_delivered():
            return True
        if self.state.data.get("steps", {}).get(step_name, {}).get("status") != "running":
            self.state.mark_started(step_name)
        print(f"  BLOCKED: 交付目标已是上一轮成功交付的成片：{self.output_file}")
        print("  AGENTS.md 交付纪律：已交付文件不得覆盖，需修订时加后缀。")
        print(f"  处理：config paths.video_name 改为 '{self.output_file.stem}_修订01.mp4' 后重跑；")
        print("        或将旧成片移出成果目录；紧急情况用 --force 越过（不推荐）。")
        self.state.mark_failed(
            step_name,
            f"Output {self.output_file.name} is a delivered artifact — use revision suffix",
            error_code=VERIFY_FAILED)
        return False

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
        quick-fix 与全量两种模式的后处理输入都是 temp/render_raw.mp4（enhance 的
        get_paths 如此解析），故检查对象同为它；仅在 render_raw 缺失（手工把视频
        放进成果目录后单独调用）时回退检查槽位文件。

        三条"放行但不裁定"的分支（产物缺失 / ffprobe 不可用 / config 无时长）与
        通过、漂移一并写 state.duration_check —— 旧实现通过路径只 print，日志不入
        git，交付后"检查点跑过"只能由"state 无记录"反推，而该反推分不清"跑过且干净"
        和"没跑成"（与 2026-09-01 修 duration_budget 同族判据，2026-09-20 Lint 登记）。
        """
        target = self.render_raw if self.render_raw.exists() else self.output_file
        max_diff = float(self.audio_sync_rules.get("duration_consistency", {})
                         .get("max_diff_seconds", 1.0))
        try:
            expected = float(self.config.get("video_duration", 0) or 0)
        except (TypeError, ValueError):
            expected = 0.0
        if not target.exists():
            # 产物缺失由后续步骤拦截，此处不重复报错；但"无从裁定"要留下正面证据
            self._record_duration_check("not_adjudicated", expected, None,
                                        None, max_diff, target, "artifact_missing")
            return True
        actual = self._probe_duration(target)
        if actual is None:
            # ffprobe 不可用时不阻断，交由 enhance 内部门禁判定
            self._record_duration_check("not_adjudicated", expected, None,
                                        None, max_diff, target, "ffprobe_unavailable")
            return True
        if expected <= 0:
            self._record_duration_check("not_adjudicated", expected, actual,
                                        None, max_diff, target, "config_duration_missing")
            return True
        diff = abs(actual - expected)
        if diff <= max_diff:
            self._record_duration_check("consistent", expected, actual,
                                        diff, max_diff, target)
            print(f"  Duration consistency OK: config {expected:.1f}s vs actual {actual:.1f}s (diff {diff:.2f}s)")
            return True
        msg = (f"Duration drift: config {expected:.1f}s vs actual {actual:.1f}s "
               f"(diff {diff:.1f}s > {max_diff:.1f}s)")
        self._record_duration_check("drift", expected, actual, diff, max_diff, target)
        print(f"  ERROR: {msg}")
        print("  定点修复指引（勿直接全量重渲）：")
        print("    1. 时间轴确需变化 → 重跑 pipeline_runner（timeline 步骤会同步 config）后渲染")
        print(f"    2. 渲染产物正确 → 手工对齐 config.video_duration={actual:.1f} 后 --quick-fix")
        self.state.mark_failed("postprocess", msg, error_code=VERIFY_FAILED)
        return False

    def _record_duration_check(self, decision, expected, actual, diff, max_diff,
                               target, reason=None):
        """时长一致性检查点的执行留痕（三态：consistent / not_adjudicated / drift）。

        与 `_record_budget_check` 同族：使"检查点跑过"成为 state 里的正证据。
        `reason` 只在 not_adjudicated 时出现，用于区分三个无从裁定的取数断点。
        """
        entry = {
            "decision": decision,
            "config_seconds": round(expected, 1) if expected > 0 else None,
            "actual_seconds": round(actual, 1) if actual is not None else None,
            "diff_seconds": round(diff, 2) if diff is not None else None,
            "max_diff_seconds": max_diff,
            "measured_target": target.name,
            "at": datetime.now().isoformat(),
        }
        if reason:
            entry["reason"] = reason
        self.state.data["duration_check"] = entry
        self.state.save()

    def _subtitle_source_note_line(self):
        """终检实测的字幕时间戳来源分布 → 交付说明可直接引用的一行（无记录返回 None）。

        为什么由这里喊：过程产物/ 整目录不入 git，temp 下的终检结果与 postprocess
        日志都不是版本化交付面；占比若只留日志行，交付后仍要人去翻日志才能回答
        "这一版字幕走的是主路径还是降级链"（2026-09-19 普查的取证成本即此）。
        文案生成点在 media_qa_gate.format_subtitle_source_line，与终检报告行同源。
        """
        facts = ((self.state.data.get("verifications", {})
                  .get("media_qa_final") or {})
                 .get("media_facts") or {}).get("subtitle_timestamp_source")
        if not facts:
            return None
        from media_qa_gate import format_subtitle_source_line
        return format_subtitle_source_line(facts)

    def _sync_subtitle_source_section(self) -> str:
        """把终检实测的字幕时间戳来源写进项目已有的交付说明 md（方案 B，2026-09-19）。

        为什么由流水线写：该节数字的唯一来源是终检 media_facts，而 temp/ 与日志不入
        git——靠提示让人粘贴，交付后"这一版走主路径还是降级链"又回到无从追溯。
        md 不存在时**不创建**：本节是交付说明的一节，不是交付说明本身，创建属人工
        收尾（模板 templates_07 照常提示）。返回一句状态供调用点打印，幂等由
        splice 的"无变化不落盘"保证。
        """
        from media_qa_gate import (delivery_notes_path, format_subtitle_source_section,
                                   splice_subtitle_source_section)
        from script_interface import atomic_write_text

        line = self._subtitle_source_note_line()
        notes = delivery_notes_path(self.output_file)
        if not line:
            return f"跳过（终检未记录来源分布）: {notes.name}"
        if not notes.exists():
            return f"跳过（交付说明尚未创建）: {notes.name}"
        new_text, changed = splice_subtitle_source_section(
            notes.read_text(encoding='utf-8'), format_subtitle_source_section(line))
        if not changed:
            return f"已是最新: {notes.name}"
        atomic_write_text(notes, new_text)
        return f"已写入: {notes}"

    def step_postprocess(self):
        if self._can_skip("postprocess"):
            return True

        self.state.mark_started("postprocess")

        # ── 交付槽位写入点把关（2026-09-03 结构性修复）：本步是 成果文件/视频/
        #    {name}.mp4 的唯一写入者（enhance step3 混音 / step6 烧字幕）。
        #    quick-fix 模式整段跳过 render，render 前置的已交付保护不会执行，
        #    故同一判据在写入点再把关一次。──
        if not self._delivery_slot_guard("postprocess"):
            return False
        # 槽位目录必须在 enhance 的 rename 之前存在（原先由 render 步的复制动作创建）
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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
            # A04（2026-09-18 审核）：成片落槽后、步骤判完成前，过独立媒体终检。
            # media_qa_gate 的全部物理检查（项名清单以该模块 docstring 为权威源）
            # 此前只被自身 CLI 与回归调用，默认生产链从未挂载——成片可以在从未被
            # 可播放性/死区/黑场/对齐度审过的情况下进交付。
            if not self._final_media_qa():
                return False
            self.state.mark_completed("postprocess", self._fingerprint("postprocess"))
            return True
        self._fail("postprocess", "Post-processing failed", SUBPROCESS_FAILED)
        return False

    def _final_media_qa(self):
        """成片媒体终检（postprocess 末端，2026-09-18 A04）。

        裁定经 media_qa_gate.adjudicate()：任一 FAIL 阻断；UNTESTED（扫描
        环境失败/样本不足）不是通过，须 --accept-media-untested 显式知情放行
        并留痕 state.media_qa_untested_decision（对齐 --accept-over-budget 惯例）。
        NOT_APPLICABLE 为声明性豁免（纯 BGM 无字幕、BGM 遮蔽起口、quick-fix
        跳视觉检查），不计违规也不计通过。quick-fix 下物理测量照常执行。
        结果落 temp/media_qa_final_result.json 并合并进 state.verifications
        ["media_qa_final"]，完工报告据此把未测计入 issues。
        """
        from media_qa_gate import MediaQAGate, adjudicate

        video_path, srt_path = self._delivery_paths()
        if self.quick_fix:
            visual = "not_applicable"
        else:
            visual = (self.state.data.get("steps", {})
                      .get("visual_check", {}).get("status") == "passed")
        qa = MediaQAGate()
        try:
            _passed, result = qa.validate(
                str(video_path),
                str(srt_path) if self.tts_enabled else None,
                visual_check_passed=visual,
                config_path=str(self.config_path),
                subtitle_source_path=str(self.temp_dir / "_subtitle_timestamp_source.json"))
        except Exception as e:
            self._fail("postprocess", f"Final media QA errored: {e}", VERIFY_FAILED)
            return False

        result["checked_at"] = datetime.now().isoformat()
        result["mode"] = "quick-fix" if self.quick_fix else "full"
        out_path = self.temp_dir / "media_qa_final_result.json"
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        except OSError as e:
            print(f"  WARNING: media_qa_final_result.json 写入失败: {e}")
        self._merge_verification_file("media_qa_final", out_path)

        counts = result.get("status_counts", {})
        print(f"  [MEDIA-QA] verdict={result['verdict']} "
              f"(PASS={counts.get('PASS', 0)}, FAIL={counts.get('FAIL', 0)}, "
              f"UNTESTED={counts.get('UNTESTED', 0)}, "
              f"NOT_APPLICABLE={counts.get('NOT_APPLICABLE', 0)}, "
              f"total={result.get('total_checks')})")
        allowed, reason = adjudicate(result,
                                     accept_untested=self.accept_media_untested)
        if result.get("verdict") == "UNTESTED" and self.accept_media_untested:
            self.state.data["media_qa_untested_decision"] = {
                "decision": "accepted_untested",
                "reason": reason,
                "untested": result.get("untested", []),
                "at": datetime.now().isoformat(),
            }
            self.state.save()
        if not allowed:
            self._fail("postprocess", reason, VERIFY_FAILED)
            return False
        if reason:
            print(f"  [MEDIA-QA] {reason}")
        return True

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
        # 交付登记：仅当完工报告裁定为 VALIDATED* 才授予交付身份（报告状态源自脚本
        # 真实测量，此处不自行判断）。登记是尽力而为——失败只告警，不翻转步骤结论。
        try:
            with open(report_out, "r", encoding="utf-8") as f:
                rep_status = str(json.load(f).get("status", ""))
        except (json.JSONDecodeError, OSError):
            rep_status = ""
        if rep_status.startswith("VALIDATED"):
            try:
                entry = self._record_delivery(video_path,
                                              srt_path if srt_path.exists() else None,
                                              rep_status)
                print(f"  [REGISTRY] 交付登记已写入 {DELIVERY_REGISTRY.name}: "
                      f"{entry['video']} "
                      f"({entry['duration_seconds']}s, {entry['bytes']} bytes)")
            except OSError as e:
                print(f"  WARNING: 交付登记写入失败（交付物保护门禁将退回完工报告判据）: {e}")
            # 交付说明的「字幕时间戳来源」节同样只在 VALIDATED 后写：报告未过就
            # 写字节进成果目录，等于把未过门的结论放进人读的那一份。
            try:
                print(f"  [DELIVERY-NOTES] {self._sync_subtitle_source_section()}")
            except OSError as e:
                print(f"  WARNING: 交付说明节写入失败（--audit 一致性维会判 FAIL）: {e}")
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
            prereqs = [(s, self._prereq_stale_reason(s)) for s in required]
            missing = [s for s, r in prereqs if r == "not-passed"]
            stale = [(s, r) for s, r in prereqs if r is not None and r != "not-passed"]
            if missing or stale:
                print(f"BLOCKED: Cannot start from '{start_from}'")
                for m in missing:
                    label = dict(STEPS).get(m, m)
                    print(f"  Missing prerequisite: {m} ({label})")
                for s, r in stale:
                    label = dict(STEPS).get(s, s)
                    rec = self.state.data.get("steps", {}).get(s, {})
                    completed = (rec.get("completed") or "?")[:19]
                    print(f"  Stale prerequisite: {s} ({label}) — {r} since {completed}"
                          f"，该结论已不对应当前输入")
                print(f"\nRun prerequisites first (drop --step to let --resume roll back"
                      f" to the earliest stale step), or use --force to override.")
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

        # 非 force 运行全链成功 → 历史 --force 留痕已过时（当前状态由合规运行
        # 重新建立），清除以免交付审计 gate_integrity 误判。force 运行自身保留。
        if not self.force and "forced_run" in self.state.data:
            del self.state.data["forced_run"]
            self.state.save()
            print("  [AUDIT] stale forced_run marker cleared (clean full run completed)")

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
        from media_qa_gate import delivery_notes_path
        notes_path = delivery_notes_path(self.output_file)
        if not notes_path.exists():
            print("-" * 60)
            print("REMINDER: Create delivery notes")
            print(f"  Template: AI视频制作工作流模板/templates_07_交付说明模板.md")
            print(f"  Output:   成果文件/交付说明_{video_name}.md")
            src_line = self._subtitle_source_note_line()
            if src_line:
                print(f"  必填「字幕时间戳来源」一节（终检实测，直接粘贴）: {src_line}")
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
                             "low-cost alternatives suggested (--resume/--quick-fix/auto "
                             "scene-patch).")
    parser.add_argument("--gate-mode", choices=["audit", "render"],
                        default="render",
                        help="Preflight gate mode: 'audit' (warn-only) or 'render' (hard-block). "
                             "Default: render")
    parser.add_argument("--engine", choices=["hyperframes", "openmontage"],
                        default=None,
                        help="Pipeline engine (auto-detected from config if omitted)")
    parser.add_argument("--no-scene-patch", action="store_true",
                        help="Opt OUT of scene-level incremental render. Since 2026-09-03 the "
                             "render step always attempts it when a baseline exists "
                             "(render_raw.mp4 + scene_fingerprints.json); the whitelist "
                             "classifier decides PATCH vs FULL and any failure falls back to a "
                             "full render. Use this flag when you suspect the incremental path "
                             "itself and want a guaranteed full render.")
    parser.add_argument("--scene-patch", action="store_true",
                        help="DEPRECATED / no-op: incremental render is now the default "
                             "behaviour. Kept so documented invocations do not fail; use "
                             "--no-scene-patch to disable.")
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
    parser.add_argument("--accept-over-render", action="store_true",
                        help="Explicitly accept exceeding the render_budget (render_rules.json, "
                             "soft enforcement). The decision is recorded in "
                             "pipeline_state.render_budget_decision for audit. Without this flag "
                             "a full render is blocked once full-render attempts reach the limit — "
                             "diagnose first; scene-patch/quick-fix are never budgeted.")
    parser.add_argument("--accept-media-untested", action="store_true",
                        help="Explicitly accept a final video whose media QA gate could not "
                             "complete some checks (verdict UNTESTED — scan tool failure or too "
                             "few speech onsets). UNTESTED is never read as passed: without "
                             "this flag postprocess fails; with it the decision is recorded in "
                             "pipeline_state.media_qa_untested_decision for audit. Media QA "
                             "FAIL (a measured violation) is never accepted by this flag.")

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

    # Config path resolution is engine-independent: the openmontage branch below
    # consumes config_path too, so it must not live inside the auto-detect branch
    # (显式 --engine openmontage 曾因此 UnboundLocalError，2026-09-18 审核 A11)。
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = CONFIG_DIR / config_path

    # Auto-detect engine from config
    engine = args.engine
    if not engine:
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

    if args.scene_patch:
        print("[NOTICE] --scene-patch 已失效：增量渲染自 2026-09-03 起为默认行为"
              "（基线齐备即自动尝试，分类器裁定 PATCH/FULL）。如需强制全量渲染请用 --no-scene-patch。")

    runner = PipelineRunner(
        config_path=args.config,
        html_project=args.html,
        quick_fix=args.quick_fix,
        force=args.force,
        gate_mode=args.gate_mode,
        shrink=(not args.no_shrink),
        fresh=args.fresh,
        no_scene_patch=args.no_scene_patch,
        confirm_fresh=args.confirm_fresh,
        accept_over_budget=args.accept_over_budget,
        accept_over_render=args.accept_over_render,
        accept_media_untested=args.accept_media_untested,
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
