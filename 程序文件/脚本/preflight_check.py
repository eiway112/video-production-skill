#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Pre-render validation gate.

Run BEFORE every HyperFrames render to catch errors early.
Prevents the most expensive mistake: running a full render only to discover
a problem that could have been caught in 2 seconds with static checks.

Checks:
  1. JSON config syntax validation
  2. HTML image reference integrity (all src= paths resolve to actual files)
  3. GSAP timestamp bounds (all time values within data-duration range)
  4. Config-HTML synchronization (scene boundaries, duration)
  5. .bak file consistency (backup exists and is valid)

Usage:
  python preflight_check.py --config config/hotel_pw_enhance.json --html hotel-partition-wall
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Fix GBK encoding issue in PowerShell
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Import shared GSAP time resolution utilities
sys.path.insert(0, str(Path(__file__).parent))
import _script_env
from _gsap_time_utils import parse_t_block as _parse_t_block
from _gsap_time_utils import resolve_script_times as _resolve_script_times
from _gsap_time_utils import parse_scene_map as _parse_scene_map
from _narration_lint import scan_config_narrations as _lint_scan_config
from _narration_lint import format_findings as _lint_format_findings

# Import script interface
import time
from script_interface import Config, Result, Logger, EXIT_CODE

# Unified validation framework — shared primitives for common rules
# (file existence / JSON structure / size range / backup pairing).
# Rule catalog: 程序文件/配置/validation_rules.yaml
import unified_validator as uv


# ── Gate Mode Definitions ──
# audit:  Check / ClosePreview / CloseExecute — warnings only, never block
# render: SampleQA / FinalRender — errors block, same structured log
GATE_MODES = ("audit", "render")

# Checks whose failures are DOWNGRADED to warnings in audit mode.
# These are "production quality" issues — critical for new renders,
# irrelevant for already-delivered projects.
RENDER_CRITICAL_CHECKS = {
    "placeholder",        # check 10: placeholder images / empty src / data URI
    "subtitle_safe",      # check 11: missing subtitle-safe div
    "image_integrity",    # check 3:  image NOT FOUND / empty src
    "scene_visibility",   # check 7:  black frame (opacity:0 w/o GSAP)
    # check 7b: 元素级同款。scene_visibility 只量 #sceneN，且**契约型 HTML 下整体
    # return 跳过**（本仓项目全部带 data-scene-*，故那条对现网项目是空转的）。
    # 2026-10-13 复盘：6 处容器带 .rv 而入场的 tween 只打其子元素 → 父层 opacity
    # 恒 0、整块子树从未出现在成片里，终检 20 项与审计 6 维全绿未拦。
    "element_reveal",
    # check 7c: 章级常驻条结构闭环（cl/cf 配对 · tag 自报归属 · 点亮锚）。手写
    # HTML 三个环节各自独立书写，任一处漏改即成片观感与结构不符——实证
    # building-perf-craft 曾缺第 6 章刻度（tag 有 06 无 cf6）。
    "chapter_bar",
    # check 7d: 画面文本中文语境夹半角标点（半角引号为主要形态）。字幕侧有 SRT
    # 口径而画面文本无；手写 HTML 正文是唯一载体（实证七域系列 _修订01 三处，
    # 字幕侧扫描 0 命中）。
    "text_punct",
    # check 4b: GSAP 主时间轴未注册进 window.__timelines。渲染器只驱动注册表
    # （全局 tl 变量它不认），缺注册即每帧取 t=0；预览与空窗预判的 seek 腿读的恰是
    # 全局 tl，故两条都看不见——只有全量渲完 + 码率门禁才暴露（一次 13 分钟级白渲）。
    "timeline_registry",
    "scene_contract",     # check 0:  contract declaration mismatch
    "cue_declaration",    # check 0b: data-cue-at 词在该场旁白里定位不到（接线断，永不亮）
    "design_rules",       # check 8:  independent timeline, missing opacity:0
    "narration_digits",   # check 13: Chinese numerals for quantitative values in narration
    # check 13b: 停顿声明（pause_marks）在 TTS 之前复核——越界档位/锚词落不到/
    # 引擎不支持这些形态若留到 TTS 步炸，已是白扣一轮账号余额（缓存被清后）。
    # 判据本体住 pause_marks.plan_scene，预检与生效路径同一裁定源。
    "pause_declaration",
    "asset_signoff",      # check 14: 屏显/旁白与位点1 素材确认单分叉
}


ROOT = _script_env.ROOT
HTML_BASE = ROOT / "程序文件" / "源码" / "hyperframes"
CONFIG_BASE = ROOT / "程序文件" / "配置" / "config"


class PreflightResult:
    def __init__(self, mode="render"):
        self.mode = mode
        self.errors = []
        self.warnings = []
        self._log_entries = []  # structured log for JSON output
        # 判据自报计数：使"跑过且判了几处"成为正证据，而非由测试侧另算一遍
        # （第二个取数面会与判据漂移，本仓 A06 同族）。由各 check_* 自行登记。
        self.counts = {}
        # 内容画像（wp 批次复盘：退出纪律首次执行）：text_only 纯文字视频
        # 本就无图片素材/设计产物，相关 4 条 WARN 属预期而非异常，降为 INFO，
        # 消除常驻警告噪音（信号疲劳会淹没真问题）。config 声明 content_profile。
        self.content_profile = ""

    def _log(self, level, check_id, msg):
        self._log_entries.append({
            "level": level,
            "check_id": check_id,
            "message": msg,
            "timestamp": datetime.now().isoformat(timespec='seconds'),
        })

    def error(self, msg, check_id="unknown"):
        """Record an error. In audit mode, render-critical errors become warnings."""
        if self.mode == "audit" and check_id in RENDER_CRITICAL_CHECKS:
            self.warnings.append(msg)
            self._log("WARN_AUDIT", check_id, msg)
            print(f"  ⚠ AUDIT-WARN: {msg}")
        else:
            self.errors.append(msg)
            self._log("ERROR", check_id, msg)
            print(f"  ✗ ERROR: {msg}")

    def warn(self, msg, check_id="unknown", profile_exempt=False):
        # profile_exempt：该警告对 text_only 画像属预期，降为 INFO 不计入 warnings
        if profile_exempt and self.content_profile == "text_only":
            self.info(f"{msg} (text_only 画像预期内，降级)", check_id)
            return
        self.warnings.append(msg)
        self._log("WARN", check_id, msg)
        print(f"  ⚠ WARN: {msg}")

    def info(self, msg, check_id="unknown"):
        """仅记录不告警：供画像降级的预期内提示使用，不计入 warnings 总数。"""
        self._log("INFO", check_id, msg)
        print(f"  · INFO: {msg}")

    def ok(self, msg, check_id="unknown"):
        self._log("OK", check_id, msg)
        print(f"  ✓ {msg}")

    @property
    def passed(self):
        return len(self.errors) == 0

    def write_log(self, log_path: Path, config_name: str = "", html_project: str = ""):
        """Write structured JSON audit/render log."""
        log_data = {
            "gate_mode": self.mode,
            "config": config_name,
            "html_project": html_project,
            "timestamp": datetime.now().isoformat(timespec='seconds'),
            "result": "PASS" if self.passed else "FAIL",
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "entries": self._log_entries,
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        print(f"\n  Log written: {log_path}")


def check_production_readiness(project_dir: Path, cfg: dict, r: PreflightResult):
    """Upstream gate: verify design artifacts exist before production proceeds.

    This is the FIRST check — it catches the most expensive class of errors:
    starting production without proper design preparation.

    Checks:
    - Project HTML directory exists
    - Photo registry exists when photos/ directory is present
    - Design artifacts declared in config are verified on disk
    - Asset scan evidence exists (inventory JSON)
    """
    print("\n[1/12] Production Readiness (Upstream Gate)")

    # 1. Project directory
    if not uv.check_path_exists(project_dir).ok:
        r.error(f"Project directory not found: {project_dir}")
        return

    html_file = project_dir / "index.html"
    if not uv.check_path_exists(html_file).ok:
        r.error(f"index.html not found in project directory")
        return
    r.ok(f"Project directory: {project_dir.name}")

    # 2. Photo registry — HARD requirement when photos/ exists
    photos_dir = project_dir / "photos"
    registry_file = project_dir / "photo-registry.json"
    if photos_dir.exists() and any(photos_dir.iterdir()):
        photo_count = len([f for f in photos_dir.iterdir() if f.is_file()])
        if registry_file.exists():
            reg_outcome = uv.check_json_file(registry_file)
            if reg_outcome.ok:
                reg = reg_outcome.data["content"]
                r.ok(f"Photo registry: {registry_file.name} "
                     f"({len(reg.get('source_photos', []))} source photos, "
                     f"{photo_count} project photos)")
            else:
                r.error("Photo registry exists but is invalid (JSON parse error)")
        else:
            r.error(
                f"photos/ has {photo_count} images but NO photo-registry.json — "
                f"run generate_photo_registry.py before production"
            )
    else:
        r.ok("No photos/ directory (photo registry not required)")

    # 3. Design artifacts from config
    # 决策三（prefab 复盘，2026-08-07）：设计产物是强制上游环节——
    # 无分镜设计就直接生产，内容必然沦为旧素材拼凑（prefab-agent-launch
    # 实证）。缺失/失效一律阻断，最小达标路径见下方 error 文案。
    design = cfg.get('design_artifacts', {})
    if design:
        for artifact_type, artifact_path in design.items():
            p = Path(artifact_path)
            if not p.is_absolute():
                # Resolve relative to project root
                p = ROOT / p
            if p.exists():
                r.ok(f"Design artifact '{artifact_type}': {p.name}")
            else:
                # 声明了但指向不存在的文件 = 失效指针，硬报错禁止静默放行
                r.error(
                    f"Declared design artifact '{artifact_type}' not found: "
                    f"{artifact_path} — fix the pointer or remove the declaration"
                )
    else:
        # Auto-detect common design artifacts in project directory
        auto_found = []
        for pattern in ["beat_table*", "storyboard*", "*分镜*", "*beat*",
                        "narration.design.json"]:
            matches = list(project_dir.glob(pattern))
            auto_found.extend(matches)
        # 去重：同一文件可能命中多个模式（如 storyboard_分镜设计.md 首跑实证）
        auto_found = list(dict.fromkeys(auto_found))

        if auto_found:
            for f in auto_found:
                r.ok(f"Design artifact (auto-detected): {f.name}")
        else:
            r.error(
                "No design artifacts found — production blocked. "
                "Minimum requirement: place storyboard_<name>.md (分镜设计) or "
                "narration.design.json in the project directory, or declare "
                "'design_artifacts' in config. Template: AI视频制作工作流模板/"
                "分镜设计模板_AI导演模式.md"
            )

    # 4. Asset scan evidence
    inventory_file = project_dir / "asset_inventory.json"
    if uv.check_path_exists(inventory_file).ok:
        r.ok(f"Asset inventory: {inventory_file.name}")
    else:
        # Non-blocking: asset scan is recommended but not mandatory
        r.warn(
            "No asset_inventory.json — run asset_scanner.py --project "
            f"{project_dir.name} --json to generate",
            profile_exempt=True
        )


def check_json_config(config_path: Path, r: PreflightResult):
    """Validate JSON config file syntax and structure."""
    print("\n[2/12] JSON Config Validation")

    if not uv.check_path_exists(config_path).ok:
        r.error(f"Config file not found: {config_path}")
        return None

    cfg_outcome = uv.check_json_file(config_path)
    if cfg_outcome.ok:
        cfg = cfg_outcome.data["content"]
        r.ok(f"JSON syntax valid: {config_path.name}")
    else:
        r.error(f"JSON syntax error in {config_path.name}: {cfg_outcome.data['error']}")
        return None

    # Required fields (rule: config_required_fields)
    if 'video_duration' not in cfg:
        r.error("Missing 'video_duration' field")
    else:
        r.ok(f"video_duration = {cfg['video_duration']}")

    # 场景源：narration_source 指针优先（P0-03 单一权威源）。解析顺序与
    # enhance_video_audio.load_config 一致：绝对路径 → HTML 项目目录 → 配置目录。
    # 声明了指针即以指针为权威：解析失败直接报错，禁止静默回退内嵌 scenes。
    if 'narration_source' in cfg:
        raw_source = str(cfg['narration_source'])
        candidates = []
        if Path(raw_source).is_absolute():
            candidates.append(Path(raw_source))
        else:
            html_project = (cfg.get('paths') or {}).get('html_project', '')
            if html_project:
                candidates.append(HTML_BASE / html_project / raw_source)
            candidates.append(config_path.parent / raw_source)
        source_path = next((c for c in candidates if c.is_file()), None)
        if source_path is None:
            tried = '; '.join(str(c) for c in candidates)
            r.error(f"narration_source='{raw_source}' declared but not found (tried: {tried})")
        else:
            src_outcome = uv.check_json_file(source_path)
            if not src_outcome.ok:
                r.error(f"narration_source JSON syntax error: {source_path.name}: {src_outcome.data['error']}")
            elif not src_outcome.data['content'].get('scenes'):
                r.error(f"narration_source has no valid 'scenes': {source_path.name}")
            else:
                cfg['scenes'] = src_outcome.data['content']['scenes']
                r.ok(f"narration_source resolved: {source_path.name} ({len(cfg['scenes'])} scenes)")

    if 'scenes' not in cfg:
        # 指针分支失败时已记录具体错误，避免重复报 Missing 'scenes'
        if 'narration_source' not in cfg:
            r.error("Missing 'scenes' field")
    else:
        scenes = cfg['scenes']
        r.ok(f"{len(scenes)} scenes defined")

        # Scene boundaries + continuity (rule: scene_bounds_valid)
        for issue in uv.check_scene_bounds(cfg).data["issues"]:
            if issue["kind"] == "missing_bounds":
                r.error(f"Scene {issue['scene_id']}: missing 'start' or 'end'")
            elif issue["kind"] == "inverted":
                r.error(f"Scene {issue['scene_id']}: end ({issue['end']}) <= start ({issue['start']})")
            elif issue["kind"] == "gap":
                r.error(
                    f"Scene gap: scene {issue['prev_id']} ends at {issue['prev_end']}, "
                    f"scene {issue['curr_id']} starts at {issue['curr_start']} "
                    f"(delta={issue['curr_start'] - issue['prev_end']:.1f}s)"
                )

    # 单一权威源（AGENTS.md §1）：config 不得内嵌场景/时间轴数据。
    # 该节无任何语义消费者：主链的场景/时间轴一律来自 narration.json + HTML T-block；
    # 唯一按数据读取它的 preflight_simple.py（不在流水线内）已于 2026-10-16 作为零引用
    # 死件删除；scene_patch_render.py:123
    # 只是把它并入 config 哈希（"渲染相关配置变更"的代理，不解读其内容）。
    # 2026-08-22 曾因它与 narration/HTML 分叉而误导 verify 报错定位。
    # 空节不判（历史脚手架脚本 fix_project_structures.py 会写空壳）。
    stale_timeline = cfg.get('timeline') or {}
    if stale_timeline.get('t_block') or stale_timeline.get('scene_metadata'):
        r.error(
            "config declares a non-empty 'timeline' block — scene/timeline authority "
            "is narration.json + HTML T-block; no script reads this block as data and "
            "it drifts silently. Delete the 'timeline' key (AGENTS.md §1 单一权威源)"
        )

    # Check .bak exists (rule: config_backup_pair)
    pair = uv.check_backup_pair(config_path, validate="json")
    bak_path = pair.data["bak_path"]
    if pair.data["state"] == "valid":
        r.ok(f"Backup config valid: {bak_path.name}")
    elif pair.data["state"] == "invalid":
        r.warn(f"Backup config has syntax error: {bak_path.name}")
    else:
        r.warn(f"No backup config found: {bak_path.name}")

    return cfg


def _load_path_field_extensions():
    """扩展名权威映射（唯一源 delivery_gate_rules.json → path_field_extensions）。

    返回 (mapping, error)：任何"读不到/读不懂/节残缺"形态都把 error 填上，由调用方
    判 ERROR 并点名断在哪条腿——不得由取数断链短路成一个干净的通过。
    """
    path = CONFIG_BASE / "quality" / "delivery_gate_rules.json"
    if not path.exists():
        return None, f"权威源缺失: {path}"
    try:
        rules = json.loads(path.read_text(encoding='utf-8')).get("path_field_extensions")
    except (json.JSONDecodeError, OSError) as e:
        return None, f"权威源不可读（{type(e).__name__}）: {path}"
    if not isinstance(rules, dict):
        return None, "权威源缺 path_field_extensions 节"
    mapping = {k: v for k, v in rules.items()
               if not k.startswith("$") and k != "description"}
    bad = sorted(k for k, v in mapping.items()
                 if not isinstance(v, str) or not v.startswith("."))
    if not mapping:
        return None, "path_field_extensions 节内无字段声明"
    if bad:
        return None, f"节内值形态非法（须为以 . 开头的扩展名）: {bad}"
    return mapping, None


def _dig_field(cfg, dotted):
    """按点号路径取值；返回 (值, 该路径是否存在)。"""
    cur = cfg
    for part in dotted.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def check_config_path_fields(cfg, r: PreflightResult):
    """CRITICAL: config 路径字段的扩展名须等于权威源声明值（check 2b）。

    存在理由（另一条会话实证）：更新 config 的脚本把 paths.video_name 写成 .srt，
    两支成片遂以 .srt 名落进 成果文件/视频/ 并在后处理步失败——该键此前无任何校验。
    字段缺位或值为空一律不判（它们按 config 形态是可选的，缺字段属项目形态而非违规）；
    只在字段在场且非空时比对结尾扩展名。
    """
    print("\n[2b/12] Config Path Field Extensions")
    check_id = "path_ext"
    mapping, err = _load_path_field_extensions()
    if err:
        r.error(f"扩展名权威源无从读取，本项未核对（{err}）", check_id)
        return
    if not cfg:
        r.error("config 未加载，路径字段扩展名无从核对（取数断链不得读成通过）", check_id)
        return

    checked = 0
    bad_fields = []
    for dotted, want in sorted(mapping.items()):
        value, present = _dig_field(cfg, dotted)
        if not present or value is None or (isinstance(value, str) and not value.strip()):
            continue
        checked += 1
        if not str(value).lower().endswith(want.lower()):
            bad_fields.append(dotted)
            r.error(f"{dotted} 扩展名应为 {want}，实为 {value!r}"
                    " — 错名产物会以该扩展名落进成果目录并在后处理步失败", check_id)
    r.counts[check_id] = {"checked": checked, "declared": len(mapping),
                          "violations": len(bad_fields)}
    if not checked:
        r.info("本 config 未声明任何受校验的路径字段（NOT_APPLICABLE，非通过）", check_id)
    elif not bad_fields:
        # 有违规时不再印通过行：判红与"全部一致"同屏出现即把违规洗白成也通过
        r.ok(f"{checked}/{len(mapping)} 个路径字段扩展名与权威源一致", check_id)


def check_html_images(html_path: Path, r: PreflightResult):
    """Validate all image references in HTML resolve to actual files."""
    print("\n[3/12] HTML Image Reference Check")

    if not html_path.exists():
        r.error(f"HTML file not found: {html_path}")
        return

    content = html_path.read_text(encoding='utf-8')
    html_dir = html_path.parent

    # Find all src= attributes pointing to images
    src_pattern = re.compile(r'src="([^"]+\.(jpg|jpeg|png|gif|webp|svg))"', re.IGNORECASE)
    matches = src_pattern.findall(content)

    if not matches:
        r.warn("No image references found in HTML", profile_exempt=True)
        return

    found = 0
    for src, ext in matches:
        img_path = html_dir / src
        # rule: image_min_size (<10KB suspected placeholder)
        size_check = uv.check_file_size_range(img_path, min_bytes=10 * 1024)
        if size_check.data["exists"]:
            if size_check.data["too_small"]:
                size_kb = size_check.data["size_bytes"] / 1024
                r.warn(f"Image suspiciously small: {src} ({size_kb:.0f}KB) — may be placeholder")
            found += 1
        else:
            r.error(f"Image NOT FOUND: {src} (expected at {img_path})", check_id="image_integrity")

    if found > 0:
        r.ok(f"{found}/{len(matches)} images verified on disk")


def check_gsap_timing(html_path: Path, r: PreflightResult):
    """Extract all GSAP time values and verify they're within data-duration."""
    print("\n[4/12] GSAP Timestamp Bounds Check")

    if not html_path.exists():
        r.error(f"HTML file not found: {html_path}")
        return

    content = html_path.read_text(encoding='utf-8')

    # Extract duration — prefer S-block (authoritative), fall back to data-duration
    script_match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
    s_map = {}
    if script_match:
        s_map = _parse_scene_map(script_match.group(1))

    if s_map:
        duration = s_map['duration']
        r.ok(f"Duration from S-block: {duration}s (authoritative)")
    else:
        dur_match = re.search(r'data-duration="(\d+(?:\.\d+)?)"', content)
        if not dur_match:
            r.error("Cannot find data-duration attribute or S-block")
            return
        duration = float(dur_match.group(1))
        r.ok(f"Duration from data-duration: {duration}s (legacy)")

    # script_match already extracted above for S-block parsing
    if not script_match:
        r.warn("No <script> section found")
        return

    script = script_match.group(1)

    # Parse T-block if present (structured timeline)
    t_map = _parse_t_block(script)
    if t_map:
        r.ok(f"T-block found: {len(t_map)} scene entries (structured timeline)")

    # Extract all time values — resolves both numeric literals and T-block expressions
    time_entries = _resolve_script_times(script, t_map)
    times = [t for t, _ in time_entries]

    if not times:
        r.warn("No GSAP time values found")
        return

    max_time = max(times)
    min_time = min(times)
    out_of_bounds = [t for t in times if t > duration + 0.5]

    r.ok(f"GSAP time range: {min_time:.1f}s - {max_time:.1f}s ({len(times)} time values)")

    if out_of_bounds:
        unique_oob = sorted(set(out_of_bounds))
        r.error(
            f"{len(out_of_bounds)} timestamps exceed data-duration ({duration}s): "
            f"{[f'{t:.1f}' for t in unique_oob[:10]]}"
        )
    else:
        r.ok(f"All {len(times)} timestamps within bounds")

    # Check transition comments vs actual times
    transition_comments = re.findall(r'TRANSITION\s+(\d+)->(\d+):\s*T=(\d+\.?\d*)s', script)
    for from_s, to_s, expected_t in transition_comments:
        expected = float(expected_t)
        # Find the actual transition time near this comment
        # Look for tl.to("#sceneN", ... }, TIME) patterns
        scene_pattern = rf'tl\.to\("#scene{from_s}".*?\}},\s*(\d+\.?\d*)\s*\)'
        actual_match = re.search(scene_pattern, script)
        if actual_match:
            actual = float(actual_match.group(1))
            if abs(actual - expected) > 1.0:
                r.warn(
                    f"Transition {from_s}->{to_s}: comment says T={expected}s, "
                    f"actual tl.to at {actual}s (delta={actual-expected:.1f}s)"
                )


# 渲染器只驱动 window.__timelines 注册表里的时间轴，**全局 tl 变量它不认**
# （hyperframes runtime：注册表为空/根 id 无匹配键时打印
#  "Root timeline not bound — render will freeze at t=0"）。缺注册时渲染器不报错、
#  每帧取 t=0，于是要等一次全量渲染跑完再由后处理码率门禁暴露。预览与空窗预判
#  都看不见这一形态：它们的 seek 腿读的是全局 tl（本仓 69/69 项目都有该变量）。
#
# 两种注册写法在现网并存（2026-10-15 普查：方括号 56 份、点号 12 份），取数面
#  必须同时覆盖——只 grep 方括号字面即把 12 份合法项目判红（取数口径窄于事实）。
_TIMELINE_REGISTRY_PATTERNS = (
    re.compile(r'''window\.__timelines\[\s*["']([^"']+)["']\s*\]\s*='''),
    re.compile(r'window\.__timelines\.([A-Za-z_$][\w$]*)\s*='),
)


def _inline_scripts(content: str):
    """内联 <script> 段（外链 script 标签无正文，不参与注册语句扫描）。"""
    return re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', content, re.DOTALL)


def timeline_registry_keys(body: str):
    """注册表键名（按出现顺序去重）——本判据与探针回退腿共用的唯一取数处。"""
    keys = []
    for pat in _TIMELINE_REGISTRY_PATTERNS:
        for m in pat.finditer(body):
            if m.group(1) not in keys:
                keys.append(m.group(1))
    return keys


def check_timeline_registration(html_path: Path, r: PreflightResult):
    """CRITICAL: GSAP 主时间轴必须注册进 window.__timelines（渲染器的唯一驱动源）。"""
    print("\n[4b/12] Timeline Registry Binding Check")
    check_id = "timeline_registry"
    if not html_path.exists():
        r.error(f"HTML file not found: {html_path}", check_id)
        return

    content = html_path.read_text(encoding='utf-8', errors='replace')
    scripts = _inline_scripts(content)
    if not scripts:
        # 取数断链：没有内联脚本＝无从判断有没有注册，不得读成"没有注册问题"
        r.error("找不到内联 <script> 段，时间轴注册无从核对（取数断链不得读成通过）",
                check_id)
        return
    body = "\n".join(scripts)

    keys = timeline_registry_keys(body)
    has_ctor = re.search(r'\bgsap\.timeline\s*\(', body) is not None
    mentions_registry = 'window.__timelines' in body
    r.counts[check_id] = {
        "registered_keys": len(keys),
        "has_timeline_ctor": has_ctor,
        "mentions_registry": mentions_registry,
    }

    if not keys:
        if has_ctor or mentions_registry:
            # 含 mentions_registry：识别到注册表字样却取不到键名＝形态超出静态识别能力。
            # 按 check_cue_declarations 的既有取舍判 ERROR——预检是阻断位点，"无从核对"
            # 降为 WARN 即把"接线断了"读成"接线没问题"，后果是白付一次全量渲染。
            r.error(
                "GSAP 时间轴未注册进 window.__timelines"
                + ("（识别到该字样的赋值但取不到键名，静态无法核对注册形态）"
                   if mentions_registry else "（有 gsap.timeline 构造、无注册语句）")
                + " — 渲染器只驱动注册表，缺注册将全程冻结在 t=0",
                check_id)
        else:
            r.info("未发现 GSAP 时间轴构造，本项不适用（NOT_APPLICABLE，非通过）",
                   check_id)
        return

    root_ids = re.findall(r'data-root="true"[^>]*?data-composition-id="([^"]+)"'
                          r'|data-composition-id="([^"]+)"[^>]*?data-root="true"',
                          content)
    root_id = next((a or b for a, b in root_ids), None)

    # 根声明了合成 id 却不在注册表里＝渲染器找不到主时间轴（与"整表为空"同一后果）。
    # 键数多少不改变这条，故判在单键回退之前。现网 69/69 根元素无该属性，零误杀。
    if root_id and root_id not in keys:
        r.error(f"根 data-composition-id=\"{root_id}\" 不在注册键 {keys} 中"
                " — 渲染器找不到主时间轴，将冻结在 t=0", check_id)
        return

    if len(keys) == 1:
        r.ok(f"主时间轴已注册：window.__timelines[\"{keys[0]}\"]", check_id)
        return

    # 多键：渲染器按"根 data-composition-id 匹配键，无该属性时取注册表第一个"回退。
    # 插入序是运行时事实，静态扫描无法确证哪个键落在第一位，故只否证不确证。
    if root_id:
        r.ok(f"{len(keys)} 个注册键，根 data-composition-id=\"{root_id}\" 在表中", check_id)
    else:
        r.warn(f"{len(keys)} 个注册键且根元素无 data-composition-id：渲染器按插入序"
               f"取第一个（键名 {keys}），静态无从裁定驱动哪条 —— 请人工确认主时间轴在首",
               check_id)


def check_config_html_sync(cfg: dict, html_path: Path, r: PreflightResult):
    """Verify config and HTML scene metadata are consistent."""
    print("\n[5/12] Config-HTML Synchronization")

    if cfg is None:
        r.warn("Skipping (config not loaded)")
        return

    content = html_path.read_text(encoding='utf-8')

    # Parse S-block if available (authoritative source)
    script_match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
    s_map = {}
    if script_match:
        s_map = _parse_scene_map(script_match.group(1))

    # Duration check — S-block > data-duration > config
    if s_map:
        html_dur = s_map['duration']
        cfg_dur = cfg.get('video_duration', 0)
        if cfg_dur and abs(html_dur - cfg_dur) > 0.5:
            r.error(f"Duration mismatch: S-block={html_dur}s, config={cfg_dur}s (config stale — re-run adjust_timeline)")
        else:
            r.ok(f"Duration aligned: S-block={html_dur}s, config={cfg_dur}s")
    else:
        dur_match = re.search(r'data-duration="(\d+(?:\.\d+)?)"', content)
        html_dur = float(dur_match.group(1)) if dur_match else None
        cfg_dur = cfg.get('video_duration', 0)
        if html_dur and cfg_dur:
            diff = abs(html_dur - cfg_dur)
            if diff > 5.0:
                r.error(f"Duration mismatch: HTML={html_dur}s, config={cfg_dur}s (diff={diff:.1f}s)")
            elif diff > 1.0:
                r.warn(f"Duration drift: HTML={html_dur}s, config={cfg_dur}s (diff={diff:.1f}s)")
            else:
                r.ok(f"Duration aligned: HTML={html_dur}s, config={cfg_dur}s")

    # Cover duration check
    if s_map:
        cover_dur = s_map.get('cover_duration', 0)
        cfg_cover = cfg.get('cover_duration', 0)
        if cfg_cover and abs(cover_dur - cfg_cover) > 0.01:
            r.error(f"Cover duration mismatch: S-block={cover_dur}s, config={cfg_cover}s")
        else:
            r.ok(f"Cover duration: {cover_dur}s")
    else:
        cover_match = re.search(r'data-cover-duration="(\d+(?:\.\d+)?)"', content)
        if cover_match:
            cover_dur = float(cover_match.group(1))
            cfg_cover = cfg.get('cover_duration', 0)
            if cfg_cover and abs(cover_dur - cfg_cover) > 0.01:
                r.error(f"Cover duration mismatch: HTML={cover_dur}, config={cfg_cover}")
            else:
                r.ok(f"Cover duration: {cover_dur}s")


def check_bak_consistency(html_path: Path, r: PreflightResult):
    """Verify .bak backup file exists and is structurally valid."""
    print("\n[6/12] Backup File Consistency")

    bak_path = html_path.with_name('index.html.bak')
    # rule: html_backup_pair
    if not uv.check_path_exists(bak_path).ok:
        r.warn("No index.html.bak found — adjust_timeline.py requires this backup")
        return

    bak_content = bak_path.read_text(encoding='utf-8')
    html_content = html_path.read_text(encoding='utf-8')

    # Check .bak has data-duration
    bak_dur_match = re.search(r'data-duration="(\d+(?:\.\d+)?)"', bak_content)
    if bak_dur_match:
        r.ok(f"Backup data-duration = {bak_dur_match.group(1)}s")
    else:
        r.error("Backup missing data-duration attribute")

    # Check .bak has GSAP script
    if '<script>' in bak_content and 'gsap.timeline' in bak_content:
        r.ok("Backup contains GSAP timeline")
    else:
        r.error("Backup missing GSAP timeline script")

    # Check .bak has same number of scenes
    bak_scenes = len(re.findall(r'id="scene\d+"', bak_content))
    html_scenes = len(re.findall(r'id="scene\d+"', html_content))
    if bak_scenes != html_scenes:
        r.warn(f"Scene count differs: backup={bak_scenes}, current={html_scenes}")
    else:
        r.ok(f"Scene count consistent: {html_scenes} scenes")

    # Check .bak has same images
    bak_imgs = set(re.findall(r'src="([^"]+\.(jpg|png))"', bak_content))
    html_imgs = set(re.findall(r'src="([^"]+\.(jpg|png))"', html_content))
    bak_img_names = {name for name, _ in bak_imgs}
    html_img_names = {name for name, _ in html_imgs}

    if bak_img_names != html_img_names:
        only_bak = bak_img_names - html_img_names
        only_html = html_img_names - bak_img_names
        if only_bak:
            r.warn(f"Images only in backup: {only_bak}")
        if only_html:
            r.warn(f"Images only in current: {only_html}")
    else:
        r.ok(f"Image references consistent: {len(html_img_names)} images")


def _scene_hidden_state(content: str, attrs: str, scene_id) -> tuple:
    """解析场景初始隐藏状态：(inline opacity:0, CSS opacity:0, CSS visibility:hidden)。"""
    css_match = re.search(rf'#scene{scene_id}\s*{{([^}}]*)}}', content)
    inline_opacity_zero = bool(re.search(r'opacity\s*:\s*0(?![.\d])', attrs))
    css_opacity_zero = False
    css_visibility_hidden = False
    if css_match:
        css = css_match.group(1)
        css_opacity_zero = bool(re.search(r'opacity\s*:\s*0(?![.\d])', css))
        css_visibility_hidden = bool(re.search(r'visibility\s*:\s*hidden', css))
    return inline_opacity_zero, css_opacity_zero, css_visibility_hidden


def _cue_unconsumed_keys(html_text: str):
    """CUE-MAP 键中"声明了但没有消费迹象"的部分 → (keys, has_wrapper, has_map)。

    这是**只报不拦**的观测面（2026-10-21 审计批），不是门禁。存在理由：
    `data-cue-at` 能证明"键算出来了"，不能证明"画面真的读了它"——repo-mesh 的
    `s1:治理件为零` 写了 7 个键、脚本只引用 6 个，该 cue 跨三次交付都是死声明，
    而预检/终检/审计全绿。这类缺陷只能靠人读脚本才发现。

    取数口径刻意取宽，宁漏不误报：
      · 页面存在 `cue(...)` / `cueGlow(...)` 包装消费时，key 是运行期拼接
        （scene + ':' + word），静态无法逐键判定——有包装就不再逐键报；
      · 没有 CUE-MAP 块（首次项目，timeline 尚未写入）时无从判定，返回空。
    """
    m = re.search(r'CUE-MAP-START([\s\S]*?)CUE-MAP-END', html_text)
    if not m:
        return [], False, False
    keys = re.findall(r'"([^"]+)"\s*:', m.group(1))
    has_wrapper = bool(re.search(r'\b(?:cue|cueGlow)\s*\(', html_text))
    if has_wrapper:
        return [], True, True
    literal = set(re.findall(r'C\[\s*["\']([^"\']+)["\']\s*\]', html_text))
    return [k for k in keys if k not in literal], False, True


def check_cue_declarations(html_path: Path, cfg: dict, r: PreflightResult):
    """[Cue] 词级 cue 声明的结构校验（2026-10-12 词级 cue 批）。

    这条**不是观感门禁**，是接线正确性判据：声明的词若不在该场旁白的发音字符流里，
    对应元素永远不会亮——"少一个动画"在成片上只能靠人眼事后发现，属渲染前必须拦下的
    形态（与 `bgm_source`／`narration_source` 的"指针找不到即报错、禁止静默回退"同判据）。

    本函数只裁预检面能裁的那一半：声明有无归属、词是否出现在**该场旁白**里。
    绝对时刻要等 ASR 字符流存在才能算，归 timeline 步的
    `cue_resolve.resolve_and_apply` 裁定（同一链、同一扫描函数）。两处各写一份正则
    即两套语法，改了一处另一处照常"读不到"而不报错（A06 同族），故扫描与归一化
    的唯一持有者在 `cue_resolve`，这里只调它。

    fail-closed 三向：旁白面整体读不回 / 该场无旁白文本 / 词不在旁白里，一律 ERROR。
    方向按 brief §2.3 记为偏离原案（原案写 UNTESTED）：预检是渲染前的阻断位点，
    "无从核对"若降为 WARN，即等于把"接线断了"读成"接线没问题"，与本仓
    A04 四态在阻断位点的用法（`visual_boundary_check` 未测即退出码 2）一致。
    """
    print("\n[0b] Word-level cue declarations (data-cue-at)")

    try:
        import cue_resolve as _cr
    except ImportError as _e:            # 取数身拿不到即点名，不得静默跳过本校验
        r.error(f"cue 声明校验未执行：cue_resolve 模块取不回（{_e}）",
                check_id="cue_declaration")
        return

    if not html_path.exists():
        r.warn(f"HTML 缺失（{html_path}），cue 声明面无从扫描——本项未执行",
               check_id="cue_declaration")
        return

    decls = _cr.scan_cue_declarations(html_path.read_text(encoding='utf-8'))
    if not decls:
        # 无声明是合法常态（本项目不走词级动画），记 OK 留正证据而非"无记录"
        r.ok("无词级 cue 声明（0 条）", check_id="cue_declaration")
        return

    # 旁白面：narration_source 已由 check_json_config 解析进 cfg['scenes']（单一权威源），
    # 此处只读该已解析面，不再自行解析指针——两处解析即两套口径。
    nar_by_scene = {}
    for s in (cfg or {}).get("scenes") or []:
        if not isinstance(s, dict):
            continue
        key = _cr.normalize_scene_id(s.get("scene_id"))
        if not key:
            continue
        text = s.get("narration") or ""
        nar_by_scene[key] = "".join(text) if isinstance(text, list) else str(text)

    if not nar_by_scene:
        r.error(f"{len(decls)} 条 cue 声明在位，但旁白面（config／narration_source 的 scenes）"
                "读不回，词能否定位无从核对——属取数断链，不得按『没有缺陷』放行",
                check_id="cue_declaration")
        return

    problems = 0
    for d in decls:
        if d["scene_id"] is None:
            r.error(_cr.format_declaration_error(d), check_id="cue_declaration")
            problems += 1
            continue
        cue_norm = _cr.normalize(d["cue"])
        if not cue_norm:
            r.error(f"场景 {d['scene_id']} 的 cue 声明「{d['cue']}」归一化后没有发音字符，"
                    "无从在任何字符流里定位", check_id="cue_declaration")
            problems += 1
            continue
        nar_norm = _cr.normalize(nar_by_scene.get(_cr.normalize_scene_id(d["scene_id"]), ""))
        if not nar_norm:
            r.error(f"场景 {d['scene_id']} 声明了 cue「{d['cue']}」，但该场在旁白源里没有文本"
                    "（cover 场本就无旁白），词无从定位", check_id="cue_declaration")
            problems += 1
            continue
        if cue_norm not in nar_norm:
            r.error(f"场景 {d['scene_id']} 的 cue 声明「{d['cue']}」不在该场旁白里——这条 cue "
                    "永远不会亮（改成旁白中的实际词，或删除该声明；注意旁白按规范用阿拉伯数字，"
                    "「三大支柱」对不上「3 大支柱」）", check_id="cue_declaration")
            problems += 1

    if problems == 0:
        r.ok(f"cue 声明全部可定位：{len(decls)} 条", check_id="cue_declaration")
        # 消费面只报不拦（2026-10-21 审计批）：声明可定位 ≠ 画面读了它。
        # 读不到 CUE-MAP（首次项目）时不报——timeline 尚未写入，无从判定。
        try:
            _unconsumed, _wrapper, _has_map = _cue_unconsumed_keys(
                html_path.read_text(encoding="utf-8"))
        except OSError:
            _unconsumed, _wrapper, _has_map = [], False, False
        if _has_map and not _wrapper and _unconsumed:
            r.warn(f"cue 消费面：CUE-MAP 有 {len(_unconsumed)} 个键在脚本里没有字面引用"
                   f"（页面也没有 cue()/cueGlow() 包装消费）-> "
                   + ", ".join(_unconsumed[:6])
                   + ("…" if len(_unconsumed) > 6 else "")
                   + "。只报不拦；若该 cue 本就不需要动画，删除声明比留着更清楚",
                   check_id="cue_declaration")


def check_scene_contracts(html_path: Path, r: PreflightResult):
    """[Contract] Validate structural declarations on scene divs.

    Scene contract mechanism: each scene div declares its structural properties
    via data-* attributes. This check verifies declarations match reality, replacing
    fragile regex guessing with deterministic declaration validation.

    Contract attributes:
      data-scene-entry="cover|gsap|static"  — how this scene becomes visible
      data-scene-entry-at="SECONDS"         — GSAP timeline position (gsap only)
      data-scene-subtitle-safe="true|false"  — whether scene has subtitle-safe zone
      data-scene-assets="file1.png,file2.png" — image assets referenced in scene

    Scenes WITHOUT contract attributes are skipped (legacy fallback to regex checks).
    """
    print("\n[0] Scene Contract Validation")

    if not html_path.exists():
        r.warn("HTML file not found — skipping contract check")
        return

    content = html_path.read_text(encoding='utf-8')

    # Extract GSAP script block for verification
    script_match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
    script = script_match.group(1) if script_match else ""

    # Find all scene divs with their full opening tag
    # Match: <div id="sceneN" class="scene" ...>  (may have data-* attrs)
    scene_pattern = re.compile(
        r'<div\s+id="(scene(\d+))"\s+class="scene"([^>]*)>',
        re.DOTALL
    )
    scenes = scene_pattern.findall(content)
    if not scenes:
        # Try alternate pattern: id before class or style
        scene_pattern2 = re.compile(
            r'<div\s+id="(scene(\d+))"([^>]*)class="scene"',
            re.DOTALL
        )
        scenes = scene_pattern2.findall(content)

    if not scenes:
        r.warn("No scene divs found — cannot validate contracts")
        return

    contracted = []   # scenes with contract attributes
    legacy = []       # scenes without contracts

    for full_id, num_str, attrs in scenes:
        scene_id = int(num_str)
        sel = f"#scene{scene_id}"

        # Check if this scene has any data-scene-* attribute
        has_contract = bool(re.search(r'data-scene-', attrs))
        if not has_contract:
            legacy.append(scene_id)
            continue

        contracted.append(scene_id)

        # --- Validate data-scene-entry ---
        entry_match = re.search(r'data-scene-entry="([^"]*)"', attrs)
        if entry_match:
            entry_type = entry_match.group(1).strip()

            if entry_type == "cover":
                # Cover must be scene0, visible by default
                if scene_id != 0:
                    r.error(
                        f"Scene {scene_id}: contract says entry='cover' but only scene0 can be cover",
                        check_id="scene_contract"
                    )
                # Verify GSAP has a fade-out for cover
                if not re.search(
                    rf'tl\.to\s*\(\s*["\']{re.escape(sel)}["\']\s*,\s*{{[^}}]*opacity\s*:\s*0',
                    script
                ):
                    r.warn(
                        f"Scene {scene_id}: contract says entry='cover' but no GSAP fade-out found"
                    )

            elif entry_type == "gsap":
                # GSAP entry: must start hidden and have GSAP make it visible
                # Check CSS or inline style for opacity:0 or visibility:hidden
                inline_opacity_zero, css_opacity_zero, css_visibility_hidden = \
                    _scene_hidden_state(content, attrs, scene_id)

                starts_hidden = inline_opacity_zero or css_opacity_zero or css_visibility_hidden
                if not starts_hidden:
                    r.error(
                        f"Scene {scene_id}: contract says entry='gsap' but scene does NOT start hidden "
                        f"(no opacity:0 or visibility:hidden found)",
                        check_id="scene_contract"
                    )

                # Check GSAP makes it visible (opacity or visibility)
                gsap_visible = False

                # Opacity-based entrance patterns (4 patterns matching visibility check)
                gsap_patterns = [
                    rf'tl\.to\s*\(\s*["\']{re.escape(sel)}["\']\s*,\s*{{[^}}]*opacity\s*:\s*([1-9]\d*\.?\d*|0\.\d+)',
                    rf'tl\.fromTo\s*\(\s*["\']{re.escape(sel)}["\']\s*,\s*{{[^}}]*opacity\s*:\s*([1-9]\d*\.?\d*|0\.\d+)',
                    rf'tl\.fromTo\s*\(\s*["\']{re.escape(sel)}["\']\s*,\s*{{[^}}]*opacity\s*:\s*0[^,]*,\s*{{[^}}]*opacity\s*:\s*([1-9]\d*\.?\d*|0\.\d+)',
                    rf'tl\.set\s*\(\s*["\']{re.escape(sel)}["\']\s*,\s*{{[^}}]*opacity\s*:\s*([1-9]\d*\.?\d*|0\.\d+)',
                ]
                for pat in gsap_patterns:
                    if re.search(pat, script):
                        gsap_visible = True
                        break

                # Visibility-based entrance patterns (if opacity patterns not found)
                if not gsap_visible:
                    vis_patterns = [
                        rf'tl\.to\s*\(\s*["\']{re.escape(sel)}["\']\s*,\s*{{[^}}]*visibility\s*:\s*["\']visible["\']',
                        rf'tl\.set\s*\(\s*["\']{re.escape(sel)}["\']\s*,\s*{{[^}}]*visibility\s*:\s*["\']visible["\']',
                        rf'tl\.fromTo\s*\(\s*["\']{re.escape(sel)}["\']\s*,\s*{{[^}}]*}}\s*,\s*{{[^}}]*visibility\s*:\s*["\']visible["\']',
                    ]
                    for pat in vis_patterns:
                        if re.search(pat, script):
                            gsap_visible = True
                            break

                if not gsap_visible:
                    r.error(
                        f"Scene {scene_id}: contract says entry='gsap' but no GSAP animation "
                        f"sets opacity > 0 or visibility: visible",
                        check_id="scene_contract"
                    )

                # Validate entry-at timing if declared
                entry_at_match = re.search(r'data-scene-entry-at="([^"]*)"', attrs)
                if entry_at_match and gsap_visible:
                    declared_time = float(entry_at_match.group(1))
                    # Find actual GSAP time position for this scene's opacity animation
                    time_match = re.search(
                        rf'tl\.(?:to|fromTo|set)\s*\(\s*["\']{re.escape(sel)}["\'][^)]*,\s*([\d.]+)\s*\)',
                        script
                    )
                    if time_match:
                        actual_time = float(time_match.group(1))
                        if abs(declared_time - actual_time) > 0.5:
                            r.error(
                                f"Scene {scene_id}: contract says entry-at='{declared_time}s' "
                                f"but GSAP timeline position is {actual_time}s (delta > 0.5s)",
                                check_id="scene_contract"
                            )

            elif entry_type == "static":
                # Static: should be visible by default (no opacity:0, no visibility:hidden)
                inline_opacity_zero, css_opacity_zero, css_visibility_hidden = \
                    _scene_hidden_state(content, attrs, scene_id)
                if inline_opacity_zero or css_opacity_zero:
                    r.error(
                        f"Scene {scene_id}: contract says entry='static' but scene has opacity:0",
                        check_id="scene_contract"
                    )
                if css_visibility_hidden:
                    r.error(
                        f"Scene {scene_id}: contract says entry='static' but scene has visibility:hidden",
                        check_id="scene_contract"
                    )

            else:
                r.warn(
                    f"Scene {scene_id}: unknown entry type '{entry_type}' "
                    f"(expected: cover|gsap|static)"
                )

        # --- Validate data-scene-subtitle-safe ---
        safe_match = re.search(r'data-scene-subtitle-safe="([^"]*)"', attrs)
        if safe_match:
            declared_safe = safe_match.group(1).strip().lower() == "true"
            # Check if subtitle-safe div actually exists inside this scene
            # Find scene content between this scene div and the next scene div
            scene_start = content.find(f'<div id="scene{scene_id}"')
            next_scene = content.find('<div id="scene', scene_start + 1)
            if next_scene == -1:
                next_scene = len(content)
            scene_html = content[scene_start:next_scene]

            has_safe_div = 'class="subtitle-safe"' in scene_html or 'subtitle-safe' in scene_html
            if declared_safe and not has_safe_div:
                r.error(
                    f"Scene {scene_id}: contract says subtitle-safe='true' "
                    f"but no .subtitle-safe div found in scene HTML",
                    check_id="scene_contract"
                )
            elif not declared_safe and has_safe_div:
                r.warn(
                    f"Scene {scene_id}: has .subtitle-safe div but contract says "
                    f"subtitle-safe='false' — update contract declaration"
                )

        # --- Validate data-scene-assets ---
        assets_match = re.search(r'data-scene-assets="([^"]*)"', attrs)
        if assets_match:
            assets_str = assets_match.group(1).strip()
            if assets_str:
                declared_assets = [a.strip() for a in assets_str.split(',') if a.strip()]
                for asset in declared_assets:
                    # Find scene HTML and check if the asset is referenced
                    scene_start = content.find(f'<div id="scene{scene_id}"')
                    next_scene = content.find('<div id="scene', scene_start + 1)
                    if next_scene == -1:
                        next_scene = len(content)
                    scene_html = content[scene_start:next_scene]

                    if asset not in scene_html:
                        r.warn(
                            f"Scene {scene_id}: contract declares asset '{asset}' "
                            f"but it is not referenced in scene HTML"
                        )

    # Summary
    if contracted:
        r.ok(f"Contract validation: {len(contracted)} scene(s) validated via declarations")
    if legacy:
        r.warn(
            f"{len(legacy)} scene(s) without contract attributes "
            f"(scene {', '.join(str(s) for s in legacy)}) — "
            f"falling back to regex guessing. Add data-scene-* attributes to eliminate guesswork."
        )
    if not contracted and not legacy:
        r.warn("No scenes found for contract validation")


def check_scene_visibility(html_path: Path, r: PreflightResult):
    """CRITICAL: Verify every scene becomes visible during the timeline.

    This catches the most devastating class of bugs: scenes with CSS opacity:0
    that never receive a GSAP animation to make them visible. Without this
    check, preflight passes but the rendered video is all black.

    Checks:
    - Each scene with CSS opacity:0 must have a GSAP animation setting opacity > 0
    - Each scene with CSS visibility:hidden must have a GSAP animation setting visibility:visible
    - Scene 0 (cover) should be visible by default
    """
    print("\n[7/12] Scene Visibility (Black Frame Prevention)")

    if not html_path.exists():
        r.error(f"HTML file not found: {html_path}")
        return

    content = html_path.read_text(encoding='utf-8')

    # Extract the GSAP script block
    script_match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
    if not script_match:
        r.warn("No <script> block — cannot verify scene visibility")
        return
    script = script_match.group(1)

    # Find all scene IDs in HTML and identify contracted scenes
    scene_ids = sorted(set(int(m) for m in re.findall(r'id="scene(\d+)"', content)))
    contracted_ids = set()
    for sid in scene_ids:
        # Check if this scene div has data-scene-* attributes (already validated by contract check)
        scene_tag_match = re.search(rf'<div\s+id="scene{sid}"[^>]*data-scene-', content)
        if not scene_tag_match:
            scene_tag_match = re.search(rf'<div\s+id="scene{sid}"[^>]*class="scene"[^>]*data-scene-', content)
        if scene_tag_match:
            contracted_ids.add(sid)

    if contracted_ids:
        legacy_ids = [s for s in scene_ids if s not in contracted_ids]
        if not legacy_ids:
            # 措辞纪律（2026-10-13）：本条对契约型场景**没有做任何裁定**，不得写成
            # "validated via contracts"——契约检查只核属性在位，不核揭示路径。
            # 真实裁定位点是 check_element_reveal（元素级，含场景根）。
            r.ok(f"All {len(scene_ids)} scenes are contract-type — this regex check "
                 f"does not adjudicate them; element-level reveal coverage is decided "
                 f"by check_element_reveal")
            return
        # Some contracted, some legacy — only check legacy scenes below
        scene_ids = legacy_ids
    if not scene_ids:
        r.warn("No scene IDs found in HTML")
        return

    invisible_scenes = []
    visible_scenes = []

    for scene_id in scene_ids:
        sel = f"#scene{scene_id}"

        # Check CSS initial state
        css_match = re.search(rf'#scene{scene_id}\s*{{([^}}]*)}}', content)
        css_opacity_zero = False
        css_visibility_hidden = False
        if css_match:
            css = css_match.group(1)
            if re.search(r'opacity\s*:\s*0(?![.\d])', css):
                css_opacity_zero = True
            if re.search(r'visibility\s*:\s*hidden', css):
                css_visibility_hidden = True

        # Scene 0 (cover) should NOT have opacity:0
        if scene_id == 0 and css_opacity_zero:
            r.error("Scene 0 (cover) has opacity:0 — cover must be visible by default")
            continue

        if not css_opacity_zero and not css_visibility_hidden:
            visible_scenes.append(scene_id)
            continue

        # Scene starts invisible — check if GSAP makes it visible
        gsap_makes_visible = False

        if css_opacity_zero:
            # Look for tl.to("#sceneN", { ... opacity: 1 ... }, TIME)
            # or tl.fromTo("#sceneN", {...}, { ... opacity: 1 ... }, TIME)
            # or tl.set("#sceneN", { ... opacity: 1 ... })
            patterns = [
                rf'tl\.to\s*\(\s*["\']#{re.escape(sel[1:])}["\']\s*,\s*{{[^}}]*opacity\s*:\s*([1-9]\d*\.?\d*|0\.\d+)',
                rf'tl\.fromTo\s*\(\s*["\']#{re.escape(sel[1:])}["\']\s*,\s*{{[^}}]*opacity\s*:\s*([1-9]\d*\.?\d*|0\.\d+)',
                rf'tl\.fromTo\s*\(\s*["\']#{re.escape(sel[1:])}["\']\s*,\s*{{[^}}]*}}\s*,\s*{{[^}}]*opacity\s*:\s*([1-9]\d*\.?\d*|0\.\d+)',
                rf'tl\.set\s*\(\s*["\']#{re.escape(sel[1:])}["\']\s*,\s*{{[^}}]*opacity\s*:\s*([1-9]\d*\.?\d*|0\.\d+)',
            ]
            for pat in patterns:
                if re.search(pat, script):
                    gsap_makes_visible = True
                    break

        if css_visibility_hidden and not gsap_makes_visible:
            # Look for visibility: "visible" in GSAP
            vis_patterns = [
                rf'tl\.to\s*\(\s*["\']#{re.escape(sel[1:])}["\']\s*,\s*{{[^}}]*visibility\s*:\s*["\']visible["\']',
                rf'tl\.set\s*\(\s*["\']#{re.escape(sel[1:])}["\']\s*,\s*{{[^}}]*visibility\s*:\s*["\']visible["\']',
                rf'tl\.fromTo\s*\(\s*["\']#{re.escape(sel[1:])}["\']\s*,\s*{{[^}}]*}}\s*,\s*{{[^}}]*visibility\s*:\s*["\']visible["\']',
            ]
            for pat in vis_patterns:
                if re.search(pat, script):
                    gsap_makes_visible = True
                    break

        if gsap_makes_visible:
            visible_scenes.append(scene_id)
        else:
            invisible_scenes.append(scene_id)

    if invisible_scenes:
        for sid in invisible_scenes:
            r.error(
                f"Scene {sid} has CSS opacity:0 but NO GSAP animation to make it visible — "
                f"this scene will be BLACK in the rendered video",
                check_id="scene_visibility"
            )
    else:
        r.ok(f"All {len(scene_ids)} scenes verified visible (CSS opacity:0 matched by GSAP entrance)")

    if visible_scenes and not invisible_scenes:
        r.ok(f"Scene visibility: {len(visible_scenes)} scenes have visible initial state or GSAP entrance")


def check_element_reveal(html_path: Path, r: PreflightResult):
    """CRITICAL (元素级): 初始隐藏的元素必须有入场语句**命中它自己**。

    与 check_scene_visibility 的分工：那条量整场 `#sceneN`，且在场景带
    `data-scene-*` 契约时整体跳过 —— 本仓现网项目全部是契约型 HTML，故它对
    现网是空转的。本条量**元素级**，专抓 2026-10-13 复盘的形态：

      CSS `.rv{opacity:0}` 挂在**容器**上，入场 tween 写成 `#容器 .chip`
      → 父层 opacity 恒 0，子元素再亮也不显示，整块内容在成片里永久不可见。

    判据（零阈值，不依赖像素/采样，故不存在"标定"问题）。**只阻断形态①**：

      ① 元素 N 初始隐藏、无 tween 命中 N、但 N 的**后代**被 tween 命中 → ERROR
      ② 元素 N 初始隐藏、N 与其后代都没有 tween 命中 → WARN（不阻断）

    为何只阻断①：它问的是**结构关系**（容器出现在选择器的祖先段、却从未作为末段被命中），
    与"认得哪个 GSAP API"无关；取数面按第 2 步说明取宽，宽取数只会让判据漏判、不会错杀。
    ②由"什么都没找到"这一否定证据支撑，而本仓揭示路径是开放集合（`forEach(el => …)`、
    自定义转场封装、动态拼接选择器），把"我没找到"读成"不存在"即 A06 同族误杀 ——
    全仓 64 份 index.html 首次普查因该形态误报 10 份，故它只报不拦。

    两条限定（不是排除）：
      · **场景根（带 data-scene-id）一并参与**。曾把它排除、理由是"整场可见性归
        check_scene_visibility"，而该条在场景带契约属性时整体 return 跳过 —— 两条判据
        互相指认，现网契约 HTML 的场景根实际无人裁定（判据自报计数：64 份 index.html 合计 380 处）。2026-10-13 普查实测
        该洞当前 0 个犯事对象（380 处全部被 GSAP 直接命中），故本条接上它**不误杀任何存量项目**，
        只将"两处分工各让一步"的真空洞关闭；
      · 元素 id 另有 CSS 规则把它设回可见（`.sc{opacity:0}` + `#scene0{opacity:1}`）时
        不算隐藏 —— 按特异度，id 规则压过类规则。

    取数断链按 ERROR 处理（预检是阻断位点，"无从核对"降为 WARN 即把接线断了读成
    接线没问题）：声明了隐藏类却一个 GSAP 目标都提不出来 → 点名并判错。

    边界：选择器按**后代**语义匹配（`A B` 允许 B 的任意层级祖先为 A），不支持
    `>` 直接子代 —— 出现即按后代近似，本仓 HTML 无该形态。
    """
    print("\n[7b/12] Element Reveal (hidden container vs child-only entrance)")

    if not html_path.exists():
        r.error(f"HTML file not found: {html_path}", check_id="element_reveal")
        return

    content = html_path.read_text(encoding='utf-8')

    # ── 1. 初始隐藏的选择器：从 <style> 里取声明了 opacity:0 / visibility:hidden 的规则 ──
    styles = "".join(re.findall(r'<style[^>]*>(.*?)</style>', content, re.DOTALL))
    styles = re.sub(r'/\*.*?\*/', '', styles, flags=re.DOTALL)   # 注释里的 { } 会让规则切分错位
    hidden_classes, hidden_ids, restored_ids = set(), set(), set()
    for sel_list, body in re.findall(r'([^{}]+)\{([^{}]*)\}', styles):
        hidden = (re.search(r'opacity\s*:\s*0(?![.\d])', body)
                  or re.search(r'visibility\s*:\s*hidden', body))
        shown = (re.search(r'opacity\s*:\s*(?:[1-9]|0\.\d)', body)
                 or re.search(r'visibility\s*:\s*visible', body))
        for one in (s.strip() for s in sel_list.split(',')):
            m = re.fullmatch(r'\.([A-Za-z_][\w-]*)', one)
            if m:
                if hidden:
                    hidden_classes.add(m.group(1))
                continue
            m = re.fullmatch(r'#([A-Za-z_][\w-]*)', one)
            if not m:
                continue
            if hidden:
                hidden_ids.add(m.group(1))
            elif shown:
                restored_ids.add(m.group(1))

    if not hidden_classes and not hidden_ids:
        r.counts["element_reveal"] = {"hidden": 0, "scene_root_hidden": 0,
                                      "blocked_container": 0, "no_entrance": 0}
        r.ok("无初始隐藏元素（CSS 未声明 opacity:0 / visibility:hidden 的类或 id 规则）",
             check_id="element_reveal")
        return

    # ── 2. "被引用过的选择器"集合 ──
    # 取数面刻意取**宽**：凡脚本里长得像 CSS 选择器的字符串字面量全部纳入，而不是只认
    # 我认识的 GSAP API。收窄会把"我没认出来"读成"没有揭示"（普查实测两类：
    # `gsap.utils.toArray("X").forEach(el => …)` 与自定义转场封装 `pushH("#scene0","#scene1",t)`）。
    # 宽取数只让判据**漏判**，不让它错杀 —— 而本判据的区分力不来自"认得哪个 API"，
    # 来自结构：容器只在 `#容器 子元素` 这类选择器的**祖先段**出现、从未作为**末段**被命中，
    # 即"入场只打子元素"。这一区分与取数面宽窄无关。
    scripts = "".join(re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL))
    selectors = re.findall(
        r'[\'"]([#.][A-Za-z_][\w-]*(?:\s+[#.>]?[A-Za-z_][\w-]*)*)[\'"]', scripts)
    selectors = [s for s in selectors
                 if not re.fullmatch(r'#[0-9a-fA-F]{3,8}', s)]          # 排除 #rrggbb
    if not selectors:
        r.counts["element_reveal"] = {"acquisition_broken": True,
                                      "hidden": -1, "scene_root_hidden": -1,
                                      "blocked_container": -1, "no_entrance": -1}
        r.error(f"CSS 声明了 {len(hidden_classes | hidden_ids)} 个初始隐藏选择器，"
                f"但脚本里一个 GSAP 目标都取不到 —— 本判据无从核对，按缺陷处理",
                check_id="element_reveal")
        return
    seg_lists = [s.split() for s in selectors]

    # ── 3. 轻量 DOM（html.parser：注释与 <script>/<style> 内容不进元素树） ──
    from html.parser import HTMLParser

    class _N:
        __slots__ = ("tag", "id", "classes", "parent", "children", "text", "scene_root")

        def __init__(self, tag, attrs, parent):
            self.tag, self.parent, self.children = tag, parent, []
            d = dict(attrs or [])
            self.id = d.get("id")
            self.classes = set((d.get("class") or "").split())
            # 场景根标记：本判据**也裁场景根**（2026-10-13 起；此前排除的理由是
            # "整场归 check_scene_visibility"，而那条对契约型 HTML 整体跳过 → 两处互指空转）。
            # 该标记现仅用于计数透出（r.counts["element_reveal"]["scene_root_hidden"]）。
            self.scene_root = "data-scene-id" in d
            self.text = ""

    class _P(HTMLParser):
        VOID = {"img", "br", "hr", "input", "meta", "link", "source", "area",
                "base", "col", "embed", "param", "track", "wbr"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.root = _N("#root", [], None)
            self.cur = self.root
            self.nodes = []
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self._skip += 1
            n = _N(tag, attrs, self.cur)
            self.cur.children.append(n)
            self.nodes.append(n)
            if tag not in self.VOID:
                self.cur = n

        def handle_endtag(self, tag):
            if tag in ("script", "style") and self._skip:
                self._skip -= 1
            node = self.cur
            while node is not None and node.tag != tag:
                node = node.parent
            if node is not None and node.parent is not None:
                self.cur = node.parent

        def handle_data(self, data):
            if data.strip():
                self.cur.text += data.strip()

    p = _P()
    p.feed(content)
    nodes = p.nodes

    def _seg_match(seg, node):
        for tok in re.findall(r'[#.]?[A-Za-z_][\w-]*', seg):
            if tok[0] == '#':
                if node.id != tok[1:]:
                    return False
            elif tok[0] == '.':
                if tok[1:] not in node.classes:
                    return False
            elif node.tag != tok:
                return False
        return True

    def _matches(segs, node):
        if not _seg_match(segs[-1], node):
            return False
        i = len(segs) - 2
        anc = node.parent
        while i >= 0:
            if anc is None:
                return False
            if _seg_match(segs[i], anc):
                i -= 1
            anc = anc.parent
        return True

    def _hidden(node):
        if node.id in restored_ids:
            return False                                   # id 规则按特异度设回可见
        return bool(node.classes & hidden_classes) or node.id in hidden_ids

    hidden_nodes = [n for n in nodes if _hidden(n)]
    if not hidden_nodes:
        r.counts["element_reveal"] = {"hidden": 0, "scene_root_hidden": 0,
                                      "blocked_container": 0, "no_entrance": 0}
        r.ok(f"CSS 声明了隐藏选择器，但无元素受它约束（{len(nodes)} 个元素）",
             check_id="element_reveal")
        return

    hit = [any(_matches(sl, n) for sl in seg_lists) for n in hidden_nodes]
    blocked_container, no_entrance = [], []
    for idx, n in enumerate(hidden_nodes):
        if hit[idx]:
            continue
        if any(_matches(sl, d) for sl in seg_lists for d in _descendants(n)):
            blocked_container.append(n)
        else:
            no_entrance.append(n)

    def _label(n):
        cls = "." + ".".join(sorted(n.classes)) if n.classes else ""
        return f"#{n.id}{cls}" if n.id else f"<{n.tag}{cls}>"

    for n in blocked_container:
        r.error(
            f"{_label(n)} 初始隐藏（opacity:0/visibility:hidden）却无任何入场语句命中它自己，"
            f"而 GSAP 打的是它的**子元素** —— 父层恒不可见，整块子树在成片里永不出现。"
            f"修法：把隐藏类从容器移到叶子上，或给容器补一条父级 tween",
            check_id="element_reveal")
    for n in no_entrance[:12]:
        r.warn(
            f"{_label(n)} 初始隐藏、未找到命中它自身或其后代的 GSAP 目标"
            f"（不阻断：入场写法是开放集合，本判据只否证不确证）",
            check_id="element_reveal")
    if len(no_entrance) > 12:
        r.warn(f"另有 {len(no_entrance) - 12} 处同形态未逐条点名（同类计数见上）",
               check_id="element_reveal")

    r.counts["element_reveal"] = {
        "hidden": len(hidden_nodes),
        "scene_root_hidden": sum(1 for n in hidden_nodes if n.scene_root),
        "blocked_container": len(blocked_container),
        "no_entrance": len(no_entrance),
    }
    if not blocked_container:
        r.ok(f"{len(hidden_nodes)} 处初始隐藏元素无「容器隐藏·入场只打子元素」形态"
             f"（{len(no_entrance)} 处未找到 GSAP 目标，仅告警不阻断）",
             check_id="element_reveal")


def _descendants(node):
    out = []
    stack = list(node.children)
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(cur.children)
    return out


def check_chapter_bar(html_path: Path, r: PreflightResult):
    """CRITICAL (结构级): 章级常驻条（#clN 章名格 / #cfN 刻度格）与场景自报归属闭环。

    2026-10-15 面板建议接入。存在理由：章级常驻条由手写 HTML 承载、编译器不产出
    （它是常设观感默认而非必填项，无条的项目属正常），条上格子、场景自报归属、
    GSAP 切换语句三个环节各自独立书写，任一处漏改即成片观感与结构不符——
    实证 building-perf-craft 曾缺第 6 章刻度（tag 有 06 无 cf6）。
    与最接近的 check_element_reveal 的分工：那条管"整体不可见"，本条管"结构性错位"。

    三腿判据（零渲染、零像素）：
      L1 配对：id="clN" 集合 == id="cfN" 集合（两向差集各点名）
      L2 闭环：sN-tag 文本行首 `NN ·` → 存在 clNN；且 clNN 的全部点亮语句锚
               == 该章**首个**带 NN 场景的 T 键（craft 修复形态证明锚点是章首场，
               同章多场取最小场景号；锚收束场会把切章点推后一整段）
      L3 点亮：每个 clN/cfN 必须有含 opacity:1（或 autoAlpha:1）的语句，锚 `T.sX`
               须为 T 块已有键、第三参缺锚判错 —— "有 DOM 无语句" = 条上死格子，
               该章永不点亮

    取数纪律：
      · DOM 面 html.parser（注释与 script/style 内容不进元素树），tag 文本取子树拼接；
      · 脚本面**先剥 /* */ 与 // 注释再按 `;` 切语句**——注释里的 "#clN" 字面不得
        凭空造出一条点亮语句（cue 声明接入时实测踩过该形态），一行多语句（craft
        :686 形态）靠 `;` 切分区分，单语句多引用（数组形态）按 findall 全取；
      · 点亮锚强制 `T.sX`：数值锚（第三参直接写秒数）在 adjust_timeline 改时间后
        不随动、切章点必漂移，属违规而非另一种合法写法。

    适用域与四态：
      · 无 #clN/#cfN → 不适用（印 ok 并计数 0），不判红；
      · sN-tag 行首无 `NN ·` 前缀（英文标签、"GATE 0 · 立项"等）→ 腿 2 无对象，
        不判红（tag 是作者侧自报，非必填）；clN 无对应 tag 不判（无缺陷证据，
        不为假设造分支）；
      · 有 cl/cf 条但 T 块取不到（`var T = {...}` 形态未命中）→ ERROR 一次点名：
        预检是阻断位点，"锚无从核对"不得静默跳过（A06 同族）。

    check_id: chapter_bar（render 模式阻断；audit 模式降 warning）。
    """
    print("\n[7c/12] Chapter Bar (cl/cf pairing, tag closure, lit anchors)")

    if not html_path.exists():
        r.error(f"HTML file not found: {html_path}", check_id="chapter_bar")
        return

    content = html_path.read_text(encoding='utf-8')

    # ── 1. DOM 面：id 集合 + 每元素直接文本（tag 文本取子树拼接） ──
    from html.parser import HTMLParser

    class _N:
        __slots__ = ("tag", "id", "parent", "children", "text")

        def __init__(self, tag, attrs, parent):
            self.tag, self.parent, self.children = tag, parent, []
            self.id = dict(attrs or []).get("id")
            self.text = ""

    class _P(HTMLParser):
        VOID = {"img", "br", "hr", "input", "meta", "link", "source", "area",
                "base", "col", "embed", "param", "track", "wbr"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.root = _N("#root", [], None)
            self.cur = self.root
            self.nodes = []

        def handle_starttag(self, tag, attrs):
            n = _N(tag, attrs, self.cur)
            self.cur.children.append(n)
            self.nodes.append(n)
            if tag not in self.VOID:
                self.cur = n

        def handle_endtag(self, tag):
            node = self.cur
            while node is not None and node.tag != tag:
                node = node.parent
            if node is not None and node.parent is not None:
                self.cur = node.parent

        def handle_data(self, data):
            if data.strip():
                self.cur.text += data.strip()

    p = _P()
    p.feed(content)

    all_ids = {n.id for n in p.nodes if n.id}
    cl_ids = sorted(int(i[2:]) for i in all_ids if re.fullmatch(r"cl\d+", i))
    cf_ids = sorted(int(i[2:]) for i in all_ids if re.fullmatch(r"cf\d+", i))
    if not cl_ids and not cf_ids:
        r.counts["chapter_bar"] = {"cl": 0, "cf": 0, "tagged_chapters": 0,
                                   "lit_ids": 0, "violations": 0}
        r.ok("无章级常驻条（#clN/#cfN 均未声明，本检查不适用）", check_id="chapter_bar")
        return

    tag_chapter = {}   # 章号 → 该章最小场景号（章首场）
    for n in p.nodes:
        m = re.fullmatch(r"s(\d+)-tag", n.id or "")
        if not m:
            continue
        t = n.text + "".join(d.text for d in _descendants(n))
        cm = re.match(r'^\s*(\d{1,2})\s*·', t)
        if cm:
            scene_no, chap = int(m.group(1)), int(cm.group(1))
            tag_chapter[chap] = min(tag_chapter.get(chap, scene_no), scene_no)

    # ── 2. 脚本面：剥注释 → 按 ; 切语句 → 引用 + opacity:1 + T.sX 锚 ──
    scripts = "".join(re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL))
    scripts = re.sub(r'/\*.*?\*/', '', scripts, flags=re.DOTALL)
    scripts = re.sub(r'//[^\n]*', '', scripts)

    tm = re.search(r'var\s+T\s*=\s*\{([^}]*)\}', scripts, re.DOTALL)
    t_keys = {int(k) for k in re.findall(r'\bs(\d+)\s*:', tm.group(1))} if tm else None

    lit = {}   # ("cl"|"cf", N) → [锚场号或 None, ...]
    for stmt in scripts.split(";"):
        refs = re.findall(r'["\']#(cl|cf)(\d+)["\']', stmt)
        if not refs:
            continue
        if not re.search(r'(?:opacity|autoAlpha)\s*:\s*1(?:\.0+)?(?![.\d])', stmt):
            continue
        am = re.search(r'\bT\.s(\d+)\b', stmt)
        anchor = int(am.group(1)) if am else None
        for kind, num in refs:
            lit.setdefault((kind, int(num)), []).append(anchor)

    # ── 3. 三腿裁定 ──
    viol = []
    for n in sorted(set(cl_ids) - set(cf_ids)):
        viol.append(f"L1 cl{n} 无配对 cf{n}（章名格有、刻度格缺）")
    for n in sorted(set(cf_ids) - set(cl_ids)):
        viol.append(f"L1 cf{n} 无配对 cl{n}（刻度格有、章名格缺）")

    for chap in sorted(tag_chapter):
        if chap not in cl_ids:
            viol.append(f"L2 第 {chap} 章自报（首场 s{tag_chapter[chap]}）但无 cl{chap} 章名格")
            continue
        for a in lit.get(("cl", chap), []):
            if a is not None and a != tag_chapter[chap]:
                viol.append(f"L2 cl{chap} 点亮锚 T.s{a} != 章首场 "
                            f"T.s{tag_chapter[chap]}（切章点须落在章首场）")

    for kind, nums in (("cl", cl_ids), ("cf", cf_ids)):
        for n in nums:
            anchors = lit.get((kind, n), [])
            if not anchors:
                viol.append(f"L3 {kind}{n} 有 DOM 无点亮语句（opacity:1）"
                            f"——条上死格子，该章永不点亮")
                continue
            for a in anchors:
                if a is None:
                    viol.append(f"L3 {kind}{n} 点亮语句缺 T 锚点（第三参不是 T.sX）")
                elif t_keys is not None and a not in t_keys:
                    viol.append(f"L3 {kind}{n} 锚 T.s{a} 不在 T 块（键：{sorted(t_keys)}）")

    if t_keys is None:
        viol.append("T 块取不到（var T = {...} 形态未命中）"
                    "——章级条锚无从核对，按缺陷处理")

    r.counts["chapter_bar"] = {
        "cl": len(cl_ids), "cf": len(cf_ids),
        "tagged_chapters": len(tag_chapter), "lit_ids": len(lit),
        "violations": len(viol),
    }
    for v in viol:
        r.error(f"章级条结构：{v}", check_id="chapter_bar")
    if not viol:
        r.ok(f"章级条结构闭环：{len(cl_ids)} 章名格 / {len(cf_ids)} 刻度格配对，"
             f"{len(tag_chapter)} 章自报归属，{len(lit)} 个格子有有效点亮锚",
             check_id="chapter_bar")


# 画面文本半角标点判据（check_text_punctuation）的主表与邻接语境集。
# 主表＝八字句读标点；参考表（() % ~ - +）刻意不入判据——中文技术文本有合法
# 用法（f(x) / 95% / 5~10 / ISO 9001-2015 / C++），全仓普查 79 处命中全为合法形态。
TEXT_PUNCT_CHARS = ".,;:!?\"'"
TEXT_PUNCT_FULLWIDTH = {",": "，", ".": "。", ";": "；", ":": "：", "!": "！",
                        "?": "？", '"': "“”", "'": "‘’"}
# 邻接语境＝中文语境的标点/全角字符区：CJK 汉字 + 中式标点（U+3000-303F）
# + 全角区（U+FF00-FFEF）。全角区不可省：`。"` 段尾闭合形态里引号右邻是
# 非汉字的全角字符，纯汉字集实测漏判 5 处（2026-10-15 探针扩集取证）。
TEXT_PUNCT_CJK_CTX = re.compile(
    r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


def check_text_punctuation(html_path: Path, r: PreflightResult):
    """CRITICAL (文本级): 画面文本中文语境夹半角标点（半角引号为主要形态）。

    2026-10-15 面板建议接入。存在理由：字幕侧有 SRT 口径而没有"画面文本"口径，
    手写 HTML 正文里的中文引号用半角双引号（"…"）在成片上显示为英文式直引号——
    实证七域系列 _修订01 交付自查抓到三处，而字幕侧扫描 0 命中（病灶只存在于
    HTML 正文）。半角逗号/句号/问号后接中文同属该形态。

    判据（主表 8 字符 , . ; : ! ? " '）：
      · 该字符**左邻或右邻**命中中文语境集（CJK 汉字 + 中式标点 U+3000-303F
        + 全角区 U+FF00-FFEF）即判违规；
      · `.` 例外：右邻为 ASCII 字母/数字即跳过（扩展名/域名/版本号/小数——
        首轮普查 4 处 `.` 全部为该形态：`01_工作规矩.md` / `v1.2` / `3.5倍`，
        无例外即 4 处误杀）；
      · 参考表（() % ~ - +）刻意不入判据（见上方常量注释）。

    取数纪律（与 _取证/20261015/text_punct_probe.py 同一口径，探针为标定件）：
      · 文本段取 html.parser 的 data（convert_charrefs=True）——注释不进 data、
        属性值（aria-label/alt）不进 data、script/style/title 整体跳过；
      · data 保留原文不 strip：strip 会破坏段边缘字符的邻接关系；
      · **跨元素边界不判**（`<b>中文</b>"…"` 里引号左邻是标签边界而非汉字）——
        保守方向是漏判而非误杀。伪元素 content（CSS 生成文本）与属性值同属
        已知漏判面，翻转条件＝出现真实病灶落在该类载体上再扩取数面（扩面须
        同步探针与回归用例）。

    适用域与四态：
      · 文件缺失 → ERROR（预检是阻断位点，"无从核对"不得静默跳过，A06 同族）；
      · 无文本段 → 不适用（印 ok 并计数 0）——空/纯脚本 HTML 无对象，不判红；
      · 违规逐条 ERROR 并带 ±14 字上下文片段与全角替换提示；干净印 ok 行
        （通过路径给出正证据，非由"无错误"反推）。

    check_id: text_punct（render 模式阻断；audit 模式降 warning）。
    """
    print("\n[7d/12] Text Punctuation (ASCII punct in CJK screen text)")

    if not html_path.exists():
        r.error(f"HTML file not found: {html_path}", check_id="text_punct")
        return

    content = html_path.read_text(encoding='utf-8', errors='replace')

    from html.parser import HTMLParser

    class _TextScanner(HTMLParser):
        SKIP = {"script", "style", "title"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.skip = 0
            self.texts = []

        def handle_starttag(self, tag, attrs):
            if tag in self.SKIP:
                self.skip += 1

        def handle_endtag(self, tag):
            if tag in self.SKIP and self.skip:
                self.skip -= 1

        def handle_data(self, data):
            if not self.skip and data.strip():
                self.texts.append(data)

    scanner = _TextScanner()
    scanner.feed(content)

    findings = []
    for seg_no, seg in enumerate(scanner.texts):
        for i, ch in enumerate(seg):
            if ch not in TEXT_PUNCT_CHARS:
                continue
            left = seg[i - 1] if i > 0 else ""
            right = seg[i + 1] if i + 1 < len(seg) else ""
            if not (TEXT_PUNCT_CJK_CTX.match(left) or TEXT_PUNCT_CJK_CTX.match(right)):
                continue
            if ch == "." and right and right.isascii() and right.isalnum():
                continue
            ctx = seg[max(0, i - 14):i] + f"【{ch}】" + seg[i + 1:i + 15]
            ctx = re.sub(r'\s+', ' ', ctx)
            findings.append(
                f"第 {seg_no + 1} 文本段：…{ctx}…（{ch} → {TEXT_PUNCT_FULLWIDTH[ch]}）")

    r.counts["text_punct"] = {"texts": len(scanner.texts),
                              "violations": len(findings)}
    for f in findings:
        r.error(f"画面文本半角标点：{f}", check_id="text_punct")
    if not findings:
        if not scanner.texts:
            r.ok("画面文本无文本段（本检查不适用）", check_id="text_punct")
        else:
            r.ok(f"画面文本无中文夹半角标点（扫描 {len(scanner.texts)} 文本段）",
                 check_id="text_punct")


def check_design_rules(html_path: Path, r: PreflightResult):
    """Check for known design anti-patterns that cause visual quality issues."""
    print("\n[8/12] Design Rules")
    content = html_path.read_text(encoding='utf-8')

    # Rule 1: Independent gsap.timeline() — seek-and-capture mode ignores it
    indep = re.findall(r'var\s+(?!tl\b)\w+\s*=\s*gsap\.timeline\(\)', content)
    if indep:
        r.error(f"Independent gsap.timeline() found ({len(indep)}) — "
                f"seek-and-capture mode ignores it, must merge into main tl",
                check_id="design_rules")

    # Rule 2: scene1+ must have opacity:0 initial value (prevents overlap)
    for i in range(1, 20):
        m = re.search(rf'#scene{i}\{{([^}}]*)\}}', content)
        if m:
            css = m.group(1)
            if not re.search(r'opacity:\s*0(?![.\d])', css):
                r.error(f"#scene{i} missing opacity:0 — may overlap with previous scene",
                        check_id="design_rules")

    # Rule 3: <img> max-height < 500px — diagram likely unreadable
    for m in re.finditer(r'<img[^>]*max-height:\s*(\d+)px', content):
        h = int(m.group(1))
        if h < 500:
            r.warn(f"<img> max-height={h}px < 500 — may be unreadable, consider HTML/CSS")

    # Rule 4: .sc padding too large — causes excessive whitespace
    sc = re.search(r'\.sc\{[^}]*padding:\s*(\d+)px\s+(\d+)px', content)
    if sc:
        top, side = int(sc.group(1)), int(sc.group(2))
        if top > 60 or side > 130:
            r.warn(f".sc padding too large (top={top}px, side={side}px) — may cause excessive whitespace")


def check_animation_density(html_path: Path, cfg: dict, r: PreflightResult):
    """Adaptive animation density check — flag 'animation deserts' before they waste a render.

    Thresholds adapt to video characteristics:
      - Short videos (<120s): max desert 20s, min density 2 events/10s
      - Medium videos (120-300s): max desert 30s, min density 1.5 events/10s
      - Long videos (>300s): max desert 40s, min density 1 event/10s

    This prevents the most common quality failure: long stretches of static content.
    """
    print("\n[9/12] Animation Density (Adaptive)")
    content = html_path.read_text(encoding='utf-8')

    # Extract video duration
    video_dur = cfg.get('video_duration', 0)
    if video_dur <= 0:
        dur_match = re.search(r'data-duration="(\d+(?:\.\d+)?)"', content)
        if dur_match:
            video_dur = float(dur_match.group(1))
    if video_dur <= 0:
        r.warn("Cannot determine video duration — skipping density check")
        return

    # Extract all GSAP timestamps from the main timeline script
    script_match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
    if not script_match:
        r.warn("No <script> block found — cannot analyze animation density")
        return

    script = script_match.group(1)

    # Parse T-block if present (structured timeline)
    t_map = _parse_t_block(script)

    # Extract all GSAP timestamps — resolves both numeric literals and T-block expressions
    time_entries = _resolve_script_times(script, t_map)
    timestamps = sorted(set(t for t, _ in time_entries))

    if len(timestamps) < 3:
        r.warn(f"Only {len(timestamps)} animation timestamps found — video may lack motion")
        return

    # Determine adaptive thresholds
    scene_count = len(cfg.get('scenes', []))
    if video_dur > 300:
        max_desert = 40  # seconds
        min_density = 1.0  # events per 10s
    elif video_dur > 120:
        max_desert = 30
        min_density = 1.5
    else:
        max_desert = 20
        min_density = 2.0

    # Calculate animation deserts (gaps between consecutive timestamps)
    deserts = []
    for i in range(len(timestamps) - 1):
        gap = timestamps[i + 1] - timestamps[i]
        if gap > max_desert:
            deserts.append((timestamps[i], timestamps[i + 1], gap))

    # Calculate overall density
    actual_density = len(timestamps) / (video_dur / 10)
    overall_density = round(actual_density, 1)

    if deserts:
        for start, end, gap in deserts:
            # Map timestamp to scene for helpful error message
            scene_label = f"t={start:.0f}s-{end:.0f}s"
            r.warn(
                f"Animation desert: {scene_label} ({gap:.0f}s gap, "
                f"threshold={max_desert}s) — add motion events or sub-scenes"
            )
    else:
        r.ok(f"No animation deserts (max gap < {max_desert}s)")

    if overall_density < min_density:
        r.warn(
            f"Low animation density: {overall_density} events/10s "
            f"(min={min_density}, {len(timestamps)} events over {video_dur:.0f}s)"
        )
    else:
        r.ok(f"Animation density: {overall_density} events/10s ({len(timestamps)} events)")

    print(f"  Adaptive thresholds: max_desert={max_desert}s, min_density={min_density}/10s "
          f"(video={video_dur:.0f}s, {scene_count} scenes)")


def check_placeholder_images(html_path: Path, r: PreflightResult):
    """Detect placeholder content that should have been replaced with real assets.

    Catches:
    - [Image], [图片], [Photo] bracket placeholders
    - placeholder/lorem ipsum text
    - empty src="" attributes
    - data URI images (base64 placeholders)
    """
    print("\n[10/12] Placeholder Detection")
    content = html_path.read_text(encoding='utf-8')

    # Track if any placeholders found
    found_any = False

    # Bracket placeholders in text content
    bracket_patterns = [
        (r'\[Image\]', '[Image] placeholder'),
        (r'\[image\]', '[image] placeholder'),
        (r'\[\u56fe\u7247\]', '[图片] placeholder'),  # [图片]
        (r'\[Photo\]', '[Photo] placeholder'),
        (r'\[photo\]', '[photo] placeholder'),
        (r'\[INSERT', '[INSERT...] placeholder'),
        (r'\[TODO', '[TODO...] placeholder'),
    ]
    for pat, label in bracket_patterns:
        matches = re.findall(pat, content)
        if matches:
            r.error(f"{label} found ({len(matches)} occurrence(s)) — replace with real content", check_id="placeholder")
            found_any = True

    # Empty src attributes
    empty_src = re.findall(r'src="\s*"', content)
    if empty_src:
        r.error(f"Empty src=\"\" found ({len(empty_src)} occurrence(s)) — must reference real files", check_id="placeholder")
        found_any = True

    # data: URI images (base64 placeholders)
    data_uris = re.findall(r'src="data:image/', content)
    if data_uris:
        r.error(f"data: URI images found ({len(data_uris)}) — use real image files, not inline base64", check_id="placeholder")
        found_any = True

    # Placeholder text patterns
    placeholder_text = re.findall(r'lorem ipsum|placeholder|TODO:|FIXME:', content, re.IGNORECASE)
    if placeholder_text:
        r.error(f"Placeholder text found ({len(placeholder_text)} occurrence(s)): "
                f"{placeholder_text[:5]}", check_id="placeholder")
        found_any = True

    if not found_any:
        r.ok("No placeholder content detected")


def check_image_clarity(html_path: Path, r: PreflightResult):
    """Check critical images have reasonable file sizes (proxy for clarity).
    
    Critical images (decision tree, curves, matrices) must be large enough
    to contain readable detail (> 100KB) and not excessively large (< 5MB).
    """
    critical_images = {
        'S6_decision_tree.png': (100000, 5000000),  # min, max bytes
        'S4_stress_strain_curve.png': (80000, 3000000),
        'S4_cost_performance_matrix.png': (80000, 3000000)
    }
    materials_dir = html_path.parent / "assets" / "materials"
    
    checked_any = False
    for img_name, (min_size, max_size) in critical_images.items():
        img_path = materials_dir / img_name
        # rule: critical_image_size
        size_check = uv.check_file_size_range(img_path, min_bytes=min_size, max_bytes=max_size)
        if size_check.data["exists"]:
            checked_any = True
            size = size_check.data["size_bytes"]
            if size_check.data["too_small"]:
                r.error(f"Image likely too small/unclear: {img_name} ({size//1000}KB, expected >{min_size//1000}KB)", 
                       check_id="image_clarity")
            elif size_check.data["too_large"]:
                r.warn(f"Image quite large: {img_name} ({size//1000}KB, limit ~{max_size//1024//1024}MB)", 
                         check_id="image_clarity")
            else:
                r.ok(f"Image {img_name}: {size//1000}KB (acceptable)")
        # Note: Don't error if image missing - that's caught by check_image_refs
    
    if not checked_any:
        r.warn("No critical images found to check clarity", check_id="image_clarity",
               profile_exempt=True)


def check_subtitle_safe_zone(html_path: Path, r: PreflightResult):
    """Verify subtitle safety zone is properly configured in every scene.

    Each scene <div id="sceneN"> must contain:
    - A <div class="subtitle-safe"> element
    - The .sc container must have overflow:hidden (physical barrier)

    The --safe-zone-height CSS variable must be defined and referenced.
    """
    print("\n[11/12] Subtitle Safety Zone")
    content = html_path.read_text(encoding='utf-8')

    # Check --safe-zone-height CSS variable exists
    safe_var = re.search(r'--safe-zone-height\s*:\s*(\d+)px', content)
    if safe_var:
        safe_height = int(safe_var.group(1))
        r.ok(f"--safe-zone-height = {safe_height}px")
    else:
        r.warn("--safe-zone-height CSS variable not found — recommended for unified safe zone management")

    # Find all scene IDs
    scene_ids = sorted(set(int(m) for m in re.findall(r'id="scene(\d+)"', content)))
    if not scene_ids:
        r.warn("No scene IDs found — skipping safe zone check")
        return

    missing_safe_div = []
    for sid in scene_ids:
        # Extract scene block (from <div id="sceneN" to next scene or end)
        scene_match = re.search(
            rf'<div\s+id="scene{sid}"[^>]*>(.*?)(?=<div\s+id="scene\d+"|$)',
            content, re.DOTALL
        )
        if scene_match:
            scene_block = scene_match.group(1)
            if 'subtitle-safe' not in scene_block:
                missing_safe_div.append(sid)

    if missing_safe_div:
        r.error(
            f"Scenes {missing_safe_div} missing <div class=\"subtitle-safe\"> — "
            f"subtitles will overlap with scene content",
            check_id="subtitle_safe"
        )
    else:
        r.ok(f"All {len(scene_ids)} scenes have subtitle-safe div")

    # Check .sc has overflow:hidden
    sc_overflow = re.search(r'\.sc\s*\{[^}]*overflow\s*:\s*hidden', content)
    if sc_overflow:
        r.ok(".sc has overflow:hidden (physical barrier active)")
    else:
        r.warn(".sc missing overflow:hidden — content may spill into subtitle area")


def check_narration_digits(cfg: dict, r: PreflightResult):
    """Enforce AGENTS.md 数字规范: narration must use Arabic digits for
    quantitative values. Chinese numerals only allowed in idioms / ordinals
    (whitelist maintained in config/quality/narration_digits_rules.json).

    Runs on the parsed config dict — before render, before TTS. Any hit fails
    the render-mode gate; audit mode downgrades to a warning.
    """
    print("\n[13/14] Narration Digit Normalization (Arabic digits for quantities)")
    if not cfg:
        r.warn("Skipped — no config loaded", check_id="narration_digits")
        return

    findings = _lint_scan_config(cfg)
    if not findings:
        r.ok("All narration uses Arabic digits for quantitative values",
             check_id="narration_digits")
        return

    for f in findings:
        r.error(
            f"{f.scope}: 命中 “{f.matched}” — 上下文 “…{f.context}…” — {f.suggestion}",
            check_id="narration_digits",
        )


def check_pause_declarations(cfg: dict, r: PreflightResult):
    """[13b] 停顿声明复核（2026-10-13 停顿可控性接入批）。

    位点价值＝在花钱之前判：本检查跑在 TTS 步之前，而声明越界 / 锚词定位不到 /
    引擎不支持 这些形态在 TTS 步炸即是白扣一轮账号余额（缓存被清后）。
    判据本体不住这里——**与生效路径同一个裁定源** `pause_marks.plan_scene`，
    预检只是把它对每场的抛错形态翻译成 check_id="pause_declaration" 的
    ERROR/WARN 面（两处各写一套阈值即 A06 同族的第二判据面，禁止）。

    旁白面读 cfg['scenes']（narration_source 已由 check_json_config 解析进该面，
    与 cue_declaration 同一处置——两处解析即两套口径）。

    引擎名与开关的读取在本函数内镜像 enhance 的口径（顶层 + audio 对象覆盖）：
    in-process 导入 enhance_video_audio 会把脚本级模块初始化拉进预检，代价大于
    这十几行镜像；等价性由用例95 的双向夹具锁定（同一组值两路读结果必须相同），
    与 check_json_config 镜像 narration_source 解析顺序、以用例18 锁定同判据。
    """
    print("\n[13b/15] Pause declarations (pause_marks → CosyVoice <break>)")
    if not cfg:
        r.warn("Skipped — no config loaded", check_id="pause_declaration")
        return

    try:
        import pause_marks as _pm
    except (ImportError, RuntimeError) as e:   # pause_marks 顶部取不回 cue_resolve 即 RuntimeError
        r.error(f"停顿声明复核未执行：pause_marks 模块取不回（{e}）——"
                "锚词定位与归一化不得另起一套口径，本项无从核对即 ERROR",
                check_id="pause_declaration")
        return

    scenes = [s for s in (cfg.get("scenes") or []) if isinstance(s, dict)]
    decl_map = {str(s.get("scene_id")): s.get("pause_marks")
                for s in scenes if s.get("pause_marks")}

    # —— 开关与引擎（镜像 enhance 口径，见 docstring 限定）——
    def _as_bool(v, default):
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() not in ("false", "0", "no", "off", "")
        return bool(v)

    auto_raw = cfg.get("pause_auto_enabled")
    if auto_raw is None and isinstance(cfg.get("audio"), dict):
        auto_raw = cfg["audio"].get("pause_auto_enabled")
    auto_on = _as_bool(auto_raw, False)

    engine = str(cfg.get("tts_engine", "qwen") or "qwen").lower()
    if isinstance(cfg.get("audio"), dict) and 'engine' in cfg["audio"]:
        engine = str(cfg["audio"]["engine"]).lower()

    tts_on = _as_bool(cfg.get("tts_enabled"), True)
    if isinstance(cfg.get("audio"), dict):
        tts_on = _as_bool(cfg["audio"].get("tts_enabled", cfg.get("tts_enabled")), True)

    n_decl = sum(len(v) for v in decl_map.values())
    if not decl_map and not auto_on:
        # 无声明是合法常态（本机制缺省关闭），记 OK 留正证据而非"无记录"
        r.ok(f"无停顿声明，自动插缺省关闭（扫过 {len(scenes)} 场）",
             check_id="pause_declaration")
        return

    # 规则文件只在有人点名停顿时才读——存量项目不因规则断链被本项误伤
    # （enhance 侧 _scene_pause_plan 的短路条件与此逐字对齐）。
    try:
        rules = _pm.load_rules()
    except _pm.PausePlanError as e:
        r.error(str(e), check_id="pause_declaration")
        return

    problems = 0
    supported = engine in rules["engines"]
    if auto_on and not supported and not decl_map:
        # 自动开着但引擎不支持：plan_scene 对"无声明+不支持引擎"返回恒等计划，
        # 机器一声不响——作者以为开了自动补停顿，实际零动作。WARN 不 ERROR：
        # 该组合无破坏性（与声明在场即 ERROR 相对）。
        r.warn(f"pause_auto_enabled=true 但引擎 {engine!r} 不在支持清单 "
               f"{rules['engines']!r}——本机制对本项目零动作，不构成缺陷但作者应知情",
               check_id="pause_declaration")

    for s in scenes:
        sid = str(s.get("scene_id"))
        text = s.get("narration") or ""
        if isinstance(text, list):
            text = "".join(text)
        decls = decl_map.get(sid) or []
        if not decls and not auto_on:
            continue
        if not tts_on and decls:
            r.error(f"场 {sid}: tts_enabled=false 但声明了 {len(decls)} 处停顿——"
                    "无 TTS 则停顿永不发生，声明在此项目形态下是假的控制面。"
                    "请删除声明或恢复旁白。", check_id="pause_declaration")
            problems += 1
            continue
        # 尖括号两形态判据（有包裹→整调用 400；无包裹→被当文字念出且门禁读不出）。
        # will_wrap 取"将被包裹"的保守归因：声明在场或自动开启即可能进 SSML 分支。
        try:
            _pm.check_angle_brackets(text, will_wrap=bool(decls) or (auto_on and supported))
        except _pm.PausePlanError as e:
            r.error(f"场 {sid}: {e}", check_id="pause_declaration")
            problems += 1
            continue
        try:
            plan = _pm.plan_scene(
                text, decls, rules, engine=engine,
                enabled=bool(auto_on and supported),
                auto_decls=_pm.auto_candidates(text, rules) if auto_on else None)
        except _pm.PausePlanError as e:
            r.error(f"场 {sid}: {e}", check_id="pause_declaration")
            problems += 1
            continue
        for note in plan.get("notes") or []:
            r.warn(f"场 {sid}: {note}", check_id="pause_declaration")

    if problems == 0 and n_decl:
        r.ok(f"停顿声明全部可执行：{n_decl} 条（{len(decl_map)} 场），"
             f"引擎 {engine!r} 在支持清单内；自动插{'开' if auto_on else '关'}",
             check_id="pause_declaration")
    elif problems == 0:
        r.ok(f"无作者声明；自动插开（引擎 {engine!r}），形状候选按规则可执行",
             check_id="pause_declaration")


def _div_block(content: str, open_tag: str) -> str:
    """返回从 open_tag 起、按 &lt;div&gt;/&lt;/div&gt; 配平闭合的整段 HTML（找不到返回 ""）。

    嵌套子 div（如 .tags 内的 .tag）会让 `.*?</div>` 非贪婪匹配在第一个子元素处截断，
    凡需读取"容器 + 全部子元素"的判据都必须走这里。
    """
    start = content.find(open_tag)
    if start < 0:
        return ""
    depth, pos = 0, start
    while pos < len(content):
        nxt_open = content.find("<div", pos)
        nxt_close = content.find("</div>", pos)
        if nxt_close < 0:
            return ""
        if 0 <= nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + 4
        else:
            depth -= 1
            pos = nxt_close + 6
            if depth == 0:
                return content[start:pos]
    return ""


def _num_tokens(text: str) -> set:
    """屏显/溯源文本中的阿拉伯数字串（含小数）。"""
    return set(re.findall(r'\d+(?:\.\d+)?', text or ""))


def _quant_tokens(text: str) -> set:
    """需要溯源的数字：剔除公历日期（"9 月 1 日"/"3 号"属日历引用而非量化指标）。"""
    t = re.sub(r'\d+(?:\.\d+)?\s*(?=[月日号])', '', text or "")
    return _num_tokens(t)


def check_alignment_model_cache(cfg: dict, r: PreflightResult):
    """ASR 强制对齐模型缓存预检 —— **只告警，绝不阻断**。

    为什么值得在渲染前喊一声：HF_HUB_OFFLINE=1 下缓存缺位时 load_align_model 返回
    None，_build_timeline_manifest 会把**整项目**句级时间戳落到 punct-gap 一级降级链，
    当轮只有一行 WARN 且终检照常通过（2026-09-18 那版交付即在交付后普查时才归因）。
    降级本身是离线环境的合法路径、成片并非不合格，所以这里不 error()——拦渲染等于
    把设计内链路判成缺陷；但让操作者在数十分钟渲染**之前**就知道本轮走哪条链，
    交付侧的机器裁定见 media_qa_gate.subtitle_timestamp_source。
    """
    print("\nAlignment Model Cache (faster-whisper 强制对齐可用性预检)")
    if str(cfg.get("tts_enabled", "true")).strip().lower() in ("false", "0", "no"):
        r.info("tts_enabled=false（纯 BGM 无旁白）— 不涉句级对齐，跳过",
               check_id="align_model_cache")
        return
    try:
        from _forced_align import align_model_cache_ready, MODEL_REPO_ID
    except ImportError as e:
        r.warn(f"无法导入 _forced_align 探测模型缓存: {e}", check_id="align_model_cache")
        return
    ready, detail = align_model_cache_ready()
    if ready:
        r.ok(f"强制对齐模型缓存就位（{MODEL_REPO_ID}）", check_id="align_model_cache")
    else:
        r.warn(f"强制对齐模型缓存缺失（{MODEL_REPO_ID}：{detail}）→ 本轮 TTS 步骤的 "
               f"load_align_model 必失败，整项目字幕时间戳将走 punct-gap 一级降级链"
               f"（设计内合法路径，不阻断渲染；成片终检 subtitle_timestamp_source 会判 "
               f"UNTESTED，需 --accept-media-untested 知情放行并留痕）",
               check_id="align_model_cache")


def check_asset_signoff(project_dir: Path, cfg: dict, html_path: Path, r: PreflightResult):
    """位点1（素材/文案人工签认单）消费门禁。

    判据源是项目目录下的 `素材确认单.json`：人工在位点1 逐场确认的图片选用、屏显文案、
    量化标签与数据溯源。该文件一旦存在即视为权威——屏显与旁白必须与它逐字一致，
    否则签认失效（"确认过的东西"和"最终播出的东西"分叉）。

    触发设计：无该文件的项目记 INFO 跳过（不是所有项目都走位点1），不做 opt-in 开关、
    不硬编码项目名单——存在即强制，与 narration_source 指针同一判据形态。

    核验面：
      1 场景覆盖：确认单场景 == HTML 场景（cover scene0 除外）
      2 素材：选用素材 display_target == data-scene-assets 且逐个在盘
      3 旁白：narration_source 解析出的 narration == 确认单「旁白文案」（逐字）
      4 类型：确认单「场景类型」== HTML S-block 同场 type
      5 溯源：屏显数字（量化标签 + 画面文案）逐个可在本场「数据点」中找到；公历日期豁免
      6 屏显文本：卡片名称行/标签 pill、hero 标题与行条、stats 数字、收尾品牌带
        —— 按元素在位与否自适应（模板无关），在位即逐字比对
    """
    print("\n[14/14] Asset sign-off (素材确认单) — 位点1 签认一致性")
    sheet_path = project_dir / "素材确认单.json"
    if not sheet_path.exists():
        r.info("无 素材确认单.json — 该项目未走位点1 签认，跳过", check_id="asset_signoff")
        return
    if not html_path.exists():
        r.error(f"素材确认单在位但 HTML 缺失：{html_path}", check_id="asset_signoff")
        return

    try:
        sheet = json.loads(sheet_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, ValueError) as e:
        r.error(f"素材确认单.json 解析失败: {e}", check_id="asset_signoff")
        return
    scenes = sheet.get("场景")
    if not isinstance(scenes, list) or not scenes:
        r.error("素材确认单.json 无有效 '场景' 列表", check_id="asset_signoff")
        return

    content = html_path.read_text(encoding='utf-8')
    nar_scenes = {}
    if cfg and cfg.get("scenes"):
        nar_scenes = {s.get("scene_id"): s for s in cfg["scenes"]}

    sblock = {}
    smap = _parse_scene_map(content)
    for s in smap.get("scenes", []):
        try:
            sblock[int(str(s["id"]).lstrip("s"))] = s
        except (KeyError, ValueError):
            continue

    errs_before = len(r.errors)
    html_ids = sorted(int(m) for m in re.findall(r'id="scene(\d+)"', content))
    html_content_ids = [i for i in html_ids if i != 0]
    sheet_ids = [sc.get("scene_id") for sc in scenes]
    if sorted(sheet_ids) != html_content_ids:
        r.error(f"确认单场景 {sorted(sheet_ids)} != HTML 非 cover 场景 {html_content_ids}",
                check_id="asset_signoff")
    declared_count = sheet.get("场景数")
    if declared_count is not None and int(declared_count) != len(scenes):
        r.error(f"确认单「场景数」{declared_count} != 实际场景条目 {len(scenes)}",
                check_id="asset_signoff")

    REQUIRED = ("scene_id", "场景类型", "画面文案", "量化标签", "旁白文案", "选用素材", "数据点")
    checked_text = 0
    for sc in scenes:
        sid = sc.get("scene_id")
        where = f"s{sid}"
        for k in REQUIRED:
            if k not in sc:
                r.error(f"{where}: 确认单缺必填字段「{k}」", check_id="asset_signoff")
        if any(k not in sc for k in REQUIRED):
            continue

        open_m = re.search(rf'<div id="scene{sid}" class="scene"[^>]*>', content)
        if not open_m:
            r.error(f"{where}: 确认单场景在 HTML 中不存在", check_id="asset_signoff")
            continue
        open_tag = open_m.group(0)
        blk = _div_block(content, open_tag)

        # --- 2 素材 ---
        picks = sc["选用素材"]
        if not picks:
            r.error(f"{where}: 确认单无选用素材", check_id="asset_signoff")
            bases = []
        else:
            bases = [str(p.get("display_target", "")).split("/")[-1] for p in picks]
        am = re.search(r'data-scene-assets="([^"]*)"', open_tag)
        declared = [a.strip() for a in am.group(1).split(",")] if am and am.group(1).strip() else []
        if declared != bases:
            r.error(f"{where}: data-scene-assets {declared} != 确认单选用素材 {bases}",
                    check_id="asset_signoff")
        for b in bases:
            if not b:
                r.error(f"{where}: 选用素材缺 display_target", check_id="asset_signoff")
            elif not (project_dir / "assets" / b).exists():
                r.error(f"{where}: 选用素材未落盘 assets/{b}", check_id="asset_signoff")
            elif b not in blk:
                r.error(f"{where}: assets/{b} 已声明但场景 HTML 未引用", check_id="asset_signoff")

        # --- 3 旁白 ---
        if nar_scenes:
            ns = nar_scenes.get(sid)
            if ns is None:
                r.error(f"{where}: 确认单场景在 narration_source 中缺失", check_id="asset_signoff")
            elif (ns.get("narration") or "") != sc["旁白文案"]:
                r.error(f"{where}: 旁白源与确认单「旁白文案」不一致", check_id="asset_signoff")

        # --- 4 场景类型 ---
        stype = sc["场景类型"]
        if sblock and sid in sblock and sblock[sid].get("type") != stype:
            r.error(f"{where}: 确认单场景类型 {stype} != S-block type {sblock[sid].get('type')}",
                    check_id="asset_signoff")

        # --- 5 数字溯源 ---
        dp_tokens = set()
        for e in sc["数据点"]:
            dp_tokens |= _num_tokens(str(e.get("数值", "")))
        for src_label, src_text in (
            [("量化标签[%d]" % i, t) for i, t in enumerate(sc["量化标签"])]
            + [("画面文案", sc["画面文案"])]
        ):
            for tok in sorted(_quant_tokens(src_text) - dp_tokens):
                r.error(f"{where}: 屏显数字 {tok}（{src_label}）在本场「数据点」中无溯源",
                        check_id="asset_signoff")

        # --- 6 屏显文本（元素在位才比对）---
        tags = sc["量化标签"]
        if stype == "card":
            mn = re.search(rf'<div class="ab name" id="s{sid}name"[^>]*>([^<]*)</div>', content)
            if mn:
                checked_text += 1
                if mn.group(1) != sc["画面文案"]:
                    r.error(f"{where}: 名称行 {mn.group(1)!r} != 确认单画面文案 {sc['画面文案']!r}",
                            check_id="asset_signoff")
            mt = re.search(rf'<div class="ab tags" id="s{sid}tags"', content)
            if mt:
                checked_text += 1
                shown = re.findall(rf'<div class="tag" id="s{sid}t\d+">([^<]*)</div>',
                                   _div_block(content, mt.group(0)))
                if shown != tags:
                    r.error(f"{where}: 标签 pill {shown} != 确认单量化标签 {tags}",
                            check_id="asset_signoff")
        elif stype == "hero":
            m1 = re.search(rf'id="h{sid}l1"[^>]*>([^<]*)</div>', content)
            m2 = re.search(rf'id="h{sid}l2"[^>]*>([^<]*)</div>', content)
            if m1:
                checked_text += 1
                title = m1.group(1) + ("，" + m2.group(1) if m2 else "")
                if title != sc["画面文案"]:
                    r.error(f"{where}: hero 标题 {title!r} != 确认单画面文案 {sc['画面文案']!r}",
                            check_id="asset_signoff")
            rows = re.findall(rf'id="h{sid}s\d"[^>]*><div class="vbar"[^>]*></div><div>([^<]*)</div>',
                              content)
            if rows:
                checked_text += 1
                if rows != tags:
                    r.error(f"{where}: hero 行条 {rows} != 确认单量化标签 {tags}",
                            check_id="asset_signoff")
        elif stype == "stats":
            for t in tags:
                for tok in sorted(_quant_tokens(t)):
                    if tok not in blk:
                        r.error(f"{where}: stats 标签数字 {tok}（{t}）未出现在场景 HTML",
                                check_id="asset_signoff")
        elif stype == "closing":
            ml = re.search(rf'<img id="s{sid}logo" src="assets/([^"]+)"', content)
            if ml:
                checked_text += 1
                if bases and ml.group(1) != bases[0]:
                    r.error(f"{where}: 品牌带 {ml.group(1)} != 确认单选用素材 {bases[0]}",
                            check_id="asset_signoff")

    if len(r.errors) == errs_before:
        r.ok(f"素材确认单一致性通过：{len(scenes)} 场（素材/旁白/类型/数字溯源"
             f"{' + 屏显文本 ' + str(checked_text) + ' 处' if checked_text else ''}）",
             check_id="asset_signoff")


def check_output_dir_cleanliness(output_dir: Path, r: PreflightResult):
    """Verify the output/delivery directory does not contain technical process files.

    Forbidden in delivery directory:
    - work-* directories (render workspaces)
    - render*.mp4 (raw render files)
    - *raw*.mp4 (unprocessed files)
    - *_v0N.mp4 (versioned test files)
    - *.noaudio.mp4 (backup files)
    """
    print("\n[12/12] Output Directory Cleanliness")

    if not output_dir.exists():
        r.ok(f"Output directory does not exist yet: {output_dir}")
        return

    tech_patterns = [
        (r'^work-', 'Render workspace directory'),
        (r'_render[_\.]', 'Raw render file'),
        (r'_raw[_\.]', 'Unprocessed render file'),
        (r'_v\d{2}[_\.]', 'Versioned test file (v01/v02...)'),
        (r'\.noaudio\.', 'No-audio backup file'),
        (r'_test[_\.]', 'Test file'),
    ]

    violations = []
    for item in output_dir.iterdir():
        name = item.name
        for pat, label in tech_patterns:
            if re.search(pat, name, re.IGNORECASE):
                violations.append((name, label))
                break

    if violations:
        for name, label in violations:
            r.error(f"Delivery directory contains '{label}': {name}")
    else:
        file_count = len(list(output_dir.iterdir()))
        r.ok(f"Delivery directory clean ({file_count} file(s))")


LOG_DIR = ROOT / "过程产物" / "临时产物"


def summarize_history_logs(html_project: str, current_mode: str):
    """Read recent preflight logs for this project and print a history summary.

    This is the mechanism that prevents 'logs exist but nobody reads them'.
    Every preflight run automatically surfaces recent history — the executor
    (human or AI) sees it whether they want to or not.
    """
    if not LOG_DIR.exists():
        return

    # Find logs for this project — 取数面同时覆盖两处：旧版散落在临时产物根目录的
    # preflight_*.json，与新版 _取证/YYYYMMDD/ 日期子目录下的产物（产生侧已迁移，
    # 消费侧同步迁移以免历史断链；A06：登记面=真实读取路径）。子目录名读自单一
    # 权威源配置，与 project_cleanup 共用。只在 _取证 内递归，不遍历 *_audio/ 项目目录。
    forensic_root = LOG_DIR / _script_env.load_forensic_rules()["forensic_subdir"]
    candidates = list(LOG_DIR.glob("preflight_*.json"))
    if forensic_root.is_dir():
        candidates += list(forensic_root.rglob("preflight_*.json"))

    # Find logs for this project
    project_logs = []
    for f in sorted(candidates, key=lambda p: p.name, reverse=True):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            if data.get('html_project') == html_project:
                project_logs.append((f, data))
        except (json.JSONDecodeError, ValueError):
            continue

    if not project_logs:
        return

    print("\n" + "-" * 60)
    print("HISTORY SUMMARY (recent preflight logs)")
    print("-" * 60)

    # Show up to 3 most recent logs
    shown = 0
    latest_warnings = []
    for log_path, data in project_logs[:3]:
        mode = data.get('gate_mode', '?')
        result = data.get('result', '?')
        ts = data.get('timestamp', '?')
        errs = data.get('error_count', 0)
        warns = data.get('warning_count', 0)
        icon = '✓' if result == 'PASS' else '✗'
        print(f"  {icon} [{mode.upper()}] {ts}  errors={errs}  warnings={warns}")
        print(f"    Log: {log_path.name}")

        # Collect warnings from the most recent log for context
        if shown == 0:
            for entry in data.get('entries', []):
                if entry.get('level') in ('WARN', 'WARN_AUDIT', 'ERROR'):
                    latest_warnings.append(entry)
        shown += 1

    # Print unresolved issues from the most recent log
    if latest_warnings:
        print(f"\n  Latest run flagged {len(latest_warnings)} issue(s):")
        for w in latest_warnings[:10]:
            level = w.get('level', '?')
            msg = w.get('message', '')
            # Highlight render-critical issues
            is_critical = w.get('check_id', '') in RENDER_CRITICAL_CHECKS
            marker = '🔴' if (is_critical and level == 'ERROR') else '⚠️'
            print(f"    {marker} [{level}] {msg[:80]}")

        if current_mode == "render":
            critical = [w for w in latest_warnings
                        if w.get('check_id', '') in RENDER_CRITICAL_CHECKS]
            if critical:
                print(f"\n  ⚡ {len(critical)} render-critical issue(s) from history — "
                      f"will HARD-BLOCK in render mode")
    else:
        print("  No warnings or errors in recent history.")

    print("-" * 60)


def main():
    parser = argparse.ArgumentParser(description="Pre-render validation gate")
    parser.add_argument("--config", default=None, help="Config file name (e.g. hotel_pw_enhance.json)")
    parser.add_argument("--html", default=None, help="HTML project folder name (e.g. hotel-partition-wall)")
    parser.add_argument("--mode", choices=GATE_MODES, default="render",
                        help="Gate mode: 'audit' (warn-only, for Check/Close) or "
                             "'render' (hard-block, for SampleQA/FinalRender). Default: render")
    args = parser.parse_args()

    # 第1步：先查找并加载config文件，从中提取html_project
    config_path = None
    if args.config:
        for subdir in ("", "pipelines", "openmontage", "system"):
            candidate = CONFIG_BASE / subdir / args.config if subdir else CONFIG_BASE / args.config
            if candidate.exists():
                config_path = candidate
                break
    
    # 第2步：从配置中读取html_project作为默认值
    config_html_project = None
    if config_path and config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                config_html_project = cfg.get('paths', {}).get('html_project')
        except (json.JSONDecodeError, ValueError):
            pass
    
    # 第3步：确定最终使用的HTML项目
    # 优先级：命令行 > 配置文件 > 硬编码默认值
    if args.html is None:
        if config_html_project:
            args.html = config_html_project
        else:
            args.html = "hotel-partition-wall"  # 最后的硬编码默认值
    
    # 第4步：如果还没有config_path，尝试用args.config查找
    if config_path is None and args.config is None:
        args.config = "hotel_pw_enhance.json"  # 硬编码默认值
        for subdir in ("", "pipelines", "openmontage", "system"):
            candidate = CONFIG_BASE / subdir / args.config if subdir else CONFIG_BASE / args.config
            if candidate.exists():
                config_path = candidate
                break
    elif config_path is None and args.config:
        for subdir in ("", "pipelines", "openmontage", "system"):
            candidate = CONFIG_BASE / subdir / args.config if subdir else CONFIG_BASE / args.config
            if candidate.exists():
                config_path = candidate
                break
        if config_path is None:
            config_path = CONFIG_BASE / args.config  # For error message

    # 受管目录名校验（2026-10-15 消费者侧根因修复，2026-10-16 回灌）：--html 带路径
    # 会穿透 HTML_BASE 拼接读到嵌套/错误位置，口径同 pipeline_runner
    args.html = _script_env.validate_managed_name(
        args.html, "--html", source="preflight_check") or args.html
    html_path = HTML_BASE / args.html / "index.html"

    print("=" * 60)
    print(f"PRE-RENDER VALIDATION GATE  [mode: {args.mode.upper()}]")
    print("=" * 60)
    print(f"HTML:   {html_path}")
    print(f"Config: {config_path}")
    print(f"Mode:   {args.mode} ({'warnings only' if args.mode == 'audit' else 'hard-block on render-critical'})")

    # Surface historical context — every run sees recent history
    summarize_history_logs(args.html or "", args.mode)

    r = PreflightResult(mode=args.mode)

    # Pre-load config for production readiness check (formal validation in check_json_config)
    pre_cfg = {}
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                pre_cfg = json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    # 内容画像：text_only 时素材类 WARN 降级为 INFO（消除常驻噪音）
    r.content_profile = str(pre_cfg.get("content_profile", "") or "")

    # Resolve project directory for production readiness
    html_project_name = args.html or pre_cfg.get('paths', {}).get('html_project', '')
    project_dir = HTML_BASE / html_project_name if html_project_name else html_path.parent

    # Resolve output directory for cleanliness check
    output_dir = ROOT / "成果文件" / "视频"

    # Run all checks — production readiness FIRST (upstream gate)
    check_production_readiness(project_dir, pre_cfg, r)
    cfg = check_json_config(config_path, r)
    check_config_path_fields(cfg, r)
    check_html_images(html_path, r)
    check_gsap_timing(html_path, r)
    check_timeline_registration(html_path, r)
    check_config_html_sync(cfg, html_path, r)
    check_bak_consistency(html_path, r)
    check_scene_contracts(html_path, r)
    check_cue_declarations(html_path, cfg or pre_cfg, r)
    check_scene_visibility(html_path, r)
    check_element_reveal(html_path, r)
    check_chapter_bar(html_path, r)
    check_text_punctuation(html_path, r)
    check_design_rules(html_path, r)
    check_animation_density(html_path, cfg or pre_cfg, r)
    check_placeholder_images(html_path, r)
    check_image_clarity(html_path, r)  # NEW: Check critical images for clarity
    check_subtitle_safe_zone(html_path, r)
    check_narration_digits(cfg or pre_cfg, r)
    check_pause_declarations(cfg or pre_cfg, r)
    check_alignment_model_cache(cfg or pre_cfg, r)
    check_asset_signoff(project_dir, cfg or pre_cfg, html_path, r)
    check_output_dir_cleanliness(output_dir, r)

    # Structured log — 取证产物落点：_取证/YYYYMMDD/ 日期子目录（产生侧消因，
    # 使『散落在临时产物根目录』不再发生）。子目录名读自单一权威源配置，
    # 与 project_cleanup 共用同一份约定（_script_env.load_forensic_rules）。
    _now = datetime.now()
    forensic_sub = _script_env.load_forensic_rules()["forensic_subdir"]
    log_dir = _script_env.TEMP_BASE / forensic_sub / _now.strftime('%Y%m%d')
    log_path = log_dir / f"preflight_{args.mode}_{_now.strftime('%Y%m%d_%H%M%S')}.json"
    r.write_log(log_path, config_name=config_path.name, html_project=args.html or "")

    # Summary
    print("\n" + "=" * 60)
    if r.passed:
        print(f"RESULT: PASS ({len(r.warnings)} warning(s))")
        if r.warnings:
            print("Warnings:")
            for w in r.warnings:
                print(f"  ⚠ {w}")
        if args.mode == "audit":
            print("\nAudit mode: no hard blocks. Review warnings above.")
        else:
            print("\nSafe to proceed with render.")
        
        # 规范化输出：Result Object
        result = Result.success(
            data={
                "log_file": str(log_path),
                "mode": args.mode,
                "errors_count": len(r.errors),
                "warnings_count": len(r.warnings)
            },
            message=f"Preflight check passed ({args.mode} mode)"
        )
        result.print_to_stdout()
        sys.exit(result.code)
    else:
        print(f"RESULT: FAIL ({len(r.errors)} error(s), {len(r.warnings)} warning(s))")
        print("\nErrors:")
        for e in r.errors:
            print(f"  ✗ {e}")
        if r.warnings:
            print("Warnings:")
            for w in r.warnings:
                print(f"  ⚠ {w}")
        print("\nDO NOT RENDER. Fix the errors above first.")
        
        # 规范化输出：Result Object（失败情况）
        result = Result.failure(
            code=EXIT_CODE.GATE_FAILURE,
            message=f"Preflight check failed ({len(r.errors)} errors)",
            data={
                "log_file": str(log_path),
                "mode": args.mode,
                "errors_count": len(r.errors),
                "warnings_count": len(r.warnings),
                "errors": r.errors[:5]  # 返回前5个错误
            }
        )
        result.print_to_stdout()
        sys.exit(result.code)


if __name__ == "__main__":
    main()
