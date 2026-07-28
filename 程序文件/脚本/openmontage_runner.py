#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OpenMontage 流水线运行器

对接 OpenMontage 的 Agent-Driven 架构，提供确定性步骤的编排层：
  - 环境预检（tool_registry 发现 + 可用性报告）
  - Pipeline manifest 加载与验证
  - 按 stage 顺序执行工具调用
  - 状态追踪 + 断点续跑
  - 产物管理（artifacts 读写）

三条路由：
  animated-explainer : Markdown/TXT -> 讲解视频
  clip-factory       : 长视频 -> 多段短视频
  podcast-repurpose  : 音频 -> 可视化视频

用法：
  python openmontage_runner.py --config explainer_demo.json
  python openmontage_runner.py --config clip_demo.json --resume
  python openmontage_runner.py --config podcast_demo.json --status
  python openmontage_runner.py --config explainer_demo.json --step compose
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Path setup — allow importing OpenMontage modules
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_env import ROOT, VENV_PYTHON
SCRIPTS = ROOT / "程序文件" / "脚本"
CONFIG_DIR = ROOT / "程序文件" / "配置"
OPENMONTAGE_SRC = ROOT / "程序文件" / "源码" / "openmontage"
OUTPUT_DIR = ROOT / "成果文件" / "视频"
OUTPUT_SUBTITLE = ROOT / "成果文件" / "字幕"
TEMP_BASE = ROOT / "过程产物" / "临时产物"

# Resolve FFmpeg/FFprobe paths — explicit lookup to avoid PATH issues in subprocess
FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"
NPX_BIN = shutil.which("npx.cmd") or shutil.which("npx") or "npx"

# Add OpenMontage to sys.path for tool/pipeline imports
if str(OPENMONTAGE_SRC) not in sys.path:
    sys.path.insert(0, str(OPENMONTAGE_SRC))

# ---------------------------------------------------------------------------
# Pipeline step definitions per pipeline type
# ---------------------------------------------------------------------------
PIPELINE_STEPS = {
    "animated-explainer": [
        ("preflight", "环境预检"),
        ("parse_input", "解析输入脚本"),
        ("generate_script", "生成脚本 artifact"),
        ("plan_scenes", "场景规划"),
        ("generate_assets", "生成素材(TTS+图像)"),
        ("compose", "视频合成"),
        ("publish", "交付校验"),
    ],
    "clip-factory": [
        ("preflight", "环境预检"),
        ("analyze_source", "源视频分析"),
        ("select_clips", "片段选取"),
        ("extract_clips", "片段提取(FFmpeg)"),
        ("enhance", "片段增强"),
        ("compose", "视频合成"),
        ("publish", "交付校验"),
    ],
    "podcast-repurpose": [
        ("preflight", "环境预检"),
        ("transcribe_audio", "音频转录"),
        ("plan_segments", "片段规划"),
        ("generate_visuals", "视觉素材生成"),
        ("compose", "视频合成"),
        ("publish", "交付校验"),
    ],
}


# ---------------------------------------------------------------------------
# Pipeline State (compatible with pipeline_runner.py's PipelineState)
# ---------------------------------------------------------------------------
class PipelineState:
    """Track pipeline execution state in a JSON file."""

    def __init__(self, state_path: Path, steps_def: list):
        self.path = Path(state_path)
        self.steps_def = steps_def
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"  WARNING: State file corrupted ({e}), attempting recovery...")
                raw = self.path.read_text(encoding='utf-8')
                parts = raw.rsplit('}\n{', 1)
                if len(parts) == 2:
                    candidate = '{' + parts[1]
                    try:
                        data = json.loads(candidate)
                        print(f"  RECOVERED: Using last valid state segment")
                        return data
                    except json.JSONDecodeError:
                        pass
                print(f"  RECOVERY FAILED: Starting with fresh state")
                return {"steps": {}, "last_run": None, "pipeline": None}
        return {"steps": {}, "last_run": None, "pipeline": None}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def mark_started(self, step: str):
        self.data["steps"][step] = {
            "status": "running",
            "started": datetime.now().isoformat(),
            "completed": None,
            "error": None,
        }
        self.data["last_run"] = datetime.now().isoformat()
        self.save()

    def mark_completed(self, step: str, extra: dict = None):
        self.data["steps"][step]["status"] = "passed"
        self.data["steps"][step]["completed"] = datetime.now().isoformat()
        if extra:
            self.data["steps"][step].update(extra)
        self.save()

    def mark_failed(self, step: str, error: str = ""):
        self.data["steps"][step]["status"] = "failed"
        self.data["steps"][step]["error"] = error[:500]
        self.save()

    def mark_skipped(self, step: str, reason: str = ""):
        self.data["steps"][step] = {
            "status": "skipped",
            "started": None,
            "completed": None,
            "error": None,
            "reason": reason,
        }
        self.save()

    def is_passed(self, step: str) -> bool:
        return self.data.get("steps", {}).get(step, {}).get("status") == "passed"

    def last_failed_step(self) -> Optional[str]:
        for name, info in reversed(list(self.data.get("steps", {}).items())):
            if info.get("status") == "failed":
                return name
        return None

    def print_status(self):
        print("=" * 60)
        print("OPENMONTAGE PIPELINE STATE")
        print("=" * 60)
        if self.data.get("last_run"):
            print(f"  Last run:  {self.data['last_run']}")
        if self.data.get("pipeline"):
            print(f"  Pipeline:  {self.data['pipeline']}")
        print()
        for step_name, step_label in self.steps_def:
            info = self.data.get("steps", {}).get(step_name, {})
            status = info.get("status", "pending")
            icons = {"passed": "+", "failed": "X", "running": ">", "skipped": "-"}
            icon = icons.get(status, " ")
            line = f"  [{icon}] {step_label}"
            if status == "failed" and info.get("error"):
                line += f"  -- {info['error'][:80]}"
            elif status == "passed" and info.get("completed"):
                line += f"  ({info['completed'][:19]})"
            elif status == "skipped" and info.get("reason"):
                line += f"  ({info['reason']})"
            print(line)
        print()


# ---------------------------------------------------------------------------
# OpenMontage Runner
# ---------------------------------------------------------------------------
class OpenMontageRunner:
    """OpenMontage pipeline runner — deterministic orchestration layer.

    Creative decisions (script writing, scene planning, asset selection) are
    made by the AI agent during interaction and persisted as artifact files.
    This runner handles: environment checks, tool invocation, file management,
    and state tracking.
    """

    def __init__(self, config: dict = None, config_path: str = None,
                 project_root: Path = None, force: bool = False):
        self.project_root = project_root or ROOT
        self.force = force

        # Load config from dict or file
        if config:
            self.config = config
        elif config_path:
            p = Path(config_path)
            if not p.is_absolute():
                # Search subdirectories (openmontage/, pipelines/, system/) then root
                base = CONFIG_DIR / "config"
                resolved = None
                for subdir in ("", "openmontage", "pipelines", "system"):
                    candidate = base / subdir / p if subdir else base / p
                    if candidate.exists():
                        resolved = candidate
                        break
                p = resolved or (base / p)
            with open(p, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        else:
            raise ValueError("Either config dict or config_path required")

        # Extract key fields
        self.pipeline_name = self.config.get("pipeline", "animated-explainer")
        self.input_config = self.config.get("input", {})
        self.output_config = self.config.get("output", {})
        self.budget_config = self.config.get("budget", {})
        self.runtime_config = self.config.get("runtime", {})
        self.paths_config = self.config.get("paths", {})

        # Resolve paths
        temp_subdir = self.paths_config.get("temp_subdir", f"openmontage_{self.pipeline_name}")
        self.temp_dir = TEMP_BASE / temp_subdir
        self.artifacts_dir = self.temp_dir / "artifacts"

        video_name = self.paths_config.get("video_name",
                                           self.output_config.get("video_name", f"{self.pipeline_name}_output.mp4"))
        self.output_file = OUTPUT_DIR / video_name

        # Step definitions for this pipeline
        self.steps_def = PIPELINE_STEPS.get(self.pipeline_name)
        if not self.steps_def:
            raise ValueError(
                f"Unknown pipeline: {self.pipeline_name}. "
                f"Available: {list(PIPELINE_STEPS.keys())}"
            )

        # State tracking
        self.state = PipelineState(
            self.temp_dir / "pipeline_state.json",
            self.steps_def
        )
        self.state.data["pipeline"] = self.pipeline_name

        # Tool registry (lazy init)
        self._registry = None
        self._tools = {}

    @property
    def step_names(self):
        return [s[0] for s in self.steps_def]

    def _init_registry(self):
        """Initialize OpenMontage tool registry and discover tools."""
        if self._registry is not None:
            return
        try:
            from tools.tool_registry import registry
            registry.discover()
            self._registry = registry
            self._tools = {name: registry.get(name) for name in registry.list_all()}
            print(f"  Tools discovered: {len(self._tools)}")
        except Exception as e:
            print(f"  WARNING: Tool registry init failed: {e}")
            print(f"  Falling back to direct tool imports")
            self._registry = None

    def _get_tool(self, name: str):
        """Get a tool instance by name from the registry."""
        if self._registry:
            tool = self._registry.get(name)
            if tool:
                return tool
        # Fallback: try direct import
        return self._tools.get(name)

    def _save_artifact(self, name: str, data: dict):
        """Save an artifact JSON to the artifacts directory."""
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifacts_dir / f"{name}.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def _load_artifact(self, name: str) -> Optional[dict]:
        """Load an artifact JSON from the artifacts directory."""
        path = self.artifacts_dir / f"{name}.json"
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def _run_subprocess(self, cmd, cwd=None, desc=""):
        """Run a subprocess command."""
        # Auto-resolve ffmpeg/ffprobe to full paths
        if cmd and cmd[0] in ("ffmpeg", "ffprobe"):
            cmd = list(cmd)
            cmd[0] = FFMPEG_BIN if cmd[0] == "ffmpeg" else FFPROBE_BIN
        print(f"  > {' '.join(str(c) for c in cmd[:8])}{'...' if len(cmd) > 8 else ''}")
        result = subprocess.run(cmd, cwd=cwd, capture_output=False)
        return result.returncode

    # -----------------------------------------------------------------------
    # Preflight: Environment check and tool availability report
    # -----------------------------------------------------------------------
    def step_preflight(self) -> bool:
        """Validate environment: tools, dependencies, paths."""
        if not self.force and self.state.is_passed("preflight"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("preflight")
        self._init_registry()

        issues = []

        # Check FFmpeg (use resolved path, not shutil.which again)
        if FFMPEG_BIN == "ffmpeg" and not shutil.which("ffmpeg"):
            issues.append("ffmpeg not found in PATH")
        if FFPROBE_BIN == "ffprobe" and not shutil.which("ffprobe"):
            issues.append("ffprobe not found in PATH")

        # Check Node.js (for Remotion)
        if not shutil.which("node"):
            issues.append("Node.js not found (required for Remotion)")

        # Check Remotion composer
        composer_dir = OPENMONTAGE_SRC / "remotion-composer"
        if not (composer_dir / "node_modules").exists():
            issues.append("Remotion node_modules missing — run npm install in remotion-composer/")

        # Check Python venv
        if not VENV_PYTHON.exists():
            issues.append(f"venv Python not found: {VENV_PYTHON}")

        # Check input file
        input_path = self.input_config.get("path", "")
        if input_path and not Path(input_path).exists():
            issues.append(f"Input file not found: {input_path}")

        # Tool availability report
        if self._registry:
            try:
                summary = self._registry.provider_menu_summary()
                runtimes = summary.get("composition_runtimes", {})
                print(f"  Composition runtimes: {runtimes}")
                caps = summary.get("capabilities", [])
                for cap in caps[:5]:
                    print(f"    {cap['capability']}: {cap['configured']}/{cap['total']} configured")
                if summary.get("runtime_warnings"):
                    for w in summary["runtime_warnings"][:3]:
                        print(f"  WARNING: {w}")
            except Exception as e:
                print(f"  (provider_menu_summary unavailable: {e})")

        if issues:
            for issue in issues:
                print(f"  ERROR: {issue}")
            self.state.mark_failed("preflight", "; ".join(issues))
            return False

        # Prepare directories
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.state.mark_completed("preflight")
        return True

    # -----------------------------------------------------------------------
    # animated-explainer steps
    # -----------------------------------------------------------------------
    def step_parse_input(self) -> bool:
        """Parse Markdown/TXT input into structured content for script generation."""
        if not self.force and self.state.is_passed("parse_input"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("parse_input")
        input_path = Path(self.input_config.get("path", ""))

        if not input_path.exists():
            self.state.mark_failed("parse_input", f"Input not found: {input_path}")
            return False

        content = input_path.read_text(encoding='utf-8')
        lines = [l.strip() for l in content.splitlines() if l.strip()]

        # Extract title (first heading or first non-empty line)
        title = ""
        sections = []
        current_section = {"heading": "", "content": []}

        for line in lines:
            if line.startswith("#"):
                if current_section["content"] or current_section["heading"]:
                    sections.append(current_section)
                heading = line.lstrip("#").strip()
                if not title:
                    title = heading
                current_section = {"heading": heading, "content": []}
            else:
                current_section["content"].append(line)

        if current_section["content"] or current_section["heading"]:
            sections.append(current_section)

        if not title:
            title = input_path.stem

        parse_result = {
            "title": title,
            "source_file": str(input_path),
            "sections": sections,
            "total_lines": len(lines),
            "char_count": len(content),
        }

        self._save_artifact("parsed_input", parse_result)
        print(f"  Title: {title}")
        print(f"  Sections: {len(sections)}, Lines: {len(lines)}")

        self.state.mark_completed("parse_input", {"title": title, "sections": len(sections)})
        return True

    def step_generate_script(self) -> bool:
        """Generate script artifact from parsed input.

        Applies default creative strategies:
          - Auto-detect scene type from content patterns (hero/stat/comparison/callout)
          - Generate visual hints from keyword extraction
          - Assign optimal narration pacing per scene type
        """
        if not self.force and self.state.is_passed("generate_script"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("generate_script")
        parsed = self._load_artifact("parsed_input")
        if not parsed:
            self.state.mark_failed("generate_script", "parsed_input artifact missing")
            return False

        sections = parsed.get("sections", [])
        title = parsed.get("title", "Untitled")

        # Visual hint keyword mapping
        visual_keywords = {
            "stat": ["%", "百分比", "数据", "增长", "下降", "万", "亿", "倍", "比例"],
            "comparison": ["对比", "vs", "相比", "区别", "不同", "优势", "劣势", "而"],
            "callout": ["注意", "重要", "关键", "核心", "必须", "警告", "提示", "记住"],
            "process": ["步骤", "流程", "第一", "第二", "第三", "然后", "接下来", "最后"],
            "image": ["外观", "效果", "展示", "样式", "设计", "图片", "照片", "视觉"],
        }

        scenes = []
        for i, section in enumerate(sections):
            heading = section.get("heading", f"Section {i+1}")
            content_lines = section.get("content", [])
            narration = " ".join(content_lines) if content_lines else heading

            # Truncate long narrations to ~30s per scene (roughly 150 chars in Chinese)
            if len(narration) > 300:
                narration = narration[:300] + "..."

            # Auto-detect scene type from content
            combined_text = heading + narration
            scene_type = "text_card"  # default
            best_score = 0
            for stype, keywords in visual_keywords.items():
                score = sum(1 for kw in keywords if kw in combined_text)
                if score > best_score:
                    best_score = score
                    scene_type = stype

            # First section is always hero
            if i == 0:
                scene_type = "hero_title"

            # Generate visual hints from content
            hints = []
            if scene_type == "stat":
                # Extract numbers/stats from text
                import re
                numbers = re.findall(r'\d+[%％万亿倍]?', combined_text)
                if numbers:
                    hints.append(f"展示数据: {', '.join(numbers[:3])}")
            elif scene_type == "comparison":
                hints.append("左右对比布局")
            elif scene_type == "process":
                hints.append("步骤流程图式布局")
            elif scene_type == "image":
                hints.append(f"配图: {heading}")

            scenes.append({
                "scene_id": i + 1,
                "heading": heading,
                "narration": narration,
                "scene_type": scene_type,
                "visual_notes": "; ".join(hints) if hints else "",
                "pacing": "slow" if scene_type in ("hero_title", "stat") else "normal",
            })

        if not scenes:
            scenes = [{
                "scene_id": 1,
                "heading": title,
                "narration": title,
                "scene_type": "hero_title",
                "visual_notes": "",
                "pacing": "slow",
            }]

        script_artifact = {
            "title": title,
            "scenes": scenes,
            "total_scenes": len(scenes),
            "generated_at": datetime.now().isoformat(),
            "source": parsed.get("source_file", ""),
        }

        self._save_artifact("script", script_artifact)
        # Summary of scene type distribution
        type_counts = {}
        for s in scenes:
            t = s.get("scene_type", "text_card")
            type_counts[t] = type_counts.get(t, 0) + 1
        type_summary = ", ".join(f"{k}:{v}" for k, v in type_counts.items())
        print(f"  Script generated: {len(scenes)} scenes ({type_summary})")

        self.state.mark_completed("generate_script", {"scenes": len(scenes)})
        return True

    def step_plan_scenes(self) -> bool:
        """Create scene plan with timing, visual specs, and asset requirements.

        Enhanced strategy:
          - Uses scene_type from generate_script for Remotion cut type mapping
          - Adapts timing to pacing (slow=1.2x, normal=1.0x, fast=0.8x)
          - Assigns assets_needed based on scene type
        """
        if not self.force and self.state.is_passed("plan_scenes"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("plan_scenes")
        script = self._load_artifact("script")
        if not script:
            self.state.mark_failed("plan_scenes", "script artifact missing")
            return False

        scenes = script.get("scenes", [])

        # Scene type → Remotion cut type mapping
        type_map = {
            "hero_title": "hero_title",
            "stat": "stat_card",
            "comparison": "comparison",
            "callout": "callout",
            "process": "text_card",
            "image": "image",
            "text_card": "text_card",
        }

        # Pacing multipliers (chars per second)
        pacing_cps = {"slow": 3.0, "normal": 4.0, "fast": 5.5}

        total_time = 0
        scene_plan = []
        for scene in scenes:
            narration = scene.get("narration", "")
            pacing = scene.get("pacing", "normal")
            cps = pacing_cps.get(pacing, 4.0)

            # Duration: based on narration length / chars-per-second, min 3s
            duration = max(3.0, len(narration) / cps)
            # Add padding for slow pacing (hero/stats need breathing room)
            if pacing == "slow":
                duration *= 1.2

            scene_type = scene.get("scene_type", "text_card")
            remotion_type = type_map.get(scene_type, "text_card")

            # Determine what assets this scene needs
            assets = []
            if scene_type == "image":
                assets.append({"type": "image", "desc": scene.get("heading", ""), "scene_id": scene["scene_id"]})
            elif scene_type == "stat":
                # stat_card uses inline data, no external assets
                pass
            elif scene_type == "comparison":
                pass

            scene_plan.append({
                "scene_id": scene["scene_id"],
                "heading": scene.get("heading", ""),
                "narration": narration,
                "start_time": round(total_time, 1),
                "duration": round(duration, 1),
                "end_time": round(total_time + duration, 1),
                "visual_type": remotion_type,
                "scene_category": scene_type,
                "pacing": pacing,
                "assets_needed": assets,
            })
            total_time += duration

        plan_artifact = {
            "title": script.get("title", ""),
            "total_duration": round(total_time, 1),
            "scenes": scene_plan,
            "compose_engine": self.runtime_config.get("compose_engine", "remotion"),
        }

        self._save_artifact("scene_plan", plan_artifact)
        # Print type distribution
        type_counts = {}
        for s in scene_plan:
            t = s.get("visual_type", "text_card")
            type_counts[t] = type_counts.get(t, 0) + 1
        type_summary = ", ".join(f"{k}:{v}" for k, v in type_counts.items())
        print(f"  Scene plan: {len(scene_plan)} scenes, ~{total_time:.0f}s ({type_summary})")

        self.state.mark_completed("plan_scenes", {
            "scenes": len(scene_plan),
            "duration": round(total_time, 1)
        })
        return True

    def step_generate_assets(self) -> bool:
        """Generate TTS audio and visual assets for each scene.

        Uses edge-tts for narration (free, already used in HyperFrames pipeline).
        Visual assets can be generated via ImageGen (Qoder) or stock image tools.
        """
        if not self.force and self.state.is_passed("generate_assets"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("generate_assets")
        scene_plan = self._load_artifact("scene_plan")
        if not scene_plan:
            self.state.mark_failed("generate_assets", "scene_plan artifact missing")
            return False

        scenes = scene_plan.get("scenes", [])
        tts_dir = self.temp_dir / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)

        # Generate TTS using edge-tts
        tts_manifest = {"scenes": []}
        for scene in scenes:
            scene_id = scene["scene_id"]
            narration = scene.get("narration", "")
            if not narration:
                continue

            tts_file = tts_dir / f"scene_{scene_id}.mp3"
            if tts_file.exists():
                print(f"  TTS cached: scene_{scene_id}.mp3")
                tts_manifest["scenes"].append({
                    "scene_id": scene_id,
                    "file": str(tts_file),
                })
                continue

            # Use edge-tts via subprocess with retry
            voice = self.config.get("voice", "zh-CN-YunxiNeural")
            rate = self.config.get("rate", "+0%")
            # Clean text for TTS: remove markdown artifacts, special chars
            clean_text = narration.replace("**", "").replace("*", "").replace("`", "")
            clean_text = clean_text.replace("##", "").replace("#", "").strip()
            if not clean_text:
                print(f"  Skipping empty narration for scene {scene_id}")
                tts_manifest["scenes"].append({
                    "scene_id": scene_id,
                    "file": "",
                    "skipped": True,
                })
                continue

            max_retries = 5
            success = False
            for attempt in range(1, max_retries + 1):
                cmd = [
                    str(VENV_PYTHON), "-m", "edge_tts",
                    "--voice", voice,
                    "--rate", rate,
                    "--text", clean_text,
                    "--write-media", str(tts_file),
                ]
                print(f"  Generating TTS: scene {scene_id} (attempt {attempt}/{max_retries})...")
                rc = self._run_subprocess(cmd, desc=f"TTS scene {scene_id}")
                if rc == 0 and tts_file.exists() and tts_file.stat().st_size > 0:
                    success = True
                    break
                else:
                    # Clean up partial file
                    if tts_file.exists():
                        tts_file.unlink()
                    import time
                    time.sleep(2 * attempt)  # Exponential backoff

            if not success:
                self.state.mark_failed("generate_assets", f"TTS failed for scene {scene_id} after {max_retries} retries")
                return False

            tts_manifest["scenes"].append({
                "scene_id": scene_id,
                "file": str(tts_file),
            })

        self._save_artifact("tts_manifest", tts_manifest)
        print(f"  TTS generated: {len(tts_manifest['scenes'])} scenes")

        self.state.mark_completed("generate_assets", {
            "tts_scenes": len(tts_manifest["scenes"])
        })
        return True

    # -----------------------------------------------------------------------
    # clip-factory steps
    # -----------------------------------------------------------------------
    def step_analyze_source(self) -> bool:
        """Analyze source video: detect scenes, extract metadata."""
        if not self.force and self.state.is_passed("analyze_source"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("analyze_source")
        input_path = self.input_config.get("path", "")

        if not Path(input_path).exists():
            self.state.mark_failed("analyze_source", f"Source not found: {input_path}")
            return False

        # Use scene_detect tool if available
        scene_detect = self._get_tool("scene_detect")
        if scene_detect:
            output_json = str(self.artifacts_dir / "scenes.json")
            try:
                result = scene_detect.execute({
                    "input_path": input_path,
                    "method": "content",
                    "min_scene_length_seconds": 2.0,
                    "output_path": output_json,
                })
                if result.success:
                    scenes_data = result.data.get("scenes", [])
                    # Derive duration from last scene's end time
                    duration = 0
                    if scenes_data:
                        last_scene = scenes_data[-1]
                        duration = last_scene.get("end_seconds", last_scene.get("end", 0))
                    self._save_artifact("source_analysis", {
                        "source": input_path,
                        "scenes": scenes_data,
                        "scene_count": result.data.get("scene_count", len(scenes_data)),
                        "duration": duration,
                    })
                    print(f"  Scenes detected: {result.data.get('scene_count', '?')}")
                    self.state.mark_completed("analyze_source", {
                        "scenes": result.data.get("scene_count", 0)
                    })
                    return True
                else:
                    print(f"  scene_detect failed: {result.error}")
            except Exception as e:
                print(f"  scene_detect exception: {e}")

        # Fallback: ffprobe-based basic analysis
        probe_result = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_format", "-show_streams",
             "-of", "json", input_path],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        try:
            probe_data = json.loads(probe_result.stdout)
            duration = float(probe_data.get("format", {}).get("duration", 0))
            analysis = {
                "source": input_path,
                "duration": duration,
                "format": probe_data.get("format", {}),
                "streams": probe_data.get("streams", []),
                "scenes": [],  # To be filled by agent or manual selection
                "scene_count": 0,
                "method": "ffprobe_fallback",
            }
            self._save_artifact("source_analysis", analysis)
            print(f"  Video duration: {duration:.1f}s (ffprobe analysis)")
            print(f"  NOTE: Scene detection via PySceneDetect unavailable,")
            print(f"        manual scene selection or agent-assisted analysis required")
            self.state.mark_completed("analyze_source", {"duration": duration})
            return True
        except (json.JSONDecodeError, KeyError) as e:
            self.state.mark_failed("analyze_source", f"ffprobe failed: {e}")
            return False

    def step_select_clips(self) -> bool:
        """Select clips from analyzed scenes for extraction.

        NOTE: Optimal clip selection requires AI agent judgment.
        This step creates a selection plan from detected scenes or
        evenly divides the video if no scene data is available.
        """
        if not self.force and self.state.is_passed("select_clips"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("select_clips")
        analysis = self._load_artifact("source_analysis")
        if not analysis:
            self.state.mark_failed("select_clips", "source_analysis artifact missing")
            return False

        duration = analysis.get("duration", 0)
        scenes = analysis.get("scenes", [])
        target_clips = self.config.get("target_clips", 3)
        max_clip_duration = self.config.get("max_clip_duration", 60)

        clips = []
        if scenes:
            # Use detected scenes — support multiple field name conventions
            for i, scene in enumerate(scenes[:target_clips]):
                start = scene.get("start_seconds", scene.get("start", 0))
                end = scene.get("end_seconds", scene.get("end", start + max_clip_duration))
                # Cap clip duration
                if end - start > max_clip_duration:
                    end = start + max_clip_duration
                clips.append({
                    "clip_id": i + 1,
                    "start": start,
                    "end": end,
                    "label": f"clip_{i+1}",
                })
        else:
            # Evenly divide
            clip_duration = min(max_clip_duration, duration / target_clips)
            for i in range(target_clips):
                start = i * clip_duration
                end = min(start + clip_duration, duration)
                if start >= duration:
                    break
                clips.append({
                    "clip_id": i + 1,
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "label": f"clip_{i+1}",
                })

        selection = {
            "source": analysis.get("source", ""),
            "clips": clips,
            "total_clips": len(clips),
        }

        self._save_artifact("clip_selection", selection)
        print(f"  Selected {len(clips)} clips from {duration:.1f}s source")

        self.state.mark_completed("select_clips", {"clips": len(clips)})
        return True

    def step_extract_clips(self) -> bool:
        """Extract selected clips using FFmpeg."""
        if not self.force and self.state.is_passed("extract_clips"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("extract_clips")
        selection = self._load_artifact("clip_selection")
        if not selection:
            self.state.mark_failed("extract_clips", "clip_selection artifact missing")
            return False

        source = selection.get("source", "")
        clips = selection.get("clips", [])
        clips_dir = self.temp_dir / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)

        extracted = []
        for clip in clips:
            clip_id = clip["clip_id"]
            start = clip["start"]
            duration = clip["end"] - clip["start"]
            output_file = clips_dir / f"clip_{clip_id:03d}.mp4"

            if output_file.exists():
                print(f"  Clip cached: clip_{clip_id:03d}.mp4")
                extracted.append({"clip_id": clip_id, "file": str(output_file)})
                continue

            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", source,
                "-t", str(duration),
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(output_file),
            ]
            print(f"  Extracting clip {clip_id}: {start:.1f}s - {clip['end']:.1f}s...")
            rc = self._run_subprocess(cmd, desc=f"Extract clip {clip_id}")
            if rc != 0 or not output_file.exists():
                # Try re-encode if copy fails
                cmd_re = [
                    "ffmpeg", "-y",
                    "-ss", str(start),
                    "-i", source,
                    "-t", str(duration),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac",
                    str(output_file),
                ]
                rc = self._run_subprocess(cmd_re, desc=f"Re-encode clip {clip_id}")
                if rc != 0 or not output_file.exists():
                    self.state.mark_failed("extract_clips", f"Failed to extract clip {clip_id}")
                    return False

            extracted.append({"clip_id": clip_id, "file": str(output_file)})

        self._save_artifact("extracted_clips", {"clips": extracted})
        print(f"  Extracted {len(extracted)} clips")

        self.state.mark_completed("extract_clips", {"clips": len(extracted)})
        return True

    def step_enhance(self) -> bool:
        """Enhance extracted clips with FFmpeg filter chain.

        Default enhancements (all configurable via config JSON "enhancement" key):
          - Resolution normalization (scale to target, pad if aspect differs)
          - Framerate normalization (uniform fps)
          - Audio loudness normalization (EBU R128)
          - Optional fade in/out transitions
          - Color/contrast enhancement (mild eq filter)
        """
        if not self.force and self.state.is_passed("enhance"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("enhance")
        extracted = self._load_artifact("extracted_clips")
        if not extracted:
            self.state.mark_failed("enhance", "extracted_clips artifact missing")
            return False

        clips = extracted.get("clips", [])
        if not clips:
            self.state.mark_failed("enhance", "No clips to enhance")
            return False

        # Enhancement configuration with sensible defaults
        enh_cfg = self.config.get("enhancement", {})
        target_w = enh_cfg.get("width", self.output_config.get("resolution", "1920x1080").split("x")[0])
        target_h = enh_cfg.get("height", self.output_config.get("resolution", "1920x1080").split("x")[-1])
        target_fps = enh_cfg.get("fps", self.output_config.get("fps", 30))
        audio_normalize = enh_cfg.get("audio_normalize", True)
        color_enhance = enh_cfg.get("color_enhance", True)
        fade_duration = enh_cfg.get("fade_duration", 0.3)  # seconds, 0 = disabled
        target_w, target_h = int(target_w), int(target_h)
        target_fps = int(target_fps)

        enhanced_dir = self.temp_dir / "clips_enhanced"
        enhanced_dir.mkdir(parents=True, exist_ok=True)

        enhanced_clips = []
        for clip in clips:
            clip_id = clip["clip_id"]
            src_file = clip["file"]
            out_file = enhanced_dir / f"clip_{clip_id:03d}_enh.mp4"

            if out_file.exists() and not self.force:
                print(f"  Enhanced clip cached: clip_{clip_id:03d}_enh.mp4")
                enhanced_clips.append({"clip_id": clip_id, "file": str(out_file)})
                continue

            # Build filter chain
            vf_parts = []
            af_parts = []

            # 1. Video: scale + pad to exact target resolution (preserves aspect, no distortion)
            vf_parts.append(
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black"
            )

            # 2. Video: normalize framerate
            vf_parts.append(f"fps={target_fps}")

            # 3. Video: mild color/contrast enhancement
            if color_enhance:
                vf_parts.append("eq=contrast=1.03:brightness=0.02:saturation=1.05")

            # 4. Video: fade in/out transitions
            if fade_duration > 0:
                # Get clip duration via ffprobe
                probe = subprocess.run(
                    [FFPROBE_BIN, "-v", "quiet", "-show_format", "-of", "json", src_file],
                    capture_output=True, text=True, encoding='utf-8', errors='replace'
                )
                try:
                    clip_dur = float(json.loads(probe.stdout)["format"]["duration"])
                except (json.JSONDecodeError, KeyError):
                    clip_dur = 5.0

                vf_parts.append(f"fade=t=in:st=0:d={fade_duration}")
                if clip_dur > fade_duration * 2:
                    fade_out_start = clip_dur - fade_duration
                    vf_parts.append(f"fade=t=out:st={fade_out_start:.2f}:d={fade_duration}")

            # 5. Audio: loudness normalization (EBU R128)
            if audio_normalize:
                af_parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")

            cmd = [
                "ffmpeg", "-y",
                "-i", src_file,
            ]
            if vf_parts:
                cmd += ["-vf", ",".join(vf_parts)]
            if af_parts:
                cmd += ["-af", ",".join(af_parts)]
            cmd += [
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                str(out_file),
            ]

            print(f"  Enhancing clip {clip_id}: {target_w}x{target_h}@{target_fps}fps"
                  + (" +audio_norm" if audio_normalize else "")
                  + (" +color" if color_enhance else "")
                  + (f" +fade({fade_duration}s)" if fade_duration > 0 else ""))
            rc = self._run_subprocess(cmd, desc=f"Enhance clip {clip_id}")
            if rc != 0 or not out_file.exists():
                self.state.mark_failed("enhance", f"Failed to enhance clip {clip_id}")
                return False

            enhanced_clips.append({"clip_id": clip_id, "file": str(out_file)})

        # Save enhanced clips manifest (overwrites extracted_clips for downstream)
        self._save_artifact("extracted_clips", {
            "clips": enhanced_clips,
            "total_clips": len(enhanced_clips),
            "enhanced": True,
            "enhancement_config": {
                "resolution": f"{target_w}x{target_h}",
                "fps": target_fps,
                "audio_normalize": audio_normalize,
                "color_enhance": color_enhance,
                "fade_duration": fade_duration,
            },
        })
        print(f"  Enhanced {len(enhanced_clips)} clips → {target_w}x{target_h}@{target_fps}fps")

        self.state.mark_completed("enhance", {"clips": len(enhanced_clips), "enhanced": True})
        return True

    # -----------------------------------------------------------------------
    # podcast-repurpose steps
    # -----------------------------------------------------------------------
    def step_transcribe_audio(self) -> bool:
        """Transcribe audio source to text with timestamps.

        Priority: openai-whisper (local) > OpenMontage transcriber tool > ffprobe fallback.
        Whisper runs fully locally with no API key, using the 'base' model
        for speed/quality balance on Chinese audio.
        """
        if not self.force and self.state.is_passed("transcribe_audio"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("transcribe_audio")
        input_path = self.input_config.get("path", "")

        if not Path(input_path).exists():
            self.state.mark_failed("transcribe_audio", f"Audio not found: {input_path}")
            return False

        # --- Strategy 1: openai-whisper (local, free, no API key) ---
        whisper_model = self.config.get("whisper_model", "base")
        try:
            import whisper as _whisper
            print(f"  Transcribing with Whisper (model={whisper_model})...")
            model = _whisper.load_model(whisper_model)
            result = model.transcribe(
                input_path,
                language="zh",
                task="transcribe",
                verbose=False,
            )
            segments = []
            for seg in result.get("segments", []):
                segments.append({
                    "id": seg.get("id", len(segments)),
                    "start": round(seg.get("start", 0), 3),
                    "end": round(seg.get("end", 0), 3),
                    "text": seg.get("text", "").strip(),
                })
            full_text = result.get("text", "").strip()
            duration = segments[-1]["end"] if segments else 0

            transcript = {
                "source": input_path,
                "segments": segments,
                "text": full_text,
                "duration": duration,
                "method": "whisper",
                "model": whisper_model,
                "language": result.get("language", "zh"),
            }
            self._save_artifact("transcript", transcript)
            print(f"  Transcription complete: {len(segments)} segments, {duration:.1f}s")
            self.state.mark_completed("transcribe_audio", {
                "segments": len(segments),
                "method": "whisper",
            })
            return True
        except ImportError:
            print("  NOTE: openai-whisper not installed, trying fallback...")
        except Exception as e:
            print(f"  Whisper failed: {e}, trying fallback...")

        # --- Strategy 2: OpenMontage transcriber tool ---
        transcriber = self._get_tool("transcriber")
        if transcriber and transcriber.get_status().value == "available":
            try:
                output_path = str(self.artifacts_dir / "transcript.json")
                result = transcriber.execute({
                    "input_path": input_path,
                    "output_path": output_path,
                    "language": "zh",
                    "diarize": False,
                })
                if result.success:
                    transcript = {
                        "source": input_path,
                        "segments": result.data.get("segments", []),
                        "text": result.data.get("text", ""),
                        "duration": result.data.get("duration", 0),
                        "method": "openmontage_transcriber",
                    }
                    self._save_artifact("transcript", transcript)
                    print(f"  Transcription complete: {len(transcript['segments'])} segments")
                    self.state.mark_completed("transcribe_audio", {
                        "segments": len(transcript["segments"])
                    })
                    return True
            except Exception as e:
                print(f"  Transcriber failed: {e}")

        # --- Strategy 3: ffprobe duration + placeholder ---
        print("  WARNING: All transcription engines unavailable")
        print("           Install openai-whisper for full transcription")

        probe = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_format", "-of", "json", input_path],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        try:
            duration = float(json.loads(probe.stdout)["format"]["duration"])
        except (json.JSONDecodeError, KeyError):
            duration = 0

        transcript = {
            "source": input_path,
            "segments": [],
            "text": "",
            "duration": duration,
            "method": "ffprobe_placeholder",
        }
        self._save_artifact("transcript", transcript)
        print(f"  Audio duration: {duration:.1f}s (placeholder only)")

        self.state.mark_completed("transcribe_audio", {"duration": duration, "method": "placeholder"})
        return True

    def step_plan_segments(self) -> bool:
        """Plan video segments from transcription.

        When whisper segments are available:
          - Groups them into ~15s visual blocks by natural pauses
          - Preserves text content for subtitle overlays
        Fallback (no transcript):
          - Evenly divides audio into time-based segments
        """
        if not self.force and self.state.is_passed("plan_segments"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("plan_segments")
        transcript = self._load_artifact("transcript")
        if not transcript:
            self.state.mark_failed("plan_segments", "transcript artifact missing")
            return False

        segments = transcript.get("segments", [])
        duration = transcript.get("duration", 0)
        method = transcript.get("method", "unknown")

        # Target segment duration (configurable)
        target_seg_dur = self.config.get("segment_duration", 15.0)

        visual_segments = []
        if segments:
            # Group transcript segments into visual blocks by natural pauses
            block_start = segments[0].get("start", 0)
            block_texts = []
            for seg in segments:
                seg_text = seg.get("text", "")
                seg_end = seg.get("end", seg.get("start", 0) + 5)
                block_texts.append(seg_text)

                # Break block at: target duration reached, natural pause (>1s gap), or last segment
                block_duration = seg_end - block_start
                is_last = seg == segments[-1]
                next_start = segments[segments.index(seg) + 1].get("start", seg_end) if not is_last else seg_end
                has_pause = (next_start - seg_end) > 0.8

                if block_duration >= target_seg_dur or (has_pause and block_duration > 5) or is_last:
                    full_text = "".join(block_texts).strip()
                    # Determine visual type from content
                    vtype = "audiogram"
                    if any(kw in full_text for kw in ["%", "数据", "增长", "万", "亿"]):
                        vtype = "audiogram_stat"
                    elif any(kw in full_text for kw in ["注意", "关键", "重要", "核心"]):
                        vtype = "audiogram_callout"

                    visual_segments.append({
                        "segment_id": len(visual_segments) + 1,
                        "start": round(block_start, 2),
                        "end": round(seg_end, 2),
                        "text": full_text,
                        "subtitle_text": full_text,  # for subtitle overlay
                        "visual_type": vtype,
                    })
                    if not is_last:
                        block_start = next_start
                    block_texts = []

            print(f"  Grouped {len(segments)} whisper segments → {len(visual_segments)} visual blocks")
        else:
            # No transcript, create time-based segments
            t = 0
            while t < duration:
                visual_segments.append({
                    "segment_id": len(visual_segments) + 1,
                    "start": round(t, 2),
                    "end": round(min(t + target_seg_dur, duration), 2),
                    "text": "",
                    "subtitle_text": "",
                    "visual_type": "audiogram",
                })
                t += target_seg_dur

        plan = {
            "source": transcript.get("source", ""),
            "duration": duration,
            "transcript_method": method,
            "segments": visual_segments,
            "total_segments": len(visual_segments),
        }

        self._save_artifact("segment_plan", plan)
        print(f"  Segment plan: {len(visual_segments)} segments, {duration:.0f}s total")

        self.state.mark_completed("plan_segments", {
            "segments": len(visual_segments)
        })
        return True

    def step_generate_visuals(self) -> bool:
        """Generate visual elements for audiogram.

        Produces composition config with:
          - Gradient background (configurable colors)
          - Per-segment subtitle text overlays
          - Waveform visualization settings
          - Auto-detected title from transcript
        """
        if not self.force and self.state.is_passed("generate_visuals"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("generate_visuals")
        segment_plan = self._load_artifact("segment_plan")
        transcript = self._load_artifact("transcript")
        if not segment_plan:
            self.state.mark_failed("generate_visuals", "segment_plan artifact missing")
            return False

        source = segment_plan.get("source", "")
        duration = segment_plan.get("duration", 0)
        segments = segment_plan.get("segments", [])

        # Extract title from transcript or filename
        title = ""
        if transcript:
            title = transcript.get("text", "")[:60] if transcript.get("text") else ""
        if not title:
            title = Path(source).stem.replace("_", " ").replace("-", " ")

        # Theme colors (configurable)
        theme = self.config.get("audiogram_theme", {})
        visuals = {
            "source_audio": source,
            "duration": duration,
            "title": title,
            "background_color": theme.get("bg_color", "#1a1a2e"),
            "background_gradient": theme.get("gradient", True),
            "gradient_colors": theme.get("gradient_colors", ["#1a1a2e", "#16213e"]),
            "waveform_color": theme.get("wave_color", "#e94560"),
            "waveform_style": theme.get("wave_style", "cline"),  # cline, p2p, point, line
            "title_color": theme.get("title_color", "#ffffff"),
            "subtitle_color": theme.get("subtitle_color", "#e0e0e0"),
            "subtitle_position": theme.get("subtitle_pos", "bottom"),  # bottom, center
            "subtitle_fontsize": theme.get("subtitle_fontsize", 28),
            "text_overlay": True,
            "segments": segments,
            "compose_method": "ffmpeg_audiogram",
        }

        self._save_artifact("visuals", visuals)
        has_text = any(s.get("text") for s in segments)
        print(f"  Visual config: {len(segments)} segments"
              + (f", title='{title[:30]}...'" if title else "")
              + (", +subtitle overlays" if has_text else ", no transcript text"))

        self.state.mark_completed("generate_visuals", {
            "segments": len(segments),
            "has_subtitles": has_text,
        })
        return True

    # -----------------------------------------------------------------------
    # Shared steps: compose + publish
    # -----------------------------------------------------------------------
    def step_compose(self) -> bool:
        """Compose final video using the configured runtime engine.

        Supports:
        - Remotion (npx remotion render)
        - FFmpeg (direct concat/filter)
        - HyperFrames (delegated to pipeline_runner.py)
        """
        if not self.force and self.state.is_passed("compose"):
            if self.output_file.exists():
                print("  [SKIPPED] Already composed")
                return True

        self.state.mark_started("compose")
        compose_engine = self.runtime_config.get("compose_engine", "remotion")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        render_output = self.temp_dir / "render_raw.mp4"

        if compose_engine == "remotion":
            success = self._compose_remotion(render_output)
        elif compose_engine == "ffmpeg":
            success = self._compose_ffmpeg(render_output)
        else:
            print(f"  Unknown compose engine: {compose_engine}")
            success = False

        if not success or not render_output.exists():
            self.state.mark_failed("compose", f"{compose_engine} composition failed")
            return False

        # Copy to output
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(render_output, self.output_file)
        size_mb = render_output.stat().st_size / 1024 / 1024
        print(f"  Composed: {self.output_file} ({size_mb:.1f} MB)")

        self.state.mark_completed("compose", {
            "output": str(self.output_file),
            "size_mb": round(size_mb, 1),
            "engine": compose_engine,
        })
        return True

    def _compose_remotion(self, output_path: Path) -> bool:
        """Compose using Remotion renderer."""
        composer_dir = OPENMONTAGE_SRC / "remotion-composer"

        # Build Remotion props from artifacts
        scene_plan = self._load_artifact("scene_plan")
        segment_plan = self._load_artifact("segment_plan")
        clip_selection = self._load_artifact("clip_selection")

        # Determine which props to use based on pipeline
        props = self._build_remotion_props(scene_plan, segment_plan, clip_selection)
        props_file = self.temp_dir / "remotion_props.json"
        with open(props_file, 'w', encoding='utf-8') as f:
            json.dump(props, f, ensure_ascii=False, indent=2)

        # Determine composition ID based on pipeline
        composition_id = "Explainer"
        if self.pipeline_name == "clip-factory":
            composition_id = "Explainer"  # Reuse Explainer for now
        elif self.pipeline_name == "podcast-repurpose":
            composition_id = "Explainer"

        npx_cmd = NPX_BIN
        if npx_cmd == "npx" and not shutil.which("npx"):
            print("  ERROR: npx not found")
            return False

        cmd = [
            npx_cmd, "remotion", "render",
            "src/index.tsx", composition_id,
            str(output_path),
            "--props", str(props_file),
            "--codec", "h264",
        ]

        print(f"  Rendering via Remotion ({composition_id})...")
        rc = self._run_subprocess(cmd, cwd=str(composer_dir), desc="Remotion render")
        return rc == 0

    def _compose_ffmpeg(self, output_path: Path) -> bool:
        """Compose using FFmpeg (concat clips or audiogram)."""
        if self.pipeline_name == "clip-factory":
            return self._ffmpeg_concat_clips(output_path)
        elif self.pipeline_name == "podcast-repurpose":
            return self._ffmpeg_audiogram(output_path)
        else:
            # Default: try Remotion first, fallback to FFmpeg
            print("  FFmpeg compose: no specific pipeline, attempting basic render")
            return False

    def _ffmpeg_concat_clips(self, output_path: Path) -> bool:
        """Concatenate extracted clips into a single video."""
        extracted = self._load_artifact("extracted_clips")
        if not extracted:
            return False

        clips = extracted.get("clips", [])
        if not clips:
            return False

        # Create concat file
        concat_file = self.temp_dir / "concat_list.txt"
        with open(concat_file, 'w', encoding='utf-8') as f:
            for clip in clips:
                # FFmpeg concat needs forward slashes on Windows
                file_path = clip["file"].replace("\\", "/")
                f.write(f"file '{file_path}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(output_path),
        ]
        print(f"  Concatenating {len(clips)} clips...")
        rc = self._run_subprocess(cmd, desc="FFmpeg concat")
        return rc == 0

    def _ffmpeg_audiogram(self, output_path: Path) -> bool:
        """Create enhanced audiogram video from audio source.

        Features:
          - Gradient background (two-color blend)
          - Centered waveform visualization
          - Title text overlay at top
          - Subtitle text from transcript segments at bottom
        """
        visuals = self._load_artifact("visuals")
        if not visuals:
            return False

        source_audio = visuals.get("source_audio", "")
        duration = visuals.get("duration", 0)
        bg_color = visuals.get("background_color", "#1a1a2e")
        wave_color = visuals.get("waveform_color", "#e94560")
        wave_style = visuals.get("waveform_style", "cline")
        title = visuals.get("title", "")
        segments = visuals.get("segments", [])
        subtitle_fontsize = visuals.get("subtitle_fontsize", 28)

        # Build filter chain
        filter_parts = []

        # 1. Background: gradient or solid
        use_gradient = visuals.get("background_gradient", True)
        if use_gradient:
            grad_colors = visuals.get("gradient_colors", ["#1a1a2e", "#16213e"])
            c1 = grad_colors[0].lstrip("#")
            c2 = grad_colors[1].lstrip("#")
            # Create vertical gradient using blend
            filter_parts.append(
                f"color=c=0x{c1}:s=1920x1080:d={duration}[bg1];"
                f"color=c=0x{c2}:s=1920x1080:d={duration}[bg2];"
                f"[bg1][bg2]blend=all_expr='A*(Y/H)+B*(1-Y/H)'[bg]"
            )
        else:
            filter_parts.append(
                f"color=c=0x{bg_color.lstrip('#')}:s=1920x1080:d={duration}[bg]"
            )

        # 2. Waveform overlay (centered, 400px tall)
        filter_parts.append(
            f"[0:a]showwaves=s=1200x400:mode={wave_style}:rate=30"
            f":colors=0x{wave_color.lstrip('#')}[wave]"
        )

        # Chain: bg + wave overlay
        filter_parts.append(
            f"[bg][wave]overlay=(W-w)/2:(H-h)/2-50[vw]"
        )
        current_label = "[vw]"

        # 3. Title text at top (if available)
        if title:
            # Escape special chars for FFmpeg drawtext
            safe_title = title.replace("'", "\u2019").replace(":", " ").replace("\\", "/")[:50]
            filter_parts.append(
                f"{current_label}drawtext=text='{safe_title}'"
                f":fontsize=42:fontcolor=white"
                f":x=(w-text_w)/2:y=80"
                f":fontfile=C\\\\:/Windows/Fonts/msyh.ttc"
                f"[vt]"
            )
            current_label = "[vt]"

        # 4. Subtitle text from segments at bottom
        # Generate SRT file for subtitle overlay
        has_subtitles = any(s.get("subtitle_text") or s.get("text") for s in segments)
        if has_subtitles:
            srt_file = self.temp_dir / "audiogram_subtitles.srt"
            with open(srt_file, 'w', encoding='utf-8') as f:
                for i, seg in enumerate(segments):
                    text = seg.get("subtitle_text") or seg.get("text", "")
                    if not text:
                        continue
                    start = seg.get("start", 0)
                    end = seg.get("end", start + 5)
                    # SRT timestamp format: HH:MM:SS,mmm
                    sh, sm, ss = int(start // 3600), int((start % 3600) // 60), start % 60
                    eh, em, es = int(end // 3600), int((end % 3600) // 60), end % 60
                    f.write(f"{i+1}\n")
                    f.write(f"{sh:02d}:{sm:02d}:{ss:06.3f} --> {eh:02d}:{em:02d}:{es:06.3f}\n".replace(".", ","))
                    # Truncate long lines for readability
                    display_text = text if len(text) <= 40 else text[:37] + "..."
                    f.write(f"{display_text}\n\n")

            srt_path = str(srt_file).replace("\\", "/").replace(":", "\\\\:")
            filter_parts.append(
                f"{current_label}subtitles={srt_path}"
                f":force_style='FontSize={subtitle_fontsize},"
                f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                f"Outline=2,Shadow=1,MarginV=80,"
                f"Alignment=2'"
                f"[vs]"
            )
            current_label = "[vs]"

        # Final output mapping
        full_filter = ";".join(filter_parts)

        # Single input: the audio file. Background colors are generated inline in filter_complex.
        cmd = [
            "ffmpeg", "-y",
            "-i", source_audio,
            "-filter_complex", full_filter,
            "-map", current_label, "-map", "0:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            str(output_path),
        ]

        features = []
        if use_gradient:
            features.append("gradient bg")
        features.append(f"{wave_style} waveform")
        if title:
            features.append("title")
        if has_subtitles:
            features.append("subtitles")

        print(f"  Creating audiogram ({duration:.0f}s) [{', '.join(features)}]...")
        rc = self._run_subprocess(cmd, desc="FFmpeg audiogram")
        return rc == 0

    def _build_remotion_props(self, scene_plan, segment_plan, clip_selection) -> dict:
        """Build Remotion composition props matching Explainer component's Cut interface.

        Each cut requires: id, source, in_seconds, out_seconds, type, and type-specific props.
        Uses visual_type from scene_plan for diverse Remotion component types.
        """
        cuts = []
        theme = "flat-motion-graphics"

        if scene_plan:
            scenes = scene_plan.get("scenes", [])
            for i, scene in enumerate(scenes):
                in_s = scene.get("start_time", 0)
                out_s = scene.get("end_time", in_s + 5)
                narration = scene.get("narration", "")
                heading = scene.get("heading", "")
                visual_type = scene.get("visual_type", "text_card")

                cut = {
                    "id": f"scene_{i+1}",
                    "source": "",
                    "type": visual_type,
                    "in_seconds": in_s,
                    "out_seconds": out_s,
                }

                # Type-specific props
                if visual_type == "hero_title":
                    cut["text"] = heading or narration[:60]
                    cut["subtitle"] = narration[:120] if narration else ""
                elif visual_type == "stat_card":
                    # Extract stat data from narration
                    import re
                    numbers = re.findall(r'\d+[%％万亿倍]?', heading + narration)
                    cut["text"] = heading or narration[:80]
                    cut["value"] = numbers[0] if numbers else ""
                    cut["description"] = narration[:120]
                elif visual_type == "comparison":
                    cut["text"] = heading or narration[:80]
                    cut["subtitle"] = narration[:200]
                elif visual_type == "callout":
                    cut["text"] = heading or narration[:100]
                    cut["subtitle"] = narration[:160] if narration else ""
                elif visual_type == "image":
                    cut["text"] = heading
                    cut["source"] = scene.get("image_path", "")
                else:  # text_card default
                    cut["text"] = narration[:200] if narration else heading

                cuts.append(cut)
        elif segment_plan:
            segments = segment_plan.get("segments", [])
            for i, seg in enumerate(segments):
                in_s = seg.get("start", 0)
                out_s = seg.get("end", in_s + 15)
                text = seg.get("text", "")
                cuts.append({
                    "id": f"seg_{i+1}",
                    "source": "",
                    "type": "text_card" if text else "hero_title",
                    "in_seconds": in_s,
                    "out_seconds": out_s,
                    "text": text[:200] if text else f"Segment {seg.get('segment_id', i+1)}",
                })
        elif clip_selection:
            clips = clip_selection.get("clips", [])
            t = 0
            for i, clip in enumerate(clips):
                duration = clip.get("end", 30) - clip.get("start", 0)
                cuts.append({
                    "id": f"clip_{i+1}",
                    "source": "",
                    "type": "hero_title",
                    "in_seconds": t,
                    "out_seconds": t + duration,
                    "text": clip.get("label", f"Clip {clip.get('clip_id', i+1)}"),
                })
                t += duration

        if not cuts:
            cuts = [{
                "id": "default",
                "source": "",
                "type": "hero_title",
                "in_seconds": 0,
                "out_seconds": 5,
                "text": "OpenMontage Output",
                "subtitle": "No scenes defined",
            }]

        return {
            "theme": theme,
            "cuts": cuts,
            "overlays": [],
            "captions": [],
            "audio": {},
        }

    def step_publish(self) -> bool:
        """Final delivery validation: check output file, naming, and completeness."""
        if not self.force and self.state.is_passed("publish"):
            print("  [SKIPPED] Already passed")
            return True

        self.state.mark_started("publish")
        issues = []

        # Check output file
        if not self.output_file.exists():
            issues.append(f"Output file missing: {self.output_file}")
        else:
            # Verify with ffprobe
            probe = subprocess.run(
                [FFPROBE_BIN, "-v", "quiet", "-show_format", "-of", "json", str(self.output_file)],
                capture_output=True, text=True, encoding='utf-8', errors='replace'
            )
            try:
                data = json.loads(probe.stdout)
                duration = float(data["format"]["duration"])
                size_mb = self.output_file.stat().st_size / 1024 / 1024
                print(f"  Output: {self.output_file}")
                print(f"  Duration: {duration:.1f}s, Size: {size_mb:.1f} MB")
            except (json.JSONDecodeError, KeyError):
                issues.append("ffprobe could not read output file")

        # Check naming convention
        output_name = self.output_file.name
        if any(kw in output_name for kw in ["final", "v01", "render", "raw", "layoutfix"]):
            issues.append(f"Output name contains technical term: {output_name}")

        if issues:
            for issue in issues:
                print(f"  ERROR: {issue}")
            self.state.mark_failed("publish", "; ".join(issues))
            return False

        print(f"  Delivery validation passed")
        self.state.mark_completed("publish")
        return True

    # -----------------------------------------------------------------------
    # Main execution
    # -----------------------------------------------------------------------
    def run(self, start_from: Optional[str] = None) -> bool:
        """Execute the full pipeline."""
        print("=" * 60)
        print(f"OPENMONTAGE PIPELINE: {self.pipeline_name}")
        print("=" * 60)
        print(f"  Input:    {self.input_config.get('path', '(none)')}")
        print(f"  Output:   {self.output_file}")
        print(f"  Temp:     {self.temp_dir}")
        print(f"  Runtime:  {self.runtime_config.get('compose_engine', 'remotion')}")
        print(f"  Budget:   ${self.budget_config.get('cap_usd', 10)}")
        print()

        # Build step function map
        step_funcs = {
            "preflight": self.step_preflight,
            "parse_input": self.step_parse_input,
            "generate_script": self.step_generate_script,
            "plan_scenes": self.step_plan_scenes,
            "generate_assets": self.step_generate_assets,
            "analyze_source": self.step_analyze_source,
            "select_clips": self.step_select_clips,
            "extract_clips": self.step_extract_clips,
            "enhance": self.step_enhance,
            "transcribe_audio": self.step_transcribe_audio,
            "plan_segments": self.step_plan_segments,
            "generate_visuals": self.step_generate_visuals,
            "compose": self.step_compose,
            "publish": self.step_publish,
        }

        # Determine start point
        start_idx = 0
        if start_from and start_from in self.step_names:
            start_idx = self.step_names.index(start_from)
        elif not self.force:
            last_fail = self.state.last_failed_step()
            if last_fail and last_fail in self.step_names:
                start_idx = self.step_names.index(last_fail)
                print(f"Resuming from failed step: {last_fail}")
                print()

        # Execute steps
        for i, (step_name, step_label) in enumerate(self.steps_def):
            if i < start_idx:
                continue

            print(f"--- Step {i+1}/{len(self.steps_def)}: {step_label} ---")
            func = step_funcs.get(step_name)
            if not func:
                print(f"  ERROR: No implementation for step '{step_name}'")
                self.state.mark_failed(step_name, "No implementation")
                return False

            ok = func()
            if not ok:
                print(f"\nPIPELINE FAILED at: {step_label}")
                print(f"Fix the issue and re-run with --resume")
                return False
            print()

        # Success
        print("=" * 60)
        print("PIPELINE COMPLETE")
        print("=" * 60)
        print(f"  Output: {self.output_file}")
        self.state.print_status()
        return True

    def run_summary(self) -> str:
        """Return a summary string for workflow_router integration."""
        pipeline = self.pipeline_name
        steps = len(self.steps_def)
        input_path = self.input_config.get("path", "(none)")
        return (
            f"OpenMontage '{pipeline}' pipeline configured ({steps} steps).\n"
            f"  Input: {input_path}\n"
            f"  Output: {self.output_file}\n"
            f"  Run with: python openmontage_runner.py --config <config.json>"
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OpenMontage pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python openmontage_runner.py --config explainer_demo.json
  python openmontage_runner.py --config clip_demo.json --resume
  python openmontage_runner.py --config podcast_demo.json --status
  python openmontage_runner.py --config explainer_demo.json --step compose
  python openmontage_runner.py --config explainer_demo.json --force
        """
    )
    parser.add_argument("--config", required=True,
                        help="OpenMontage config JSON file")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last failed step")
    parser.add_argument("--step", help="Run from a specific step")
    parser.add_argument("--status", action="store_true",
                        help="Show current pipeline state and exit")
    parser.add_argument("--force", action="store_true",
                        help="Re-run all steps regardless of previous state")

    args = parser.parse_args()

    # Load config (search subdirectories if not absolute)
    config_path = Path(args.config)
    if not config_path.is_absolute():
        base = CONFIG_DIR / "config"
        resolved = None
        for subdir in ("", "openmontage", "pipelines", "system"):
            candidate = base / subdir / config_path if subdir else base / config_path
            if candidate.exists():
                resolved = candidate
                break
        config_path = resolved or (base / config_path)

    if not config_path.exists():
        print(f"ERROR: Config not found: {config_path}")
        sys.exit(1)

    runner = OpenMontageRunner(
        config_path=str(config_path),
        force=args.force,
    )

    if args.status:
        runner.state.print_status()
        return

    start_from = None
    if args.step:
        start_from = args.step
    elif args.resume:
        last_fail = runner.state.last_failed_step()
        if last_fail:
            start_from = last_fail
            print(f"Resuming from: {last_fail}")
        else:
            print("No failed step found, running full pipeline.")

    success = runner.run(start_from=start_from)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
