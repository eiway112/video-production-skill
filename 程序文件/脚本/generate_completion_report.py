#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
from script_interface import Result, EXIT_CODE
# -*- coding: utf-8 -*-
"""
数据驱动的项目完工报告生成器（P0-08 整改）

功能：
  从真实执行数据生成完工报告
  不允许任何硬编码的"PASS"或"COMPLETED"
  
  数据源（按优先级）：
  1. pipeline_state.json （流水线执行状态）
  2. ffprobe 输出 （视频元数据）
  3. 字幕解析结果 （字幕统计）
  4. 文件系统 （文件大小、修改时间）
  
用法：
  python generate_completion_report.py \
      --project-name "项目名称" \
      --video-file "output.mp4" \
      --subtitle-file "output.srt" \
      --state-file "pipeline_state.json" \
      --output-file "completion_report.json"
"""

import json
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 交付门禁自适应参数表（单一权威源：config/quality/delivery_gate_rules.json）
_DELIVERY_RULES_PATH = (
    Path(__file__).resolve().parent.parent / "配置" / "config" / "quality" / "delivery_gate_rules.json"
)
# documented 默认值与配置文件保持一致；读取失败回退并告警，绝不让门禁静默旁路
_DELIVERY_RULE_DEFAULTS = {
    "product_consistency": {
        "mtime_tolerance_base_seconds": 10.0,
        "mtime_tolerance_ratio_of_duration": 0.05,
        "mtime_tolerance_max_seconds": 120.0,
    },
    "audit": {
        "prohibited_terms": ["final", "v01", "render", "raw", "tmp", "draft"],
        "require_srt_same_stem": True,
    }
}


def _load_delivery_rules() -> Dict[str, Any]:
    """加载交付门禁规则；失败回退 documented 默认值（打印告警，不抛异常）。"""
    try:
        with open(_DELIVERY_RULES_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        merged = json.loads(json.dumps(_DELIVERY_RULE_DEFAULTS))
        for key, val in loaded.items():
            if not key.startswith("$") and isinstance(val, dict):
                merged.setdefault(key, {}).update(val)
        return merged
    except (OSError, json.JSONDecodeError) as e:
        print(f"  WARNING: delivery_gate_rules.json unreadable ({e}) — "
              f"falling back to built-in defaults", file=sys.stderr)
        return json.loads(json.dumps(_DELIVERY_RULE_DEFAULTS))


def _adaptive_mtime_tolerance(video_duration_s: float) -> float:
    """产物 mtime 容差随视频时长自适应缩放（P0 整改，2026-08-04 prefab 复盘）。

    教训：固定 ±10s 容差误判拒收了 618.9s 长视频的合格成片（实测偏差 -12.7s），
    触发后续 9 小时连环重渲。容差 = base + ratio × 时长，封顶 max：
    短视频保持严格（137s → ~17s），长视频按比例放宽（618.9s → ~41s），
    封顶 120s 保证"隔天陈旧产物"量级的偏移仍会被拒收。
    """
    pc = _load_delivery_rules().get("product_consistency", {})
    base = float(pc.get("mtime_tolerance_base_seconds", 10.0))
    ratio = float(pc.get("mtime_tolerance_ratio_of_duration", 0.05))
    cap = float(pc.get("mtime_tolerance_max_seconds", 120.0))
    dur = max(0.0, float(video_duration_s or 0.0))
    return min(cap, base + ratio * dur)


def run_delivery_audit(report: Dict[str, Any],
                       video_file: str,
                       subtitle_file: Optional[str],
                       state_file: Optional[str],
                       config_file: str) -> Dict[str, Any]:
    """交付审计（agent-wiki 角色分离落地：模型不得给自己打分）。

    背景：SKILL.md 旧版"自评量表"由执行者自评 5 维度，属 Generator 自证。
    本函数把 5 维度全部改为从真实数据推导，治具裁定，退出码说话：
      1. end_to_end_authenticity  ← 全步 passed + 音视频流真实存在
      2. gate_integrity           ← verifications 全过 + 无未测项 + 无 --force 绕过留痕
      3. delivery_compliance      ← 交付文件名无技术词 + srt 与 mp4 同基名
      4. storyboard_fidelity      ← narration_source 指针可解析、场景指纹可追溯
      5. completion_integrity     ← 报告状态 VALIDATED + ffprobe 实测 + 产物一致性已执行

    参数 config_file 为项目流水线配置（审计模式下必填，供 prohibited_terms 与
    narration_source 解析）。返回 {"dimensions": {...}, "passed": bool}。
    """
    import hashlib

    validation = report.get("validation", {})
    audit_rules = _load_delivery_rules().get("audit", {})
    dims: Dict[str, Dict[str, Any]] = {}

    # ── 维度1：端到端真实性 ──
    step_checks = {k: v for k, v in validation.items()
                   if k.startswith("step_") and k.endswith("_passed")}
    d1 = (bool(step_checks) and all(step_checks.values())
          and validation.get("has_video_stream", False)
          and validation.get("has_audio_stream", False))
    dims["end_to_end_authenticity"] = {
        "passed": d1,
        "evidence": f"{sum(step_checks.values())}/{len(step_checks)} steps passed, "
                    f"video_stream={validation.get('has_video_stream')}, "
                    f"audio_stream={validation.get('has_audio_stream')}",
    }

    # ── 维度2：门禁完整性 ──
    d2_reasons = []
    verif_checks = {k: v for k, v in validation.items()
                    if k.startswith("verification_") and k.endswith("_passed")}
    if not verif_checks:
        d2_reasons.append("verifications not recorded in state (gate evidence missing)")
    elif not all(verif_checks.values()):
        d2_reasons.append(f"{sum(1 for v in verif_checks.values() if not v)} verification(s) failed")
    # 未测＝门禁没跑完＝门禁不完整，不得因"无错误"记为通过（2026-09-18 审核根因一）
    untested_by_verif = {
        k[len("verification_"):-len("_untested")]: v
        for k, v in validation.items()
        if k.startswith("verification_") and k.endswith("_untested") and v
    }
    if untested_by_verif:
        d2_reasons.append(
            "verification(s) incomplete (UNTESTED is not a pass): "
            + ", ".join(f"{k}={n}" for k, n in sorted(untested_by_verif.items())))
    forced_run = None
    if state_file and Path(state_file).exists():
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                forced_run = json.load(f).get("forced_run")
        except (OSError, json.JSONDecodeError):
            forced_run = None
    if forced_run:
        d2_reasons.append(f"--force bypass recorded at {forced_run.get('at', '?')}")
    dims["gate_integrity"] = {
        "passed": not d2_reasons,
        "evidence": "; ".join(d2_reasons) if d2_reasons
                    else f"{len(verif_checks)} verifications passed, no --force trace",
    }

    # ── 维度3：交付合规 ──
    d3_reasons = []
    cfg: Dict[str, Any] = {}
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        d3_reasons.append(f"config unreadable: {e}")
    prohibited = (cfg.get("delivery") or {}).get(
        "prohibited_terms", audit_rules.get("prohibited_terms", []))
    delivered = [Path(video_file).stem]
    if subtitle_file:
        delivered.append(Path(subtitle_file).stem)
    for stem in delivered:
        low = stem.lower()
        hits = [t for t in prohibited if str(t).lower() in low]
        if hits:
            d3_reasons.append(f"prohibited term(s) {hits} in '{stem}'")
    if audit_rules.get("require_srt_same_stem", True):
        if not subtitle_file:
            d3_reasons.append("subtitle file not provided — cannot confirm same-stem delivery")
        elif Path(subtitle_file).stem != Path(video_file).stem:
            d3_reasons.append(
                f"srt stem '{Path(subtitle_file).stem}' != mp4 stem '{Path(video_file).stem}'")
    dims["delivery_compliance"] = {
        "passed": not d3_reasons,
        "evidence": "; ".join(d3_reasons) if d3_reasons
                    else f"no prohibited terms, srt/mp4 same stem",
    }

    # ── 维度4：分镜忠实度（narration 指针闭环可追溯）──
    d4_reasons = []
    narration_evidence = "narration_source not declared (inline scenes in config)"
    if 'narration_source' in cfg:
        try:
            # 复用 _script_env 的权威解析（P0-03 指针闭环：失效指针硬报错）
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from _script_env import resolve_narration_scenes
            scenes, source_path = resolve_narration_scenes(
                cfg, config_file, float(cfg.get("cover_duration", 0) or 0))
            digest = hashlib.sha256(
                Path(source_path).read_bytes()).hexdigest()[:16]
            narration_evidence = (f"{Path(source_path).name}: "
                                  f"{len(scenes)} content scenes, sha256={digest}")
        except Exception as e:
            d4_reasons.append(f"narration_source broken at audit time: {e}")
    dims["storyboard_fidelity"] = {
        "passed": not d4_reasons,
        "evidence": "; ".join(d4_reasons) if d4_reasons else narration_evidence,
    }
    report["data_sources"]["narration_authority"] = narration_evidence

    # ── 维度5：收尾完成度 ──
    d5_reasons = []
    if report.get("status") != "VALIDATED":
        d5_reasons.append(f"report status is {report.get('status')}, not VALIDATED")
    video_src = report.get("data_sources", {}).get("video_file", {})
    if not video_src.get("ffprobe_available"):
        d5_reasons.append("ffprobe metadata unavailable — duration not measured")
    if "product_state_consistent" not in validation:
        d5_reasons.append("product-state consistency check not executed")
    elif not validation["product_state_consistent"]:
        d5_reasons.append("product-state consistency check failed")
    dims["completion_integrity"] = {
        "passed": not d5_reasons,
        "evidence": "; ".join(d5_reasons) if d5_reasons
                    else "status VALIDATED, ffprobe measured, mtime consistency passed",
    }

    passed = all(d["passed"] for d in dims.values())
    return {"dimensions": dims, "passed": passed,
            "basis": "agent-wiki 角色分离：5 维度全部由真实数据推导，治具裁定，禁止自证"}


def ffprobe_get_metadata(video_path: str) -> Dict[str, Any]:
    """使用 ffprobe 获取视频元数据"""
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_format', '-show_streams',
                '-of', 'json',
                video_path
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}

def parse_srt_basic(srt_path: str) -> Dict[str, Any]:
    """基本的 SRT 字幕统计"""
    try:
        with open(srt_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        
        lines = content.split('\n')
        entry_count = len([l for l in lines if l.strip().isdigit()])
        
        # 提取时间范围
        import re
        times = re.findall(r'(\d+):(\d+):(\d+),(\d+)\s+-->\s+(\d+):(\d+):(\d+),(\d+)',
                          content)
        
        if times:
            # 最后一个条目的结束时间
            last_time = times[-1]
            last_end_ms = (int(last_time[4]) * 3600 +
                          int(last_time[5]) * 60 +
                          int(last_time[6])) * 1000 + int(last_time[7])
            last_end_s = last_end_ms / 1000.0
        else:
            last_end_s = 0.0
        
        return {
            'entry_count': entry_count,
            'lines': len(lines),
            'last_end_time_s': last_end_s,
            'format_valid': entry_count > 0
        }
    except Exception as e:
        return {'error': str(e), 'format_valid': False}

def generate_report(
    project_name: str,
    video_file: str,
    subtitle_file: Optional[str] = None,
    state_file: Optional[str] = None,
    audit: bool = False,
    config_file: Optional[str] = None
) -> Dict[str, Any]:
    """生成数据驱动的完工报告
    
    参数：
      project_name: 项目名称
      video_file: 视频文件路径
      subtitle_file: 字幕文件路径
      state_file: pipeline_state.json 路径
      audit: True 时追加交付审计（5 维度治具裁定，见 run_delivery_audit）
      config_file: 项目流水线配置路径（audit=True 时必填）
    
    返回：
      完工报告字典
    """
    
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_name": project_name,
        "status": "UNKNOWN",
        "issues": [],
        "data_sources": {},
        "validation": {}
    }
    
    video_path = Path(video_file)
    all_valid = True
    
    # 数据源1：视频文件
    if not video_path.exists():
        report["issues"].append(f"Video file not found: {video_file}")
        all_valid = False
    else:
        video_metadata = ffprobe_get_metadata(str(video_path))
        report["data_sources"]["video_file"] = {
            "path": str(video_path),
            "size_bytes": video_path.stat().st_size,
            "size_mb": video_path.stat().st_size / (1024 * 1024),
            "modified_at": datetime.fromtimestamp(video_path.stat().st_mtime).isoformat(),
            "ffprobe_available": "format" in video_metadata
        }
        
        # 验证：有效的 ffprobe 输出
        if "format" in video_metadata:
            fmt = video_metadata.get("format", {})
            duration = float(fmt.get("duration", -1))
            report["validation"]["video_duration"] = duration
            
            if duration <= 0:
                report["issues"].append(f"Invalid video duration: {duration}")
                all_valid = False
        
        # 验证：音频流存在
        streams = video_metadata.get("streams", [])
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        
        # 实测媒体事实（fps/分辨率/编码）：供交付说明直接引用，避免手工填写漂移；
        # 与 config 声明值的一致性拦截由 media_qa_gate 的 declared_matches_measured 负责，报告只如实记录。
        if video_streams:
            vs = video_streams[0]
            fps = None
            m = re.match(r'^(\d+)/(\d+)$', vs.get("r_frame_rate", ""))
            if m and int(m.group(2)) > 0:
                fps = int(m.group(1)) / int(m.group(2))
            report["data_sources"]["video_file"]["measured_fps"] = fps
            report["data_sources"]["video_file"]["measured_resolution"] = (
                f"{vs.get('width')}x{vs.get('height')}"
                if vs.get('width') and vs.get('height') else None)
            report["data_sources"]["video_file"]["video_codec"] = vs.get("codec_name")
        if audio_streams:
            aus = audio_streams[0]
            report["data_sources"]["video_file"]["audio_codec"] = aus.get("codec_name")
            report["data_sources"]["video_file"]["audio_sample_rate"] = aus.get("sample_rate")
        
        report["validation"]["has_video_stream"] = len(video_streams) > 0
        report["validation"]["has_audio_stream"] = len(audio_streams) > 0
        
        if not report["validation"]["has_video_stream"]:
            report["issues"].append("No video stream found in output")
            all_valid = False
        
        if not report["validation"]["has_audio_stream"]:
            report["issues"].append("No audio stream found in output")
            all_valid = False
    
    # 数据源2：字幕文件
    if subtitle_file:
        subtitle_path = Path(subtitle_file)
        if not subtitle_path.exists():
            report["issues"].append(f"Subtitle file not found: {subtitle_file}")
            all_valid = False
        else:
            srt_stats = parse_srt_basic(str(subtitle_path))
            report["data_sources"]["subtitle_file"] = {
                "path": str(subtitle_path),
                "size_bytes": subtitle_path.stat().st_size,
                "size_kb": subtitle_path.stat().st_size / 1024,
                "modified_at": datetime.fromtimestamp(subtitle_path.stat().st_mtime).isoformat(),
                **srt_stats
            }
            
            # 验证：字幕格式有效
            report["validation"]["srt_format_valid"] = srt_stats.get("format_valid", False)
            if not srt_stats.get("format_valid", False):
                report["issues"].append("Subtitle file format invalid or empty")
                all_valid = False
            
            # 验证：字幕不越过视频时长
            if (report["validation"].get("video_duration", -1) > 0 and
                srt_stats.get("last_end_time_s", 0) > 0):
                duration = report["validation"]["video_duration"]
                srt_end = srt_stats.get("last_end_time_s", 0)
                overflow = srt_end - duration
                report["validation"]["srt_time_valid"] = overflow <= 0.5
                
                if overflow > 0.5:
                    report["issues"].append(
                        f"Subtitle overflow: ends at {srt_end:.1f}s, "
                        f"video is {duration:.1f}s (+{overflow:.1f}s)"
                    )
                    all_valid = False
    
    # 数据源3：Pipeline 状态
    if state_file:
        state_path = Path(state_file)
        if state_path.exists():
            try:
                with open(state_path, 'r', encoding='utf-8') as f:
                    pipeline_state = json.load(f)
                
                report["data_sources"]["pipeline_state"] = state_path.name
                
                # 验证：所有必要步骤是否通过
                required_steps = ["preflight", "tts", "timeline", "render", "verify", "postprocess"]
                pipeline_steps = pipeline_state.get("steps", {})
                
                for step in required_steps:
                    if step in pipeline_steps:
                        step_record = pipeline_steps[step]
                        # Support both fingerprint-style "passed" boolean and
                        # pipeline_runner-style "status": "passed" records.
                        is_passed = bool(
                            step_record.get("passed", False)
                            or step_record.get("status", "").lower() == "passed"
                        )
                        # P0（2026-07-29）：类型化跳过（tts_enabled=false 声明的
                        # 纯BGM路径，reason 以 "tts_disabled" 开头）视为满足——
                        # 这是 config 显式声明的合法路径，记入 typed_skips 可审计；
                        # quick-fix 跳过（reason='quick-fix mode'）仍按未通过处理。
                        if (not is_passed and step in ("tts", "timeline")
                                and str(step_record.get("status", "")).lower() == "skipped"
                                and str(step_record.get("reason", "")).startswith("tts_disabled")):
                            is_passed = True
                            report["data_sources"].setdefault("typed_skips", {})[step] = \
                                step_record.get("reason", "")
                        report["validation"][f"step_{step}_passed"] = is_passed
                        
                        if not is_passed:
                            # 透传结构化错误码（有则显示；旧 state 无此字段时不显示）
                            ecode = step_record.get("error_code")
                            suffix = f" [{ecode}]" if ecode else ""
                            report["issues"].append(f"Pipeline step '{step}' did not pass{suffix}")
                            # P0 整改（2026-08-04 prefab 复盘）：失败步骤的子进程日志
                            # 路径透传到报告——用户不再面对"黑盒失败"，可直接打开日志排障
                            log_path = step_record.get("log_path")
                            if log_path:
                                report["issues"].append(f"  ↳ step log: {log_path}")
                            all_valid = False
                    else:
                        report["issues"].append(f"Pipeline step '{step}' not recorded")
                        all_valid = False

                # P0（2026-07-29）：产物-状态一致性检查。成片 mtime 与
                # postprocess 完成时刻偏差超过容差 → 成片可能在流水线之外
                # 被改写（eiway-122-wall 逃逸脚本直接覆写成片的教训）。
                # 判定依据：正常路径下成片由 postprocess 步骤产出，其 mtime
                # 与该步骤完成时刻应基本一致。
                # P0 整改（2026-08-04 prefab 复盘）：容差不再固定 10s，改为
                # base + ratio × 视频时长（封顶 max，参数见 delivery_gate_rules.json）。
                # 固定容差曾误判拒收 618.9s 合格成片（-12.7s 超差），诱发 9 小时连环重渲。
                # 用绝对差值：mtime 早于完成时刻超差同样可疑（陈旧产物/时钟回拨）。
                _pp_completed = pipeline_steps.get("postprocess", {}).get("completed")
                if _pp_completed and video_path.exists():
                    try:
                        _pp_ts = datetime.fromisoformat(_pp_completed).timestamp()
                        _video_ts = video_path.stat().st_mtime
                        _video_dur_for_tol = float(report["validation"].get("video_duration", 0) or 0)
                        _tolerance_s = round(_adaptive_mtime_tolerance(_video_dur_for_tol), 1)
                        _lag = _video_ts - _pp_ts
                        _consistent = abs(_lag) <= _tolerance_s
                        report["data_sources"]["state_product_check"] = {
                            "postprocess_completed": _pp_completed,
                            "video_mtime": datetime.fromtimestamp(_video_ts).isoformat(),
                            "video_mtime_minus_completed_s": round(_lag, 3),
                            "tolerance_s": _tolerance_s,
                            "tolerance_basis_duration_s": _video_dur_for_tol,
                            "basis": ("成片 mtime 与 postprocess 完成时刻的绝对差不得超过自适应容差"
                                      "（base 10s + 5% × 视频时长，封顶 120s，参数见"
                                      " config/quality/delivery_gate_rules.json）；"
                                      "超差说明成片在流水线之外被改写或为陈旧产物，拒收"),
                        }
                        report["validation"]["product_state_consistent"] = _consistent
                        if not _consistent:
                            report["issues"].append(
                                f"Video mtime deviates {_lag:+.1f}s from postprocess "
                                f"completion (adaptive tolerance ±{_tolerance_s}s for "
                                f"{_video_dur_for_tol:.0f}s video) — product may "
                                f"have been modified outside the pipeline; delivery rejected")
                            all_valid = False
                    except (ValueError, OSError) as _e:
                        report["issues"].append(
                            f"State-product consistency check failed: {_e}")
                        all_valid = False

                # 数据源b：验证/质检结果追溯（verifications 节，由 pipeline_runner 合并写入）
                # 向后兼容：旧 state 无此节 → 标注不可追溯（既不崩溃也不误判为失败）。
                verifications = pipeline_state.get("verifications", {})
                if verifications:
                    report["data_sources"]["verifications"] = {
                        k: {"passed": v.get("passed"),
                            "untested": len(v.get("untested") or []),
                            "not_applicable": len(v.get("not_applicable") or [])}
                        for k, v in verifications.items()
                    }
                    # 字幕时间戳来源分布透传到报告：交付说明 md 的实测值以本报告为
                    # 抄写面（temp 下的终检 JSON 不入 git，2026-09-19 普查裁定）。
                    for vres in verifications.values():
                        _src = (vres.get("media_facts") or {}).get(
                            "subtitle_timestamp_source")
                        if _src:
                            report["data_sources"]["subtitle_timestamp_source"] = _src
                            break
                    for vkey, vres in verifications.items():
                        v_passed = bool(vres.get("passed", False))
                        report["validation"][f"verification_{vkey}_passed"] = v_passed
                        # 未测计数与 passed 并列透出：报告不得把"没跑完的检查"记成"通过"，
                        # 也不得与"合法不适用"混算（后者是声明过的豁免）。
                        v_untested = list(vres.get("untested") or [])
                        report["validation"][f"verification_{vkey}_untested"] = len(v_untested)
                        if v_untested:
                            report["issues"].append(
                                f"Verification '{vkey}' incomplete: {len(v_untested)} "
                                f"check(s) UNTESTED — {v_untested[0]}"
                            )
                        if not v_passed:
                            errs = vres.get("errors", [])
                            report["issues"].append(
                                f"Verification '{vkey}' failed: "
                                f"{errs[0] if errs else 'see result details'}"
                            )
                            all_valid = False
                else:
                    report["data_sources"]["verifications"] = "not_recorded (legacy state, not traceable)"

                # 数据源c：渲染成本度量（render_metrics 节，pipeline_runner 写入，2026-08-19）
                # 只透传不裁定——成本不作质量门禁（预算门禁在渲染时点判定并留痕）；
                # 旧 state 无此节 → 不输出（向后兼容）。
                render_metrics = pipeline_state.get("render_metrics")
                if isinstance(render_metrics, dict):
                    cost = {
                        "full_render_attempts": render_metrics.get("full_render_attempts", 0),
                        "full_render_total_minutes": round(
                            float(render_metrics.get("full_render_total_seconds", 0)) / 60.0, 1),
                        "scene_patch_attempts": render_metrics.get("scene_patch_attempts", 0),
                        "scene_patch_hits": render_metrics.get("scene_patch_hits", 0),
                        "watchdog_kills": render_metrics.get("watchdog_kills", 0),
                    }
                    decision = pipeline_state.get("render_budget_decision")
                    if isinstance(decision, dict):
                        cost["budget_decision"] = decision
                    report["data_sources"]["render_cost"] = cost
            except Exception as e:
                report["issues"].append(f"Failed to read pipeline state: {e}")
                all_valid = False
        else:
            # P0（2026-07-29）：state 文件缺失曾被完全静默旁路（eiway-122-wall
            # 交付时无任何状态记录却生成了报告）——缺失即无法证明流水线执行过，
            # 显式拒收。
            report["issues"].append(
                f"Pipeline state file missing: {state_file} — cannot verify "
                f"pipeline execution; delivery rejected")
            all_valid = False
    else:
        # P0（2026-07-29）：未提供 state 同样无法核验流水线执行，不再只软提示。
        report["issues"].append(
            "Pipeline state file not provided — cannot verify pipeline execution; "
            "delivery rejected")
        all_valid = False
    
    # 总体判定（状态必须来自真实验证结果，禁止硬编码 COMPLETED/PASS/成功）
    if all_valid and not report["issues"]:
        report["status"] = "VALIDATED"
    elif all_valid and report["issues"]:
        report["status"] = "VALIDATED_WITH_ISSUES"
    else:
        report["status"] = "FAILED"
    
    # 验证摘要：只统计布尔型检查项。validation 里的非布尔项（如 video_duration 实测值）
    # 是测量数据而非检查结论，计入总数会产生“10/11通过但0失败”的幽灵项。
    bool_checks = {k: v for k, v in report["validation"].items() if isinstance(v, bool)}
    report["validation_summary"] = {
        "total_checks": len(bool_checks),
        "passed_checks": sum(1 for v in bool_checks.values() if v is True),
        "failed_checks": sum(1 for v in bool_checks.values() if v is False),
        "issues_count": len(report["issues"])
    }

    # 交付审计（--audit）：5 维度治具裁定，替代 SKILL.md 旧版自评量表。
    # 必须在 status/validation_summary 定型之后执行（维度5 消费它们）。
    if audit:
        if not config_file:
            raise ValueError("audit=True requires config_file (delivery audit needs pipeline config)")
        report["audit"] = run_delivery_audit(
            report, video_file, subtitle_file, state_file, config_file)

    return report

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate data-driven project completion report')
    parser.add_argument('--project-name', required=True, help='Project name')
    parser.add_argument('--video-file', required=True, help='Path to output video file')
    parser.add_argument('--subtitle-file', help='Path to output subtitle file')
    parser.add_argument('--state-file', help='Path to pipeline_state.json')
    parser.add_argument('--config-file', help='Pipeline config JSON path (required with --audit)')
    parser.add_argument('--audit', action='store_true',
                        help='Run delivery audit (5 dimensions, fixture-verdict; replaces self-scoring)')
    parser.add_argument('--output-file', help='Output report file path')
    
    args = parser.parse_args()

    if args.audit and not args.config_file:
        print("ERROR: --audit requires --config-file", file=sys.stderr)
        sys.exit(EXIT_CODE.CONFIG_ERROR)

    report = generate_report(
        project_name=args.project_name,
        video_file=args.video_file,
        subtitle_file=args.subtitle_file,
        state_file=args.state_file,
        audit=args.audit,
        config_file=args.config_file
    )
    
    # 输出报告
    print("[COMPLETION REPORT]")
    print(f"  Project: {report['project_name']}")
    print(f"  Status: {report['status']}")
    print(f"  Generated: {report['generated_at']}")
    print(f"  Validation: {report['validation_summary']['passed_checks']}/{report['validation_summary']['total_checks']} checks passed")
    
    if report['issues']:
        print(f"\n[ISSUES] ({len(report['issues'])} found)")
        for issue in report['issues']:
            print(f"  - {issue}")
    else:
        print(f"\n[OK] No issues found")

    # 审计段输出与裁定（治具裁定优先于报告状态：审计不过即交付拒收）
    audit = report.get("audit")
    if audit:
        print(f"\n[DELIVERY AUDIT] {'PASSED' if audit['passed'] else 'FAILED'}")
        for dim, res in audit["dimensions"].items():
            mark = "PASS" if res["passed"] else "FAIL"
            print(f"  [{mark}] {dim}: {res['evidence']}")
    
    # 保存报告文件
    if args.output_file:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] Report saved: {args.output_file}")
    
    # 以状态码退出（更新为 Result Object）
    # 审计具有最终裁定权：即使报告 VALIDATED，审计任一维度失败即交付拒收。
    audit_failed = bool(report.get("audit")) and not report["audit"]["passed"]
    if audit_failed:
        failed_dims = [d for d, r in report["audit"]["dimensions"].items() if not r["passed"]]
        result = Result.failure(
            code=EXIT_CODE.GATE_FAILURE,
            message=f"Delivery audit FAILED on dimension(s): {', '.join(failed_dims)}",
            data={"audit": report["audit"], "issues": report['issues']}
        )
        result.print_to_stdout()
        sys.exit(result.code)
    if report['status'] in ('VALIDATED', 'VALIDATED_WITH_ISSUES'):
        result = Result.success(
            data={
                "report_file": args.output_file,
                "status": report['status'],
                "passed_checks": report['validation_summary']['passed_checks']
            },
            message="Completion report generated successfully"
        )
        result.print_to_stdout()
        sys.exit(result.code)
    else:
        result = Result.failure(
            code=EXIT_CODE.GATE_FAILURE,
            message="Completion report failed validation",
            data={"issues": report['issues']}
        )
        result.print_to_stdout()
        sys.exit(result.code)

if __name__ == '__main__':
    main()
