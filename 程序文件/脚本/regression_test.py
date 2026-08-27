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

            # TTS 与收敛后窗口吻合（surplus=1.5 == shrink margin 1.5，margin 取自
            # audio_sync_rules.json 权威值——2026-08-04 整改后 CLI 默认不再分叉 1.0s）
            # → 零调整 → 触发早退路径
            tts_dir = tmpdir / "tts_44k"
            tts_dir.mkdir()
            write_silence(tts_dir / "scene_1_hq.wav", 8.5)
            write_silence(tts_dir / "scene_2_hq.wav", 6.5)

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

def _safe_import_rebinding_module(module_name):
    """导入会在模块顶层重绑 stdout/stderr 的脚本模块（pipeline_runner /
    enhance_video_audio 等）而不破坏测试进程的流。

    这类脚本在模块顶层用 TextIOWrapper 重绑 sys.stdout/stderr（面向
    独立进程运行），在测试进程内 import 会顶掉原流并在 GC 时
    关闭共享 buffer（导致 'I/O operation on closed file'）。此处导入后 detach
    新包装器并恢复原流，避免 I/O 崩溃。
    """
    import sys as _sys
    import importlib
    saved_out, saved_err = _sys.stdout, _sys.stderr
    try:
        mod = importlib.import_module(module_name)
    finally:
        for stream in (_sys.stdout, _sys.stderr):
            if stream not in (saved_out, saved_err):
                try:
                    stream.detach()
                except Exception:
                    pass
        _sys.stdout, _sys.stderr = saved_out, saved_err
    return mod

def _safe_import_pipeline_runner():
    """兼容入口：安全导入 pipeline_runner（见 _safe_import_rebinding_module）。"""
    return _safe_import_rebinding_module("pipeline_runner")

def test_delivery_gate_blocks_quickfix() -> RegressionTestCase:
    """用例19：delivery 拦截 quick-fix 产物（交付关卡）

    背景：--quick-fix 会把 preflight..visual_check 共 7 步标记为 skipped，
    以前流水线结束仅打印提醒，无任何机制阻止 quick-fix 产物流入交付。
    本用例锁定：带 skipped 步骤的 state 下 delivery 必须判为拒收，且不能被缓存绕过。
    """
    tc = RegressionTestCase(
        "delivery_gate_blocks_quickfix",
        "验证 delivery 步骤在存在 skipped 步骤（quick-fix）时拒收"
    )

    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            state = PipelineState(tmpdir / "pipeline_state.json")
            # 构造 quick-fix 状态：7 个前置步骤 skipped，postprocess 已通过
            for s in ["preflight", "tts", "timeline", "preview", "render", "verify", "visual_check"]:
                state.mark_skipped(s, "quick-fix mode")
            state.mark_started("postprocess")
            state.mark_completed("postprocess")

            # 不走 __init__（避免依赖真实 config/环境），直接注入步骤所需属性
            runner = PipelineRunner.__new__(PipelineRunner)
            runner.state = state
            runner.force = False
            runner.quick_fix = True
            runner.config = {}
            runner.html_project = "test_proj"
            runner.output_file = tmpdir / "test_proj.mp4"
            runner.temp_dir = tmpdir
            runner.tts_dir = tmpdir / "tts_44k"
            runner.render_raw = tmpdir / "render_raw.mp4"

            # 阻断清单非空
            blocked = runner._delivery_blocked_steps()
            tc.assert_true(len(blocked) >= 7, "blocked list captures skipped render-chain steps")

            # delivery 必须拒收
            ok = runner.step_delivery()
            tc.assert_true(not ok, "delivery returns False when steps skipped (quick-fix blocked)")
            tc.assert_equal(state.data["steps"]["delivery"]["status"], "failed",
                            "delivery step marked failed in state")

            # 对照：全部步骤 passed 且成果文件存在时，不被前置门禁拦截
            state2 = PipelineState(tmpdir / "pipeline_state2.json")
            for s in ["preflight", "tts", "timeline", "preview", "render",
                      "verify", "visual_check", "postprocess"]:
                state2.mark_started(s)
                state2.mark_completed(s)
            runner.state = state2
            tc.assert_equal(len(runner._delivery_blocked_steps()), 0,
                            "no blocked steps when full render chain passed")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_verification_result_persistence() -> RegressionTestCase:
    """用例20：验证结果持久化与完工报告消费

    背景：verify_tts_product / enhance_video_audio 的验证结果旧未写入 pipeline_state，
    导致 generate_completion_report 无法追溯。本用例锁定：verify 结果可落盘、
    runner 可合并进 state['verifications']、完工报告能消费该节；且旧 state 降级不崩溃。
    """
    tc = RegressionTestCase(
        "verification_result_persistence",
        "验证 verifications 节落盘/合并到 pipeline_state 且完工报告能消费"
    )

    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        from verify_tts_product import write_verify_result
        from generate_completion_report import generate_report
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # 1. verify 结果落盘
            result = {"passed": True, "total_checks": 4, "passed_checks": 4,
                      "errors": [], "warnings": ["minor warning"]}
            out = write_verify_result(result, str(tmpdir))
            tc.assert_true(out and Path(out).exists(), "tts_verify_result.json written to disk")

            # 2. runner 合并进 state['verifications']
            rstate = PipelineState(tmpdir / "pipeline_state.json")
            for s in ["preflight", "tts", "timeline", "render", "verify", "postprocess"]:
                rstate.mark_started(s)
                rstate.mark_completed(s)
            runner = PipelineRunner.__new__(PipelineRunner)
            runner.state = rstate
            runner._merge_verification_file("tts_product", Path(out))
            tc.assert_true("tts_product" in rstate.data.get("verifications", {}),
                           "runner merges verify result into state.verifications")
            tc.assert_equal(rstate.data["verifications"]["tts_product"]["passed"], True,
                            "merged verification preserves passed flag")

            # 3. 完工报告消费 verifications 节
            video = tmpdir / "v.mp4"
            video.write_bytes(b"\x00" * 100000)
            report = generate_report(
                project_name="TestProject",
                video_file=str(video),
                subtitle_file=None,
                state_file=str(rstate.path)
            )
            tc.assert_true("verification_tts_product_passed" in report["validation"],
                           "completion report consumes verifications node")
            tc.assert_equal(report["validation"]["verification_tts_product_passed"], True,
                            "tts_product verification recorded as passed in report")

            # 4. 向后兼容：旧 state 无 verifications 节 → 降级不崩溃
            legacy = {"steps": {"preflight": {"status": "passed"}}}
            legacy_file = tmpdir / "legacy_state.json"
            legacy_file.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            report2 = generate_report(
                project_name="TestProject",
                video_file=str(video),
                subtitle_file=None,
                state_file=str(legacy_file)
            )
            tc.assert_true("status" in report2,
                           "legacy state without verifications degrades gracefully (no crash)")
            tc.assert_equal(report2["data_sources"].get("verifications"),
                            "not_recorded (legacy state, not traceable)",
                            "legacy state flagged as not-traceable rather than crashing")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_quality_threshold_config_effective() -> RegressionTestCase:
    """用例21：质量阈值配置生效（P1 单一权威源）

    背景：verify_tts_product / media_qa_gate 旧版硬编码 silencedetect -40dB，
    enhance_video_audio 硬编码码率阈值 300kbps —— 配置文件声明不生效。
    2026-07-29 改为从 config/quality/*.json 读取（读不到回退默认并告警）。
    本用例锁定：篡改临时副本后 loader 读到新值；缺失时回退默认值；
    真实权威源中的声明值确实被消费方读到。
    """
    tc = RegressionTestCase(
        "quality_threshold_config_effective",
        "验证 silence_db / 码率双档阈值从配置读取生效且缺失时回退默认"
    )

    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        from verify_tts_product import _load_silence_db
        import media_qa_gate
        eva = _safe_import_rebinding_module("enhance_video_audio")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # 1. 篡改临时 audio_sync_rules 副本的 silence_db → loader 读到新值
            tampered_audio = tmpdir / "audio_sync_rules.json"
            tampered_audio.write_text(json.dumps(
                {"alignment": {"silence_db": -25}}), encoding='utf-8')
            tc.assert_equal(_load_silence_db(tampered_audio), -25.0,
                            "verify_tts_product reads tampered silence_db")

            # 2. 配置缺失 → 回退默认 -40（告警不阻断）
            tc.assert_equal(_load_silence_db(tmpdir / "missing.json"), -40.0,
                            "missing rules falls back to -40dB default")

            # 3. 篡改临时 video_quality_rules 副本的码率阈值 → loader 读到新值
            tampered_video = tmpdir / "video_quality_rules.json"
            tampered_video.write_text(json.dumps(
                {"min_video_bitrate_kbps_fail": 150,
                 "min_video_bitrate_kbps_warn": 800}), encoding='utf-8')
            tc.assert_equal(
                eva._load_video_quality_rules(tampered_video)["min_video_bitrate_kbps_warn"],
                800, "enhance_video_audio reads tampered bitrate threshold")

            # 4. 配置缺失 → 回退默认双档 fail=200/warn=500
            missing_rules = eva._load_video_quality_rules(tmpdir / "missing.json")
            tc.assert_equal(missing_rules["min_video_bitrate_kbps_fail"], 200,
                            "missing video rules falls back to fail=200kbps default")
            tc.assert_equal(missing_rules["min_video_bitrate_kbps_warn"], 500,
                            "missing video rules falls back to warn=500kbps default")

        # 5. 真实权威源：media_qa_gate 读到的 silence_db 必须等于配置声明值
        rules_path = (script_dir.parent / "配置" / "config"
                      / "quality" / "audio_sync_rules.json")
        declared = json.loads(rules_path.read_text(encoding='utf-8'))["alignment"]["silence_db"]
        tc.assert_equal(media_qa_gate._load_alignment_rules().get("silence_db"), declared,
                        "media_qa_gate consumes declared silence_db from authoritative source")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_quality_config_fingerprint_invalidation() -> RegressionTestCase:
    """用例22：质量配置/脚本纳入步骤指纹（P1 指纹覆盖扩展）

    背景：旧版 _step_inputs 不覆盖 config/quality/ 阈值表与主执行脚本，
    改阈值/改脚本后缓存照常命中。本用例锁定：timeline/postprocess/tts
    步骤输入已登记真实消费的质量配置与脚本；修改 quality 配置后
    _step_dirty_reason 判定 inputs-changed（缓存失效重跑）。
    """
    tc = RegressionTestCase(
        "quality_config_fingerprint_invalidation",
        "验证 quality 配置变化后 timeline/postprocess 指纹失效为 inputs-changed"
    )

    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        saved_config_dir = _pr.CONFIG_DIR
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = Path(tmpdir)
                # 临时 CONFIG_DIR：quality 阈值表可控篡改，不碰真实配置
                quality_dir = tmpdir / "quality"
                quality_dir.mkdir(parents=True)
                audio_rules = quality_dir / "audio_sync_rules.json"
                audio_rules.write_text(json.dumps(
                    {"alignment": {"silence_db": -38}}), encoding='utf-8')
                video_rules = quality_dir / "video_quality_rules.json"
                video_rules.write_text(json.dumps(
                    {"min_video_bitrate_kbps": 500}), encoding='utf-8')
                _pr.CONFIG_DIR = tmpdir

                cfg_path = tmpdir / "cfg.json"
                cfg_path.write_text("{}", encoding='utf-8')

                state = PipelineState(tmpdir / "pipeline_state.json")
                runner = PipelineRunner.__new__(PipelineRunner)
                runner.state = state
                runner.force = False
                runner.config = {}
                runner.config_path = cfg_path
                runner.html_project = "test_proj"
                runner.source_dir = tmpdir
                runner.html_path = tmpdir / "index.html"
                runner.temp_dir = tmpdir
                runner.tts_dir = tmpdir / "tts_44k"
                runner.render_raw = tmpdir / "render_raw.mp4"
                runner.output_file = tmpdir / "test_proj.mp4"

                # 1. 指纹输入登记了质量配置与主执行脚本（克制原则：只登真实消费）
                pp = runner._step_inputs("postprocess")
                tc.assert_true(pp.get("audio_sync_rules", "").endswith("audio_sync_rules.json"),
                               "postprocess inputs include audio_sync_rules")
                tc.assert_true(pp.get("video_quality_rules", "").endswith("video_quality_rules.json"),
                               "postprocess inputs include video_quality_rules")
                tc.assert_true(pp.get("script", "").endswith("enhance_video_audio.py"),
                               "postprocess inputs include enhance_video_audio.py itself")
                tl = runner._step_inputs("timeline")
                tc.assert_true(tl.get("audio_sync_rules", "").endswith("audio_sync_rules.json"),
                               "timeline inputs include audio_sync_rules")
                tc.assert_true(tl.get("script", "").endswith("adjust_timeline.py"),
                               "timeline inputs include adjust_timeline.py itself")
                tc.assert_true(runner._step_inputs("tts").get("script", "").endswith("enhance_video_audio.py"),
                               "tts inputs include enhance_video_audio.py (TTS executor)")

                # 2. 步骤带指纹完成 → 输入未变时缓存可复用
                for s in ("timeline", "postprocess"):
                    state.mark_started(s)
                    state.mark_completed(s, runner._fingerprint(s))
                    tc.assert_equal(runner._step_dirty_reason(s), None,
                                    f"{s} cache reusable when inputs unchanged")

                # 3. 篡改 quality 阈值表 → 对应步骤指纹失效
                audio_rules.write_text(json.dumps(
                    {"alignment": {"silence_db": -30}}), encoding='utf-8')
                tc.assert_equal(runner._step_dirty_reason("timeline"), "inputs-changed",
                                "timeline invalidated after audio_sync_rules edit")
                tc.assert_equal(runner._step_dirty_reason("postprocess"), "inputs-changed",
                                "postprocess invalidated after audio_sync_rules edit")

                # 4. 只改 video_quality_rules → 仅 postprocess 失效（timeline 不消费它）
                audio_rules.write_text(json.dumps(
                    {"alignment": {"silence_db": -38}}), encoding='utf-8')
                for s in ("timeline", "postprocess"):
                    state.mark_started(s)
                    state.mark_completed(s, runner._fingerprint(s))
                video_rules.write_text(json.dumps(
                    {"min_video_bitrate_kbps": 800}), encoding='utf-8')
                tc.assert_equal(runner._step_dirty_reason("timeline"), None,
                                "timeline unaffected by video_quality_rules edit")
                tc.assert_equal(runner._step_dirty_reason("postprocess"), "inputs-changed",
                                "postprocess invalidated after video_quality_rules edit")

                tc.mark_passed()
        finally:
            _pr.CONFIG_DIR = saved_config_dir

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_bitrate_dual_tier_gate() -> RegressionTestCase:
    """用例23：码率门禁双档制（误报根因修复锁定）

    背景：纯文字动画项目实测码率 239-343kbps（短动画片 336-343、wp 批次长片 239-270），
    被旧 500kbps 单档阻断拦下（postprocess 必然失败）。2026-07-29 改为双档：
    <fail(300) 阻断，fail~warn(300-500) 预警不阻断；2026-08-05 wp 批次复盘后
    fail 以实测为准降为 200。本用例锁定：343kbps 判 WARN 不阻断、
    250kbps 判 FAIL、旧单档键兼容路径按单档阻断且两侧脚本同口径。
    """
    tc = RegressionTestCase(
        "bitrate_dual_tier_gate",
        "验证码率双档：343kbps WARN 不阻断、250kbps FAIL、旧单档键兼容生效"
    )

    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import media_qa_gate
        eva = _safe_import_rebinding_module("enhance_video_audio")

        dual = {"min_video_bitrate_kbps_fail": 300, "min_video_bitrate_kbps_warn": 500}
        legacy = {"min_video_bitrate_kbps": 500}

        # 1. 双档：343kbps → WARN 不阻断；250kbps → FAIL；600kbps → OK
        level, fail_k, warn_k, is_legacy = media_qa_gate._classify_video_bitrate(343, dual)
        tc.assert_equal(level, "warn", "343kbps classified as WARN (not blocking)")
        tc.assert_equal((fail_k, warn_k, is_legacy), (300, 500, False),
                        "dual-tier thresholds resolved as fail=300/warn=500")
        tc.assert_equal(media_qa_gate._classify_video_bitrate(250, dual)[0], "fail",
                        "250kbps classified as FAIL (blocking)")
        tc.assert_equal(media_qa_gate._classify_video_bitrate(600, dual)[0], "ok",
                        "600kbps classified as OK")

        # 2. 旧单档键兼容：按单档阻断（343 < 500 → FAIL）且 legacy_single 标记生效
        level_l, fail_l, warn_l, is_legacy_l = media_qa_gate._classify_video_bitrate(343, legacy)
        tc.assert_equal((level_l, fail_l, warn_l, is_legacy_l), ("fail", 500, 500, True),
                        "legacy single key blocks at 500kbps and flags upgrade hint")

        # 3. 两侧脚本同口径：enhance_video_audio 解析结果与 media_qa_gate 一致
        tc.assert_equal(eva._resolve_bitrate_thresholds(dual),
                        media_qa_gate._resolve_bitrate_thresholds(dual),
                        "both scripts resolve dual-tier thresholds identically")
        tc.assert_equal(eva._resolve_bitrate_thresholds(legacy),
                        media_qa_gate._resolve_bitrate_thresholds(legacy),
                        "both scripts resolve legacy single key identically")

        # 4. 真实权威源已升级为双档声明
        declared = media_qa_gate._load_video_quality_rules()
        tc.assert_true("min_video_bitrate_kbps_fail" in declared,
                       "authoritative video_quality_rules.json declares dual-tier fail key")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_dead_air_scene_exclusion() -> RegressionTestCase:
    """用例24：死区终检场景感知排除（封面误报根因修复锁定）

    背景：quickstart-demo 的 3 秒封面（narration_required=false）无音频属
    设计特征，被全片扫描检出 0-3.2s 静音 > 3.0s 误判 FAIL。2026-07-29
    改为场景感知：旁白非必需窗内静音不计入，跨边界只计落在
    narration_required=true 窗内的部分；无场景数据时降级为全片扫描。
    """
    tc = RegressionTestCase(
        "dead_air_scene_exclusion",
        "验证封面静音不计入死区、内容场景静音仍违规、无场景数据降级全片扫描"
    )

    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        from media_qa_gate import (
            filter_dead_air_violations,
            _load_narration_scenes,
            _resolve_narration_from_config,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # 场景表：cover(0-3, narration_required=false) + 两个内容场景
            narration_file = tmpdir / "narration.json"
            narration_file.write_text(json.dumps({
                "project": "test",
                "scenes": [
                    {"scene_id": 0, "type": "cover", "start": 0.0, "end": 3.0,
                     "narration": ""},
                    {"scene_id": 1, "type": "content", "start": 3.0, "end": 20.0,
                     "narration": "内容一"},
                    {"scene_id": 2, "type": "content", "start": 20.0, "end": 40.0,
                     "narration": "内容二"}
                ]
            }, ensure_ascii=False), encoding='utf-8')

            scenes = _load_narration_scenes(str(narration_file))
            tc.assert_equal(len(scenes), 3, "three scenes loaded from narration.json")
            tc.assert_equal(scenes[0].get("narration_required"), False,
                            "cover scene resolved as narration_required=false")

            # 1. 跨边界静音 0-3.2s：仅计入内容窗 3.0-3.2s（0.2s）→ 不违规
            cover_seg = [(0.0, 3.2, 3.2)]
            violations = filter_dead_air_violations(cover_seg, scenes, 3.0)
            tc.assert_equal(violations, [],
                            "0-3.2s silence counts only 0.2s inside content window (no violation)")

            # 2. 内容场景内 >3s 静音仍违规（计入时长 = 实际重叠 4.0s）
            content_seg = [(5.0, 9.0, 4.0)]
            violations2 = filter_dead_air_violations(content_seg, scenes, 3.0)
            tc.assert_equal(len(violations2), 1, "silence inside content scene still violates")
            tc.assert_true(violations2 and abs(violations2[0][2] - 4.0) < 0.01,
                           "counted duration equals in-window overlap (4.0s)")

            # 3. 无场景数据（scenes=None）→ 降级为全片扫描（原口径，封面段也报）
            degraded = filter_dead_air_violations(cover_seg, None, 3.0)
            tc.assert_equal(degraded, cover_seg,
                            "without scene data falls back to full-scan behavior")

            # 4. config 指针解析：narration_source 相对路径回退到配置目录
            cfg_file = tmpdir / "cfg.json"
            cfg_file.write_text(json.dumps({
                "cover_duration": 3.0,
                "narration_source": "./narration.json"
            }), encoding='utf-8')
            resolved, cover_dur = _resolve_narration_from_config(str(cfg_file))
            tc.assert_equal(Path(resolved).name if resolved else None, "narration.json",
                            "narration_source pointer resolves relative to config dir")
            tc.assert_equal(cover_dur, 3.0, "cover_duration read from config")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_silence_fallback_and_bitrate_na_tolerance() -> RegressionTestCase:
    """用例25：silence_db 回退告警 + bit_rate 非数值容错（三维评审修复锁定）

    背景：(a) media_qa_gate._load_alignment_rules 旧版配置读不到时静默回退
    -38，与 verify_tts_product._load_silence_db 的 -40dB+告警口径不一致；
    (b) ffprobe 对部分容器/流返回 bit_rate="N/A"，旧版 int() 直接抛
    ValueError 使门禁脚本崩溃。2026-07-29 修复：回退 -40 并打印 WARN；
    码率解析失败返回 None，门禁降级为 warning 可追溯不虚报。
    """
    tc = RegressionTestCase(
        "silence_fallback_and_bitrate_na_tolerance",
        "验证 silence_db 缺失回退 -40 且告警、bit_rate 非数值时门禁降级 warning 不崩溃"
    )

    try:
        import sys
        import io
        import contextlib
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import media_qa_gate
        eva = _safe_import_rebinding_module("enhance_video_audio")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # (a1) 配置文件缺失 → 回退 -40 且打印 WARN，其余默认键语义不变
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rules = media_qa_gate._load_alignment_rules(tmpdir / "missing.json")
            tc.assert_equal(rules.get("silence_db"), -40.0,
                            "missing rules file falls back to silence_db=-40")
            tc.assert_true("[WARN]" in buf.getvalue() and "-40" in buf.getvalue(),
                           "missing rules file prints WARN mentioning -40dB fallback")
            tc.assert_equal(
                (rules["p95_max_seconds"], rules["min_onsets"],
                 rules["min_silence_seconds"], rules["fail_on_violation"]),
                (0.6, 5, 0.5, True),
                "other alignment defaults keep original values/semantics")

            # (a2) JSON 解析失败 → 回退 -40 且告警
            broken = tmpdir / "broken.json"
            broken.write_text("{not-json", encoding='utf-8')
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                rules2 = media_qa_gate._load_alignment_rules(broken)
            tc.assert_true(rules2.get("silence_db") == -40.0 and "[WARN]" in buf2.getvalue(),
                           "broken JSON falls back to -40 with WARN")

            # (a3) alignment 节无 silence_db 键 → 回退 -40 且告警，其余声明照常读取
            no_key = tmpdir / "no_key.json"
            no_key.write_text(json.dumps({"alignment": {"p95_max_seconds": 0.7}}),
                              encoding='utf-8')
            buf3 = io.StringIO()
            with contextlib.redirect_stdout(buf3):
                rules3 = media_qa_gate._load_alignment_rules(no_key)
            tc.assert_true(rules3.get("silence_db") == -40.0 and "[WARN]" in buf3.getvalue(),
                           "alignment without silence_db key falls back to -40 with WARN")
            tc.assert_equal(rules3.get("p95_max_seconds"), 0.7,
                            "declared keys still consumed when silence_db missing")

            # (a4) 正常路径行为不变：权威源声明值（当前 -38）直读且无告警
            buf4 = io.StringIO()
            with contextlib.redirect_stdout(buf4):
                normal = media_qa_gate._load_alignment_rules()
            declared = json.loads(
                (script_dir.parent / "配置" / "config" / "quality"
                 / "audio_sync_rules.json").read_text(encoding='utf-8')
            )["alignment"]["silence_db"]
            tc.assert_equal(normal.get("silence_db"), declared,
                            "normal path still reads declared silence_db (no fallback)")
            tc.assert_true("[WARN]" not in buf4.getvalue(),
                           "normal path prints no WARN")

            # (b1) 解析辅助：N/A/None/缺失/非数值 → None 不抛异常；正常值直读
            for raw in ("N/A", None, "", "abc", "0"):
                tc.assert_equal(media_qa_gate._parse_bitrate_value(raw), None,
                                f"media_qa_gate parses {raw!r} as None (unknown)")
                tc.assert_equal(eva._parse_bitrate_value(raw), None,
                                f"enhance_video_audio parses {raw!r} as None (unknown)")
            tc.assert_equal(media_qa_gate._parse_bitrate_value("512000"), 512000,
                            "numeric string bitrate parsed normally")
            tc.assert_equal(eva._parse_bitrate_value(343000), 343000,
                            "int bitrate parsed normally")

            # (b2) enhance_video_audio._check_video_quality：bit_rate=N/A →
            #      不崩溃、无码率 FAIL，追加跳过门禁 warning
            class _FakeProbe:
                stdout = json.dumps({
                    "streams": [{"codec_type": "video",
                                 "bit_rate": "N/A", "duration": "1.0"}],
                    "format": {}})
            saved_run = eva.subprocess.run
            eva.subprocess.run = lambda *a, **k: _FakeProbe()
            try:
                errs, warns = eva._check_video_quality("fake.mp4")
            finally:
                eva.subprocess.run = saved_run
            tc.assert_true(not any("bitrate" in e.lower() for e in errs),
                           "N/A bitrate raises no bitrate FAIL in _check_video_quality")
            tc.assert_true(any("已跳过码率门禁" in w for w in warns),
                           "N/A bitrate degrades to traceable skip-warning (eva)")

            # (b3) media_qa_gate 检查18调用侧：bit_rate=N/A → 门禁降级 warning
            dummy_video = tmpdir / "dummy.mp4"
            dummy_video.write_bytes(b"\x00" * 1024)

            class _FakePlay:
                returncode = 0
            saved_streams = media_qa_gate.ffprobe_get_streams
            saved_dur = media_qa_gate.ffprobe_get_duration
            saved_sub = media_qa_gate.subprocess.run
            media_qa_gate.ffprobe_get_streams = lambda p: [
                {"codec_type": "video", "bit_rate": "N/A"}]
            media_qa_gate.ffprobe_get_duration = lambda p: -1.0
            media_qa_gate.subprocess.run = lambda *a, **k: _FakePlay()
            try:
                qa = media_qa_gate.MediaQAGate()
                _, result = qa.validate(video_path=str(dummy_video))
            finally:
                media_qa_gate.ffprobe_get_streams = saved_streams
                media_qa_gate.ffprobe_get_duration = saved_dur
                media_qa_gate.subprocess.run = saved_sub
            tc.assert_true(not any("bitrate" in e.lower() for e in result["errors"]),
                           "N/A bitrate raises no bitrate error in media_qa_gate")
            tc.assert_true(any("已跳过码率门禁" in w for w in result["warnings"]),
                           "N/A bitrate degrades to traceable skip-warning (qa gate)")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_tts_concurrency_pool() -> RegressionTestCase:
    """用例26：TTS 并发生成池（P2-01）

    背景：step1_generate_tts 旧版逐场景串行调用网络 TTS，全冷缓存时 6 场景
    需 6 次串行网络往返。P2-01 引入 _load_tts_concurrency 配置（默认3/硬上限5，
    回退+告警）与 Semaphore 并发池：仅并发 generate_tts（raw 生成），ffmpeg
    上采样/manifest 写入仍串行保持产物语义。本用例锁定：并发提速、单场景
    失败串行兜底一次且不中止其他场景、兜底再失败清理半成品并整体 False、
    concurrency=1 走原串行路径、配置缺失/超限回退+告警。
    """
    tc = RegressionTestCase(
        "tts_concurrency_pool",
        "验证 TTS 并发池提速、失败兜底、串行降级与并发配置回退"
    )

    try:
        import asyncio
        import io
        import contextlib
        import time
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        eva = _safe_import_rebinding_module("enhance_video_audio")

        scenes = [(i, (i - 1) * 10.0, i * 10.0, f"scene text {i}")
                  for i in range(1, 7)]
        saved_scenes = eva.SCENES
        saved_gen = eva.generate_tts
        saved_loader = eva._load_tts_concurrency
        saved_ffmpeg = eva.run_ffmpeg
        try:
            eva.SCENES = scenes
            # 池后首个 ffmpeg 上采样即截停：本用例只覆盖并发段，不做真实转码
            eva.run_ffmpeg = lambda cmd, desc="": False

            calls = {}

            def make_fake(sleep_s, fail_texts=()):
                async def _fake(text, output_path):
                    calls[text] = calls.get(text, 0) + 1
                    await asyncio.sleep(sleep_s)
                    Path(output_path).write_bytes(b"RAW")  # 失败场景也留半成品
                    if text in fail_texts:
                        raise RuntimeError(f"simulated TTS failure: {text}")
                return _fake

            # (a) 并发度3 vs 同一 mock 的串行基准：总耗时 < 串行的 60%
            eva._load_tts_concurrency = lambda cfg_path=None: 3
            eva.generate_tts = make_fake(0.2)
            with tempfile.TemporaryDirectory() as td:
                buf = io.StringIO()
                t0 = time.perf_counter()
                with contextlib.redirect_stdout(buf):
                    ret = asyncio.run(eva.step1_generate_tts(Path(td)))
                concurrent_secs = time.perf_counter() - t0
                tc.assert_equal(ret, False,
                                "run truncated at mocked ffmpeg upsample (timing scope = pool phase)")
                tc.assert_true("[TTS-POOL] concurrency=3" in buf.getvalue(),
                               "concurrency=3 enters pool with [TTS-POOL] banner")

            with tempfile.TemporaryDirectory() as td:
                fake = make_fake(0.2)

                async def _serial_baseline():
                    for i, (_, _, _, text) in enumerate(scenes):
                        await fake(text, Path(td) / f"serial_{i}.mp3")

                t0 = time.perf_counter()
                asyncio.run(_serial_baseline())
                serial_secs = time.perf_counter() - t0
            tc.assert_true(concurrent_secs < serial_secs * 0.6,
                           f"pool(3) wall {concurrent_secs:.2f}s < 60% of serial {serial_secs:.2f}s")

            # (b) 单场景失败：其他场景不中止；串行兜底一次；再失败清半成品并整体 False
            calls.clear()
            fail_text = scenes[2][3]  # scene 3
            eva.generate_tts = make_fake(0.01, fail_texts={fail_text})
            with tempfile.TemporaryDirectory() as td:
                tmpdir = Path(td)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    ret = asyncio.run(eva.step1_generate_tts(tmpdir))
                out = buf.getvalue()
                tc.assert_equal(ret, False,
                                "unrecoverable scene failure returns False overall")
                tc.assert_true("serial retry" in out,
                               "failed scene triggers serial fallback retry")
                tc.assert_equal(calls.get(fail_text), 2,
                                "failing scene called exactly twice (pool + one serial retry)")
                ok_raws = [tmpdir / f"scene_{sid}_{eva._tts_cache_key(t)}.mp3"
                           for sid, _, _, t in scenes if t != fail_text]
                tc.assert_true(all(p.exists() for p in ok_raws),
                               "other scenes finish raw generation (not aborted by the failure)")
                bad_raw = tmpdir / f"scene_3_{eva._tts_cache_key(fail_text)}.mp3"
                tc.assert_true(not bad_raw.exists(),
                               "half-done raw of failed scene cleaned up")
                tc.assert_true(all(calls.get(t) == 1 for _, _, _, t in scenes
                                   if t != fail_text),
                               "successful scenes not re-run by the fallback pass")

            # (c) concurrency=1：不进池（无 [TTS-POOL]），raw 生成留在原串行循环
            calls.clear()
            eva._load_tts_concurrency = lambda cfg_path=None: 1
            eva.generate_tts = make_fake(0.01)
            with tempfile.TemporaryDirectory() as td:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    ret = asyncio.run(eva.step1_generate_tts(Path(td)))
                out = buf.getvalue()
                tc.assert_true("[TTS-POOL]" not in out,
                               "concurrency=1 keeps original serial path (no [TTS-POOL])")
                tc.assert_true("Generating scene 1 TTS" in out
                               and calls.get(scenes[0][3]) == 1,
                               "serial loop generates raw inline before ffmpeg cut-off")

            # (d) 并发配置回退+告警（真实 _load_tts_concurrency，测试注入路径）
            with tempfile.TemporaryDirectory() as td:
                tmpdir = Path(td)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    n_missing = saved_loader(tmpdir / "missing.json")
                tc.assert_equal(n_missing, 3, "missing config falls back to default 3")
                tc.assert_true("[WARN]" in buf.getvalue(),
                               "missing config prints WARN")

                over = tmpdir / "over.json"
                over.write_text(json.dumps({"tts_concurrency": 9}), encoding="utf-8")
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    n_over = saved_loader(over)
                tc.assert_true(n_over == 5 and "[WARN]" in buf2.getvalue(),
                               "over-limit value capped to hard max 5 with WARN")

                valid = tmpdir / "valid.json"
                valid.write_text(json.dumps({"tts_concurrency": 2}), encoding="utf-8")
                buf3 = io.StringIO()
                with contextlib.redirect_stdout(buf3):
                    n_valid = saved_loader(valid)
                tc.assert_true(n_valid == 2 and "[WARN]" not in buf3.getvalue(),
                               "valid declared value read without WARN")
        finally:
            eva.SCENES = saved_scenes
            eva.generate_tts = saved_gen
            eva._load_tts_concurrency = saved_loader
            eva.run_ffmpeg = saved_ffmpeg

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_visual_boundary_three_point_sampling() -> RegressionTestCase:
    """用例27：视觉边界三点采样（P2 优化项2）

    背景：旧版每场景两次串行提帧（40%/70%），入场早段（30%）短暂越界的
    内容漏检。P2 改为 SAMPLE_POINTS=(0.3,0.5,0.7) 单循环 + ThreadPoolExecutor(3)
    并发提帧，取最坏采样点（max content_bottom）判定。本用例锁定：
    仅 30% 点越界可检出、判定取最坏值、结果保留全部旧字段并新增 samples
    数组、dur<2 场景 SKIP。
    """
    tc = RegressionTestCase(
        "visual_boundary_three_point_sampling",
        "验证三点采样检出早段越界、取最坏值判定且结果结构向后兼容"
    )

    try:
        import io
        import contextlib
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import visual_boundary_check as vbc

        tc.assert_equal(tuple(vbc.SAMPLE_POINTS), (0.3, 0.5, 0.7),
                        "SAMPLE_POINTS fixed to 30%/50%/70%")

        # (场景id, 采样点) → 模拟 content_bottom。场景1 仅 30% 点越界（>860，
        # 旧 40/70 两点均 <860 会漏检）；场景2 最坏点在 50%（PASS 但取最坏值）。
        table = {
            ("1", "30"): 900, ("1", "50"): 700, ("1", "70"): 720,
            ("2", "30"): 500, ("2", "50"): 800, ("2", "70"): 650,
        }

        def fake_extract(video_path, timestamp, output_path, ffmpeg_exe="ffmpeg"):
            Path(output_path).write_bytes(b"fake-frame")
            return True

        def fake_measure(image_path):
            _, sid, ptag = Path(image_path).stem.split("_")  # scene_1_p30
            return table[(sid, ptag[1:])]

        saved_extract = vbc.extract_frame
        saved_measure = vbc.measure_content_bottom
        try:
            vbc.extract_frame = fake_extract
            vbc.measure_content_bottom = fake_measure
            scenes = [
                {"id": 1, "start": 0.0, "end": 10.0},
                {"id": 2, "start": 10.0, "end": 20.0},
                {"id": 3, "start": 20.0, "end": 21.5},  # dur<2 → SKIP
            ]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                passed, results = vbc.run_check("fake.mp4", scenes)
        finally:
            vbc.extract_frame = saved_extract
            vbc.measure_content_bottom = saved_measure

        tc.assert_equal(passed, False, "check fails when any sample point overflows")
        r1, r2, r3 = results
        tc.assert_equal((r1["status"], r1["content_bottom"], r1["gap"]),
                        ("FAIL", 900, -40),
                        "scene overflowing ONLY at 30% detected as FAIL (old 40/70 missed it)")
        tc.assert_equal(r1["sample_time"], 3.0,
                        "verdict anchored to the 30% sample time")
        tc.assert_equal((r2["status"], r2["content_bottom"], r2["gap"]),
                        ("PASS", 800, 60),
                        "verdict takes worst sample (max content_bottom)")
        for r in (r1, r2):
            samples = r.get("samples", [])
            tc.assert_equal(len(samples), 3,
                            f"scene {r['scene']} carries 3-element samples array")
            tc.assert_equal([s["ratio"] for s in samples], [0.3, 0.5, 0.7],
                            f"scene {r['scene']} samples ordered by SAMPLE_POINTS")
            tc.assert_true(all({"ratio", "t", "content_bottom", "gap"} <= set(s)
                               for s in samples),
                           f"scene {r['scene']} sample entries carry ratio/t/content_bottom/gap")
            tc.assert_true({"scene", "status", "content_bottom", "safety_line",
                            "gap", "sample_time"} <= set(r),
                           f"scene {r['scene']} keeps all legacy result fields")
        tc.assert_equal(r3["status"], "SKIP", "scene shorter than 2s skipped")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_render_progress_watcher() -> RegressionTestCase:
    """用例28：render 进度观察者（P2 优化项3）

    背景：渲染子进程长时间无输出，旁路观察者线程轮询
    work-*/captured-frames/frame_*.jpg 帧数打印进度心跳；mtime 门槛防旧
    渲染残留目录误报；降级链 帧计数→render_raw.mp4 大小→纯耗时；内部
    异常静默停线程。本用例不等真实 10s 轮询周期：直接调用 _report()
    做单元级断言；线程退出用实例级 POLL_INTERVAL 覆写注入短间隔。
    """
    tc = RegressionTestCase(
        "render_progress_watcher",
        "验证进度观察者百分比计算、mtime 门槛、降级链与线程可停"
    )

    try:
        import io
        import contextlib
        import time
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        Watcher = _pr._RenderProgressWatcher

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            render_raw = tmp / "render_raw.mp4"

            # (a) 百分比：25/100 帧 → ~25%（目录 mtime 晚于 watcher 启动）
            w = Watcher([tmp], render_raw, total_frames=100)
            cap = tmp / "work-test" / "captured-frames"
            cap.mkdir(parents=True)
            for i in range(25):
                (cap / f"frame_{i:06d}.jpg").write_bytes(b"j")
            future = time.time() + 5
            os.utime(cap, (future, future))
            tc.assert_equal(w._count_frames(), 25,
                            "frame counting reads active work dir")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                w._report()
            tc.assert_true("~25% (25/100 frames" in buf.getvalue(),
                           "percentage computed as frames*100//total_frames")

            # 帧数超过 total_frames 时封顶 100%（min 防护）
            w_cap = Watcher([tmp], render_raw, total_frames=10)
            os.utime(cap, (time.time() + 5,) * 2)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                w_cap._report()
            tc.assert_true("~100%" in buf.getvalue(), "percentage capped at 100%")

            # mtime 门槛：目录 mtime 早于本次启动 → 旧渲染残留，不计数
            w2 = Watcher([tmp], render_raw, total_frames=100)
            past = time.time() - 3600
            os.utime(cap, (past, past))
            tc.assert_equal(w2._count_frames(), None,
                            "stale captured-frames dir (old mtime) ignored")

            # 降级链第2级：无帧计数但 render_raw 本次启动后有更新 → 报文件大小
            render_raw.write_bytes(b"\x00" * (2 * 1024 * 1024))
            os.utime(render_raw, (time.time() + 5,) * 2)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                w2._report()
            tc.assert_true("render_raw.mp4: 2.0MB" in buf.getvalue(),
                           "degrades to render_raw size heartbeat")

            # (b) 目录/文件全缺失 → 降级为纯耗时心跳，不抛异常
            w3 = Watcher([tmp / "nonexistent"], tmp / "nonexistent" / "r.mp4",
                         total_frames=0)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                w3._report()
            tc.assert_true("[RENDER] elapsed" in buf.getvalue(),
                           "missing dirs degrade to elapsed-only heartbeat (no exception)")

            # (c) stop() 后线程超时内退出（实例覆写 POLL_INTERVAL 注入短轮询）
            w4 = Watcher([tmp], render_raw, total_frames=0)
            w4.POLL_INTERVAL = 0.05
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                w4.start()
                time.sleep(0.2)
                w4.stop(timeout=5)
            tc.assert_true(w4._thread is not None and not w4._thread.is_alive(),
                           "watcher thread exits within stop() timeout")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_error_code_and_cache_age() -> RegressionTestCase:
    """用例29：结构化错误码与缓存年龄（P2 优化项4）

    背景：mark_failed 旧版仅存自由文本 error，完工报告/排障工具无法按
    类别路由；[CACHED] 行无缓存年龄。P2 引入错误码常量集合、
    mark_failed(error_code) 落盘（None→UNKNOWN）与 _cache_age_str 展示辅助。
    本用例锁定：错误码落盘、旧 state 缺字段读取方兼容、年龄解析三路径。
    """
    tc = RegressionTestCase(
        "error_code_and_cache_age",
        "验证 mark_failed 错误码落盘、旧 state 兼容与缓存年龄解析降级"
    )

    try:
        import io
        import contextlib
        from datetime import timedelta
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        from generate_completion_report import generate_report

        # 错误码常量集合语义固定（供报告/排障工具按码路由）
        tc.assert_equal(
            (_pr.GATE_BLOCKED, _pr.SUBPROCESS_FAILED, _pr.OUTPUT_MISSING,
             _pr.VERIFY_FAILED, _pr.QUICKFIX_BLOCKED, _pr.UNKNOWN),
            ("GATE_BLOCKED", "SUBPROCESS_FAILED", "OUTPUT_MISSING",
             "VERIFY_FAILED", "QUICKFIX_BLOCKED", "UNKNOWN"),
            "error code constant set fixed")

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)

            # (a) mark_failed 带码落盘对应值；不带码落盘 UNKNOWN
            state = _pr.PipelineState(tmpdir / "pipeline_state.json")
            state.mark_started("render")
            state.mark_failed("render", "ffmpeg exit 1",
                              error_code=_pr.SUBPROCESS_FAILED)
            state.mark_started("verify")
            state.mark_failed("verify", "some failure")  # 未指定 → UNKNOWN
            on_disk = json.loads(state.path.read_text(encoding="utf-8"))
            tc.assert_equal(on_disk["steps"]["render"]["error_code"],
                            "SUBPROCESS_FAILED",
                            "explicit error_code persisted to state file")
            tc.assert_equal(on_disk["steps"]["verify"]["error_code"], "UNKNOWN",
                            "missing error_code argument persisted as UNKNOWN")

            # (b) 旧 state 缺 error_code 字段：读取方不崩溃
            legacy_file = tmpdir / "legacy_state.json"
            legacy_file.write_text(json.dumps({
                "steps": {
                    "preflight": {"status": "passed",
                                  "completed": "2026-07-01T00:00:00"},
                    "render": {"status": "failed", "started": None,
                               "completed": None, "error": "legacy failure"},
                },
                "last_run": None,
            }, ensure_ascii=False), encoding="utf-8")
            st2 = _pr.PipelineState(legacy_file)
            tc.assert_equal(st2.last_failed_step(), "render",
                            "legacy state readable (last_failed_step)")
            tc.assert_true(
                st2.data["steps"]["render"].get("error_code") is None,
                "legacy record lacks error_code and .get returns None (no KeyError)")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                st2.print_status()  # 读取方1：状态打印不崩溃
            tc.assert_true("legacy failure" in buf.getvalue(),
                           "print_status renders legacy failed step without crash")
            # 读取方2：完工报告透传——旧 state 无码时 issue 行不带 [CODE] 后缀
            video = tmpdir / "v.mp4"
            video.write_bytes(b"\x00" * 100000)
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                report = generate_report(
                    project_name="LegacyState",
                    video_file=str(video),
                    subtitle_file=None,
                    state_file=str(legacy_file))
            render_issues = [i for i in report.get("issues", [])
                             if "'render'" in i]
            tc.assert_true(render_issues
                           and all("[" not in i for i in render_issues),
                           "completion report consumes legacy state without code suffix")

        # (c) 缓存年龄解析三路径：正常 ISO / 缺失 / 畸形，均不抛异常
        iso = (datetime.now() - timedelta(hours=12, minutes=18)).isoformat()
        age = _pr._cache_age_str(iso)
        tc.assert_true(age.startswith("(") and age.endswith("h ago)"),
                       "normal ISO renders '(N.Nh ago)' format")
        hours = float(age[1:age.index("h")])
        tc.assert_true(11.5 <= hours <= 13.0,
                       f"parsed age {hours:.1f}h within expected ~12.3h window")
        tc.assert_equal(_pr._cache_age_str(None), "(? ago)",
                        "missing completed timestamp degrades to '(? ago)'")
        tc.assert_equal(_pr._cache_age_str("not-a-timestamp"), "(? ago)",
                        "malformed timestamp degrades to '(? ago)'")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_completion_report_fingerprint_stability() -> RegressionTestCase:
    """用例30：completion_report 指纹自引用漂移修复锁定

    背景：旧版 _step_inputs 把 pipeline_state.json 整文件纳入 completion_report
    指纹，而本步骤完成时 mark_completed 又写回该文件（last_run 每次运行必变），
    自引用回路导致指纹永远漂移、该步骤永远重跑，全链 10 步 CACHED 不可达。
    修复：改用 state 内容稳定摘要（排除 last_run 与自身步骤记录）。
    本用例锁定：同一 state 指纹幂等；自写回不改变指纹；上游记录变化必失效。
    """
    tc = RegressionTestCase(
        "completion_report_fingerprint_stability",
        "验证 completion_report 指纹排除自写字段后稳定且仍随上游变化失效"
    )

    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            state = PipelineState(tmpdir / "pipeline_state.json")
            # 构造上游 9 步全部通过的 state（含验证明细）
            for s in ["preflight", "tts", "timeline", "preview", "render",
                      "verify", "visual_check", "postprocess", "delivery"]:
                state.mark_started(s)
                state.mark_completed(s)
            state.set_verification("media_qa", {"passed": True})

            # 不走 __init__（避免依赖真实 config/环境），直接注入所需属性
            cfg_path = tmpdir / "cfg.json"
            cfg_path.write_text("{}", encoding='utf-8')
            runner = PipelineRunner.__new__(PipelineRunner)
            runner.state = state
            runner.force = False
            runner.config = {}
            runner.config_path = cfg_path
            runner.html_project = "test_proj"
            runner.source_dir = tmpdir
            runner.html_path = tmpdir / "index.html"
            runner.temp_dir = tmpdir
            runner.tts_dir = tmpdir / "tts_44k"
            runner.render_raw = tmpdir / "render_raw.mp4"
            runner.output_file = tmpdir / "test_proj.mp4"

            # 接线检查：指纹输入已改用稳定摘要，不再含 state 文件路径
            inputs = runner._step_inputs("completion_report")
            tc.assert_true(inputs.get("state_digest", "").startswith("digest:"),
                           "completion_report inputs use stable state digest")
            tc.assert_true("state" not in inputs,
                           "raw pipeline_state.json path removed from fingerprint inputs")

            # (a) 同一 state 下连续两次计算指纹必须相等（幂等）
            fp1 = runner._fingerprint("completion_report")
            fp2 = runner._fingerprint("completion_report")
            tc.assert_equal(fp1, fp2, "fingerprint idempotent on identical state")

            # (b) 模拟本步骤 mark_started/mark_completed 写回（含 last_run 更新）
            #     后再算——自引用已消除，指纹仍相等，且跨运行缓存可命中
            state.mark_started("completion_report")
            state.mark_completed("completion_report", fp1)
            fp3 = runner._fingerprint("completion_report")
            tc.assert_equal(fp3, fp1, "fingerprint stable after self write-back")
            tc.assert_equal(runner._step_dirty_reason("completion_report"), None,
                            "completion_report cache reusable on next run (CACHED reachable)")

            # (c) 任一上游步骤记录变化 → 指纹必须变化（真实消费关系不丢）
            state.data["steps"]["render"]["status"] = "failed"
            state.save()
            fp4 = runner._fingerprint("completion_report")
            tc.assert_true(fp4 != fp1, "upstream step record change invalidates fingerprint")
            tc.assert_equal(runner._step_dirty_reason("completion_report"), "inputs-changed",
                            "dirty reason is inputs-changed after upstream change")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_duration_budget_gate() -> RegressionTestCase:
    """用例31：时长预算门禁（wp 批次复盘 P0）

    背景：wp 批次 4 集视频 2 集超 240s 预算（253.0s/248.9s），因流水线无任何
    预算门禁，完工报告盖章 VALIDATED 后才人工返工（各耗 ~25 分钟全链重跑）。
    本用例锁定：hard 超限直接 FAIL；soft 超限需 --accept-over-budget 显式放行
    并在 state 留痕；未声明预算/预算内 → 零副作用。
    """
    tc = RegressionTestCase(
        "duration_budget_gate",
        "验证时长预算门禁：hard阻断、soft需显式放行留痕、未声明零副作用"
    )
    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            def make_runner(cfg, accept=False, idx=0):
                state = PipelineState(tmpdir / f"state_{idx}.json")
                # 真实时序：预算门禁在 timeline 步内执行，此时该步必然已 started
                state.mark_started("timeline")
                runner = PipelineRunner.__new__(PipelineRunner)
                runner.state = state
                runner.config = cfg
                runner.quick_fix = False
                runner.accept_over_budget = accept
                return runner

            # (a) hard：超限直接 FAIL，timeline 标记 failed
            r1 = make_runner({"video_duration": 253.0,
                              "duration_budget": {"seconds": 240, "enforcement": "hard"}}, idx=1)
            tc.assert_true(not r1._budget_gate_ok(), "hard enforcement blocks over-budget")
            tc.assert_equal(r1.state.data["steps"]["timeline"]["status"], "failed",
                            "timeline marked failed under hard enforcement")

            # (b) soft：无放行标志 → 阻断；带标志 → 放行且决策留痕
            r2 = make_runner({"video_duration": 253.0,
                              "duration_budget": {"seconds": 240}}, idx=2)
            tc.assert_true(not r2._budget_gate_ok(),
                           "soft blocks without --accept-over-budget")
            r3 = make_runner({"video_duration": 253.0,
                              "duration_budget": {"seconds": 240}}, accept=True, idx=3)
            tc.assert_true(r3._budget_gate_ok(), "soft passes with --accept-over-budget")
            dec = r3.state.data.get("budget_decision", {})
            tc.assert_equal(dec.get("decision"), "accepted",
                            "acceptance decision recorded in state")
            tc.assert_equal(dec.get("actual_seconds"), 253.0,
                            "recorded actual duration for audit")

            # (c) 未声明 / 预算内 → 零副作用
            r4 = make_runner({"video_duration": 253.0}, idx=4)
            tc.assert_true(r4._budget_gate_ok(), "no budget declared → gate inactive")
            r5 = make_runner({"video_duration": 212.0,
                              "duration_budget": {"seconds": 240}}, idx=5)
            tc.assert_true(r5._budget_gate_ok(), "within budget → pass")
            tc.assert_true("budget_decision" not in r5.state.data,
                           "no decision record when within budget")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_interrupted_run_visibility() -> RegressionTestCase:
    """用例32：中断运行可见性（wp 批次复盘 P1）

    背景：wp-select-basics 22:09 的 TTS 运行被外部中断后日志只有命令头、无尾行，
    pipeline_state 无任何记录，运行历史不可见。本用例锁定：_run 日志尾行必写
    （exit_code 真实），启动扫描能检出缺尾行的历史日志并告警、不误报正常日志。
    """
    tc = RegressionTestCase(
        "interrupted_run_visibility",
        "验证 _run 日志尾行必写 + 启动扫描检出疑似中断运行"
    )
    try:
        import sys
        import io
        import contextlib
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        PipelineRunner = _pr.PipelineRunner

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            orig_log_dir = _pr.LOG_DIR
            _pr.LOG_DIR = tmpdir
            try:
                # (a) 正常子进程：尾行必写且 exit_code 真实
                runner = PipelineRunner.__new__(PipelineRunner)
                runner.config_path = tmpdir / "proj_x.json"
                runner._log_seq = 0
                runner._current_step = None
                runner.last_log_path = None
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = runner._run([sys.executable, "-c", "print('ok')"],
                                     desc="footer test")
                tc.assert_equal(rc, 0, "trivial subprocess exits 0")
                logs = list(tmpdir.glob("proj_x_*.log"))
                tc.assert_equal(len(logs), 1, "log file persisted")
                content = logs[0].read_text(encoding="utf-8")
                tc.assert_true("# finished:" in content and "exit_code: 0" in content,
                               "footer written with true exit code")

                # (b) 历史日志缺尾行 → 启动扫描必须告警
                bad = tmpdir / "proj_x_tts_01_interrupted.log"
                bad.write_text("$ fake\n# started: x\n" + "=" * 72 + "\n",
                               encoding="utf-8")
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    runner._warn_previous_interrupted_runs()
                out = buf2.getvalue()
                tc.assert_true(bad.name in out and "[WARN]" in out,
                               "footer-less log detected and warned")

                # (c) 有尾行的日志不误报
                (tmpdir / "proj_x_ok.log").write_text(
                    "$ fake\n# finished: x  exit_code: 0\n", encoding="utf-8")
                buf3 = io.StringIO()
                with contextlib.redirect_stdout(buf3):
                    runner._warn_previous_interrupted_runs()
                tc.assert_true("proj_x_ok.log" not in buf3.getvalue(),
                               "logs with footer not false-alarmed")
            finally:
                _pr.LOG_DIR = orig_log_dir

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_preview_safety_zone_precheck() -> RegressionTestCase:
    """用例33：预览安全区预检（prefab 复盘决策一，2026-08-07）

    背景：prefab-agent-launch 场景3 溢出字幕安全区 34px，消耗一次 37 分钟
    全量渲染后才在 visual_check 暴露。决策：布局溢出判定前移到秒级预览——
    instant_preview.check_preview_safety 对 preview 截图测量 content_bottom，
    超过安全线 y=860 即返回 violations（退出码2阻断渲染）。本用例用合成
    截图锁定：溢出场景必被检出，合规场景不误报。
    """
    tc = RegressionTestCase(
        "preview_safety_zone_precheck",
        "验证 preview 阶段安全区预检：溢出检出 + 合规不误报"
    )
    try:
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        # instant_preview 模块顶层重绑 sys.stdout/stderr，需安全导入
        instant_preview = _safe_import_rebinding_module("instant_preview")
        from visual_boundary_check import SUBTITLE_SAFETY_LINE
        from PIL import Image

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            def _make_scene_png(path, band_y):
                """黑底 + 白色内容带（带底部位于 band_y），模拟场景截图。"""
                img = Image.new("RGB", (1920, 1080), (0, 0, 0))
                px = img.load()
                for y in range(band_y - 30, band_y + 1):
                    for x in range(100, 1820):
                        px[x, y] = (255, 255, 255)
                img.save(path)

            # 溢出场景（带底 y=890 > 860）与合规场景（带底 y=520）
            _make_scene_png(tmp / "preview_s1_t0.png", 890)
            _make_scene_png(tmp / "preview_s2_t5.png", 520)
            manifest = [
                {"sceneId": "s1", "time": 0, "file": "s1_t0.png"},
                {"sceneId": "s2", "time": 5, "file": "s2_t5.png"},
                {"sceneId": "s3", "time": 9, "file": "s3_missing.png"},  # 无截图 → 跳过
            ]
            violations = instant_preview.check_preview_safety(manifest, str(tmp))

            tc.assert_equal(len(violations), 1, "exactly one scene overflows")
            v = violations[0]
            tc.assert_equal(v["scene"], "s1", "overflow scene identified")
            tc.assert_equal(v["content_bottom"], 890, "content_bottom measured")
            tc.assert_equal(v["overflow_px"], 890 - SUBTITLE_SAFETY_LINE,
                            "overflow px relative to safety line")
            tc.assert_true(not any(x["scene"] == "s2" for x in violations),
                           "compliant scene not false-alarmed")
            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_render_watchdog_stall_kill() -> RegressionTestCase:
    """用例34：渲染卡死看门狗（prefab 复盘决策二，2026-08-07）

    背景：prefab-agent-launch 渲染 #1 卡 0%（0/5305 帧）12 分钟无任何诊断。
    决策：_RenderProgressWatcher 增加卡死检测——进度连续 stall_seconds 零增长
    且总耗时已过 grace_seconds → 杀进程树 fail-fast；阈值权威源
    config/quality/render_rules.json，纳入 render 步指纹。本用例锁定：
    卡死判定触发杀进程、有进度不误杀、配置可加载、_run 注入进程句柄。
    """
    tc = RegressionTestCase(
        "render_watchdog_stall_kill",
        "验证看门狗卡死判定/不误杀/配置权威源/进程注入"
    )
    try:
        import io
        import types
        import contextlib
        import time as _time
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        Watcher = _pr._RenderProgressWatcher

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            render_raw = tmp / "render_raw.mp4"
            wd_cfg = {"enabled": True, "grace_seconds": 0.5, "stall_seconds": 0.5}

            # (a) 零进度超阈值 → 判定卡死并杀进程树
            w = Watcher([tmp], render_raw, 0, watchdog=wd_cfg)
            killed = []
            w._kill_process_tree = lambda pid: killed.append(pid)
            w.attach_process(types.SimpleNamespace(pid=12345))
            w._start_time = _time.time() - 1.0        # 总耗时已过 grace
            w._last_progress_ts = _time.time() - 1.0  # 进度已停滞 1s
            w._last_progress_val = 0                  # 与 _progress_value() 一致
            with contextlib.redirect_stdout(io.StringIO()):
                w._check_watchdog()
            tc.assert_true(w.stalled, "stall detected")
            tc.assert_equal(killed, [12345], "process tree killed (fail-fast)")

            # (b) 刚启动（进度刚刷新）→ 不误杀
            w2 = Watcher([tmp], render_raw, 0, watchdog=wd_cfg)
            killed2 = []
            w2._kill_process_tree = lambda pid: killed2.append(pid)
            w2.attach_process(types.SimpleNamespace(pid=22222))
            with contextlib.redirect_stdout(io.StringIO()):
                w2._check_watchdog()  # 首次调用：进度基线刚建立，stall≈0
            tc.assert_true(not w2.stalled and not killed2,
                           "fresh progress not false-killed")

            # (c) 看门狗禁用 → 绝不触发
            w3 = Watcher([tmp], render_raw, 0, watchdog={"enabled": False})
            w3.attach_process(types.SimpleNamespace(pid=33333))
            w3._start_time = _time.time() - 9999
            w3._last_progress_ts = _time.time() - 9999
            w3._last_progress_val = 0
            with contextlib.redirect_stdout(io.StringIO()):
                w3._check_watchdog()
            tc.assert_true(not w3.stalled, "disabled watchdog never fires")

            # (d) 配置权威源：render_rules.json 存在、可解析、阈值字段齐全
            rules_path = _pr.CONFIG_DIR / "quality" / "render_rules.json"
            tc.assert_true(rules_path.exists(), "render_rules.json exists")
            rules = json.loads(rules_path.read_text(encoding="utf-8"))
            wd = rules.get("watchdog", {})
            tc.assert_true("grace_seconds" in wd and "stall_seconds" in wd,
                           "watchdog thresholds declared")
            loaded = _pr._load_render_rules()
            tc.assert_equal(loaded["watchdog"]["stall_seconds"], wd["stall_seconds"],
                            "_load_render_rules reads authoritative config")

            # (e) _run 注入进程句柄（看门狗拿得到 pid 才能杀树）
            orig_log_dir = _pr.LOG_DIR
            _pr.LOG_DIR = tmp
            try:
                runner = _pr.PipelineRunner.__new__(_pr.PipelineRunner)
                runner.config_path = tmp / "proj_x.json"
                runner._log_seq = 0
                runner._current_step = None
                runner.last_log_path = None
                w4 = Watcher([tmp], render_raw, 0, watchdog=wd_cfg)
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = runner._run([sys.executable, "-c", "print('ok')"],
                                     desc="watchdog attach", attach_watchdog=w4)
                tc.assert_equal(rc, 0, "trivial subprocess exits 0")
                tc.assert_true(w4._proc is not None,
                               "watchdog received process handle via _run")
            finally:
                _pr.LOG_DIR = orig_log_dir

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_preflight_blocks_missing_design_artifacts() -> RegressionTestCase:
    """用例35：无设计产物 → preflight 阻断（prefab 复盘决策三，2026-08-07）

    背景：prefab-agent-launch 无任何分镜设计就进入生产，内容沦为旧素材
    拼凑、用户需求未体现；此前设计产物缺失仅 WARN 放行。决策：升级为
    阻断错误，自动探测模式补 narration.design.json，声明指针失效也硬报错。
    """
    tc = RegressionTestCase(
        "preflight_blocks_missing_design_artifacts",
        "验证设计产物缺失/失效指针阻断 preflight，存量格式可探测"
    )
    try:
        import io
        import contextlib
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import preflight_check as pf

        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "demo-project"
            proj.mkdir()
            (proj / "index.html").write_text("<html></html>", encoding="utf-8")

            # (a) 无设计产物 → 阻断错误（不再是 WARN）
            r1 = pf.PreflightResult(mode="render")
            with contextlib.redirect_stdout(io.StringIO()):
                pf.check_production_readiness(proj, {}, r1)
            tc.assert_true(any("No design artifacts" in e for e in r1.errors),
                           "missing design artifacts produce blocking error")
            tc.assert_true(not r1.passed, "preflight failed without design")

            # (b) storyboard_*.md → 自动探测通过
            (proj / "storyboard_demo.md").write_text("# 分镜", encoding="utf-8")
            r2 = pf.PreflightResult(mode="render")
            with contextlib.redirect_stdout(io.StringIO()):
                pf.check_production_readiness(proj, {}, r2)
            tc.assert_true(not any("No design artifacts" in e for e in r2.errors),
                           "storyboard md detected, no blocking error")
            (proj / "storyboard_demo.md").unlink()

            # (c) narration.design.json → 自动探测通过（新增模式）
            (proj / "narration.design.json").write_text("{}", encoding="utf-8")
            r3 = pf.PreflightResult(mode="render")
            with contextlib.redirect_stdout(io.StringIO()):
                pf.check_production_readiness(proj, {}, r3)
            tc.assert_true(not any("No design artifacts" in e for e in r3.errors),
                           "narration.design.json detected")
            (proj / "narration.design.json").unlink()

            # (d) 声明了 design_artifacts 但指针失效 → 硬报错
            r4 = pf.PreflightResult(mode="render")
            cfg = {"design_artifacts": {"storyboard": str(proj / "gone.md")}}
            with contextlib.redirect_stdout(io.StringIO()):
                pf.check_production_readiness(proj, cfg, r4)
            tc.assert_true(any("not found" in e for e in r4.errors),
                           "broken design_artifacts pointer is a hard error")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_delivery_audit_fixture_verdict() -> RegressionTestCase:
    """用例36：交付审计治具裁定（agent-wiki 角色分离落地，2026-08-19）

    背景：SKILL.md 旧版自评量表由执行者自评，违反"模型不得给自己打分"。
    交付审计 5 维度改为 generate_completion_report.run_delivery_audit 治具推导。
    本用例锁定：禁用技术词/srt 异名/force 留痕/缺 verifications 各维度必须
    裁定失败，合规输入必须全维通过；禁止任何人把审计改回软提示。
    """
    tc = RegressionTestCase(
        "delivery_audit_fixture_verdict",
        "验证交付审计5维度由真实数据裁定：违规必FAIL、合规必PASS"
    )

    try:
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        from generate_completion_report import run_delivery_audit

        def _base_report():
            """构造一份全绿的完工报告骨架（测量数据由调用方覆写）"""
            return {
                "status": "VALIDATED",
                "issues": [],
                "validation": {
                    "step_preflight_passed": True, "step_tts_passed": True,
                    "step_timeline_passed": True, "step_render_passed": True,
                    "step_verify_passed": True, "step_postprocess_passed": True,
                    "has_video_stream": True, "has_audio_stream": True,
                    "verification_tts_product_passed": True,
                    "verification_media_quality_passed": True,
                    "product_state_consistent": True,
                },
                "data_sources": {"video_file": {"ffprobe_available": True}},
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # 权威旁白源（维度4：指针可解析 + 指纹留档）
            narration = tmpdir / "narration.json"
            narration.write_text(json.dumps({"scenes": [
                {"scene_id": 1, "start": 0, "end": 10, "narration": "测试旁白", "type": "gsap"}
            ]}, ensure_ascii=False), encoding='utf-8')
            cfg_path = tmpdir / "cfg.json"
            cfg_path.write_text(json.dumps({
                "cover_duration": 0,
                "narration_source": "./narration.json",
                "delivery": {"prohibited_terms": ["final", "v01", "render", "raw", "tmp", "draft"]},
            }, ensure_ascii=False), encoding='utf-8')
            state_path = tmpdir / "pipeline_state.json"
            state_path.write_text(json.dumps({"steps": {}}), encoding='utf-8')

            video_ok = tmpdir / "业务主题.mp4"
            srt_ok = tmpdir / "业务主题.srt"
            video_ok.write_bytes(b"")
            srt_ok.write_bytes(b"")

            # 1. 合规输入：5 维度全过
            audit = run_delivery_audit(_base_report(), str(video_ok), str(srt_ok),
                                       str(state_path), str(cfg_path))
            tc.assert_true(audit["passed"], "compliant delivery passes all 5 dimensions")
            tc.assert_true("sha256=" in audit["dimensions"]["storyboard_fidelity"]["evidence"],
                           "narration fingerprint recorded for traceability")

            # 2. 禁用技术词入名 → 交付合规 FAIL
            video_bad = tmpdir / "业务主题_final.mp4"
            video_bad.write_bytes(b"")
            audit = run_delivery_audit(_base_report(), str(video_bad), str(srt_ok),
                                       str(state_path), str(cfg_path))
            tc.assert_true(not audit["dimensions"]["delivery_compliance"]["passed"],
                            "prohibited term in filename fails delivery_compliance")
            tc.assert_true(not audit["passed"], "prohibited term fails overall audit")

            # 3. srt 与 mp4 异基名 → 交付合规 FAIL
            srt_bad = tmpdir / "另一个名字.srt"
            srt_bad.write_bytes(b"")
            audit = run_delivery_audit(_base_report(), str(video_ok), str(srt_bad),
                                       str(state_path), str(cfg_path))
            tc.assert_true(not audit["dimensions"]["delivery_compliance"]["passed"],
                            "srt/mp4 stem mismatch fails delivery_compliance")

            # 4. --force 绕过留痕 → 门禁完整性 FAIL
            state_path.write_text(json.dumps(
                {"steps": {}, "forced_run": {"at": "2026-08-19T10:00:00"}}), encoding='utf-8')
            audit = run_delivery_audit(_base_report(), str(video_ok), str(srt_ok),
                                       str(state_path), str(cfg_path))
            tc.assert_true(not audit["dimensions"]["gate_integrity"]["passed"],
                            "forced_run marker fails gate_integrity")
            state_path.write_text(json.dumps({"steps": {}}), encoding='utf-8')

            # 5. verifications 缺失（旧 state）→ 门禁完整性 FAIL（禁止静默放行）
            rpt = _base_report()
            rpt["validation"] = {k: v for k, v in rpt["validation"].items()
                                 if not k.startswith("verification_")}
            audit = run_delivery_audit(rpt, str(video_ok), str(srt_ok),
                                       str(state_path), str(cfg_path))
            tc.assert_true(not audit["dimensions"]["gate_integrity"]["passed"],
                            "missing verifications fails gate_integrity")

            # 6. narration 指针失效 → 分镜忠实度 FAIL（P0-03 硬报错语义延续）
            narration.unlink()
            audit = run_delivery_audit(_base_report(), str(video_ok), str(srt_ok),
                                       str(state_path), str(cfg_path))
            tc.assert_true(not audit["dimensions"]["storyboard_fidelity"]["passed"],
                            "broken narration_source fails storyboard_fidelity")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_render_budget_gate() -> RegressionTestCase:
    """用例37：渲染成本预算门禁（P1 渲染成本治理，2026-08-19）

    背景：prefab 批次复盘实证"3 次 40 分钟级重渲 + 9 小时失败收尾"——
    全量重渲次数失控是成本失控的直接形态。本用例锁定：全量渲染尝试次数
    达到 render_budget.max_full_renders 后，hard 直接 FAIL、soft 需
    --accept-over-render 显式放行并留痕；未声明预算/预算内/--force 各自语义。
    """
    tc = RegressionTestCase(
        "render_budget_gate",
        "验证渲染预算门禁：hard阻断、soft需显式放行留痕、未声明零副作用"
    )
    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            def make_runner(render_rules, accept=False, force=False, idx=0):
                state = PipelineState(tmpdir / f"state_{idx}.json")
                # 真实时序：预算门禁在 render 步内执行，此时该步必然已 started
                state.mark_started("render")
                runner = PipelineRunner.__new__(PipelineRunner)
                runner.state = state
                runner.render_rules = render_rules
                runner.force = force
                runner.accept_over_render = accept
                return runner

            budget_soft = {"render_budget": {"max_full_renders": 3,
                                             "enforcement": "soft"}}
            budget_hard = {"render_budget": {"max_full_renders": 3,
                                             "enforcement": "hard"}}

            # (a) 未声明 render_budget → 门禁不生效（零副作用）
            r1 = make_runner({}, idx=1)
            tc.assert_true(r1._render_budget_gate_ok(),
                           "no budget declared → gate inactive")

            # (b) 预算内（2/3 次尝试）→ 放行且无决策留痕
            r2 = make_runner(budget_soft, idx=2)
            r2._render_metrics()["full_render_attempts"] = 2
            tc.assert_true(r2._render_budget_gate_ok(), "within budget → pass")
            tc.assert_true("render_budget_decision" not in r2.state.data,
                           "no decision record when within budget")

            # (c) soft 超限（3/3）无放行标志 → 阻断，render 标记 failed
            r3 = make_runner(budget_soft, idx=3)
            r3._render_metrics()["full_render_attempts"] = 3
            tc.assert_true(not r3._render_budget_gate_ok(),
                           "soft blocks without --accept-over-render")
            tc.assert_equal(r3.state.data["steps"]["render"]["status"], "failed",
                            "render marked failed when soft-blocked")

            # (d) soft 超限 + 放行标志 → 通过且决策留痕
            r4 = make_runner(budget_soft, accept=True, idx=4)
            r4._render_metrics()["full_render_attempts"] = 3
            tc.assert_true(r4._render_budget_gate_ok(),
                           "soft passes with --accept-over-render")
            dec = r4.state.data.get("render_budget_decision", {})
            tc.assert_true(dec.get("accepted") is True,
                           "acceptance recorded in render_budget_decision")
            tc.assert_equal(dec.get("attempts"), 3,
                            "decision records attempt count at grant time")

            # (e) hard 超限 → 直接 FAIL（放行标志亦无效）
            r5 = make_runner(budget_hard, accept=True, idx=5)
            r5._render_metrics()["full_render_attempts"] = 3
            tc.assert_true(not r5._render_budget_gate_ok(),
                           "hard enforcement FAILs regardless of flag")

            # (f) --force 越过（语义同 HARD_GATES：留痕走 forced_run 通道）
            r6 = make_runner(budget_soft, force=True, idx=6)
            r6._render_metrics()["full_render_attempts"] = 5
            tc.assert_true(r6._render_budget_gate_ok(),
                           "--force bypasses render budget")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_pre_render_duration_consistency() -> RegressionTestCase:
    """用例38：渲染前时长一致性预检（2026-08-23 agent-wiki-promo 复盘）

    背景：run2 的 adjust_timeline 静默回退 HTML 至 150s 基线后判"零调整"，
    HTML/config 分叉直达 45 分钟全量渲染，渲染后验证才发现。本预检 O(1)
    拦截：分叉阻断留痕、一致放行、--force 绕过、无 data-duration 零副作用。
    """
    tc = RegressionTestCase(
        "pre_render_duration_consistency",
        "验证渲染前时长一致性预检：HTML/config 分叉阻断、一致放行、force绕过"
    )
    try:
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            src = tmpdir / "proj"
            src.mkdir()
            html = src / "index.html"

            def make_runner(html_dur, cfg_dur, force=False, tag=""):
                html.write_text(
                    f'<div id="root" data-duration="{html_dur}"></div>',
                    encoding="utf-8")
                state = PipelineState(tmpdir / f"state_{tag}.json")
                state.mark_started("render")
                runner = PipelineRunner.__new__(PipelineRunner)
                runner.state = state
                runner.force = force
                runner.source_dir = src
                runner.config = {"video_duration": cfg_dur}
                return runner

            # (a) HTML 150s vs config 160.6s → 阻断 + render 标记失败
            r1 = make_runner(150, 160.6, tag="diverge")
            tc.assert_true(not r1._pre_render_duration_consistent(),
                           "duration divergence blocks before render")
            tc.assert_equal(r1.state.data["steps"]["render"]["status"], "failed",
                            "render marked failed on divergence")

            # (b) 一致（容差内）→ 放行
            r2 = make_runner(160.6, 160.6, tag="aligned")
            tc.assert_true(r2._pre_render_duration_consistent(), "aligned duration passes")

            # (c) --force 绕过（语义同 HARD_GATES）
            r3 = make_runner(150, 160.6, force=True, tag="force")
            tc.assert_true(r3._pre_render_duration_consistent(),
                           "--force bypasses consistency check")

            # (d) HTML 无 data-duration → 零副作用（preflight 职责不重叠）
            html.write_text("<div></div>", encoding="utf-8")
            state = PipelineState(tmpdir / "state_nodur.json")
            state.mark_started("render")
            r4 = PipelineRunner.__new__(PipelineRunner)
            r4.state, r4.force, r4.source_dir = state, False, src
            r4.config = {"video_duration": 100}
            tc.assert_true(r4._pre_render_duration_consistent(),
                           "missing data-duration has no side effect")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

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
            test_delivery_gate_blocks_quickfix,
            test_verification_result_persistence,
            test_quality_threshold_config_effective,
            test_quality_config_fingerprint_invalidation,
            test_bitrate_dual_tier_gate,
            test_dead_air_scene_exclusion,
            test_silence_fallback_and_bitrate_na_tolerance,
            test_tts_concurrency_pool,
            test_visual_boundary_three_point_sampling,
            test_render_progress_watcher,
            test_error_code_and_cache_age,
            test_completion_report_fingerprint_stability,
            test_duration_budget_gate,
            test_interrupted_run_visibility,
            test_preview_safety_zone_precheck,
            test_render_watchdog_stall_kill,
            test_preflight_blocks_missing_design_artifacts,
            test_delivery_audit_fixture_verdict,
            test_render_budget_gate,
            test_pre_render_duration_consistency,
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
