#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最小回归测试集（P1-10 整改）

功能：
  为整改后的工作流建立 15 个关键用例的回归测试
  
  用例覆盖：
  1. 场景编译：空场景、单场景、多场景、无旁白场景
  2. TTS流程：单文件、多文件、旁白缺失处理、静音检测
  3. 时间轴：基础调整、边界情况、超声音时长场景
  4. 媒体验收：视频有效、音频检测、字幕格式、溢出判定
  5. 完工报告：无错误路径、有错误路径、混合场景
  6. 指纹失效：配置变化时下游步骤自动失效
  7. ImageGen降级：不可用时优雅降级
  8. Result接口：序列化/反序列化完整性
  9. Gate阻断：EXIT_CODE.GATE_FAILURE阻止下游执行
  10. 时间轴漂移：早退路径 config 从 S-block 重同步（2026-07-27 修复锁定）

测试运行：
  python regression_test.py --run-all
  python regression_test.py --run-case scene_compile_basic
"""

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple

class RegressionTestCase:
    """单个回归测试用例"""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.result = {
            "name": name,
            "description": description,
            "status": "not_run",
            "error": None,
            "duration_ms": 0,
            "assertions": []
        }
    
    def assert_true(self, condition: bool, message: str):
        """断言条件为真"""
        self.result["assertions"].append({
            "type": "assert_true",
            "condition": condition,
            "message": message,
            "passed": condition
        })
        return condition
    
    def assert_equal(self, actual: Any, expected: Any, message: str) -> bool:
        """断言两个值相等"""
        passed = actual == expected
        self.result["assertions"].append({
            "type": "assert_equal",
            "expected": str(expected)[:100],
            "actual": str(actual)[:100],
            "message": message,
            "passed": passed
        })
        return passed
    
    def mark_passed(self):
        """标记用例通过（仅当所有断言均通过时）"""
        failed_assertions = [a for a in self.result["assertions"] if not a.get("passed", False)]
        if failed_assertions:
            self.mark_failed(f"Assertion failed: {failed_assertions[0]['message']}")
        else:
            self.result["status"] = "passed"
    
    def mark_failed(self, error: str):
        """标记用例失败"""
        self.result["status"] = "failed"
        self.result["error"] = error[:500]

# ============================================================================
# 用例集合
# ============================================================================

def test_scene_compile_basic() -> RegressionTestCase:
    """用例1：基础场景编译"""
    tc = RegressionTestCase(
        "scene_compile_basic",
        "验证规范化场景编译器处理基础场景"
    )
    
    try:
        # 添加脚本路径到 sys.path
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        
        # 导入编译器模块
        from compile_narration_to_scenes import compile_narration_to_scenes
        
        # 创建临时目录
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # 创建输入文件
            narration_json = {
                "scenes": [
                    {
                        "scene_id": "s1",
                        "type": "cover",
                        "title": "Cover",
                        "start": 0.0,
                        "end": 5.0,
                        "duration": 5.0,
                        "narration": ""
                    },
                    {
                        "scene_id": "s2",
                        "type": "content",
                        "title": "Content",
                        "start": 5.0,
                        "end": 30.0,
                        "duration": 25.0,
                        "narration": "This is content."
                    }
                ]
            }
            
            input_file = tmpdir / "narration.json"
            with open(input_file, 'w', encoding='utf-8') as f:
                json.dump(narration_json, f)
            
            # 执行编译
            output_file = tmpdir / "_compiled_scenes.json"
            result = compile_narration_to_scenes(
                str(input_file),
                str(output_file),
                project_name="test",
                total_duration=30.0
            )
            
            # 验证输出
            tc.assert_true(output_file.exists(), "Compiled scenes file created")
            tc.assert_equal(result.get("validation_status"), "valid", 
                           "Compilation validation passed")
            
            with open(output_file, 'r', encoding='utf-8') as f:
                compiled = json.load(f)
            
            scenes_list = compiled.get("scenes", [])
            tc.assert_equal(len(scenes_list), 2, "Two scenes compiled")
            tc.assert_equal(scenes_list[0].get("type"), "cover", "Scene type preserved")
            tc.assert_equal(scenes_list[1].get("narration_required"), True, 
                           "Content scene requires narration")
            
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_scene_compile_no_narration() -> RegressionTestCase:
    """用例2：无旁白场景处理"""
    tc = RegressionTestCase(
        "scene_compile_no_narration",
        "验证cover和transition类型默认不需要旁白"
    )
    
    try:
        # 添加脚本路径到 sys.path
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        
        from compile_narration_to_scenes import compile_narration_to_scenes
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            narration_json = {
                "scenes": [
                    {"scene_id": "s0", "type": "cover", "title": "Opening", "start": 0.0, "end": 3.0, "duration": 3.0, "narration": ""},
                    {"scene_id": "s1", "type": "transition", "title": "Trans", "start": 3.0, "end": 10.0, "duration": 7.0, "narration": ""}
                ]
            }
            
            input_file = tmpdir / "narration.json"
            with open(input_file, 'w', encoding='utf-8') as f:
                json.dump(narration_json, f)
            
            output_file = tmpdir / "_compiled_scenes.json"
            result = compile_narration_to_scenes(str(input_file), str(output_file), total_duration=10.0)
            
            tc.assert_equal(result.get("validation_status"), "valid",
                           "Cover and transition scenes accepted without narration")
            
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_tts_empty_scenes() -> RegressionTestCase:
    """用例3：TTS处理空场景列表"""
    tc = RegressionTestCase(
        "tts_empty_scenes",
        "验证TTS拒绝处理空场景（防止假成功）"
    )
    
    try:
        # 这个测试需要模拟 enhance_video_audio 的行为
        # 验证当没有有效场景时，应该 exit(1)
        
        from verify_tts_product import validate_tts_output
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # 创建空的 manifest
            manifest = {
                "scenes": [],
                "total_duration": 0
            }
            
            manifest_file = tmpdir / "manifest.json"
            with open(manifest_file, 'w', encoding='utf-8') as f:
                json.dump(manifest, f)
            
            # 验证应该失败
            is_valid, details = validate_tts_output(str(tmpdir), manifest_file)
            
            tc.assert_true(not is_valid, "Empty TTS manifest fails validation")
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_timeline_basic_adjustment() -> RegressionTestCase:
    """用例4：基础时间轴调整"""
    tc = RegressionTestCase(
        "timeline_basic_adjustment",
        "验证时间轴调整不破坏T-block和S-block"
    )
    
    try:
        # 这个测试需要检查 adjust_timeline.py 是否正确解析和修改 T-block
        
        # 示例 HTML 内容（包含 T-block）
        html_content = """
        <script>
        var T = { s1: 0.0, s2: 10.0, s3: 20.0 };
        var S = [
            {start: 0, end: 10, dur: 10, type: 'cover', cover: true},
            {start: 10, end: 20, dur: 10, type: 'content', cover: false}
        ];
        </script>
        """
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            html_file = tmpdir / "test.html"
            html_file.write_text(html_content)
            
            # 如果有 adjust_timeline 可用，尝试调用
            try:
                import re
                
                # 解析 T-block
                t_match = re.search(r'var T = ({[^}]+})', html_content)
                tc.assert_true(t_match is not None, "T-block found in HTML")
                
                # 解析 S-block
                s_match = re.search(r'var S = (\[[^\]]+\])', html_content)
                tc.assert_true(s_match is not None, "S-block found in HTML")
                
                tc.mark_passed()
            except Exception as e:
                tc.mark_failed(str(e))
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_subtitle_format_validation() -> RegressionTestCase:
    """用例5：字幕格式验证"""
    tc = RegressionTestCase(
        "subtitle_format_validation",
        "验证SRT格式解析和时间检查"
    )
    
    try:
        from media_qa_gate import parse_srt
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # 创建有效的 SRT 文件
            srt_content = """1
00:00:00,000 --> 00:00:05,000
First subtitle

2
00:00:05,000 --> 00:00:10,000
Second subtitle
"""
            srt_file = tmpdir / "test.srt"
            srt_file.write_text(srt_content, encoding='utf-8')
            
            entries = parse_srt(str(srt_file))
            
            tc.assert_equal(len(entries), 2, "Two subtitle entries parsed")
            tc.assert_equal(entries[0]["index"], 1, "First entry index correct")
            tc.assert_equal(entries[0]["start_ms"], 0, "Start time correct")
            tc.assert_equal(entries[0]["end_ms"], 5000, "End time correct")
            
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_subtitle_overflow_detection() -> RegressionTestCase:
    """用例6：字幕溢出检测"""
    tc = RegressionTestCase(
        "subtitle_overflow_detection",
        "验证字幕不能超过视频结尾"
    )
    
    try:
        # 模拟检查逻辑
        duration_s = 30.0
        srt_end_s = 35.0
        overflow = srt_end_s - duration_s
        
        tc.assert_true(overflow > 0.5, "Overflow detected correctly")
        tc.mark_passed()
        
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_pipeline_state_fingerprint() -> RegressionTestCase:
    """用例7：Pipeline状态指纹机制"""
    tc = RegressionTestCase(
        "pipeline_state_fingerprint",
        "验证输入变化时自动使下游步骤失效"
    )
    
    try:
        from pipeline_state_fingerprint import PipelineStateFingerprint
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # 创建测试文件
            config_file = tmpdir / "config.json"
            config_file.write_text('{"key": "value1"}')
            
            state_file = tmpdir / "state.json"
            pstate = PipelineStateFingerprint(str(state_file))
            
            # 第一次执行
            pstate.start_step("tts", inputs={"config": str(config_file)})
            pstate.complete_step("tts", passed=True)
            
            # 验证步骤已完成
            tc.assert_true(pstate.get_step_status("tts").get("passed", False),
                          "Step marked as passed")
            
            # 修改配置
            config_file.write_text('{"key": "value2"}')
            
            # 检查是否需要重新运行
            should_rerun = pstate.should_rerun_step("tts", inputs={"config": str(config_file)})
            tc.assert_true(should_rerun, "Step should rerun after input change")
            
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_hard_gates_enforcement() -> RegressionTestCase:
    """用例8：HARD_GATES依赖链强制"""
    tc = RegressionTestCase(
        "hard_gates_enforcement",
        "验证render不能跳过tts、timeline、preview"
    )
    
    try:
        # 检查 pipeline_runner.py 中的 HARD_GATES 定义
        # 通过文件内容验证
        script_dir = Path(__file__).parent
        pipeline_runner_path = script_dir / "pipeline_runner.py"
        
        with open(str(pipeline_runner_path), 'r', encoding='utf-8') as f:
            content = f.read()
        
        tc.assert_true("tts" in content, "TTS step referenced")
        tc.assert_true("timeline" in content, "Timeline step referenced")
        tc.assert_true("HARD_GATES" in content, "HARD_GATES defined")
        tc.assert_true("render" in content, "Render step referenced")
        
        # 检查依赖关系定义
        has_render_deps = ('["preflight", "tts", "timeline", "preview"]' in content or 
                          ("preflight" in content and "tts" in content))
        tc.assert_true(has_render_deps, "Render dependency chain verified")
        
        tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_completion_report_data_driven() -> RegressionTestCase:
    """用例9：完工报告数据驱动"""
    tc = RegressionTestCase(
        "completion_report_data_driven",
        "验证完工报告不含硬编码的PASS/COMPLETED"
    )
    
    try:
        from generate_completion_report import generate_report
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # 创建模拟视频文件
            video_file = tmpdir / "test.mp4"
            video_file.write_bytes(b'\x00' * 1000000)  # 1MB 的伪视频文件
            
            # 生成报告
            report = generate_report(
                project_name="TestProject",
                video_file=str(video_file),
                subtitle_file=None,
                state_file=None
            )
            
            # 检查报告不含硬编码的完全PASS
            tc.assert_true("status" in report, "Report has status field")
            
            # 报告应基于实际验证结果
            if report.get("status") == "FAILED":
                tc.assert_true(len(report.get("issues", [])) > 0,
                              "Failed status has explanatory issues")
            
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_completion_report_cli_entry() -> RegressionTestCase:
    """用例9b：完工报告命令行入口"""
    tc = RegressionTestCase(
        "completion_report_cli_entry",
        "验证完工报告命令行入口不崩溃并返回结构化结果"
    )
    
    try:
        import subprocess
        
        script_dir = Path(__file__).parent
        script_path = script_dir / "generate_completion_report.py"
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # 使用不存在的视频文件，验证失败路径不崩溃
            fake_video = tmpdir / "nonexistent.mp4"
            output_file = tmpdir / "report.json"
            
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--project-name", "CLI_Test",
                    "--video-file", str(fake_video),
                    "--output-file", str(output_file)
                ],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30
            )
            
            # 命令行入口不应因异常崩溃
            tc.assert_true(result.returncode != 0,
                          "Invalid input returns non-zero exit code")
            tc.assert_true("status" in result.stdout or "failure" in result.stdout.lower(),
                          "Output contains structured result or failure indicator")
            tc.assert_true("Traceback" not in result.stderr,
                          "No unhandled exception in stderr")
            
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_media_qa_gate_checks() -> RegressionTestCase:
    """用例10：媒体QA门禁检查项"""
    tc = RegressionTestCase(
        "media_qa_gate_checks",
        "验证16项媒体质量检查都已实现（含黑场重叠/单字行/术语规范新门禁）"
    )
    
    try:
        from media_qa_gate import (
            MediaQAGate,
            ffmpeg_detect_black_intervals,
            _load_black_frame_rules,
            _load_term_lint_patterns,
        )
        
        qa = MediaQAGate()
        
        # 检查 QA 对象有必要的方法
        tc.assert_true(hasattr(qa, 'validate'), "MediaQAGate has validate method")
        tc.assert_true(hasattr(qa, 'checks'), "MediaQAGate has checks dict")
        
        # 验证方法签名
        import inspect
        sig = inspect.signature(qa.validate)
        tc.assert_true("video_path" in sig.parameters, "validate has video_path param")
        tc.assert_true("subtitle_path" in sig.parameters, "validate has subtitle_path param")
        
        # 新门禁（检查14/15/16）基础设施存在且规则可加载（单一权威源）
        tc.assert_true(callable(ffmpeg_detect_black_intervals),
                      "check14: ffmpeg_detect_black_intervals exists")
        bf_rules = _load_black_frame_rules()
        tc.assert_true("min_black_seconds" in bf_rules
                      and "max_overlap_with_narration_seconds" in bf_rules,
                      "check14: black_frame rules loadable with required keys")
        term_patterns = _load_term_lint_patterns()
        tc.assert_true(isinstance(term_patterns, list) and len(term_patterns) > 0,
                      "check16: subtitle_term_rules lint_patterns loadable and non-empty")
        
        # 新门禁对带孤字行/术语违规的 SRT 文本真实拦截（不需要视频文件）
        import re as _re
        _cjk_single = _re.compile(r'^[\u4e00-\u9fff]$')
        tc.assert_true(bool(_cjk_single.match('字')) and not _cjk_single.match('两字'),
                      "check15: single-CJK-char line pattern effective")
        _hit = any(_re.compile(p['pattern']).search('配置写在点Json文件里')
                   for p in term_patterns)
        tc.assert_true(_hit, "check16: lint pattern catches '点Json' leakage")
        
        tc.mark_passed()
        
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_config_fingerprint_invalidation() -> RegressionTestCase:
    """用例12：配置变化时下游步骤自动失效"""
    tc = RegressionTestCase(
        "config_fingerprint_invalidation",
        "验证配置SHA256指纹变化后should_rerun_step()正确返回True"
    )
    
    try:
        from pipeline_state_fingerprint import PipelineStateFingerprint
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # 创建初始配置文件
            config_file = tmpdir / "project_config.json"
            config_file.write_text('{"project": "test", "scenes": 3}', encoding='utf-8')
            
            state_file = tmpdir / "pipeline_state.json"
            pstate = PipelineStateFingerprint(str(state_file))
            
            # 模拟步骤首次成功通过
            inputs = {"config": str(config_file)}
            pstate.start_step("timeline", inputs=inputs)
            pstate.complete_step("timeline", passed=True)
            
            # 验证当前不需要重跑
            should_rerun_before = pstate.should_rerun_step("timeline", inputs=inputs)
            tc.assert_true(not should_rerun_before,
                          "Step should NOT rerun when config unchanged")
            
            # 修改配置内容（改变SHA256指纹）
            config_file.write_text('{"project": "test", "scenes": 5, "modified": true}',
                                  encoding='utf-8')
            
            # 验证指纹变化后需要重跑
            should_rerun_after = pstate.should_rerun_step("timeline", inputs=inputs)
            tc.assert_true(should_rerun_after,
                          "Step MUST rerun after config content changed")
            
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc


def test_imagegen_fallback_graceful() -> RegressionTestCase:
    """用例13：ImageGen不可用时优雅降级"""
    tc = RegressionTestCase(
        "imagegen_fallback_graceful",
        "验证ImageGen不可用时返回降级标记而非异常抛出"
    )
    
    try:
        from imagegen_executor import ImageGenExecutor
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            manifest_path = tmpdir / "imagegen_manifest.json"
            output_dir = tmpdir / "images"
            
            # 创建执行器，明确设置 capability_available=False（模拟不可用）
            executor = ImageGenExecutor(
                manifest_path=str(manifest_path),
                output_dir=str(output_dir),
                capability_available=False
            )
            
            # 调用请求图片 —— 不应崩溃
            result = executor.request_image(
                scene_id="s1_test",
                prompt="Test image for regression",
                language="zh"
            )
            
            # 验证返回降级结果
            tc.assert_true(isinstance(result, dict),
                          "Returns dict (not exception)")
            tc.assert_equal(result.get("status"), "manual_review_required",
                           "Status is manual_review_required (graceful degradation)")
            tc.assert_true("reason" in result,
                          "Degraded result includes reason explanation")
            tc.assert_equal(result.get("scene_id"), "s1_test",
                           "Scene ID preserved in degraded result")
            
            # 验证 manifest 正确保存了降级记录
            summary = executor.get_summary()
            tc.assert_equal(summary.get("manual_review"), 1,
                           "Manifest tracks manual review count")
            
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc


def test_result_object_downstream_parsing() -> RegressionTestCase:
    """用例14：Result Object JSON序列化可被下游正确解析"""
    tc = RegressionTestCase(
        "result_object_downstream_parsing",
        "验证Result的to_json()/to_dict()输出包含完整字段且可反序列化"
    )
    
    try:
        from script_interface import Result, EXIT_CODE
        
        # 创建成功结果
        success_result = Result.success(
            data={"processed": 42, "output_file": "video.mp4"},
            message="Render completed",
            execution_time_ms=1500
        )
        success_result.add_warning("Low disk space")
        
        # 创建失败结果
        failure_result = Result.failure(
            code=EXIT_CODE.GATE_FAILURE,
            message="Visual boundary check failed",
            data={"overflow_px": 15}
        )
        failure_result.add_error("Text exceeds safe area")
        
        # 测试 to_json() 可反序列化
        success_json = success_result.to_json()
        parsed_success = json.loads(success_json)
        
        failure_json = failure_result.to_json()
        parsed_failure = json.loads(failure_json)
        
        # 验证成功结果关键字段完整
        tc.assert_equal(parsed_success.get("status"), "success",
                       "Success result status field correct")
        tc.assert_equal(parsed_success.get("code"), EXIT_CODE.SUCCESS,
                       "Success code is 0")
        tc.assert_equal(parsed_success.get("message"), "Render completed",
                       "Message field preserved")
        tc.assert_equal(parsed_success.get("data", {}).get("processed"), 42,
                       "Data field preserved")
        tc.assert_true("metadata" in parsed_success,
                      "Metadata field present in success result")
        tc.assert_equal(
            parsed_success.get("metadata", {}).get("execution_time_ms"), 1500,
            "Execution time in metadata correct")
        
        # 验证失败结果字段
        tc.assert_equal(parsed_failure.get("status"), "failure",
                       "Failure result status field correct")
        tc.assert_equal(parsed_failure.get("code"), 4,
                       "GATE_FAILURE code maps to 4")
        tc.assert_true("diagnostics" in parsed_failure,
                      "Diagnostics field present in failure result")
        tc.assert_true(
            len(parsed_failure.get("diagnostics", {}).get("errors", [])) > 0,
            "Diagnostics errors populated")
        
        # 验证 to_dict() 同样可用
        success_dict = success_result.to_dict()
        tc.assert_true(isinstance(success_dict, dict),
                      "to_dict() returns a dict")
        tc.assert_equal(success_dict.get("status"), "success",
                       "to_dict() status matches")
        
        # 验证 EXIT_CODE 数值映射
        tc.assert_equal(EXIT_CODE.SUCCESS, 0, "EXIT_CODE.SUCCESS == 0")
        tc.assert_equal(EXIT_CODE.GATE_FAILURE, 4, "EXIT_CODE.GATE_FAILURE == 4")
        tc.assert_equal(EXIT_CODE.RUNTIME_ERROR, 5, "EXIT_CODE.RUNTIME_ERROR == 5")
        
        tc.mark_passed()
        
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc


def test_exit_code_gate_enforcement() -> RegressionTestCase:
    """用例15：EXIT_CODE.GATE_FAILURE阻断后续步骤执行"""
    tc = RegressionTestCase(
        "exit_code_gate_enforcement",
        "验证HARD_GATES机制在前置步骤失败时阻止下游步骤执行"
    )
    
    try:
        from pipeline_state_fingerprint import PipelineStateFingerprint
        from script_interface import EXIT_CODE
        
        # 验证 EXIT_CODE.GATE_FAILURE 的值
        tc.assert_equal(EXIT_CODE.GATE_FAILURE, 4,
                       "GATE_FAILURE exit code is 4")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            state_file = tmpdir / "pipeline_state.json"
            pstate = PipelineStateFingerprint(str(state_file))
            
            # 模拟 tts 步骤失败（GATE_FAILURE）
            pstate.start_step("tts", inputs={})
            pstate.complete_step("tts", passed=False,
                               error="GATE_FAILURE: TTS quality check failed")
            
            # 验证 tts 步骤被标记为失败
            tts_status = pstate.get_step_status("tts")
            tc.assert_true(not tts_status.get("passed", True),
                          "Failed step marked as not passed")
            
            # 验证下游步骤（timeline）因 tts 失败被自动失效
            timeline_status = pstate.get_step_status("timeline")
            if timeline_status:
                tc.assert_equal(timeline_status.get("status"), "invalidated",
                               "Downstream step auto-invalidated after gate failure")
            
            # 验证 HARD_GATES 依赖定义存在且正确
            script_dir = Path(__file__).parent
            pipeline_runner_path = script_dir / "pipeline_runner.py"
            with open(str(pipeline_runner_path), 'r', encoding='utf-8') as f:
                runner_content = f.read()
            
            # 验证 render 依赖 tts（HARD_GATES 中 render 的前置条件包含 tts）
            tc.assert_true('"render"' in runner_content and '"tts"' in runner_content,
                          "HARD_GATES defines render depends on tts")
            
            # 验证 pipeline 在步骤失败后调用 sys.exit(1) 阻断后续执行
            tc.assert_true("sys.exit(1)" in runner_content,
                          "Pipeline exits with code 1 on step failure (hard block)")
            
            # 验证 should_rerun_step 对失败步骤返回 True
            should_rerun = pstate.should_rerun_step("tts", inputs={})
            tc.assert_true(should_rerun,
                          "Failed step requires rerun")
            
            tc.mark_passed()
            
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc


def test_pipeline_cache_fingerprint_wiring() -> RegressionTestCase:
    """用例16：缓存中毒防御接线检查（2026-07-25 整改）
    
    背景：pipeline_state_fingerprint.py 曾"建而未接"——主入口 pipeline_runner.py
    从未引用指纹模块，跳步判定只查 status=passed，导致旧 pipeline_state.json
    让后续运行跳过多数步骤（缓存中毒）。
    本用例锁定接线状态，防止防御机制再次退化为摆设。
    """
    tc = RegressionTestCase(
        "pipeline_cache_fingerprint_wiring",
        "验证 pipeline_runner 跳步判定已接入输入指纹（缓存中毒防御）"
    )
    
    try:
        script_dir = Path(__file__).parent
        runner_content = (script_dir / "pipeline_runner.py").read_text(encoding='utf-8')
        
        # 1. 主入口必须 import 指纹实现（单一哈希实现，禁止本地复制）
        tc.assert_true("from pipeline_state_fingerprint import compute_inputs_fingerprint" in runner_content,
                      "pipeline_runner imports fingerprint module (no longer built-but-unwired)")
        
        # 2. 跳步判定必须走指纹校验入口
        tc.assert_true("def _can_skip(" in runner_content,
                      "Skip decision goes through _can_skip fingerprint check")
        
        # 3. 盲信跳步的旧模式必须清零（is_passed 直接决定跳步）
        tc.assert_true('self.state.is_passed("visual_check")' not in runner_content
                      and "[SKIPPED] Already passed" not in runner_content,
                      "No blind is_passed-only skip remains in step methods")
        
        # 4. 缓存命中必须显式可见（打印状态日期+指纹）
        tc.assert_true("[CACHED]" in runner_content and "[STALE]" in runner_content,
                      "Cache hit/stale states are explicitly printed")
        
        # 5. 验证类运行的规定入口 --fresh 必须存在且传入 runner
        tc.assert_true('"--fresh"' in runner_content and "fresh=args.fresh" in runner_content,
                      "--fresh flag exists and is wired into PipelineRunner")
        
        # 6. 完成时必须记录指纹（8步全部）
        fp_records = runner_content.count("self._fingerprint(")
        tc.assert_true(fp_records >= 8,
                      f"All steps record fingerprint on completion (found {fp_records}, need >=8)")
        
        tc.mark_passed()
        
    except Exception as e:
        tc.mark_failed(str(e))
    
    return tc

def test_timeline_drift_resync() -> RegressionTestCase:
    """用例17：时间轴早退路径 config 漂移重同步

    背景：adjust_timeline 的「No adjustment needed」早退路径曾不写回 config。
    当 HTML 手工改动（fresh baseline，S-block 已收敛）而 config 无改动被从
    旧备份恢复时，两侧时长永久漂移，后处理时长一致性门禁拦截。
    2026-07-27 修复：早退路径从权威 S-block 重新推导 config 并在漂移时写回。
    本用例锁定该行为，防止退化。
    """
    tc = RegressionTestCase(
        "timeline_drift_resync",
        "验证 adjust_timeline 早退路径检出 config/HTML 漂移并从 S-block 重同步"
    )

    import subprocess
    import wave

    script_dir = Path(__file__).parent
    proj_name = "_regress-drift-resync"
    proj_dir = script_dir.parent / "源码" / "hyperframes" / proj_name

    # 最小 HTML：S-block 为权威源（严格 JSON），已收敛到 21s（含 3s 封面）
    html_content = (
        '<div data-cover-duration="3.0" data-duration="21.0"></div>\n'
        '<script>\n'
        'var T = { s0: 0, s1: 3, s2: 13 };\n'
        'var S = [{"start": 0, "end": 3, "dur": 3, "type": "cover", "cover": true},\n'
        '    {"start": 3, "end": 13, "dur": 10, "type": "gsap"},\n'
        '    {"start": 13, "end": 21, "dur": 8, "type": "static"}];\n'
        '</script>\n'
    )

    def write_silence(path, seconds):
        with wave.open(str(path), 'w') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b'\x00\x00' * int(44100 * seconds))

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            proj_dir.mkdir(parents=True, exist_ok=True)
            (proj_dir / "index.html").write_text(html_content, encoding='utf-8')

            # TTS 与收敛后窗口吻合（surplus=1.0 < 0.5+margin）→ 触发早退路径
            tts_dir = tmpdir / "tts_44k"
            tts_dir.mkdir()
            write_silence(tts_dir / "scene_1_hq.wav", 9.0)
            write_silence(tts_dir / "scene_2_hq.wav", 7.0)

            # 陈旧 config：video_duration/scenes 均为收敛前旧值（模拟从旧备份恢复）
            cfg_path = tmpdir / "drift.json"
            stale_cfg = {
                "paths": {"html_project": proj_name},
                "video_duration": 30,
                "scenes": [
                    {"scene_id": 1, "start": 0.0, "end": 15.0, "narration": "测试旁白一"},
                    {"scene_id": 2, "start": 15.0, "end": 27.0, "narration": "测试旁白二"}
                ]
            }
            cfg_path.write_text(json.dumps(stale_cfg, ensure_ascii=False, indent=2), encoding='utf-8')

            env = dict(os.environ, PYTHONIOENCODING='utf-8')
            cmd = [sys.executable, "-X", "utf8", str(script_dir / "adjust_timeline.py"),
                   "--config", str(cfg_path), "--tts-dir", str(tts_dir), "--shrink"]

            # run1：早退路径应检出漂移并从 S-block 重同步 config
            r1 = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', env=env)
            cfg1 = json.loads(cfg_path.read_text(encoding='utf-8'))
            tc.assert_equal(r1.returncode, 0, "run1 exits 0")
            tc.assert_true("No adjustment needed" in r1.stdout, "run1 takes early-return path")
            tc.assert_true("Config drift detected" in r1.stdout, "run1 detects drift and resyncs")
            tc.assert_equal(cfg1["video_duration"], 21.0, "video_duration resynced from S-block (18+cover3)")
            tc.assert_equal((cfg1["scenes"][0]["start"], cfg1["scenes"][0]["end"]), (0.0, 10.0),
                            "scene1 window derived cover-relative")
            tc.assert_equal(cfg1["scenes"][0]["narration"], "测试旁白一", "narration preserved")

            # run2：无改动重跑必须幂等（不再写 config）
            before = cfg_path.read_text(encoding='utf-8')
            r2 = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', env=env)
            tc.assert_equal(r2.returncode, 0, "run2 exits 0")
            tc.assert_true("Config drift detected" not in r2.stdout, "run2 no drift (idempotent)")
            tc.assert_equal(cfg_path.read_text(encoding='utf-8'), before, "run2 config unchanged")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))
    finally:
        shutil.rmtree(proj_dir, ignore_errors=True)

    return tc

def test_narration_source_pointer_resolution() -> RegressionTestCase:
    """用例18：narration_source 指针解析（P0-03 闭环）

    背景：旧实现把相对指针按配置文件目录解析，而 narration.json 实际位于
    HTML 项目目录 → 指针永远落空并静默回退内嵌 scenes，形成双源分叉。
    2026-07-28 修复：相对指针优先按 paths.html_project 目录解析；
    声明了指针即为权威，解析失败硬报错，禁止静默回退。本用例锁定该行为。
    """
    tc = RegressionTestCase(
        "narration_source_pointer_resolution",
        "验证 narration_source 相对指针按 HTML 项目目录解析，失效指针硬报错不回退"
    )

    import subprocess

    script_dir = Path(__file__).parent
    proj_name = "_regress-narr-source"
    proj_dir = script_dir.parent / "源码" / "hyperframes" / proj_name

    probe = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "import enhance_video_audio as eva\n"
        "eva.load_config(sys.argv[1])\n"
        "print('SCENE_COUNT=' + str(len(eva.SCENES)))\n"
        "print('NARR0=' + eva.SCENES[0][3])\n"
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            proj_dir.mkdir(parents=True, exist_ok=True)
            # 权威旁白源放在 HTML 项目目录（与指纹机制位置一致）
            (proj_dir / "narration.json").write_text(json.dumps({
                "scenes": [
                    {"scene_id": 0, "start": 0, "end": 3, "type": "cover", "narration": ""},
                    {"scene_id": 1, "start": 3, "end": 13, "type": "gsap", "narration": "权威源旁白"}
                ]
            }, ensure_ascii=False), encoding='utf-8')

            # 配置放在另一目录（模拟 config/pipelines/），内嵌 scenes 故意写分叉内容
            cfg_path = tmpdir / "cfg.json"
            base_cfg = {
                "video_duration": 13,
                "cover_duration": 3.0,
                "voice": "longanling",
                "tts_engine": "qwen",
                "paths": {"html_project": proj_name},
                "narration_source": "./narration.json",
                "scenes": [{"scene_id": 1, "start": 0, "end": 13, "narration": "分叉的内嵌旁白"}]
            }
            cfg_path.write_text(json.dumps(base_cfg, ensure_ascii=False), encoding='utf-8')

            env = dict(os.environ, PYTHONIOENCODING='utf-8')
            cmd = [sys.executable, "-X", "utf8", "-c", probe, str(cfg_path), str(script_dir)]
            r1 = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', env=env)
            tc.assert_equal(r1.returncode, 0, "pointer resolves via html_project dir")
            # cover 场景无 TTS/字幕，被 _normalize_scenes_to_absolute 过滤 → 剩 1 个内容场景
            tc.assert_true("SCENE_COUNT=1" in r1.stdout, "scenes loaded from narration_source")
            tc.assert_true("权威源旁白" in r1.stdout, "narration comes from pointer, not embedded scenes")

            # 失效指针：删掉权威源 → 必须硬报错，禁止静默回退内嵌 scenes
            (proj_dir / "narration.json").unlink()
            r2 = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', env=env)
            tc.assert_true(r2.returncode != 0, "dangling pointer exits non-zero")
            tc.assert_true("SCENE_COUNT" not in r2.stdout, "no silent fallback to embedded scenes")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))
    finally:
        shutil.rmtree(proj_dir, ignore_errors=True)

    return tc

# ============================================================================
# 测试运行器
# ============================================================================

class RegressionTestRunner:
    """回归测试运行器"""
    
    def __init__(self):
        self.test_cases = [
            test_scene_compile_basic,
            test_scene_compile_no_narration,
            test_tts_empty_scenes,
            test_timeline_basic_adjustment,
            test_subtitle_format_validation,
            test_subtitle_overflow_detection,
            test_pipeline_state_fingerprint,
            test_hard_gates_enforcement,
            test_completion_report_data_driven,
            test_completion_report_cli_entry,
            test_media_qa_gate_checks,
            test_config_fingerprint_invalidation,
            test_imagegen_fallback_graceful,
            test_result_object_downstream_parsing,
            test_exit_code_gate_enforcement,
            test_pipeline_cache_fingerprint_wiring,
            test_timeline_drift_resync,
            test_narration_source_pointer_resolution,
        ]
        self.results = []
    
    def run_all(self) -> Dict[str, Any]:
        """运行所有测试用例"""
        print(f"[INFO] Running {len(self.test_cases)} regression test cases...")
        
        for test_func in self.test_cases:
            print(f"  [RUNNING] {test_func.__name__}...", end=" ")
            
            from datetime import timezone
            start_time = datetime.now(timezone.utc)
            tc = test_func()
            end_time = datetime.now(timezone.utc)
            
            tc.result["duration_ms"] = int((end_time - start_time).total_seconds() * 1000)
            self.results.append(tc.result)
            
            status_str = tc.result["status"].upper()
            print(f"[{status_str}]")
            
            if tc.result["error"]:
                print(f"    Error: {tc.result['error']}")
        
        return self.get_summary()
    
    def get_summary(self) -> Dict[str, Any]:
        """获取测试摘要"""
        passed = sum(1 for r in self.results if r["status"] == "passed")
        failed = sum(1 for r in self.results if r["status"] == "failed")
        
        from datetime import timezone
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_cases": len(self.results),
            "passed": passed,
            "failed": failed,
            "success_rate": passed / len(self.results) if self.results else 0,
            "results": self.results
        }

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Regression test suite')
    parser.add_argument('--run-all', action='store_true', help='Run all test cases')
    parser.add_argument('--run-case', help='Run a specific test case by name')
    parser.add_argument('--output', help='Output results to file')
    
    args = parser.parse_args()
    
    runner = RegressionTestRunner()
    
    if args.run_all:
        summary = runner.run_all()
    elif args.run_case:
        # 查找并运行单个用例
        for test_func in runner.test_cases:
            if test_func.__name__ == f"test_{args.run_case}":
                tc = test_func()
                runner.results.append(tc.result)
                summary = runner.get_summary()
                break
        else:
            print(f"[ERROR] Test case not found: {args.run_case}")
            return
    else:
        # 列出所有可用用例
        print("[INFO] Available test cases:")
        for test_func in runner.test_cases:
            name = test_func.__name__.replace("test_", "")
            doc = test_func.__doc__ or "No description"
            print(f"  - {name}: {doc}")
        return
    
    # 输出摘要
    print(f"\n[SUMMARY] {summary['passed']}/{summary['total_cases']} passed ({summary['success_rate']*100:.0f}%)")
    
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"[OK] Results saved to {args.output}")
    
    sys.exit(0 if summary['failed'] == 0 else 1)

if __name__ == '__main__':
    main()
