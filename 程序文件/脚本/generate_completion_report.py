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
    state_file: Optional[str] = None
) -> Dict[str, Any]:
    """生成数据驱动的完工报告
    
    参数：
      project_name: 项目名称
      video_file: 视频文件路径
      subtitle_file: 字幕文件路径
      state_file: pipeline_state.json 路径
    
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
        # 与 config 声明值的一致性拦截由 media_qa_gate 检查13负责，报告只如实记录。
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
                            all_valid = False
                    else:
                        report["issues"].append(f"Pipeline step '{step}' not recorded")
                        all_valid = False

                # P0（2026-07-29）：产物-状态一致性检查。成片 mtime 与
                # postprocess 完成时刻偏差超过容差 → 成片可能在流水线之外
                # 被改写（eiway-122-wall 逃逸脚本直接覆写成片的教训）。
                # 判定依据：正常路径下成片由 postprocess 步骤产出，其 mtime
                # 与该步骤完成时刻应基本一致；容差 10s 覆盖文件系统时间精度
                # 与流水线收尾写入（重命名/faststart），正常项目不会误伤。
                # 用绝对差值：mtime 早于完成时刻超差同样可疑（陈旧产物/时钟回拨）。
                _pp_completed = pipeline_steps.get("postprocess", {}).get("completed")
                if _pp_completed and video_path.exists():
                    try:
                        _pp_ts = datetime.fromisoformat(_pp_completed).timestamp()
                        _video_ts = video_path.stat().st_mtime
                        _tolerance_s = 10.0
                        _lag = _video_ts - _pp_ts
                        _consistent = abs(_lag) <= _tolerance_s
                        report["data_sources"]["state_product_check"] = {
                            "postprocess_completed": _pp_completed,
                            "video_mtime": datetime.fromtimestamp(_video_ts).isoformat(),
                            "video_mtime_minus_completed_s": round(_lag, 3),
                            "tolerance_s": _tolerance_s,
                            "basis": ("成片 mtime 与 postprocess 完成时刻的绝对差不得超过容差"
                                      "（10s，覆盖文件系统时间精度与收尾重命名/faststart 写入）；"
                                      "超差说明成片在流水线之外被改写或为陈旧产物，拒收"),
                        }
                        report["validation"]["product_state_consistent"] = _consistent
                        if not _consistent:
                            report["issues"].append(
                                f"Video mtime deviates {_lag:+.1f}s from postprocess "
                                f"completion (tolerance ±{_tolerance_s}s) — product may "
                                f"have been modified outside the pipeline; delivery rejected")
                            all_valid = False
                    except (ValueError, OSError) as _e:
                        report["issues"].append(
                            f"State-product consistency check failed: {_e}")
                        all_valid = False

                # 数据源3b：验证/质检结果追溯（verifications 节，由 pipeline_runner 合并写入）
                # 向后兼容：旧 state 无此节 → 标注不可追溯（既不崩溃也不误判为失败）。
                verifications = pipeline_state.get("verifications", {})
                if verifications:
                    report["data_sources"]["verifications"] = {
                        k: {"passed": v.get("passed")} for k, v in verifications.items()
                    }
                    for vkey, vres in verifications.items():
                        v_passed = bool(vres.get("passed", False))
                        report["validation"][f"verification_{vkey}_passed"] = v_passed
                        if not v_passed:
                            errs = vres.get("errors", [])
                            report["issues"].append(
                                f"Verification '{vkey}' failed: "
                                f"{errs[0] if errs else 'see result details'}"
                            )
                            all_valid = False
                else:
                    report["data_sources"]["verifications"] = "not_recorded (legacy state, not traceable)"
            except Exception as e:
                report["issues"].append(f"Failed to read pipeline state: {e}")
                all_valid = False
    else:
        report["issues"].append("Pipeline state file not provided")
        # 这不一定是失败，但应该标记
    
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
    
    return report

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate data-driven project completion report')
    parser.add_argument('--project-name', required=True, help='Project name')
    parser.add_argument('--video-file', required=True, help='Path to output video file')
    parser.add_argument('--subtitle-file', help='Path to output subtitle file')
    parser.add_argument('--state-file', help='Path to pipeline_state.json')
    parser.add_argument('--output-file', help='Output report file path')
    
    args = parser.parse_args()
    
    report = generate_report(
        project_name=args.project_name,
        video_file=args.video_file,
        subtitle_file=args.subtitle_file,
        state_file=args.state_file
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
    
    # 保存报告文件
    if args.output_file:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] Report saved: {args.output_file}")
    
    # 以状态码退出（更新为 Result Object）
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
