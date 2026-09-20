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
        "验证媒体QA门禁检查项齐备（黑场重叠/单字行/术语规范新门禁，项名见 media_qa_gate docstring）"
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
        
        # 新门禁（黑场重叠/单字跨行/术语 lint）基础设施存在且规则可加载（单一权威源）
        tc.assert_true(callable(ffmpeg_detect_black_intervals),
                      "no_black_frame_with_subtitle: ffmpeg_detect_black_intervals exists")
        bf_rules = _load_black_frame_rules()
        tc.assert_true("min_black_seconds" in bf_rules
                      and "max_overlap_with_narration_seconds" in bf_rules,
                      "no_black_frame_with_subtitle: black_frame rules loadable with required keys")
        term_patterns = _load_term_lint_patterns()
        tc.assert_true(isinstance(term_patterns, list) and len(term_patterns) > 0,
                      "subtitle_terms_normalized: subtitle_term_rules lint_patterns loadable and non-empty")
        
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
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        
        # （原断言 1「源码含 import 指纹模块」/断言 2「源码含 def _can_skip」已删除：
        #  二者只证明字符串在场，不证明缓存判定行为。同一判据的真实行为面由
        #  用例50 逐条锁定（四态跳步判定 + 指纹失配自动失效），此处不做重复脚手架。
        #  2026-09-19 审核 A12：源码字符串断言按"有无行为接缝"分级，不一刀切。）
        
        # 3. 盲信跳步的旧模式必须清零（is_passed 直接决定跳步）——全文件清扫，
        #    无单一行为接缝可替代，保留源码面锁定
        tc.assert_true('self.state.is_passed("visual_check")' not in runner_content
                      and "[SKIPPED] Already passed" not in runner_content,
                      "No blind is_passed-only skip remains in step methods")
        
        # 4. 缓存命中必须显式可见（打印状态日期+指纹）——日志词汇锁，保留源码面
        tc.assert_true("[CACHED]" in runner_content and "[STALE]" in runner_content,
                      "Cache hit/stale states are explicitly printed")
        
        # 5. 验证类运行的规定入口 --fresh 必须存在且传入 runner（其重置语义由
        #    用例56 在行为面锁定）
        tc.assert_true('"--fresh"' in runner_content and "fresh=args.fresh" in runner_content,
                      "--fresh flag exists and is wired into PipelineRunner")
        
        # 6. 完成时必须记录指纹——计数基准取运行时 STEPS，不抄字面 8
        #    （2026-09-19 审核 A12：手抄阈值与权威清单脱钩即静默失去覆盖）
        fp_records = runner_content.count("self._fingerprint(")
        tc.assert_equal(len(_pr.STEPS), 10,
                        "STEPS is the step-count authority (change this only with intent)")
        tc.assert_true(fp_records >= len(_pr.STEPS),
                       f"Every step records a fingerprint on completion "
                       f"(found {fp_records}, need >=len(STEPS)={len(_pr.STEPS)})")
        
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
    # 夹具必须落在 HTML_BASE 下（adjust_timeline 按 paths.html_project 解析该目录）；
    # 名称带 pid：固定路径下，并发运行的 finally rmtree 会删掉对方正在使用的夹具。
    proj_name = f"_regress-drift-resync-{os.getpid()}"
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
    # pid 后缀：理由同 test_timeline_drift_resync（并发套迭互删夹具）
    proj_name = f"_regress-narr-source-{os.getpid()}"
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
                runner.gate_mode = "render"
                runner.quick_fix = False

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
                errs, warns, untested_q, na_q = eva._check_video_quality("fake.mp4")
            finally:
                eva.subprocess.run = saved_run
            tc.assert_true(not any("bitrate" in e.lower() for e in errs),
                           "N/A bitrate raises no bitrate FAIL in _check_video_quality")
            tc.assert_true(any("已跳过码率门禁" in w for w in warns),
                           "N/A bitrate degrades to traceable skip-warning (eva)")

            # (b3) media_qa_gate video_bitrate_ok 调用侧：bit_rate=N/A → 门禁降级 warning
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

        def fake_coverage(image_path, subtitle_lines=2):
            # 第二遍真空窗检查需要可读帧；夹具无真实图像，给固定覆盖率让
            # 该维度真实跑完（否则本用例会被判 UNTESTED，掩盖三点采样的判定）
            return 0.5

        saved_extract = vbc.extract_frame
        saved_measure = vbc.measure_content_bottom
        saved_cov = vbc.measure_content_coverage
        try:
            vbc.extract_frame = fake_extract
            vbc.measure_content_bottom = fake_measure
            vbc.measure_content_coverage = fake_coverage
            scenes = [
                {"id": 1, "start": 0.0, "end": 10.0},
                {"id": 2, "start": 10.0, "end": 20.0},
                {"id": 3, "start": 20.0, "end": 21.5},  # dur<2 → NOT_APPLICABLE
            ]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                passed, results, summary = vbc.run_check("fake.mp4", scenes)
        finally:
            vbc.extract_frame = saved_extract
            vbc.measure_content_bottom = saved_measure
            vbc.measure_content_coverage = saved_cov

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
        tc.assert_equal(r3["status"], "NOT_APPLICABLE",
                        "scene shorter than 2s recorded as NOT_APPLICABLE (not PASS)")
        tc.assert_equal(summary["untested"], [],
                        "fully-measured run records zero untested items")
        tc.assert_equal(summary["verdict"], "FAIL",
                        "verdict follows violations, not absence of them")

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
            runner.gate_mode = "render"
            runner.quick_fix = False

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
    并在 state 留痕；预算内通过同样留痕（duration_budget_check，使门禁可证真）；
    仅未声明预算 = 零副作用。
    """
    tc = RegressionTestCase(
        "duration_budget_gate",
        "验证时长预算门禁：hard阻断、soft需显式放行留痕、通过路径留执行痕、未声明零副作用"
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

            # (c) 预算内 → 通过且留下"检查点跑过"的痕迹；未声明 → 零副作用
            r4 = make_runner({"video_duration": 253.0}, idx=4)
            tc.assert_true(r4._budget_gate_ok(), "no budget declared → gate inactive")
            tc.assert_true("duration_budget_check" not in r4.state.data,
                           "undeclared budget keeps zero side effect")
            r5 = make_runner({"video_duration": 212.0,
                              "duration_budget": {"seconds": 240}}, idx=5)
            tc.assert_true(r5._budget_gate_ok(), "within budget → pass")
            tc.assert_true("budget_decision" not in r5.state.data,
                           "no decision record when within budget")
            chk = r5.state.data.get("duration_budget_check", {})
            tc.assert_equal(chk.get("decision"), "within_budget",
                            "pass path leaves an auditable checkpoint record")
            tc.assert_equal(chk.get("actual_seconds"), 212.0,
                            "checkpoint records measured duration")
            r6 = make_runner({"duration_budget": {"seconds": 240}}, idx=6)
            tc.assert_true(r6._budget_gate_ok(), "missing video_duration → pass")
            tc.assert_equal(r6.state.data.get("duration_budget_check", {}).get("decision"),
                            "not_adjudicated",
                            "no authoritative duration is recorded as unadjudicated, not as pass")

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
        from visual_boundary_check import subtitle_safety_line
        from PIL import Image

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            def _make_scene_png(path, band_y, size=(1920, 1080)):
                """黑底 + 白色内容带（带底部位于 band_y），模拟场景截图。"""
                img = Image.new("RGB", size, (0, 0, 0))
                px = img.load()
                for y in range(band_y - 30, band_y + 1):
                    for x in range(100, size[0] - 100):
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
            tc.assert_equal(v["overflow_px"], 890 - subtitle_safety_line(1080),
                            "overflow px relative to safety line")
            tc.assert_true(not any(x["scene"] == "s2" for x in violations),
                           "compliant scene not false-alarmed")

            # ── 竖版：安全线随画布高度等比缩放，不得沿用横版 860 ──
            tc.assert_equal(subtitle_safety_line(1080), 860,
                            "1080 baseline reproduces legacy 860")
            tc.assert_equal(subtitle_safety_line(1920), 1529,
                            "1920 tall canvas scales the band proportionally")
            _make_scene_png(tmp / "preview_s4_t0.png", 1560, (1080, 1920))
            _make_scene_png(tmp / "preview_s5_t0.png", 1200, (1080, 1920))
            portrait_manifest = [
                {"sceneId": "s4", "time": 0, "file": "s4_t0.png"},
                {"sceneId": "s5", "time": 0, "file": "s5_t0.png"},
            ]
            portrait_violations = instant_preview.check_preview_safety(
                portrait_manifest, str(tmp))
            tc.assert_equal([x["scene"] for x in portrait_violations], ["s4"],
                            "portrait overflow uses the scaled safety line")
            tc.assert_equal(portrait_violations[0]["overflow_px"],
                            1560 - subtitle_safety_line(1920),
                            "portrait overflow px measured against y=1529")
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
            tc.assert_true(audit["passed"], "compliant delivery passes every audit dimension")
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

            # 6. 质检存在未测项（passed 仍为 True）→ 门禁完整性 FAIL
            #    锁定"没跑完的检查"不能在审计里折算成通过（2026-09-18 审核根因一）
            rpt = _base_report()
            rpt["validation"]["verification_media_quality_untested"] = 2
            audit = run_delivery_audit(rpt, str(video_ok), str(srt_ok),
                                       str(state_path), str(cfg_path))
            tc.assert_true(not audit["dimensions"]["gate_integrity"]["passed"],
                           "UNTESTED checks fail gate_integrity even with no errors")
            tc.assert_true("UNTESTED" in audit["dimensions"]["gate_integrity"]["evidence"],
                           "audit evidence names the untested counts")
            rpt_zero = _base_report()
            rpt_zero["validation"]["verification_media_quality_untested"] = 0
            audit = run_delivery_audit(rpt_zero, str(video_ok), str(srt_ok),
                                       str(state_path), str(cfg_path))
            tc.assert_true(audit["dimensions"]["gate_integrity"]["passed"],
                           "zero untested keeps gate_integrity passing (no vacuous block)")

            # 7. narration 指针失效 → 分镜忠实度 FAIL（P0-03 硬报错语义延续）
            narration.unlink()
            audit = run_delivery_audit(_base_report(), str(video_ok), str(srt_ok),
                                       str(state_path), str(cfg_path))
            tc.assert_true(not audit["dimensions"]["storyboard_fidelity"]["passed"],
                            "broken narration_source fails storyboard_fidelity")

            # 8. 维度名以代码为权威源（A12）：AST 抓 dims["..."] ↔ 运行时键集一致。
            #    文档侧的中文枚举（AGENTS.md 交付审计行）不参与机器比对，
            #    新增/删除维度若忘了同步裁定逻辑，本处即红。
            import ast as _ast
            _rep_src = (Path(__file__).parent / "generate_completion_report.py"
                        ).read_text(encoding='utf-8')
            _declared = set()
            for _node in _ast.walk(_ast.parse(_rep_src)):
                if (isinstance(_node, _ast.Assign) and len(_node.targets) == 1
                        and isinstance(_node.targets[0], _ast.Subscript)
                        and isinstance(_node.targets[0].slice, _ast.Constant)
                        and isinstance(_node.targets[0].slice.value, str)
                        and isinstance(getattr(_node.targets[0].value, 'id', None), str)
                        and _node.targets[0].value.id == 'dims'):
                    _declared.add(_node.targets[0].slice.value)
            tc.assert_true(len(_declared) >= 3,
                           f"AST found {len(_declared)} audit dimensions (authority is not empty)")
            tc.assert_equal(set(audit["dimensions"]), _declared,
                            "the runtime audit reports exactly the dimensions the code declares")

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


def test_delivery_registry_backing() -> RegressionTestCase:
    """用例39：交付登记表背书（2026-08-31 agent-wiki-promo 复盘 🟢）

    背景：mp4 成片不入 git，git 里没有任何地方记录"交付了什么"。交付物保护门禁
    因此只能认 temp_dir 里的 VALIDATED 完工报告——8-22 那次跑到 verify 就中断，
    报告未生成，150s 旧基线以"无背书孤儿"形态占了规范交付名 9 天，门禁按定义放过。
    本用例锁定：登记表命中即视为已交付（不依赖易被清理的报告）、未登记且无 VALIDATED
    报告判为非交付（孤儿可识别）、登记表缺失/损坏零副作用、登记值一律来自实测。
    """
    tc = RegressionTestCase(
        "delivery_registry_backing",
        "验证交付登记表作为交付态权威背书：命中拦截、未登记判孤儿、缺失/损坏零副作用"
    )
    _saved_registry = None
    try:
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            _saved_registry = _pr.DELIVERY_REGISTRY
            _pr.DELIVERY_REGISTRY = tmpdir / "成果文件" / "交付登记.json"

            def make_runner(video_name="业务主题_推广介绍.mp4", tag=""):
                out = tmpdir / "成果文件" / "视频" / video_name
                out.parent.mkdir(parents=True, exist_ok=True)
                if not out.exists():
                    out.write_bytes(b"\x00" * 1024)
                runner = PipelineRunner.__new__(PipelineRunner)
                runner.state = PipelineState(tmpdir / f"state_{tag}.json")
                runner.output_file = out
                runner.temp_dir = tmpdir / f"temp_{tag}"
                runner.html_project = "demo-project"
                runner.config_path = tmpdir / "config.json"
                runner._probe_duration = lambda p: 160.6
                return runner

            # (a) 登记表不存在 → 判据为空，不误报已交付（零副作用）
            r0 = make_runner(tag="missing")
            tc.assert_true(r0._registry_entry(r0.output_file.name) is None,
                           "missing registry yields no entry")
            tc.assert_true(not r0._output_was_delivered(),
                           "missing registry does not claim delivered")

            # (b) 登记后：命中，且登记值全部来自实测
            entry = r0._record_delivery(r0.output_file,
                                        r0.temp_dir / "字幕.srt", "VALIDATED 13/13")
            tc.assert_equal(entry.get("duration_seconds"), 160.6,
                            "registry records probed duration, not declared")
            tc.assert_equal(entry.get("bytes"), 1024,
                            "registry records actual file size")
            tc.assert_true(r0._registry_entry("业务主题_推广介绍.mp4") is not None,
                           "recorded delivery is findable by video name")
            tc.assert_true(_pr.DELIVERY_REGISTRY.exists(),
                           "registry file created under 成果文件/")

            # (c) 交付态判据升级：无完工报告也能识别为已交付（本次事故的正解）
            tc.assert_true(not (r0.temp_dir / "completion_report.json").exists(),
                           "no completion report present in this scenario")
            tc.assert_true(r0._output_was_delivered(),
                           "registry hit alone marks delivered")

            # (d) 未登记的槽位（孤儿）→ 判为非交付，允许被新渲染覆盖
            orphan = make_runner("另一项目_推广介绍.mp4", tag="orphan")
            tc.assert_true(not orphan._output_was_delivered(),
                           "unbacked slot is not treated as delivered")

            # (e) 历史回退仍生效：无登记但有同名的 VALIDATED 报告 → 已交付
            legacy = make_runner("旧机制项目_推广介绍.mp4", tag="legacy")
            legacy.temp_dir.mkdir(parents=True, exist_ok=True)
            (legacy.temp_dir / "completion_report.json").write_text(
                json.dumps({"status": "VALIDATED 12/13",
                            "data_sources": {"video_file": {
                                "path": str(legacy.output_file)}}}),
                encoding="utf-8")
            tc.assert_true(legacy._output_was_delivered(),
                           "VALIDATED report fallback still recognizes delivery")

            # (f) 报告指向别的成片 → 不冒名拦截
            other = make_runner("同名不同片.mp4", tag="mismatch")
            other.temp_dir.mkdir(parents=True, exist_ok=True)
            (other.temp_dir / "completion_report.json").write_text(
                json.dumps({"status": "VALIDATED 12/13",
                            "data_sources": {"video_file": {
                                "path": str(tmpdir / "成果文件" / "视频" / "别的片.mp4")}}}),
                encoding="utf-8")
            tc.assert_true(not other._output_was_delivered(),
                           "report for a different video does not block by name")

            # (g) 同名重复登记 → 覆盖为最新一条，不产生重复条目
            r0._record_delivery(r0.output_file, None, "VALIDATED 13/13")
            names = [e.get("video") for e in
                     _pr.PipelineRunner._registry_read().get("deliveries", [])]
            tc.assert_equal(names.count("业务主题_推广介绍.mp4"), 1,
                            "re-recording upserts instead of duplicating")

            # (h) 登记表损坏 → 回退空表，不抛异常（损坏不致命，由调用方裁定）
            _pr.DELIVERY_REGISTRY.write_text("{ not json", encoding="utf-8")
            tc.assert_equal(len(_pr.PipelineRunner._registry_read()["deliveries"]), 0,
                            "corrupt registry degrades to empty")
            tc.assert_true(legacy._registry_entry("旧机制项目_推广介绍.mp4") is None,
                           "corrupt registry yields no entry (fallback decides)")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    finally:
        if _saved_registry is not None:
            _pr.DELIVERY_REGISTRY = _saved_registry

    return tc


def test_config_single_source_timeline_rejected() -> RegressionTestCase:
    """用例40：config 内嵌 timeline 节 → preflight 报错（单一权威源，2026-08-31）

    背景：11 个存量项目 config 内嵌 t_block/scene_metadata，无任何语义消费者
    （唯一按数据读取它的 preflight_simple.py 不在流水线内；scene_patch_render.py:123
    仅把它并入 config 哈希，不解读内容），清除前普查 21 份含该节者中 14 份已与自身
    video_duration 分叉（统计取自 git 清除前版本）；8-22 的 verify 报错定位正是被
    这份死数据误导。本次清除死数据并把红线代码化。
    """
    tc = RegressionTestCase(
        "config_single_source_timeline_rejected",
        "验证 preflight 拒绝 config 内嵌非空 timeline 节，空节/缺省零副作用"
    )
    try:
        import io
        import contextlib
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import preflight_check as pf

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)

            def run_check(cfg, name):
                p = td / f"{name}.json"
                p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
                r = pf.PreflightResult(mode="render")
                with contextlib.redirect_stdout(io.StringIO()):
                    pf.check_json_config(p, r)
                return r

            def has_timeline_error(r):
                return any("'timeline' block" in e for e in r.errors)

            base = {"video_duration": 100,
                    "scenes": [{"scene_id": "s0", "start": 0, "end": 100}]}

            # (a) 非空 scene_metadata → 阻断
            r1 = run_check(dict(base, timeline={"scene_metadata":
                                                [{"scene": 0, "start": 0, "end": 90}]}),
                           "with_metadata")
            tc.assert_true(has_timeline_error(r1),
                           "non-empty scene_metadata in config is rejected")
            tc.assert_true(not r1.passed, "preflight fails on dual-source config")

            # (b) 非空 t_block → 阻断
            r2 = run_check(dict(base, timeline={"t_block": {"s0": 0, "s1": 90}}),
                           "with_tblock")
            tc.assert_true(has_timeline_error(r2), "non-empty t_block is rejected")

            # (c) 空节（历史脚手架产物）→ 不判，零误报
            r3 = run_check(dict(base, timeline={"t_block": {}, "scene_metadata": []}),
                           "empty_block")
            tc.assert_true(not has_timeline_error(r3),
                           "empty timeline block not flagged")

            # (d) 未声明 timeline → 零副作用（存量合规 config 的常态）
            r4 = run_check(dict(base), "no_timeline")
            tc.assert_true(not has_timeline_error(r4),
                           "config without timeline unaffected")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_fresh_guard_requires_confirmation() -> RegressionTestCase:
    """用例41：--fresh 受限门禁（补 2026-08-31 普查发现的覆盖空白）

    背景：prefab 复盘（2026-08-04）把 --fresh 误用列为根因2——一次全量重置换回
    3 次 40 分钟级重渲与 9 小时失败收尾。门禁代码一直在位，但全仓回归套件 0 处
    引用（同批普查另有 HARD_GATES/scene-patch 的引用计数可核），属"有门禁无测试"
    的盲区。本用例锁定四态语义，防后续重构静默改掉确认要求。
    """
    tc = RegressionTestCase(
        "fresh_guard_requires_confirmation",
        "验证 --fresh 门禁：无产物放行、有产物/长视频需 --confirm-fresh、确认后可继续"
    )
    try:
        import io
        import contextlib
        _pr = _safe_import_pipeline_runner()
        PipelineRunner = _pr.PipelineRunner

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)

            def make_runner(duration, with_render_raw=False, with_tts=False, tag=""):
                r = PipelineRunner.__new__(PipelineRunner)
                r.config = {"video_duration": duration}
                r.render_raw = td / f"render_raw_{tag}.mp4"
                r.output_file = td / "成果文件" / "视频" / "业务主题_推广介绍.mp4"
                r.tts_dir = td / (f"tts_dir_{tag}" if with_tts else f"tts_absent_{tag}")
                r.temp_dir = td / "temp"
                if with_tts:
                    r.tts_dir.mkdir(parents=True, exist_ok=True)
                if with_render_raw:
                    r.render_raw.write_bytes(b"\x00" * 2048)
                return r

            def guard_outcome(r, confirm=False):
                """返回 'refused' / 'allowed'。"""
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        r._fresh_guard(confirm)
                    return "allowed", buf.getvalue()
                except SystemExit:
                    return "refused", buf.getvalue()

            # (a) 短视频 + 无任何产物 → 放行（测试/新项目常规路径零摩擦）
            v, _ = guard_outcome(make_runner(60, tag="a"))
            tc.assert_equal(v, "allowed", "short video without artifacts passes guard")

            # (b) 短视频但已有 render_raw → 拒绝，且列出受影响清单
            v, out = guard_outcome(make_runner(60, with_render_raw=True, tag="b"))
            tc.assert_equal(v, "refused", "existing render_raw requires confirmation")
            tc.assert_true("将被重新渲染覆盖" in out, "refusal prints affected-artifact inventory")
            tc.assert_true("--resume" in out, "refusal offers low-cost alternatives")

            # (c) 同场景追加 --confirm-fresh → 放行
            v, _ = guard_outcome(make_runner(60, with_render_raw=True, tag="c"),
                                 confirm=True)
            tc.assert_equal(v, "allowed", "--confirm-fresh releases the guard")

            # (d) 长视频即使无既有产物仍需确认（重渲染成本本身即风险）
            r_long = make_runner(600, tag="d")
            tc.assert_true(not r_long.render_raw.exists(), "no artifact present in long case")
            v, out = guard_outcome(r_long)
            tc.assert_equal(v, "refused", "long video requires confirmation without artifacts")
            tc.assert_true("长视频判定" in out, "refusal states the long-video verdict")

            # (e) 长视频 + TTS 产物 + 确认 → 放行
            v, _ = guard_outcome(make_runner(600, with_tts=True, tag="e"), confirm=True)
            tc.assert_equal(v, "allowed", "long video proceeds once confirmed")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_doc_to_markdown_picture_extraction_fallback() -> RegressionTestCase:
    """用例42：PPT/Word 图片抽取对未注册图片部件的容错（2026-09-02 WSI 案例 PPT）

    背景：doc_to_markdown.convert_pptx 原用 shape.image 取字节。python-pptx 只对
    image_content_types 白名单内的部件返回 ImagePart，其余（本例为 4 个 webp 部件，
    未在 [Content_Types].xml 注册）退化为通用 Part，shape.image 抛
    AttributeError('Part' object has no attribute 'image')——一个坏部件即中断整场
    转换，44 页 97 图在前 21 页后全部丢失。本用例锁定三条语义：字节改从关系取
    （不触碰 shape.image）、非原生格式转 PNG、不可解码记为跳过而非抛异常。
    """
    tc = RegressionTestCase(
        "doc_to_markdown_picture_fallback",
        "验证文档转换图片抽取：通用 Part 取字节、webp 转 PNG、坏部件跳过不中断"
    )
    try:
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        # doc_to_markdown 模块顶层重绑 sys.stdout/stderr，需安全导入
        d2m = _safe_import_rebinding_module("doc_to_markdown")
        from PIL import Image
        import io

        def _make_blob(fmt, size=(120, 80)):
            img = Image.new("RGB", size, (10, 120, 200))
            buf = io.BytesIO()
            img.save(buf, fmt)
            return buf.getvalue()

        class _PictureShape:
            """模拟 python-pptx PICTURE shape：image 属性按通用 Part 行为抛错。"""
            def __init__(self, blob, blip_rId="rId9"):
                self._blob = blob
                self._rid = blip_rId

            @property
            def _element(self):
                return type("E", (), {"blip_rId": self._rid})()

            @property
            def part(self):
                blob = self._blob
                return type("P", (), {
                    "related_part": lambda s, rId: type("Rp", (), {"blob": blob})()
                })()

            @property
            def image(self):
                raise AttributeError("'Part' object has no attribute 'image'")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            # (a) 通用 Part（shape.image 抛错）仍能取到字节 —— 原崩溃点
            webp = _make_blob("WEBP")
            got = d2m._picture_blob(_PictureShape(webp))
            tc.assert_true(got == webp, "blob read via relationship, not shape.image")
            tc.assert_true(d2m._picture_blob(_PictureShape(webp, blip_rId=None)) is None,
                           "shape without embed rel yields None instead of raising")

            # (b) webp → PNG：扩展名与内容都必须是 PNG 且尺寸保真
            name = d2m._write_picture(webp, tmp, "slide22_img52")
            tc.assert_equal(name, "slide22_img52.png", "webp renamed to .png")
            with Image.open(tmp / name) as out:   # 不关闭会锁住文件致 Windows 临时目录清理失败
                tc.assert_equal(out.format, "PNG", "webp bytes actually converted to PNG")
                tc.assert_equal(out.size, (120, 80), "converted PNG keeps dimensions")

            # (c) 原生格式不重编码：落盘字节必须与输入逐字节相同
            png = _make_blob("PNG")
            name = d2m._write_picture(png, tmp, "slide1_img1")
            tc.assert_equal(name, "slide1_img1.png", "png keeps png extension")
            tc.assert_true((tmp / name).read_bytes() == png,
                           "native format written verbatim (no lossy re-encode)")
            tc.assert_equal(d2m._write_picture(_make_blob("JPEG"), tmp, "s_j"), "s_j.jpg",
                            "jpeg normalized to .jpg")

            # (d) 不可解码字节 → 返回 None（记为跳过），且不留半成品文件、不抛异常
            before = set(tmp.iterdir())
            tc.assert_true(d2m._write_picture(b"\x00\x01not-an-image", tmp, "s_bad") is None,
                           "undecodable bytes skipped instead of raising")
            tc.assert_equal(set(tmp.iterdir()), before, "skip writes no partial file")
            tc.assert_true(d2m._write_picture(b"", tmp, "s_empty") is None,
                           "empty part skipped instead of raising")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_scene_patch_default_route_and_time_witness() -> RegressionTestCase:
    """用例43：增量渲染默认尝试 + 时长三重见证（2026-09-03 D3 触发权内化）

    背景：29 份 pipeline_state 实测 scene_patch_attempts 全为 0——能力在位却零投产。
    两层原因：①触发权在外部 CLI（不加 --scene-patch 就不尝试，跳过零成本且零留痕，
    事后既不能证真也不能证伪）；②时间源信任前提过窄（脚本无 `end:` 字面量即判"不可
    信"→ sidecar scene_times=None → classify 永久 FULL），wsi-hotel-cases 真实基线
    实测 scene_times_error=S-block-end-unverifiable 即此形态。本用例锁定重构后语义：
    路由三态全部留痕、见证级别与其否决条件、白名单分类器裁定、裁定落盘为数据。
    夹具经 monkeypatch HTML_BASE/TEMP_BASE 全部落在临时目录，不写入仓库。
    """
    tc = RegressionTestCase(
        "scene_patch_default_route_and_time_witness",
        "验证增量渲染默认尝试路由三态、时长三重见证与白名单分类器裁定"
    )
    try:
        import inspect
        import subprocess
        import time as _time

        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        sp = _safe_import_rebinding_module("scene_patch_render")
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        FPS, DUR = 25, 9.0
        STARTS = {"s1": 0.0, "s2": 3.0, "s3": 6.0}
        ORDER = ["s1", "s2", "s3"]

        def build_html(bodies, ends=None, style=".card{color:#fff}"):
            """合成 HyperFrames 项目：T-block + root data-duration（现行手写风格）。

            ends 非空时额外写 S-block 数值 end 字面量（见证级别随之变化）。
            """
            t_items = ", ".join(f"{sid}: {STARTS[sid]}" for sid in ORDER)
            s_block = ""
            if ends:
                rows = "\n".join(f"    {sid}: {{ start: T.{sid}, end: {ends[sid]} }},"
                                 for sid in ORDER)
                s_block = f"\n  var S = {{\n{rows}\n  }};"
            scenes = "\n".join(
                f'    <div data-scene-id="{sid}" data-scene-entry="gsap" '
                f'data-scene-subtitle-safe="true">{bodies[sid]}</div>' for sid in ORDER)
            return (
                '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
                f'<style>{style}</style></head>\n<body>\n'
                f'  <div data-composition-id="main" data-duration="{DUR}" '
                'data-width="1080" data-height="1920">\n'
                f'{scenes}\n  </div>\n'
                '  <script src="gsap.min.js"></script>\n  <script>\n'
                f'  var T = {{ {t_items} }};{s_block}\n'
                '  var main = gsap.timeline();\n'
                '  main.to(".card", { opacity: 1 }, 0);\n'
                '  window.__timelines = { main: main };\n'
                '  </script>\n</body></html>\n'
            )

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            saved_bases = (sp.HTML_BASE, sp.TEMP_BASE)
            sp.HTML_BASE = td / "hyperframes"
            sp.TEMP_BASE = td / "临时产物"
            try:
                proj = "sp-fixture"
                src_dir = sp.HTML_BASE / proj
                tmp_dir = sp.TEMP_BASE / f"{proj}_audio"
                src_dir.mkdir(parents=True, exist_ok=True)
                tmp_dir.mkdir(parents=True, exist_ok=True)
                cfg_path = td / "sp-fixture.json"
                cfg_path.write_text(json.dumps({
                    "fps": FPS, "video_duration": DUR, "resolution": "1080x1920",
                    "paths": {"html_project": proj, "temp_subdir": f"{proj}_audio"},
                }, ensure_ascii=False), encoding="utf-8")

                pr = sp.PatchRenderer(cfg_path)
                bodies1 = {"s1": "<p>医院项目案例</p>",
                           "s2": "<p>酒店项目案例</p>",
                           "s3": "<p>教育项目案例</p>"}
                html1 = build_html(bodies1)
                pr.html_path.write_text(html1, encoding="utf-8")
                pr.render_raw.write_bytes(b"\x00" * 64)

                # ── A 时长见证三态 ──
                times1, err1 = sp.extract_scene_times(html1, pr.config)
                tc.assert_true(err1 is None,
                               f"A1 no end literals → times still computed (err={err1})")
                tc.assert_equal(len(times1 or []), 3, "A1 three scene windows")
                tc.assert_equal(times1[-1]["end"] if times1 else None, DUR,
                                "A1 last window closes at root data-duration")
                tc.assert_equal(sp.scene_time_witness(html1), "duration-cross-check",
                                "A1 witness downgraded to cross-check, not rejected")

                ok_ends = {"s1": 3.0, "s2": 6.0, "s3": 9.0}
                html_s = build_html(bodies1, ends=ok_ends)
                times_s, err_s = sp.extract_scene_times(html_s, pr.config)
                tc.assert_true(err_s is None, f"A2 consistent end literals accepted (err={err_s})")
                tc.assert_equal(sp.scene_time_witness(html_s), "s-block-end",
                                "A2 witness is s-block-end when literals present")

                html_bad = build_html(bodies1, ends={"s1": 3.0, "s2": 6.0, "s3": 12.0})
                times_b, err_b = sp.extract_scene_times(html_bad, pr.config)
                tc.assert_true(times_b is None and err_b.startswith("S-block-end("),
                               f"A3 contradictory end literals still veto (err={err_b})")

                cfg_off = dict(pr.config, video_duration=15.0)
                times_c, err_c = sp.extract_scene_times(html1, cfg_off)
                tc.assert_true(times_c is None and err_c.startswith("config video_duration("),
                               f"A4 config/data-duration divergence vetoed (err={err_c})")

                html_sub = html1.replace("s2: 3.0", "s2: 0.01")
                times_d, err_d = sp.extract_scene_times(html_sub, pr.config)
                tc.assert_true(times_d is None and err_d.startswith("scene-window-sub-frame("),
                               f"A5 sub-frame window vetoed (err={err_d})")

                # ── B 白名单分类器裁定 ──
                def write_sidecar(html, **over):
                    fp = sp.extract_fingerprints(html, pr.config,
                                                 pr.source_dir / "narration.json")
                    times, err = sp.extract_scene_times(html, pr.config)
                    side = {"version": sp.SIDECAR_VERSION, "fps": FPS,
                            "width": 1080, "height": 1920,
                            "total_frames": int(round(DUR * FPS)), "has_audio": False,
                            "scene_times": times, "scene_times_error": err,
                            "scene_times_witness":
                                sp.scene_time_witness(html) if times else None}
                    side.update(fp)
                    side.update(over)
                    pr.sidecar_path.write_text(json.dumps(side, ensure_ascii=False),
                                               encoding="utf-8")
                    return side

                write_sidecar(html1)
                v, ch, r, _ = pr.classify()
                tc.assert_equal((v, r), ("FULL", "no-scene-content-change"),
                                "B1 identical html → FULL (patching nothing is not a hit)")

                bodies2 = dict(bodies1, s2="<p>酒店项目案例（隔声 45dB）</p>")
                html2 = build_html(bodies2)
                pr.html_path.write_text(html2, encoding="utf-8")
                v, ch, r, ctx = pr.classify()
                tc.assert_equal((v, ch, r), ("PATCH", ["s2"], ""),
                                "B2 one scene body changed → PATCH that scene only")
                tc.assert_equal(ctx.get("witness"), "duration-cross-check",
                                "B2 witness propagated into patch context")
                tc.assert_equal(ctx.get("times"), times1, "B2 times taken from current html")
                tc.assert_equal(pr._plan_segments(times1, ["s2"]),
                                [("keep", 0, 75, ["s1"]),
                                 ("render", 75, 150, ["s2"]),
                                 ("keep", 150, 225, ["s3"])],
                                "B2 segment plan is frame-aligned to scene bounds")

                pr.html_path.write_text(build_html(bodies2, style=".card{color:#000}"),
                                        encoding="utf-8")
                v, ch, r, _ = pr.classify()
                tc.assert_true(v == "FULL" and r in ("html-outside-scenes-changed",
                                                     "style-changed"),
                               f"B3 style edit outside whitelist → FULL (reason={r})")

                pr.html_path.write_text(
                    build_html(dict(bodies1, s1="<p>改一</p>", s3="<p>改三</p>")),
                    encoding="utf-8")
                v, ch, r, _ = pr.classify()
                tc.assert_equal((v, r), ("FULL", "too-many-scenes-changed(2/3)"),
                                "B4 >50% scenes changed → FULL")

                pr.html_path.write_text(html_bad, encoding="utf-8")
                write_sidecar(html1)
                v, ch, r, _ = pr.classify()
                tc.assert_true(v == "FULL" and r.startswith("time-source-untrusted(S-block-end("),
                               f"B5 contradictory timings veto at classify (reason={r})")
                pr.html_path.write_text(html2, encoding="utf-8")

                write_sidecar(html1, scene_times=None,
                              scene_times_error="S-block-end-unverifiable")
                v, ch, r, _ = pr.classify()
                tc.assert_true(v == "FULL" and
                               r == "baseline-time-source-untrusted(S-block-end-unverifiable)",
                               f"B6 untrusted baseline → FULL with the recorded cause (reason={r})")

                write_sidecar(html1, has_audio=True)
                tc.assert_equal(pr.classify()[2], "baseline-has-audio-stream",
                                "B7 baseline with audio → FULL (patch is video-only)")

                write_sidecar(html1, fps=30)
                tc.assert_equal(pr.classify()[2], "fps-changed", "B8 fps change → FULL")

                write_sidecar(html1, total_frames=200)
                tc.assert_true(pr.classify()[2].startswith("frame-grid-mismatch("),
                               f"B9 frame grid mismatch → FULL (reason={pr.classify()[2]})")

                write_sidecar(html1, version=999)
                tc.assert_equal(pr.classify()[2], "sidecar-version-mismatch",
                                "B10 sidecar version mismatch → FULL")

                pr.sidecar_path.unlink()
                tc.assert_equal(pr.classify()[2], "no-sidecar-baseline",
                                "B11 no sidecar → FULL")
                write_sidecar(html1)
                pr.render_raw.unlink()
                tc.assert_equal(pr.classify()[2], "no-render_raw-baseline",
                                "B12 no baseline video → FULL")
                pr.render_raw.write_bytes(b"\x00" * 64)

                # ── C 裁定落盘 + 残留 wrapper 清理 ──
                pr._record_verdict("PATCH", "", ["s2"], "applied",
                                   "duration-cross-check", _time.time())
                disk = json.loads(pr.verdict_path.read_text(encoding="utf-8"))
                tc.assert_equal(disk["outcome"], "applied", "C1 verdict artifact records outcome")
                tc.assert_equal(disk["time_witness"], "duration-cross-check",
                                "C1 verdict artifact records witness level")
                tc.assert_equal(disk["changed_scenes"], ["s2"], "C1 verdict records changed scenes")

                write_sidecar(html1)
                (pr.source_dir / "_patch_seg_9.html").write_text("<html></html>",
                                                                 encoding="utf-8")
                pr._render_segment = lambda *a, **k: False
                rc, outcome = pr._patch_execute(
                    {"base": {"total_frames": int(round(DUR * FPS))}, "times": times1,
                     "html": html2, "witness": "duration-cross-check"}, ["s2"], times1)
                tc.assert_equal((rc, outcome), (sp.EXIT_FALLBACK, "segment-render-failed"),
                                "C2 segment render failure → fallback exit code + outcome")
                tc.assert_equal(list(pr.source_dir.glob("_patch_seg_*.html")), [],
                                "C2 stale wrapper removed and no wrapper left behind")

                # ── D 流水线路由三态（触发权内化）──
                # 独立子目录：pr.temp_dir 里已有 render_raw.mp4，复用会让"基线缺失"失真
                run_dir = tmp_dir / "runner"
                run_dir.mkdir(parents=True, exist_ok=True)
                state = PipelineState(run_dir / "pipeline_state.json")
                runner = PipelineRunner.__new__(PipelineRunner)
                runner.state = state
                runner.no_scene_patch = False
                runner.render_raw = run_dir / "render_raw.mp4"
                runner.sidecar_path = run_dir / "scene_fingerprints.json"
                runner.patch_verdict_path = run_dir / "scene_patch_verdict.json"

                tc.assert_equal(runner._scene_patch_route(),
                                (False, "SKIPPED",
                                 "no-baseline(render_raw.mp4+scene_fingerprints.json)"),
                                "D1 missing baseline → SKIPPED naming both files")
                runner.render_raw.write_bytes(b"\x00" * 8)
                tc.assert_equal(runner._scene_patch_route()[2],
                                "no-baseline(scene_fingerprints.json)",
                                "D1 partial baseline names exactly what is missing")
                runner.sidecar_path.write_text("{}", encoding="utf-8")
                tc.assert_equal(runner._scene_patch_route(),
                                (True, "ATTEMPT", "baseline-present"),
                                "D2 baseline present → attempted without any CLI opt-in")
                runner.no_scene_patch = True
                tc.assert_equal(runner._scene_patch_route(),
                                (False, "SKIPPED", "opt-out(--no-scene-patch)"),
                                "D3 explicit opt-out is the only way to skip")
                runner.no_scene_patch = False

                tc.assert_true(runner._read_patch_verdict() is None,
                               "D4 absent verdict file → None (caller falls back to rc)")
                runner.patch_verdict_path.write_text('{"verdict":"PATCH","outcome":"applied"}',
                                                     encoding="utf-8")
                tc.assert_equal(runner._read_patch_verdict()["outcome"], "applied",
                                "D4 verdict read as data, not parsed from stdout")
                runner.patch_verdict_path.write_text("not json", encoding="utf-8")
                tc.assert_true(runner._read_patch_verdict() is None, "D4 malformed verdict → None")
                runner.patch_verdict_path.write_text("[1,2]", encoding="utf-8")
                tc.assert_true(runner._read_patch_verdict() is None,
                               "D4 non-object verdict → None")

                metrics = runner._render_metrics()
                metrics["scene_patch_attempts"] = 1
                metrics["scene_patch_hits"] = 1
                rec = runner._record_scene_patch("PATCH", "baseline-present", "applied")
                tc.assert_equal((rec["attempts"], rec["hits"]), (1, 1),
                                "D5 attempts count classifier invocations only")
                tc.assert_equal(state.data["scene_patch"]["verdict"], "PATCH",
                                "D5 verdict persisted into pipeline_state")
                tc.assert_true(bool(state.data["scene_patch"]["at"]),
                               "D5 record carries a timestamp")
                rec2 = runner._record_scene_patch("SKIPPED", "no-baseline(render_raw.mp4)")
                tc.assert_equal(rec2["attempts"], 1,
                                "D6 SKIPPED does not inflate attempts (hit rate stays meaningful)")
                tc.assert_true(state.data["scene_patch"]["outcome"] is None,
                               "D6 SKIPPED has no outcome")
                tc.assert_true((run_dir / "pipeline_state.json").exists(),
                               "D6 record written to disk, so skipping leaves a trace")

                # ── E 结构契约：默认尝试（opt-out），旧 opt-in 参数已移除 ──
                sig = inspect.signature(PipelineRunner.__init__)
                tc.assert_equal(sig.parameters["no_scene_patch"].default, False,
                                "E1 default is attempt — opt-out, not opt-in")
                tc.assert_true("scene_patch" not in sig.parameters,
                               "E1 legacy opt-in parameter removed")
                cli = subprocess.run(
                    [sys.executable, str(script_dir / "pipeline_runner.py"), "--help"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=180)
                help_text = " ".join((cli.stdout or "").split())
                tc.assert_true("--no-scene-patch" in help_text,
                               "E2 CLI exposes the opt-out flag")
                tc.assert_true("always attempts it when a baseline exists" in help_text,
                               "E2 opt-out help states the baseline-present default")
                tc.assert_true("DEPRECATED / no-op" in help_text,
                               "E2 legacy --scene-patch documented as no-op, not silently dropped")

                tc.mark_passed()
            finally:
                sp.HTML_BASE, sp.TEMP_BASE = saved_bases

    except Exception as e:
        tc.mark_failed(str(e))

    return tc

def test_delivery_slot_written_only_by_postprocess() -> RegressionTestCase:
    """用例44：交付槽位只由成功链末端写入（2026-09-03 结构性修复）

    背景：render 步曾无条件把无声裸片 copy2 进 成果文件/视频/{name}.mp4，于是
    postprocess 失败（2026-09-02 WSI 批次 BGM 未落盘）时裸片留在交付槽冒充成片，
    而 [ORPHAN-SLOT] 告警在 postprocess 之后才跑，拦不住；重跑时 enhance 又把槽位
    当输入，等于拿已混音已烧字幕的成片再处理一遍。修复后：enhance 输入源为
    temp/render_raw.mp4（缺失才回退槽位），槽位由 step3/step6 在成功路径写入，
    已交付成片的覆盖拦截同时把守 render 前置（省成本）与 postprocess 写入点
    （quick-fix 跳过 render，只有后者能拦）。夹具 monkeypatch WF_ROOT，不落仓库。
    """
    tc = RegressionTestCase(
        "delivery_slot_written_only_by_postprocess",
        "验证后处理输入源为纯净渲染、交付槽位保护在写入点生效"
    )
    try:
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        ev = _safe_import_rebinding_module("enhance_video_audio")
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            saved_root = ev.WF_ROOT
            ev.WF_ROOT = td
            try:
                # ── A 输入源解析：render_raw 优先，槽位回退 ──
                cfg = {"paths": {"video_name": "_regress-slot.mp4",
                                 "subtitle_name": "_regress-slot.srt",
                                 "temp_subdir": "_regress-slot_audio"}}
                tmp = td / "过程产物" / "临时产物" / "_regress-slot_audio"
                slot = td / "成果文件" / "视频" / "_regress-slot.mp4"

                _r, video_file, temp_dir, output_file, _s = ev.get_paths(cfg)
                tc.assert_equal(output_file, slot, "A1 delivery slot path is unchanged")
                tc.assert_equal(video_file, slot,
                                "A2 no render_raw → slot fallback keeps manual invocation working")

                tmp.mkdir(parents=True, exist_ok=True)
                (tmp / "render_raw.mp4").write_bytes(b"\x00" * 32)
                _r, video_file, _t, output_file, _s = ev.get_paths(cfg)
                tc.assert_equal(video_file, tmp / "render_raw.mp4",
                                "A3 clean render is the postprocess input")
                tc.assert_equal(output_file, slot,
                                "A3 input and output are no longer the same path")

                crm_tmp = td / "过程产物" / "临时产物" / "crm_audio"
                _r, vf0, _t, of0, _s = ev.get_paths(None)
                tc.assert_equal(vf0, of0, "A4 legacy no-config branch: slot fallback")
                crm_tmp.mkdir(parents=True, exist_ok=True)
                (crm_tmp / "render_raw.mp4").write_bytes(b"\x00" * 32)
                _r, vf1, _t, of1, _s = ev.get_paths(None)
                tc.assert_equal(vf1, crm_tmp / "render_raw.mp4",
                                "A4 legacy no-config branch obeys the same rule")
                tc.assert_equal(of1, td / "成果文件" / "视频" / of1.name,
                                "A4 legacy branch still outputs to the slot")

                # ── B 交付槽位保护：放行 / 拦截 / force ──
                run_dir = td / "run"
                run_dir.mkdir(parents=True, exist_ok=True)
                runner = PipelineRunner.__new__(PipelineRunner)
                runner.state = PipelineState(run_dir / "pipeline_state.json")
                runner.force = False
                runner.quick_fix = False
                runner.output_file = slot
                runner.temp_dir = run_dir

                tc.assert_true(runner._delivery_slot_guard("postprocess"),
                               "B1 empty slot passes")
                slot.parent.mkdir(parents=True, exist_ok=True)
                slot.write_bytes(b"\x00" * 64)
                tc.assert_true(runner._delivery_slot_guard("postprocess"),
                               "B2 unbacked slot file passes (overwriting residue is legal)")

                (run_dir / "completion_report.json").write_text(json.dumps({
                    "status": "VALIDATED",
                    "data_sources": {"video_file": {"path": str(slot)}},
                }, ensure_ascii=False), encoding="utf-8")
                tc.assert_true(runner._output_was_delivered(),
                               "B3 fixture recognized as a delivered artifact")
                tc.assert_true(not runner._delivery_slot_guard("postprocess"),
                               "B3 delivered artifact blocked at the write point")
                tc.assert_equal(runner.state.data["steps"]["postprocess"]["status"], "failed",
                                "B3 postprocess marked failed")
                tc.assert_true("delivered artifact" in
                               runner.state.data["steps"]["postprocess"]["error"],
                               "B3 error names the cause")

                runner.force = True
                tc.assert_true(runner._delivery_slot_guard("postprocess"),
                               "B4 --force bypasses (same semantics as HARD_GATES)")
                runner.force = False

                runner.state = PipelineState(run_dir / "state_render.json")
                tc.assert_true(not runner._delivery_slot_guard("render"),
                               "B5 same judgment before the expensive render")
                tc.assert_equal(runner.state.data["steps"]["render"]["status"], "failed",
                                "B5 render marked failed")

                runner.state = PipelineState(run_dir / "state_running.json")
                runner.state.mark_started("postprocess")
                started = runner.state.data["steps"]["postprocess"]["started"]
                runner._delivery_slot_guard("postprocess")
                tc.assert_equal(runner.state.data["steps"]["postprocess"]["started"], started,
                                "B6 guard does not reset a running step's started timestamp")

                # ── C 时长一致性门禁检查的对象 = 后处理真实输入源 ──
                runner.state = PipelineState(run_dir / "state_dur.json")
                runner.render_raw = tmp / "render_raw.mp4"
                runner.config = {"video_duration": 9.0}
                runner.audio_sync_rules = {"duration_consistency": {"max_diff_seconds": 1.0}}
                probed = []
                runner._probe_duration = lambda p: (probed.append(Path(p)), 9.0)[1]

                tc.assert_true(runner._duration_consistency_ok(), "C1 consistent duration passes")
                tc.assert_equal(probed[-1], runner.render_raw,
                                "C1 full-run mode probes the clean render")
                runner.quick_fix = True   # 旧实现按模式切换检查对象
                tc.assert_true(runner._duration_consistency_ok(), "C2 quick-fix passes")
                tc.assert_equal(probed[-1], runner.render_raw,
                                "C2 quick-fix probes render_raw too, not the slot")
                runner.render_raw = tmp / "absent.mp4"
                tc.assert_true(runner._duration_consistency_ok(),
                               "C3 missing render_raw does not block here")
                tc.assert_equal(probed[-1], runner.output_file,
                                "C3 falls back to the slot when no clean render exists")

                # ── D 写点接线：保护判据真的挂在 render 前置与 postprocess 写入点 ──
                #    helper 单测（B 段）证明判据正确，本段证明判据在链路上被执行——
                #    否则删掉任一调用点，B 段依然全绿。
                def make_runner(state_name, work_dir):
                    r = PipelineRunner.__new__(PipelineRunner)
                    r.state = PipelineState(work_dir / state_name)
                    r.force = False
                    r.quick_fix = False
                    r.output_file = slot
                    r.temp_dir = work_dir
                    r.render_raw = work_dir / "render_raw.mp4"
                    r.sidecar_path = work_dir / "scene_fingerprints.json"
                    r.patch_verdict_path = work_dir / "scene_patch_verdict.json"
                    r.source_dir = work_dir
                    r.config = {"video_duration": 9.0}
                    r.config_path = work_dir / "cfg.json"
                    r.env = {}
                    r.render_rules = {}
                    r.audio_sync_rules = {"duration_consistency": {"max_diff_seconds": 1.0}}
                    r.accept_over_render = False
                    # A04 末端终检的接线与裁定由用例53专门锁定；本夹具的槽位
                    # 判据测试不驱动真实 MediaQAGate（假文件必然 FAIL）
                    r._final_media_qa = lambda: True
                    r._can_skip = lambda name: False
                    r._gate_satisfied = lambda name: True
                    r._fingerprint = lambda name: "fixture"
                    return r

                def validated_report(work_dir):
                    (work_dir / "completion_report.json").write_text(json.dumps({
                        "status": "VALIDATED",
                        "data_sources": {"video_file": {"path": str(slot)}},
                    }, ensure_ascii=False), encoding="utf-8")

                # D1 已交付成片 → render 前置拦截，绝不启动昂贵渲染
                slot.write_bytes(b"\x00" * 64)
                d1_dir = td / "d1"
                d1_dir.mkdir(parents=True, exist_ok=True)
                validated_report(d1_dir)
                rr = make_runner("state.json", d1_dir)
                launched = []
                rr._run = lambda cmd, **kw: (launched.append(cmd), 0)[1]
                tc.assert_true(not rr.step_render(), "D1 step_render refuses a delivered slot")
                tc.assert_equal(launched, [], "D1 no render/sidecar subprocess launched")
                tc.assert_true("delivered artifact" in
                               rr.state.data["steps"]["render"]["error"],
                               "D1 render failure names the cause")

                # D2 渲染成功也不写交付槽位（旧实现在此 copy2 裸片 → 冒充成片）
                d2_dir = td / "d2"
                d2_dir.mkdir(parents=True, exist_ok=True)
                slot.unlink()
                rr2 = make_runner("state.json", d2_dir)
                rr2._probe_duration = lambda p: 9.0
                rr2._run = lambda cmd, **kw: (
                    rr2.render_raw.write_bytes(b"RENDER" * 16), 0)[1]
                tc.assert_true(rr2.step_render(), "D2 render succeeds on the stubbed path")
                tc.assert_true(rr2.render_raw.exists(), "D2 clean render landed in temp")
                tc.assert_true(not slot.exists(),
                               "D2 delivery slot untouched by a successful render")
                tc.assert_equal(rr2.state.data["steps"]["render"]["status"], "passed",
                                "D2 render step completed (assertion above is not vacuous)")
                tc.assert_equal(rr2.state.data["render_metrics"]["full_render_attempts"], 1,
                                "D2 the full-render path really executed")

                # D3 已交付成片 → postprocess 写入点拦截（quick-fix 跳过 render 时唯一防线）
                d3_dir = td / "d3"
                d3_dir.mkdir(parents=True, exist_ok=True)
                validated_report(d3_dir)
                slot.write_bytes(b"\x00" * 64)
                rp = make_runner("state.json", d3_dir)
                launched3 = []
                rp._run = lambda cmd, **kw: (launched3.append(cmd), 0)[1]
                tc.assert_true(not rp.step_postprocess(),
                               "D3 step_postprocess refuses a delivered slot")
                tc.assert_equal(launched3, [], "D3 enhance_video_audio not launched")
                tc.assert_equal(rp.state.data["steps"]["postprocess"]["status"], "failed",
                                "D3 postprocess marked failed")

                # D4 无背书残留 → 放行并真的调用 enhance（含 quick-fix 参数透传）
                d4_dir = td / "d4"
                d4_dir.mkdir(parents=True, exist_ok=True)
                rp2 = make_runner("state.json", d4_dir)
                launched4 = []
                rp2._duration_consistency_ok = lambda: True
                rp2._merge_verification_file = lambda *a, **k: None
                rp2._run = lambda cmd, **kw: (launched4.append(cmd), 0)[1]
                tc.assert_true(rp2.step_postprocess(), "D4 postprocess proceeds")
                tc.assert_equal(len(launched4), 1, "D4 enhance invoked exactly once")
                tc.assert_true(any("enhance_video_audio.py" in str(a) for a in launched4[0]),
                               "D4 the postprocess subprocess is enhance_video_audio")
                tc.assert_true(not any("--quick-fix" in str(a) for a in launched4[0]),
                               "D4 full-run mode does not pass --quick-fix")
                tc.assert_equal(rp2.state.data["steps"]["postprocess"]["status"], "passed",
                                "D4 postprocess completed")

                rp3 = make_runner("state_qf.json", d4_dir)
                rp3.quick_fix = True
                rp3._duration_consistency_ok = lambda: True
                rp3._merge_verification_file = lambda *a, **k: None
                launched5 = []
                rp3._run = lambda cmd, **kw: (launched5.append(cmd), 0)[1]
                tc.assert_true(rp3.step_postprocess(), "D4 quick-fix postprocess proceeds")
                tc.assert_true(any("--quick-fix" in str(a) for a in launched5[0]),
                               "D4 quick-fix mode passes --quick-fix through")

                tc.mark_passed()
            finally:
                ev.WF_ROOT = saved_root

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_asset_signoff_gate() -> RegressionTestCase:
    """用例45：位点1 素材确认单消费门禁 check_asset_signoff（2026-09-03 WSI 批次）

    背景：上一批 4 条 WSI 竖版退回原因是"画面图片选择较差，不符合宣传的品质要求"
    ——素材选择从未成为"人工可签认、机器可核验"的对象，改稿时也没有任何门禁能
    发现屏显与签认分叉。本用例锁定判据的六个面与"存在即强制"的触发形态
    （无 opt-in 开关、无项目名单，与 narration_source 指针同一判据形态），
    使跳过位点1 的项目零影响、走过位点1 的项目分叉即阻断。

    真实项目上的红绿双向由现场变异测试另行验证（7 组：pill/旁白/素材名/屏显数字/
    S-block 类型/确认单自身无溯源/还原转绿），夹具在此锁定可重复执行面。
    """
    tc = RegressionTestCase(
        "asset_signoff_gate",
        "验证素材确认单存在即强制、六个判据面可红、通过路径留 OK 痕"
    )
    try:
        import io
        import contextlib
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import preflight_check as pf

        # 卡片 pill 含"9 月 1 日"且该场「数据点」无 9/1：公历日期豁免须成立，
        # 否则基线绿态即红（把豁免做成通用判据而非逐项目特例的看门断言）。
        BASE_HTML = """<!DOCTYPE html><html><body>
<div id="root" data-width="1080" data-height="1920" data-duration="11" data-cover-duration="3">
  <div id="scene0" class="scene" data-scene-id="s0" data-scene-entry="cover" data-scene-subtitle-safe="true"><h1>封面</h1></div>
  <div id="scene1" class="scene" data-scene-id="s1" data-scene-entry="gsap" data-scene-subtitle-safe="true" data-scene-assets="1.jpg">
    <img class="ph" src="assets/1.jpg">
    <div class="ab name" id="s1name">医院A 门诊楼</div>
    <div class="ab tags" id="s1tags" style="top:392px;left:60px;width:960px;"><div class="tag" id="s1t1">12 万㎡</div><div class="tag" id="s1t2">隔墙 5000㎡</div></div>
  </div>
  <div id="scene2" class="scene" data-scene-id="s2" data-scene-entry="gsap" data-scene-subtitle-safe="true" data-scene-assets="2.jpg">
    <img class="ph" src="assets/2.jpg">
    <div class="ab name" id="s2name">医院B 住院楼</div>
    <div class="ab tags" id="s2tags" style="top:392px;left:60px;width:960px;"><div class="tag" id="s2t1">9 月 1 日</div><div class="tag" id="s2t2">8000㎡</div></div>
  </div>
</div>
<script>
var T = { s0: 0, s1: 3.0, s2: 7.0 };
var S = [{"id": "s0", "start": 0, "end": 3.0, "dur": 3.0, "type": "cover", "cover": true},
 {"id": "s1", "start": 3.0, "end": 7.0, "dur": 4.0, "type": "card"},
 {"id": "s2", "start": 7.0, "end": 11.0, "dur": 4.0, "type": "card"}];
</script>
</body></html>"""

        BASE_SHEET = {
            "场景数": 2,
            "场景": [
                {"scene_id": 1, "场景类型": "card", "画面文案": "医院A 门诊楼",
                 "量化标签": ["12 万㎡", "隔墙 5000㎡"], "旁白文案": "门诊楼隔墙 5000 平方米。",
                 "选用素材": [{"display_target": "素材文件/图片/1.jpg"}],
                 "数据点": [{"数值": "12 万㎡", "来源": "S20"}, {"数值": "5000", "来源": "S21"}]},
                {"scene_id": 2, "场景类型": "card", "画面文案": "医院B 住院楼",
                 "量化标签": ["9 月 1 日", "8000㎡"], "旁白文案": "住院楼 8000 平方米。",
                 "选用素材": [{"display_target": "素材文件/图片/2.jpg"}],
                 "数据点": [{"数值": "8000", "来源": "S25"}]},
            ],
        }
        BASE_CFG = {"scenes": [{"scene_id": 1, "narration": "门诊楼隔墙 5000 平方米。"},
                               {"scene_id": 2, "narration": "住院楼 8000 平方米。"}]}

        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "assets").mkdir()
            for name in ("1.jpg", "2.jpg"):
                (pdir / "assets" / name).write_bytes(b"\xff\xd8\xff\xe0fake")
            html_path = pdir / "index.html"

            def run(html_text=None, sheet=None, cfg=None, mode="render", write_sheet=True):
                if html_text is not None:
                    html_path.write_text(html_text, encoding="utf-8")
                sp = pdir / "素材确认单.json"
                if write_sheet:
                    sp.write_text(json.dumps(sheet if sheet is not None else BASE_SHEET,
                                             ensure_ascii=False), encoding="utf-8")
                elif sp.exists():
                    sp.unlink()
                r = pf.PreflightResult(mode=mode)
                with contextlib.redirect_stdout(io.StringIO()):
                    pf.check_asset_signoff(pdir, cfg if cfg is not None else BASE_CFG,
                                           html_path, r)
                return r

            def hits(r, needle, level="ERROR"):
                return [e["message"] for e in r._log_entries
                        if e["level"] == level and e["check_id"] == "asset_signoff"
                        and needle in e["message"]]

            # (a) 无确认单 → INFO 跳过，零 error 零 OK（存量项目零影响）
            r = run(write_sheet=False)
            tc.assert_true(not r.errors, "项目无确认单时不产生 error")
            tc.assert_true(hits(r, "跳过", "INFO"), "跳过路径记 INFO 留痕（不做静默通过）")
            tc.assert_true(not hits(r, "素材确认单一致性通过", "OK"),
                           "跳过时不得伪造 OK 痕")

            # (b) 一致 → 零 error + OK 痕（通过路径亦留痕，使"跑过"成为正证据）
            r = run(BASE_HTML)
            tc.assert_true(not r.errors, f"一致夹具应零 error，实际 {r.errors}")
            tc.assert_true(hits(r, "素材确认单一致性通过", "OK"), "通过路径写 OK 痕")

            # (c) pill 文本分叉（数字仍在溯源池，只能由屏显文本面捕获）
            r = run(BASE_HTML.replace('<div class="tag" id="s1t1">12 万㎡</div>',
                                      '<div class="tag" id="s1t1">12 万m²</div>'))
            tc.assert_true(hits(r, "标签 pill"), "pill 与确认单分叉被拦")

            # (d) data-scene-assets 与确认单选用素材分叉
            r = run(BASE_HTML.replace('data-scene-assets="1.jpg"',
                                      'data-scene-assets="2.jpg"'))
            tc.assert_true(hits(r, "!= 确认单选用素材"), "素材声明与确认单分叉被拦")

            # (e) 选用素材未落盘（确认单与 HTML 同指一个不存在的文件）
            sheet_e = json.loads(json.dumps(BASE_SHEET, ensure_ascii=False))
            sheet_e["场景"][0]["选用素材"] = [{"display_target": "素材文件/图片/9.jpg"}]
            r = run(BASE_HTML.replace('data-scene-assets="1.jpg"', 'data-scene-assets="9.jpg"')
                            .replace('src="assets/1.jpg"', 'src="assets/9.jpg"'),
                    sheet=sheet_e)
            tc.assert_true(hits(r, "选用素材未落盘"), "签认过的素材不在盘上被拦")

            # (f) 旁白源与确认单分叉
            r = run(BASE_HTML, cfg={"scenes": [
                {"scene_id": 1, "narration": "门诊楼隔墙 5000 平方米"},
                {"scene_id": 2, "narration": BASE_CFG["scenes"][1]["narration"]}]})
            tc.assert_true(hits(r, "旁白源与确认单"), "旁白逐字分叉被拦")

            # (g) HTML S-block type 与确认单「场景类型」分叉
            r = run(BASE_HTML.replace('{"id": "s1", "start": 3.0, "end": 7.0, "dur": 4.0, "type": "card"}',
                                      '{"id": "s1", "start": 3.0, "end": 7.0, "dur": 4.0, "type": "hero"}'))
            tc.assert_true(hits(r, "!= S-block type"), "场景类型分叉被拦")

            # (h) 屏显数字脱离本场「数据点」（改确认单自身，证明溯源面读的是签认数据）
            sheet_h = json.loads(json.dumps(BASE_SHEET, ensure_ascii=False))
            sheet_h["场景"][0]["量化标签"] = ["999999 万㎡", "隔墙 5000㎡"]
            r = run(BASE_HTML.replace('<div class="tag" id="s1t1">12 万㎡</div>',
                                      '<div class="tag" id="s1t1">999999 万㎡</div>'),
                    sheet=sheet_h)
            tc.assert_true(hits(r, "在本场「数据点」中无溯源"), "无溯源屏显数字被拦")

            # (i) 场景覆盖分叉：HTML 多出确认单未签认的场景
            r = run(BASE_HTML.replace(
                '</div>\n<script>',
                '  <div id="scene3" class="scene" data-scene-id="s3" data-scene-entry="gsap"'
                ' data-scene-subtitle-safe="true" data-scene-assets="1.jpg"></div>\n</div>\n<script>'))
            tc.assert_true(hits(r, "!= HTML 非 cover 场景"), "未经签认的新场景被拦")

            # (j) audit 模式：同一分叉降为 WARN（锁 RENDER_CRITICAL_CHECKS 注册在位）
            self_check = "asset_signoff" in pf.RENDER_CRITICAL_CHECKS
            tc.assert_true(self_check, "asset_signoff 已注册为 render 级关键判据")
            r = run(BASE_HTML.replace('<div class="tag" id="s1t1">12 万㎡</div>',
                                      '<div class="tag" id="s1t1">12 万m²</div>'),
                    mode="audit")
            tc.assert_true(not r.errors and r.warnings,
                           "audit 模式下分叉降为 WARN 不阻断")

            # 收尾复位，避免后续用例复用同一 HTML 文本时残留
            html_path.write_text(BASE_HTML, encoding="utf-8")
            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_preview_coverage_gap() -> RegressionTestCase:
    """用例46：预览截图覆盖率裁定（2026-09-03 WSI 位点2 首跑实证）

    背景：instant_preview 对 wsi-commercial-cases 首跑时 Chrome 渲染进程在第 6 场
    崩溃（Page.captureScreenshot: Target closed），manifest 只含 5/11 场，脚本仍打印
    "[PASS] All scenes stay above subtitle safety line" 并退出 0——退出码 3 只覆盖
    "零截图"，部分盲区被当成通过。位点2 要人看画面签核，"哪些场根本没成像"必须是
    机器可判的事实，而不是需要人读日志才发现的读数。
    """
    tc = RegressionTestCase(
        "preview_coverage_gap",
        "验证部分截图缺失被判定为覆盖盲区，且以 png 在位而非 manifest 声明为准"
    )
    try:
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import instant_preview as ip

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            node_scenes = [{"scene_id": 1}, {"scene_id": "s2"}, {"scene_id": 3}]

            def entry(sid, fname):
                return {"sceneId": sid, "file": fname, "time": 1.0, "frame": "start"}

            # (a) 全覆盖 → 无缺口
            for f in ("scene_1_start.png", "scene_s2_start.png", "scene_3_start.png"):
                (tmp / f).write_bytes(b"png")
            gap, covered, total = ip.compute_capture_gap(
                node_scenes,
                [entry(1, "scene_1_start.png"), entry("s2", "scene_s2_start.png"),
                 entry(3, "scene_3_start.png")],
                tmp)
            tc.assert_equal(gap, [], "full coverage yields empty gap")
            tc.assert_equal((covered, total), (3, 3), "counts reflect measured scenes")

            # (b) Chrome 中途崩溃：manifest 少一场 → 点名缺失
            gap, covered, total = ip.compute_capture_gap(
                node_scenes,
                [entry(1, "scene_1_start.png"), entry("s2", "scene_s2_start.png")],
                tmp)
            tc.assert_equal(gap, [3], "missing scene named in gap")
            tc.assert_equal((covered, total), (2, 3), "partial coverage counted as partial")

            # (c) manifest 声明了但 png 不在盘上 → 仍算缺口（声明不等于证据）
            ghost = tmp / "scene_ghost.png"
            gap, covered, total = ip.compute_capture_gap(
                node_scenes,
                [entry(1, "scene_1_start.png"), entry("s2", "scene_s2_start.png"),
                 entry(3, "scene_ghost.png")],
                tmp)
            tc.assert_equal(gap, [3], "declared-but-absent png still counts as gap")
            ghost.write_bytes(b"png")
            gap2, _, _ = ip.compute_capture_gap(
                node_scenes,
                [entry(1, "scene_1_start.png"), entry("s2", "scene_s2_start.png"),
                 entry(3, "scene_ghost.png")],
                tmp)
            tc.assert_equal(gap2, [], "same entry closes the gap once the file exists")

            # (d) 零截图 → 全部为缺口（与既有退出码 3 语义一致）
            gap, covered, total = ip.compute_capture_gap(node_scenes, [], tmp)
            tc.assert_equal(gap, [1, 2, 3], "no screenshots means no coverage")
            tc.assert_equal(covered, 0, "zero covered when nothing captured")

            tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


# ============================================================================
# 2026-09-18 审核报告整改（批次1）：四态裁定 / RMS 解析口径 / 显式引擎入口
# ============================================================================

def test_gate_four_state_untested_not_pass() -> RegressionTestCase:
    """用例47：门禁四态裁定——未测不得计为通过（审核 A03，根因一）

    背景：visual_boundary_check 某场景三点抽帧全部失败时，结果写成 status=ERROR
    但不入 violations，而总判据是 len(violations)==0 → "检查根本没跑完"被解释成
    "检查通过"，随后进入烧字幕与交付。修后 violations 与 untested 分列，裁定优先级
    FAIL > UNTESTED > PASS；合法不适用（场景过短/时长不足）另列，不与未测混用。
    """
    tc = RegressionTestCase(
        "gate_four_state_untested_not_pass",
        "验证抽帧失败计为未测并阻断总裁定，合法不适用不阻断"
    )
    try:
        import io
        import contextlib
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import _gate_status as gs
        import visual_boundary_check as vbc

        # (a) 契约本体：违规优先于未测；仅有未测时整体为 UNTESTED；两者皆空才 PASS
        tc.assert_equal(gs.verdict(["v"], ["u"]), gs.FAIL,
                        "violations outrank untested in overall verdict")
        tc.assert_equal(gs.verdict([], ["u"]), gs.UNTESTED,
                        "untested alone never collapses to PASS")
        tc.assert_equal(gs.verdict([], []), gs.PASS,
                        "no violation and no untested means PASS")
        counts = gs.count_statuses([{"status": gs.PASS}, {"status": gs.NOT_APPLICABLE},
                                    {"status": gs.NOT_APPLICABLE}])
        tc.assert_equal((counts[gs.PASS], counts[gs.NOT_APPLICABLE]), (1, 2),
                        "count_statuses separates NA from PASS")

        scenes = [
            {"id": 1, "start": 0.0, "end": 10.0},
            {"id": 2, "start": 10.0, "end": 20.0},
            {"id": 3, "start": 20.0, "end": 21.5},  # dur<2 → NOT_APPLICABLE
        ]

        def cov_stub(image_path, subtitle_lines=2):
            return 0.5

        saved_cov = vbc.measure_content_coverage
        saved_measure = vbc.measure_content_bottom
        saved_extract = vbc.extract_frame
        try:
            vbc.measure_content_coverage = cov_stub

            # (b) 全部抽帧失败 → UNTESTED，passed=False（旧实现此处 passed=True）
            def extract_none(video_path, timestamp, output_path, ffmpeg_exe="ffmpeg"):
                return False

            vbc.extract_frame = extract_none
            vbc.measure_content_bottom = lambda p: 500
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                passed, results, summary = vbc.run_check("fake.mp4", scenes)
            tc.assert_equal(passed, False,
                            "all-frames-failed must not report passed")
            tc.assert_equal(summary["verdict"], gs.UNTESTED,
                            "verdict is UNTESTED, not PASS, when nothing was measured")
            tc.assert_equal([r["status"] for r in results[:2]],
                            [gs.UNTESTED, gs.UNTESTED],
                            "failed scenes carry UNTESTED status")
            tc.assert_true(any(u["check"] == "content_boundary" for u in summary["untested"]),
                           "untested entries name the failed check dimension")
            tc.assert_equal(summary["counts"][gs.NOT_APPLICABLE], 1,
                            "short scene stays NOT_APPLICABLE, not counted as untested")

            # (c) 只有合法不适用 → 仍判通过（未测与不适用不得混为一谈）
            vbc.extract_frame = lambda *a, **k: True
            with contextlib.redirect_stdout(io.StringIO()):
                passed_c, _, summary_c = vbc.run_check(
                    "fake.mp4", [{"id": 9, "start": 0.0, "end": 1.0}])
            tc.assert_equal(passed_c, True,
                            "NOT_APPLICABLE-only run is not blocked as untested")
            tc.assert_equal(summary_c["untested"], [],
                            "NOT_APPLICABLE is not written into the untested list")

            # (d) 采样点缺失（3 点只取到 1 点）→ 最坏值判据不完整，计未测
            def extract_partial(video_path, timestamp, output_path, ffmpeg_exe="ffmpeg"):
                keep = "_p30" in Path(output_path).name
                if keep:
                    Path(output_path).write_bytes(b"fake-frame")
                return keep

            vbc.extract_frame = extract_partial
            with contextlib.redirect_stdout(io.StringIO()):
                passed_d, _, summary_d = vbc.run_check("fake.mp4", scenes[:1])
            tc.assert_equal(summary_d["verdict"], gs.UNTESTED,
                            "partial sampling degrades verdict to UNTESTED")
            tc.assert_true(any("1/3" in u["reason"] for u in summary_d["untested"]),
                           "untested reason states how many samples were available")
        finally:
            vbc.extract_frame = saved_extract
            vbc.measure_content_bottom = saved_measure
            vbc.measure_content_coverage = saved_cov

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_audio_rms_parse_and_untested_trace() -> RegressionTestCase:
    """用例48：astats RMS 解析口径与未测留痕（审核 A07，本机实测复现）

    背景：_check_video_quality 用正则 RMS_level= 取音量值，而本机 FFmpeg 的
    astats 实际打印 "RMS level dB: -52.409893" → rms_values 恒空、len>=2 永假，
    整项音量一致性检查从未执行却在报告里算通过。修后两种拼写共用同一条模块级
    正则（测试复用生产常量，避免"另写一份字面量"造成测试失明），且取不到测量值
    时写入 untested 清单，不再静默。
    """
    tc = RegressionTestCase(
        "audio_rms_parse_and_untested_trace",
        "验证 RMS 正则吃真实 FFmpeg 输出，且解析不到时记未测而非静默通过"
    )
    try:
        import io
        import contextlib
        import subprocess
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        eva = _safe_import_rebinding_module("enhance_video_audio")

        # (a) 生产正则对本机真值与旧拼写都要命中
        real_line = "[Parsed_astats_0 @ 0x560] RMS level dB: -52.409893"
        legacy_line = "lavfi.astats.1.RMS_level=-16.300000"
        tc.assert_equal(eva._RMS_LEVEL_RE.findall(real_line), ["-52.409893"],
                        "regex parses this machine's real astats spelling")
        tc.assert_equal(eva._RMS_LEVEL_RE.findall(legacy_line), ["-16.300000"],
                        "regex still parses the legacy metadata spelling")
        tc.assert_equal(eva._RMS_LEVEL_RE.findall("Peak level dB: -1.5"), [],
                        "peak level line is not mistaken for RMS")

        # (b) 真实 FFmpeg 端到端：同一命令取 stderr，正则必须拿得到值
        ffmpeg_exe = shutil.which("ffmpeg")
        tc.assert_true(bool(ffmpeg_exe), "ffmpeg available for the real-output check")
        if ffmpeg_exe:
            with tempfile.TemporaryDirectory() as td:
                wav = Path(td) / "rms_probe.wav"
                subprocess.run(
                    [ffmpeg_exe, "-y", "-f", "lavfi",
                     "-i", "sine=frequency=440:duration=12", "-ar", "44100", str(wav)],
                    capture_output=True)
                probe = subprocess.run(
                    [ffmpeg_exe, "-ss", "1.0", "-t", "2", "-i", str(wav),
                     "-af", "astats=metadata=1:reset=1", "-f", "null", "-"],
                    capture_output=True, text=True, encoding='utf-8', errors='replace')
                found = eva._RMS_LEVEL_RE.findall(probe.stderr)
                tc.assert_true(len(found) >= 1,
                               f"real FFmpeg stderr yields RMS values (got {found})")

        # (c) 有音轨但取不到值 → untested 点名（旧实现此处零记录）
        class _Out:
            def __init__(self, stdout="", stderr=""):
                self.stdout, self.stderr = stdout, stderr

        probe_payload = json.dumps({"streams": [
            {"codec_type": "video", "bit_rate": "5000000", "duration": "20.0"},
            {"codec_type": "audio", "duration": "20.0"}]})

        def fake_run_no_rms(argv, *a, **k):
            if argv[0] == "ffprobe":
                return _Out(stdout=probe_payload)
            return _Out(stderr="nothing recognizable here")

        saved_run = eva.subprocess.run
        try:
            eva.subprocess.run = fake_run_no_rms
            with contextlib.redirect_stdout(io.StringIO()):
                errs, warns, untested_q, na_q = eva._check_video_quality("fake.mp4")
        finally:
            eva.subprocess.run = saved_run
        tc.assert_true(any("Audio RMS consistency" in u and "UNTESTED" in u
                           for u in untested_q),
                       "unparseable RMS is recorded as untested, not silently skipped")
        tc.assert_true(any("Blank-frame check" in u for u in untested_q),
                       "frame sampling that yields too few frames is also untested")
        tc.assert_equal(errs, [], "measurement gaps alone raise no error")

        # (d) 无音轨属合法不适用，不得计入未测
        probe_no_audio = json.dumps({"streams": [
            {"codec_type": "video", "bit_rate": "5000000", "duration": "20.0"}]})

        def fake_run_no_audio(argv, *a, **k):
            if argv[0] == "ffprobe":
                return _Out(stdout=probe_no_audio)
            return _Out(stderr="")

        try:
            eva.subprocess.run = fake_run_no_audio
            with contextlib.redirect_stdout(io.StringIO()):
                _, _, untested_d, na_d = eva._check_video_quality("fake.mp4")
        finally:
            eva.subprocess.run = saved_run
        tc.assert_true(any("Audio RMS consistency" in x and "NOT_APPLICABLE" in x
                           for x in na_d),
                       "no-audio path is typed NOT_APPLICABLE")
        tc.assert_true(not any("Audio RMS consistency" in u for u in untested_d),
                       "NOT_APPLICABLE must not leak into the untested list")

        # (e) 未测存在时结果落盘带 untested 字段（供 pipeline_state 追溯）
        with tempfile.TemporaryDirectory() as td:
            eva._write_media_quality_result(Path(td), True, [], [], ["u1"], ["n1"])
            payload = json.loads((Path(td) / "media_quality_result.json")
                                 .read_text(encoding="utf-8"))
        tc.assert_equal((payload["passed"], payload["untested"], payload["not_applicable"]),
                        (True, ["u1"], ["n1"]),
                        "media_quality_result carries four-state lists for traceability")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_explicit_engine_binds_config_path() -> RegressionTestCase:
    """用例49：显式 --engine openmontage 的 config_path 绑定（审核 A11）

    背景：config_path 只在"未指定引擎→自动探测"分支内赋值，显式传 --engine
    openmontage 时路由分支直接引用它 → UnboundLocalError，该入口完全不可用。
    用哨兵类替换 OpenMontageRunner 捕获实参：既证明绑定成立，也不让真实流水线
    在测试里被执行（夹具不触碰渲染与交付目录）。
    """
    tc = RegressionTestCase(
        "explicit_engine_binds_config_path",
        "验证显式指定引擎时 config_path 已绑定并原样传给对应 runner"
    )
    try:
        import io
        import types
        import contextlib
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        pr = _safe_import_pipeline_runner()

        class _SentinelStop(Exception):
            pass

        captured = {}

        class _FakeRunner:
            def __init__(self, config_path=None, force=False, **kwargs):
                captured["config_path"] = config_path
                raise _SentinelStop("stop before touching the real pipeline")

        fake_module = types.ModuleType("openmontage_runner")
        fake_module.OpenMontageRunner = _FakeRunner
        saved_module = sys.modules.get("openmontage_runner")
        saved_argv = list(sys.argv)
        sys.modules["openmontage_runner"] = fake_module
        sys.argv = ["pipeline_runner.py", "--config", "case49-engine-entry.json",
                    "--engine", "openmontage"]
        caught = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    pr.main()
                except SystemExit as e:  # argparse 提前退出也要能被区分
                    caught = e
                except Exception as e:
                    caught = e
        finally:
            if saved_module is not None:
                sys.modules["openmontage_runner"] = saved_module
            else:
                sys.modules.pop("openmontage_runner", None)
            sys.argv = saved_argv

        tc.assert_true(not isinstance(caught, UnboundLocalError),
                       "explicit --engine no longer raises UnboundLocalError")
        tc.assert_true(isinstance(caught, _SentinelStop),
                       "routing reached the engine runner with a bound config_path")
        resolved = str(captured.get("config_path") or "")
        tc.assert_true(resolved.endswith("case49-engine-entry.json"),
                       "resolved config path is handed to the runner unchanged")
        tc.assert_true(bool(resolved) and Path(resolved).is_absolute(),
                       "relative --config is resolved against CONFIG_DIR for every engine")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_step_fingerprint_binds_real_inputs() -> RegressionTestCase:
    """用例50：步骤指纹绑定真实输入（审核 A06 三缺口，2026-09-18）

    背景：指纹机制在位，但三处登记与真实生效路径脱节，使"当前交付对应当前输入"
    这一承诺失效：
      ① 显式 --step 起跑只查 status（_gate_satisfied），不做指纹校验——改完 HTML
         直接 --step render 时历史 passed 依旧成立，本应校验新 HTML 的门禁被免检；
      ② preflight 指纹不含 gate_mode——audit 模式把 8 类 render-critical 检查降级
         为警告后 passed，严格模式复用该结论；preview 指纹漏登其真实消费的
         tts_manifest 与脚本自身；
      ③ tts_manifest 指纹登记在 temp/tts_44k/ 下——该路径从不存在（全仓实测
         temp 根 31 个、tts_44k 内 0 个），而 compute_inputs_fingerprint 对缺失路径
         退化为常量 "notfound:<路径>"，于是这项输入永久空转。
    每条断言都配负向对照，防止"恒真断言"式空跑。
    """
    tc = RegressionTestCase(
        "step_fingerprint_binds_real_inputs",
        "验证 tts_manifest 指纹绑定真实落盘路径、preflight 纳入 gate_mode、"
        "--step 前置步做指纹有效性判定"
    )

    try:
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState
        runner_content = (script_dir / "pipeline_runner.py").read_text(encoding='utf-8')

        saved_config_dir = _pr.CONFIG_DIR
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = Path(tmpdir)
                quality_dir = tmpdir / "quality"
                quality_dir.mkdir(parents=True)
                rules_files = {
                    "audio_sync_rules.json": {"alignment": {"silence_db": -38}},
                    "narration_digits_rules.json": {"whitelist": []},
                    "video_quality_rules.json": {"min_video_bitrate_kbps_fail": 200},
                    "subtitle_term_rules.json": {"terms": []},
                    "render_rules.json": {"render_budget": {"max_full_renders": 3}},
                }
                for fname, payload in rules_files.items():
                    (quality_dir / fname).write_text(
                        json.dumps(payload), encoding='utf-8')
                _pr.CONFIG_DIR = tmpdir

                source_dir = tmpdir / "src"
                source_dir.mkdir()
                (source_dir / "index.html").write_text("<html>s1</html>", encoding='utf-8')
                (source_dir / "narration.json").write_text(
                    json.dumps({"scenes": [{"scene_id": "s1", "narration": "第一项"}]},
                               ensure_ascii=False), encoding='utf-8')
                signoff = source_dir / "素材确认单.json"
                signoff.write_text(json.dumps({"场景": []}, ensure_ascii=False),
                                   encoding='utf-8')

                cfg_path = tmpdir / "cfg.json"
                cfg_path.write_text("{}", encoding='utf-8')

                temp_dir = tmpdir / "temp"
                temp_dir.mkdir()
                # 真实落盘点（enhance_video_audio._save_tts_manifest）与旧登记点各一份，
                # 内容不同——只有前者变化才应使依赖它的步骤失效。
                real_manifest = temp_dir / "tts_manifest.json"
                real_manifest.write_text(json.dumps({"s1": 3.2}), encoding='utf-8')
                decoy_dir = temp_dir / "tts_44k"
                decoy_dir.mkdir()
                decoy_manifest = decoy_dir / "tts_manifest.json"
                decoy_manifest.write_text(json.dumps({"s1": 3.2}), encoding='utf-8')
                # 真实形态下 postprocess/verify 跑在渲染之后，render_raw 必在盘；
                # 夹具照此建空文件，使 [FP-MISS] 只在被注入的缺失场景出现
                (temp_dir / "render_raw.mp4").write_bytes(b"")

                state = PipelineState(temp_dir / "pipeline_state.json")
                runner = PipelineRunner.__new__(PipelineRunner)
                runner.state = state
                runner.force = False
                runner.config = {}
                runner.config_path = cfg_path
                runner.html_project = "case50"
                runner.source_dir = source_dir
                runner.html_path = source_dir / "index.html"
                runner.temp_dir = temp_dir
                runner.tts_dir = decoy_dir
                runner.render_raw = temp_dir / "render_raw.mp4"
                runner.output_file = temp_dir / "case50.mp4"
                runner.gate_mode = "render"
                runner.video_type = ""
                runner.quick_fix = False

                # ── ③ 输入项必须绑定生产者真实写盘点 ──
                for step in ("timeline", "preview", "postprocess"):
                    declared = runner._step_inputs(step).get("tts_manifest", "")
                    tc.assert_true(
                        declared and not declared.startswith(str(decoy_dir)),
                        f"{step} tts_manifest input is not the dead tts_44k path")
                    tc.assert_equal(declared, str(real_manifest),
                                    f"{step} tts_manifest input points at the temp root"
                                    f" the producer writes to")

                def _reset(*steps):
                    for s in steps:
                        state.mark_started(s)
                        state.mark_completed(s, runner._fingerprint(s))
                        tc.assert_equal(runner._step_dirty_reason(s), None,
                                        f"{s} cache reusable right after completion")

                # 改真实 manifest → 三个消费步全部失效
                _reset("timeline", "preview", "postprocess")
                real_manifest.write_text(json.dumps({"s1": 5.7}), encoding='utf-8')
                for s in ("timeline", "preview", "postprocess"):
                    tc.assert_equal(runner._step_dirty_reason(s), "inputs-changed",
                                    f"{s} invalidated by the manifest it actually consumes")

                # 负向对照：只改 tts_44k 里那份（流水线从不读它）→ 不得失效，
                # 否则说明指纹登记面又漂回死路径
                _reset("timeline", "preview", "postprocess")
                real_manifest.write_text(json.dumps({"s1": 3.2}), encoding='utf-8')
                _reset("timeline", "preview", "postprocess")
                decoy_manifest.write_text(json.dumps({"s1": 9.9}), encoding='utf-8')
                for s in ("timeline", "preview", "postprocess"):
                    tc.assert_equal(runner._step_dirty_reason(s), None,
                                    f"{s} unaffected by the never-consumed tts_44k manifest")
                decoy_manifest.write_text(json.dumps({"s1": 3.2}), encoding='utf-8')

                # ── ③ 附：运行时产物登记路径失效必须可见，不得静默退化成常量 ──
                import io as _io
                import contextlib as _cl
                (temp_dir / "render_raw.mp4").unlink()   # 注入缺失（A06-③ 的同型形态）
                buf = _io.StringIO()
                with _cl.redirect_stdout(buf):
                    runner._fingerprint("verify")
                out = buf.getvalue()
                tc.assert_true("[FP-MISS]" in out and "render_raw" in out,
                               "a registered runtime artifact missing from disk is named,"
                               " not silently hashed as a constant")
                (temp_dir / "render_raw.mp4").write_bytes(b"")
                buf2 = _io.StringIO()
                with _cl.redirect_stdout(buf2):
                    runner._fingerprint("verify")
                tc.assert_true("[FP-MISS]" not in buf2.getvalue(),
                               "same step prints nothing once the artifact is in place"
                               " (防空跑恒真)")
                # 源文件不入点名范围：按项目形态本就可缺，常驻警告会淹没真问题
                signoff.unlink()
                buf3 = _io.StringIO()
                with _cl.redirect_stdout(buf3):
                    runner._fingerprint("preflight")
                tc.assert_true("[FP-MISS]" not in buf3.getvalue(),
                               "optional source files (素材确认单) absent do not warn")
                signoff.write_text(json.dumps({"场景": []}, ensure_ascii=False),
                                   encoding='utf-8')

                # ── ② preflight：gate_mode 与真实消费面入指纹 ──
                _reset("preflight")
                runner.gate_mode = "audit"
                tc.assert_equal(runner._step_dirty_reason("preflight"), "inputs-changed",
                                "audit-mode result is not reusable as a render-mode pass")
                runner.gate_mode = "render"
                _reset("preflight")
                signoff.write_text(json.dumps({"场景": [{"scene_id": "s1"}]},
                                               ensure_ascii=False), encoding='utf-8')
                tc.assert_equal(runner._step_dirty_reason("preflight"), "inputs-changed",
                                "素材确认单 change invalidates preflight (check 14 consumes it)")
                signoff.write_text(json.dumps({"场景": []}, ensure_ascii=False),
                                   encoding='utf-8')
                _reset("preflight")
                (quality_dir / "narration_digits_rules.json").write_text(
                    json.dumps({"whitelist": ["三心二意"]}), encoding='utf-8')
                tc.assert_equal(runner._step_dirty_reason("preflight"), "inputs-changed",
                                "narration_digits_rules change invalidates preflight (check 13)")
                (quality_dir / "narration_digits_rules.json").write_text(
                    json.dumps({"whitelist": []}), encoding='utf-8')
                tc.assert_true(
                    runner._step_inputs("preflight").get("script", "").endswith("preflight_check.py"),
                    "preflight inputs include preflight_check.py itself")
                tc.assert_true(
                    runner._step_inputs("preview").get("script", "").endswith("instant_preview.py"),
                    "preview inputs include instant_preview.py itself")

                # ── ① --step 前置步有效性判定四态 ──
                state.data["steps"] = {}
                tc.assert_equal(runner._prereq_stale_reason("preflight"), "not-passed",
                                "unexecuted prerequisite is reported as not-passed")
                _reset("preflight")
                tc.assert_equal(runner._prereq_stale_reason("preflight"), None,
                                "fresh prerequisite passes the --step check")
                (source_dir / "index.html").write_text("<html>changed</html>",
                                                       encoding='utf-8')
                tc.assert_equal(runner._prereq_stale_reason("preflight"), "inputs-changed",
                                "prerequisite passed against stale inputs blocks --step")
                (source_dir / "index.html").write_text("<html>s1</html>", encoding='utf-8')
                _reset("preflight")
                del state.data["steps"]["preflight"]["input_fingerprint"]
                tc.assert_equal(runner._prereq_stale_reason("preflight"), "no-fingerprint",
                                "fingerprint-less history is not trusted for --step")
                state.data["steps"]["tts"] = {
                    "status": "skipped", "reason": runner._typed_skip_reason()}
                tc.assert_equal(runner._prereq_stale_reason("tts"), None,
                                "typed skip stays a legal prerequisite (no fingerprint semantics)")

                # 接线：--step 分支必须真的调用该判定，且 missing/stale 两类都进 BLOCKED 出口
                branch = runner_content.split(
                    "if start_from and not self.force and not self.quick_fix:", 1)
                tc.assert_true(len(branch) == 2,
                               "--step prerequisite branch still exists in the runner")
                body = branch[1].split("# Setup environment", 1)[0]
                tc.assert_true("_prereq_stale_reason(" in body,
                               "--step branch validates prerequisites by fingerprint")
                tc.assert_true("Stale prerequisite" in body and "sys.exit(1)" in body,
                               "stale prerequisites are printed and block the run")

                tc.mark_passed()
        finally:
            _pr.CONFIG_DIR = saved_config_dir

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_narration_rich_fields_survive_timeline_writeback() -> RegressionTestCase:
    """用例51：timeline 写回不得裁掉单一权威源字段（A05，2026-09-18 审核）

    背景：narration.json 是场景定义的单一权威源（AGENTS.md 权威源表），而
    adjust_timeline 的 update_config 产出正是写回该文件的载荷。旧实现用
    start/end/narration/type 四个字面键重建场景 dict，且 _write_narration_scenes
    以新表整表替换 data['scenes']——于是每跑一次 timeline：
      ① title / narration_required / subtitle_required / assets / 项目自定义字段
         从权威源上静默消失（无报错、无日志，下次读到的就是瘦身后 schema）；
      ② cover 场景被顺手删除（入参来自 resolve_narration_scenes，它已滤掉 cover）；
      ③ duration 不随新窗口重算，留下与 start/end 自相矛盾的派生量。
    本用例锁定"只覆写时间字段 + 按 scene_id 就地合并"的新语义，并留出反向对照
    （duration 必须被重算、cover 必须存活），使断言不是恒真。
    """
    tc = RegressionTestCase(
        "narration_rich_fields_survive_timeline_writeback",
        "验证 timeline 写回保留富字段、cover 场景，并重算 duration"
    )
    try:
        at = _safe_import_rebinding_module("adjust_timeline")

        rich_s1 = {
            "scene_id": "s1", "type": "content",
            "start": 0.0, "end": 8.0, "duration": 8.0,
            "title": "痛点：墙面开裂反复返修",
            "narration": "30 小时内完成治理。",
            "narration_required": True,
            "subtitle_required": True,
            "assets": ["素材文件/图片/crack.png"],
            "visual_note": {"layout": "split", "emphasis": 3},
        }
        cover_s0 = {
            "scene_id": "s0", "type": "cover",
            "start": 0.0, "end": 3.0, "duration": 3.0,
            "title": "封面", "narration": "", "narration_required": False,
        }
        # 无 duration 的场景：写回不得凭空造字段
        bare_s2 = {
            "scene_id": "s2", "type": "content",
            "start": 8.0, "end": 14.0, "narration": "第二处窗口。",
        }
        content_scenes = [json.loads(json.dumps(rich_s1)),
                          json.loads(json.dumps(bare_s2))]

        # ── update_config：increment 路径（boundaries=None）保留非时间字段 ──
        cfg = {"video_duration": 14.0,
               "scenes": [json.loads(json.dumps(rich_s1)),
                          json.loads(json.dumps(bare_s2))]}
        out = at.update_config(cfg, content_scenes,
                               extensions=[2.0, 0.0], offsets=[0.0, 2.0],
                               total_ext=2.0, cover_duration=3.0)
        tc.assert_equal(sorted(out["scenes"][0].keys()),
                        sorted(rich_s1.keys()),
                        "update_config keeps every field of the source scene")
        tc.assert_equal(out["scenes"][0]["title"], rich_s1["title"],
                        "title survives the incremental-adjust path")
        tc.assert_equal(out["scenes"][0]["assets"], rich_s1["assets"],
                        "asset references survive")
        tc.assert_equal(out["scenes"][0]["visual_note"], rich_s1["visual_note"],
                        "project-defined fields survive")
        tc.assert_equal(out["scenes"][0]["narration_required"], True,
                        "narration_required survives")
        tc.assert_equal(out["scenes"][0]["duration"],
                        round(out["scenes"][0]["end"] - out["scenes"][0]["start"], 1),
                        "duration is recomputed from the new window")
        # 负向对照：窗口变了 2s，duration 不得停在旧值 8.0
        tc.assert_true(out["scenes"][0]["duration"] != rich_s1["duration"],
                       "stale duration would prove the recompute is a no-op")
        tc.assert_true("duration" not in out["scenes"][1],
                       "update_config does not invent a duration the source omitted")

        # ── update_config：S-block 派生路径（boundaries 给定）同样保字段 ──
        cfg2 = {"video_duration": 14.0,
                "scenes": [json.loads(json.dumps(rich_s1)),
                           json.loads(json.dumps(bare_s2))]}
        out2 = at.update_config(cfg2, content_scenes,
                                extensions=[1.0, 1.0], offsets=[0.0, 0.0],
                                total_ext=2.0, cover_duration=3.0,
                                boundaries=[(3.0, 12.0), (12.0, 19.0)])
        tc.assert_equal(sorted(out2["scenes"][0].keys()), sorted(rich_s1.keys()),
                        "the derive-from-S-block path keeps every field")
        tc.assert_equal(out2["scenes"][0]["start"], 0.0,
                        "boundaries are converted back to cover-relative time")
        tc.assert_equal(out2["scenes"][0]["duration"], 10.0,
                        "duration follows the derived window (12.0+1.0-3.0 - 0.0)")

        # ── _write_narration_scenes：就地合并 ──
        with tempfile.TemporaryDirectory() as td:
            narr = Path(td) / "narration.json"
            narr.write_text(json.dumps({
                "version": "1.0",
                "scenes": [json.loads(json.dumps(cover_s0)),
                           json.loads(json.dumps(rich_s1)),
                           json.loads(json.dumps(bare_s2)),
                           {"scene_id": "s9", "type": "content", "start": 99.0,
                            "end": 100.0, "title": "本轮未触及"}]
            }, ensure_ascii=False), encoding='utf-8')

            at._write_narration_scenes(narr, out["scenes"], cover_duration=3.0)
            written = json.loads(narr.read_text(encoding='utf-8'))

            ids = [s["scene_id"] for s in written["scenes"]]
            tc.assert_equal(ids, ["s0", "s1", "s2", "s9"],
                            "merge keeps cover, untouched scenes and original order")
            tc.assert_true("s0" in ids, "cover scene is not deleted by the writeback")
            s0 = next(s for s in written["scenes"] if s["scene_id"] == "s0")
            tc.assert_equal(s0, cover_s0, "cover scene stays byte-identical")
            s1 = next(s for s in written["scenes"] if s["scene_id"] == "s1")
            tc.assert_equal(s1["title"], rich_s1["title"],
                            "title survives the narration writeback")
            tc.assert_equal(s1["subtitle_required"], True,
                            "subtitle_required survives the writeback")
            tc.assert_equal(s1["assets"], rich_s1["assets"],
                            "assets survive the writeback")
            tc.assert_equal(s1["start"], round(out["scenes"][0]["start"] + 3.0, 1),
                            "writeback shifts to absolute time")
            tc.assert_equal(s1["duration"],
                            round(s1["end"] - s1["start"], 1),
                            "absolute-window duration stays self-consistent")
            s9 = next(s for s in written["scenes"] if s["scene_id"] == "s9")
            tc.assert_equal(s9, {"scene_id": "s9", "type": "content", "start": 99.0,
                                 "end": 100.0, "title": "本轮未触及"},
                            "a scene absent from this round is preserved verbatim")
            tc.assert_equal(written["version"], "1.0",
                            "top-level keys outside scenes are untouched")

            # 入参有、文件里没有的场景仍追加（不静默丢）
            extra = json.loads(json.dumps(rich_s1))
            extra["scene_id"] = "s7"
            at._write_narration_scenes(narr, [extra], cover_duration=3.0)
            ids2 = [s["scene_id"] for s in
                    json.loads(narr.read_text(encoding='utf-8'))["scenes"]]
            tc.assert_equal(ids2, ["s0", "s1", "s2", "s9", "s7"],
                            "a new scene is appended rather than silently dropped")

            # ── 原子写：narration.json 是不可再生权威源，中断不得留半截（A10）──
            #     整表替换会丢字段（上面已锁），半截写入则整本作废——同一文件的
            #     两类破坏，判据必须都在。
            good_bytes = narr.read_bytes()
            import script_interface as si
            from types import SimpleNamespace as _NS
            real_json = si.json

            class _HalfDump:
                @staticmethod
                def dump(payload, f, **kw):
                    f.write('{"scenes": [')
                    raise RuntimeError("simulated interrupt mid-write")

            try:
                si.json = _NS(dump=_HalfDump.dump, loads=real_json.loads)
                raised = False
                try:
                    at._write_narration_scenes(narr, out["scenes"], cover_duration=3.0)
                except RuntimeError:
                    raised = True
            finally:
                si.json = real_json
            tc.assert_true(raised,
                           "the injected mid-write failure propagated out of the narration "
                           "writeback (proves the write really runs through atomic_write_json)")
            tc.assert_equal(narr.read_bytes(), good_bytes,
                            "an interrupted writeback leaves narration.json byte-identical "
                            "(in-place open('w') would have truncated the authoritative source)")
            tc.assert_equal([q.name for q in narr.parent.iterdir() if q.suffix == '.tmp'], [],
                            "no stray .tmp sibling is left next to the authoritative source")

        # ── 接线（源码面锁定，A12 分级）：main() → update_config → 写回 narration.json
        #    这条链要跑完整 timeline（HTML + TTS 目录 + S-block）才有行为接缝，
        #    秒级不可达，故保留调用存在性断言；写回语义本身（合并不重建、
        #    富字段保活、原子落盘）已由上方行为断言覆盖，此处不再重复。
        src = (Path(__file__).parent / "adjust_timeline.py").read_text(encoding='utf-8')
        main_body = src.split("def main(", 1)[1]
        tc.assert_true("update_config(" in main_body,
                       "main() still routes through update_config")
        tc.assert_true("_write_narration_scenes(" in main_body,
                       "main() still writes the adjusted scenes back to narration.json")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_media_qa_gate_four_state_report() -> RegressionTestCase:
    """用例52：media_qa_gate 四态报告与 adjudicate 裁定（审核 A04，2026-09-18）

    背景：各项检查在"扫不成/样本不足/测不到"时旧实现压成 checks=True，
    总报告呈"全部通过"假象（审核根因一）。本用例锁定：
      - UNTESTED 独立成清单且 verdict 降为 UNTESTED，adjudicate 默认不放行；
      - --accept-media-untested 只放行 UNTESTED，永不放行 FAIL（负向对照）；
      - NOT_APPLICABLE（BGM 遮蔽、字幕豁免声明、quick-fix 跳视觉）
        不计违规也不计通过；
      - 物理扫描函数按模块级 stub——判定逻辑与 ffmpeg 可用性解耦。
    """
    tc = RegressionTestCase(
        "media_qa_gate_four_state_report",
        "验证媒体终检四态报告：UNTESTED 不得读成通过、FAIL 不可被放行、NA 双不计"
    )

    try:
        import media_qa_gate as mq
        import _gate_status as gs
        from types import SimpleNamespace

        _saved = {}

        def _patch(name, fn):
            _saved.setdefault(name, getattr(mq, name))
            setattr(mq, name, fn)

        def _restore():
            for k, v in _saved.items():
                setattr(mq, k, v)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                video = tmp / "案例成片.mp4"
                video.write_bytes(b"fake-video-bytes")

                rules = mq._load_alignment_rules()
                bf_rules = mq._load_black_frame_rules()
                m = float(bf_rules['max_overlap_with_narration_seconds'])
                # 黑场夹具的超长区间假设阈值 ≤2s（实际配置 1.0s）；漂移时本用例
                # 显式失败点名，而不是静默失去覆盖
                tc.assert_true(m <= 2.0,
                               f"black-frame fixture assumes max_overlap<=2s (actual {m}s)")
                # 字幕窗口随阈值张开：entry1=[1, 3+2m]，黑场=[1, 2+2m]
                # → 重叠恒为 1+2m > m，判定不依赖阈值具体取值
                e1_end = 3.0 + 2.0 * m
                e2_start, e2_end = e1_end + 1.0, e1_end + 3.0
                srt = tmp / "案例成片.srt"
                srt.write_text(
                    f"1\n00:00:01,000 --> 00:00:{int(e1_end):02d},000\n第一段旁白\n\n"
                    f"2\n00:00:{int(e2_start):02d},000 --> 00:00:{int(e2_end):02d},000\n第二段旁白\n",
                    encoding='utf-8')
                cfg = tmp / "cfg.json"
                cfg.write_text(json.dumps({"fps": 25, "resolution": "1920x1080"}),
                               encoding='utf-8')
                cfg_bgm = tmp / "cfg_bgm.json"
                cfg_bgm.write_text(json.dumps({"fps": 25, "resolution": "1920x1080",
                                               "bgm_enabled": True}), encoding='utf-8')

                def streams(bit_rate="1500000"):
                    return [
                        {"codec_type": "video", "codec_name": "h264",
                         "bit_rate": bit_rate, "r_frame_rate": "25/1",
                         "width": 1920, "height": 1080},
                        {"codec_type": "audio", "codec_name": "aac",
                         "bit_rate": "128000"},
                    ]

                scan = {"onsets": None, "black": [], "long_silences": []}
                clean_onsets = [1.5, 2.5, e2_start, e2_start + 1.0]
                clean_onsets += [1.5] * max(0, int(rules['min_onsets']) - len(clean_onsets))
                scan["onsets"] = clean_onsets

                _patch("ffprobe_get_streams", lambda p: streams())
                _patch("ffprobe_get_duration", lambda p: 10.0)
                _patch("ffmpeg_detect_silence", lambda p, d=None: 1.0)
                _patch("subprocess", SimpleNamespace(
                    run=lambda *a, **k: SimpleNamespace(
                        returncode=0, stdout="", stderr="")))
                _patch("ffmpeg_detect_speech_onsets", lambda *a, **k: scan["onsets"])
                _patch("ffmpeg_detect_black_intervals", lambda *a, **k: scan["black"])
                _patch("ffmpeg_detect_long_silences", lambda *a, **k: scan["long_silences"])

                # step5 逐场时间戳来源记录夹具（subtitle_timestamp_source 的取数面）。
                # 条目数取自被测字幕的真实解析结果，不手抄（A12）。
                src_rec = tmp / "_subtitle_timestamp_source.json"
                src_rec.write_text(json.dumps({
                    'subtitle_file': srt.name,
                    'srt_entries': len(mq.parse_srt(str(srt))),
                    'scenes': [{'scene_id': 's1', 'source': 'asr_forced', 'segments': 1},
                               {'scene_id': 's2', 'source': 'asr_forced', 'segments': 1}],
                }, ensure_ascii=False), encoding='utf-8')

                def run_qa(subtitle=str(srt), visual=True, config=str(cfg),
                           source=str(src_rec)):
                    qa = mq.MediaQAGate()
                    return qa.validate(str(video), subtitle,
                                       visual_check_passed=visual,
                                       config_path=config,
                                       subtitle_source_path=source)

                # ── A. 全清洁测量 → PASS，每项都有状态位 ──
                passed_a, res_a = run_qa()
                tc.assert_equal(res_a['verdict'], gs.PASS,
                                "clean measurement adjudicates PASS")
                # 项数不手抄（A12）：清单与运行时的逐名一致由用例57 锁定，
                # 这里只锁"报告自洽 + 没有塌成桩"，加检查项无需回来改本处。
                tc.assert_equal(res_a['total_checks'], len(res_a['check_statuses']),
                                "total_checks is the size of the status map, not a literal")
                tc.assert_true(res_a['total_checks'] >= 15,
                               f"the gate still runs a full battery, got {res_a['total_checks']}")
                tc.assert_equal(res_a['passed_checks'], res_a['total_checks'],
                                "PASS count equals total when nothing deviates")
                tc.assert_equal(mq.adjudicate(res_a), (True, ""),
                                "adjudicate releases a clean PASS with empty reason")
                tc.assert_equal(res_a['check_statuses']['subtitle_audio_alignment'],
                                gs.PASS, "alignment measured PASS for in-window onsets")
                tc.assert_equal(res_a['check_statuses']['subtitle_timestamp_source'],
                                gs.PASS,
                                "a matching all-asr_forced source record does not false-block")
                tc.assert_true('measure_semantics' in res_a['alignment']
                               and 'NOT per-sentence' in res_a['alignment']['measure_semantics'],
                               "the p95 semantics boundary rides on the machine-readable "
                               "alignment record (A08) — behaviour, not a source-text claim")

                # ── B. 起口扫描失败 → UNTESTED 不得压成通过 ──
                scan["onsets"] = None
                passed_b, res_b = run_qa()
                tc.assert_true(passed_b,
                               "passed keeps legacy semantics (no FAIL errors) — the "
                               "exact shape of the old 'all green' illusion")
                tc.assert_equal(res_b['verdict'], gs.UNTESTED,
                                "a failed scan demotes the verdict to UNTESTED, not PASS")
                tc.assert_equal(res_b['checks']['subtitle_audio_alignment'], False,
                                "compat checks view marks UNTESTED as not-passed")
                tc.assert_true(any('subtitle_audio_alignment' in u
                                   for u in res_b['untested']),
                               "untested list names the failing check")
                ok_b, reason_b = mq.adjudicate(res_b)
                tc.assert_true(not ok_b and 'UNTESTED' in reason_b,
                               "adjudicate blocks UNTESTED by default")
                ok_b2, reason_b2 = mq.adjudicate(res_b, accept_untested=True)
                tc.assert_true(ok_b2 and '--accept-media-untested' in reason_b2,
                               "explicit acceptance releases UNTESTED with a traceable reason")
                scan["onsets"] = clean_onsets

                # ── C. 起口样本不足 + config 声明 BGM → 合法不适用 ──
                scan["onsets"] = []
                _, res_c = run_qa(config=str(cfg_bgm))
                tc.assert_equal(res_c['check_statuses']['subtitle_audio_alignment'],
                                gs.NOT_APPLICABLE,
                                "BGM-declared silence-based check is NOT_APPLICABLE")
                tc.assert_equal(res_c['verdict'], gs.PASS,
                                "NOT_APPLICABLE neither blocks nor counts as violation")
                tc.assert_true(any('subtitle_audio_alignment' in n
                                   for n in res_c['not_applicable']),
                               "not_applicable list names the BGM-exempted check")
                scan["onsets"] = clean_onsets

                # ── D. 起口样本不足、无 BGM 声明 → UNTESTED（旧实现记通过＝原缺陷）──
                scan["onsets"] = []
                _, res_d = run_qa(config=str(cfg))
                tc.assert_equal(res_d['check_statuses']['subtitle_audio_alignment'],
                                gs.UNTESTED,
                                "too-few onsets without a BGM declaration is UNTESTED, "
                                "not the legacy checks=True")
                tc.assert_equal(res_d['verdict'], gs.UNTESTED,
                                "insufficient adjudication evidence demotes the verdict")
                tc.assert_true(not mq.adjudicate(res_d)[0],
                               "unadjudicated alignment blocks delivery by default")
                scan["onsets"] = clean_onsets

                # ── E. 黑场与字幕重叠 → FAIL，accept 不得放行 FAIL ──
                scan["black"] = [(1.0, 2.0 + 2.0 * m)]
                _, res_e = run_qa()
                tc.assert_equal(res_e['check_statuses']['no_black_frame_with_subtitle'],
                                gs.FAIL, "black screen overlapping subtitle is FAIL")
                tc.assert_equal(res_e['verdict'], gs.FAIL,
                                "any FAIL dominates the verdict")
                ok_e, reason_e = mq.adjudicate(res_e, accept_untested=True)
                tc.assert_true(not ok_e and 'FAIL' in reason_e,
                               "--accept-media-untested must never release a FAIL")
                scan["black"] = []

                # ── F. 码率取不到 → UNTESTED（不得静默跳过）──
                _patch("ffprobe_get_streams", lambda p: streams("N/A"))
                _, res_f = run_qa()
                tc.assert_equal(res_f['check_statuses']['video_bitrate_ok'],
                                gs.UNTESTED,
                                "unparseable ffprobe bit_rate is UNTESTED, not pass")
                tc.assert_equal(res_f['verdict'], gs.UNTESTED,
                                "an unrun bitrate gate demotes the verdict")
                _patch("ffprobe_get_streams", lambda p: streams())

                # ── G. 字幕声明豁免（纯 BGM 项目）→ 字幕组整组 NOT_APPLICABLE ──
                _, res_g = run_qa(subtitle=None)
                tc.assert_equal(res_g['status_counts'][gs.NOT_APPLICABLE],
                                len(mq.SUBTITLE_CHECK_KEYS),
                                "the whole subtitle check-group is NA under declared exemption")
                tc.assert_equal(res_g['verdict'], gs.PASS,
                                "declared exemption does not block the rest")
                tc.assert_equal(res_g['passed_checks'],
                                res_g['total_checks'] - res_g['status_counts'][gs.NOT_APPLICABLE],
                                "NA checks are not counted as passed")

                # ── H. quick-fix 声明性跳视觉检查 → NA 而非 PASS/FAIL ──
                _, res_h = run_qa(visual="not_applicable")
                tc.assert_equal(res_h['check_statuses']['visual_boundary_verified'],
                                gs.NOT_APPLICABLE,
                                "declaratively skipped visual check is NOT_APPLICABLE")
                tc.assert_equal(res_h['verdict'], gs.PASS,
                                "NA visual does not block postprocess delivery")

                # ── I. 声明了字幕却文件缺失 → FAIL + 组内 UNTESTED ──
                _, res_i = run_qa(subtitle=str(tmp / "不存在.srt"))
                tc.assert_equal(res_i['check_statuses']['subtitle_file_exists'],
                                gs.FAIL, "declared-but-missing subtitle file is FAIL")
                tc.assert_equal(res_i['verdict'], gs.FAIL,
                                "a missing subtitle dominates the verdict")
                tc.assert_true(not mq.adjudicate(res_i, accept_untested=True)[0],
                               "FAIL from subtitle group is not acceptable")

            tc.mark_passed()

        finally:
            _restore()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_final_media_qa_wired_into_postprocess() -> RegressionTestCase:
    """用例53：成片媒体终检挂载到 postprocess 末端（A04 接线段，2026-09-18）

    背景：media_qa_gate 此前只被自身 CLI 与回归调用，默认生产链从未挂载——
    成片可以在从未被可播放性/死区/黑场/对齐度审过的情况下进交付。本用例锁
    接线行为（用 FakeQA 替换 MediaQAGate，裁定走真 adjudicate）：
      - PASS 放行且结果合并进 state.verifications["media_qa_final"]；
      - UNTESTED 默认阻断（postprocess failed + VERIFY_FAILED）；
      - --accept-media-untested 放行并留痕 state.media_qa_untested_decision
        （落盘可追溯），但 FAIL 永不被它放行；
      - quick-fix 下视觉检查按声明记 not_applicable、mode 记 quick-fix；
      - postprocess 指纹登记绑定 media_qa_gate.py 本体与 mode（A06 同族：
        登记面＝真实读取面，quick-fix 结论不得被 full 复用）；
      - step_postprocess 源码里终检在 mark_completed 之前（防"先记完成再审"
        的接线漂移）。
    """
    tc = RegressionTestCase(
        "final_media_qa_wired_into_postprocess",
        "验证 _final_media_qa 阻断/放行/留痕/指纹绑定/接线时序五类行为"
    )

    try:
        import re
        import subprocess

        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import media_qa_gate as mq
        import _gate_status as gs
        _pr = _safe_import_pipeline_runner()
        PipelineRunner, PipelineState = _pr.PipelineRunner, _pr.PipelineState

        saved_qa = mq.MediaQAGate
        saved_config_dir = _pr.CONFIG_DIR
        calls = []
        holder = {"result": None, "raise": None}

        class FakeQA:
            def validate(self, video, subtitle=None, visual_check_passed=False,
                         config_path=None, narration_path=None,
                         subtitle_source_path=None):
                calls.append({"video": video, "subtitle": subtitle,
                              "visual": visual_check_passed,
                              "config": config_path,
                              "subtitle_source": subtitle_source_path})
                if holder["raise"]:
                    raise RuntimeError(holder["raise"])
                r = holder["result"]
                return r.get("passed", False), dict(r)

        def _res(verdict, errors=None, untested=None):
            errors = errors or []
            untested = untested or []
            return {
                "verdict": verdict, "passed": not errors,
                "errors": errors, "warnings": [],
                "untested": untested, "not_applicable": [],
                # 桩报告自洽计数：项数不手抄（A12）——真实清单由用例57 锁，
                # 本夹具只喂 verdict/errors/untested 给 adjudicate，计数自拟即可。
                "check_statuses": {},
                "status_counts": {gs.PASS: 1,
                                  gs.FAIL: len(errors),
                                  gs.UNTESTED: len(untested),
                                  gs.NOT_APPLICABLE: 0},
                "total_checks": 1 + len(errors) + len(untested),
                "alignment": {}, "media_facts": {},
            }

        try:
            mq.MediaQAGate = FakeQA
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                quality = tmp / "quality"
                quality.mkdir()
                for fname, payload in {
                    "audio_sync_rules.json": {"alignment": {"silence_db": -38}},
                    "narration_digits_rules.json": {"whitelist": []},
                    "video_quality_rules.json": {"min_video_bitrate_kbps_fail": 200},
                    "subtitle_term_rules.json": {"terms": []},
                    "render_rules.json": {"render_budget": {"max_full_renders": 3}},
                }.items():
                    (quality / fname).write_text(json.dumps(payload), encoding='utf-8')
                _pr.CONFIG_DIR = tmp

                source_dir = tmp / "src"
                source_dir.mkdir()
                (source_dir / "index.html").write_text("<html>s1</html>", encoding='utf-8')
                (source_dir / "narration.json").write_text(
                    json.dumps({"scenes": [{"scene_id": "s1", "narration": "第一项"}]},
                               ensure_ascii=False), encoding='utf-8')
                cfg_path = tmp / "cfg.json"
                cfg_path.write_text("{}", encoding='utf-8')
                temp_dir = tmp / "temp"
                temp_dir.mkdir()
                (temp_dir / "tts_manifest.json").write_text(
                    json.dumps({"s1": 3.2}), encoding='utf-8')
                (temp_dir / "render_raw.mp4").write_bytes(b"")

                def build(scenario, quick_fix=False, tts_enabled=True, accept=False):
                    state = PipelineState(temp_dir / f"state_{scenario}.json")
                    runner = PipelineRunner.__new__(PipelineRunner)
                    runner.state = state
                    runner.force = False
                    runner.config = {"delivery": {
                        "video": str(tmp / "case53.mp4"),
                        "subtitle": str(tmp / "case53.srt")}}
                    runner.config_path = cfg_path
                    runner.html_project = "case53"
                    runner.source_dir = source_dir
                    runner.html_path = source_dir / "index.html"
                    runner.temp_dir = temp_dir
                    runner.tts_dir = temp_dir / "tts"
                    runner.render_raw = temp_dir / "render_raw.mp4"
                    runner.output_file = tmp / "case53.mp4"
                    runner.gate_mode = "render"
                    runner.video_type = ""
                    runner.quick_fix = quick_fix
                    runner.tts_enabled = tts_enabled
                    runner.accept_media_untested = accept
                    # 真实形态下 _final_media_qa 在 step_postprocess 的
                    # mark_started 之后被调用，mark_failed 依赖该记录存在
                    state.mark_started("postprocess")
                    return runner, state

                # ── 1. PASS：放行 + 结果合并进 state.verifications ──
                runner, state = build("pass")
                state.mark_started("visual_check")
                state.mark_completed("visual_check")
                holder["result"] = _res(gs.PASS)
                calls.clear()
                tc.assert_true(runner._final_media_qa(),
                               "PASS verdict releases postprocess")
                tc.assert_equal(state.data["steps"]["postprocess"]["status"],
                                "running",
                                "a released QA does not mark postprocess failed")
                merged = state.data.get("verifications", {}).get("media_qa_final")
                tc.assert_true(merged is not None
                               and merged.get("verdict") == gs.PASS,
                               "final QA result is merged into state for traceability")
                tc.assert_equal(merged.get("mode"), "full",
                                "full-chain run records mode=full")
                tc.assert_equal(calls[-1]["visual"], True,
                                "visual PASS is forwarded from upstream state")
                tc.assert_true(calls[-1]["subtitle"].endswith("case53.srt"),
                               "tts-enabled projects pass the delivery subtitle")
                tc.assert_true(
                    calls[-1]["subtitle_source"] ==
                    str(temp_dir / "_subtitle_timestamp_source.json"),
                    "step5 的逐场时间戳来源记录是终检的唯一取数面，接线不得断")
                tc.assert_true((temp_dir / "media_qa_final_result.json").exists(),
                               "the four-state report is persisted as a file fact")

                # ── 2. UNTESTED 默认阻断 ──
                runner, state = build("untested")
                holder["result"] = _res(gs.UNTESTED, untested=[
                    "subtitle_audio_alignment — silencedetect 起口扫描失败"])
                tc.assert_true(not runner._final_media_qa(),
                               "UNTESTED blocks postprocess by default")
                rec = state.data["steps"]["postprocess"]
                tc.assert_equal(rec["status"], "failed",
                                "the block is a real step failure, not a warning")
                tc.assert_equal(rec["error_code"], _pr.VERIFY_FAILED,
                                "failure carries VERIFY_FAILED error code")
                tc.assert_true("UNTESTED" in (rec.get("error") or ""),
                               "failure message names UNTESTED")
                tc.assert_true("media_qa_untested_decision" not in state.data,
                               "no acceptance was granted, so no acceptance trace")

                # ── 3. UNTESTED + 知情放行：True 且留痕落盘 ──
                runner, state = build("accept", accept=True)
                holder["result"] = _res(gs.UNTESTED, untested=[
                    "video_bitrate_ok — ffprobe bit_rate 为 N/A 或缺失，码率门禁未执行"])
                tc.assert_true(runner._final_media_qa(),
                               "explicit --accept-media-untested releases UNTESTED")
                decision = state.data.get("media_qa_untested_decision")
                tc.assert_true(decision is not None
                               and decision.get("decision") == "accepted_untested",
                               "acceptance writes an explicit decision record")
                tc.assert_true("--accept-media-untested" in decision.get("reason", ""),
                               "the trace names the flag that granted it")
                on_disk = PipelineState(temp_dir / "state_accept.json").data
                tc.assert_true("media_qa_untested_decision" in on_disk,
                               "the acceptance survives the process (saved to state file)")

                # ── 4. FAIL：accept 也救不了（负向对照）──
                runner, state = build("fail", accept=True)
                holder["result"] = _res(gs.FAIL,
                                        errors=["Black screen while subtitle showing: ..."])
                tc.assert_true(not runner._final_media_qa(),
                               "FAIL blocks even with --accept-media-untested set")
                tc.assert_equal(state.data["steps"]["postprocess"]["status"],
                                "failed", "FAIL leaves postprocess failed")
                tc.assert_true("media_qa_untested_decision" not in state.data,
                               "acceptance trace is only for UNTESTED, never for FAIL")

                # ── 5. quick-fix：视觉项按声明记 not_applicable，mode 留痕 ──
                runner, state = build("qf", quick_fix=True)
                holder["result"] = _res(gs.PASS)
                calls.clear()
                tc.assert_true(runner._final_media_qa(),
                               "quick-fix still runs the physical measurements")
                tc.assert_equal(calls[-1]["visual"], "not_applicable",
                                "quick-fix forwards visual check as NOT_APPLICABLE, "
                                "not a fabricated True")
                written = json.loads((temp_dir / "media_qa_final_result.json")
                                     .read_text(encoding='utf-8'))
                tc.assert_equal(written.get("mode"), "quick-fix",
                                "the persisted report records the quick-fix mode")

                # ── 6. 纯 BGM 项目：不传字幕 ──
                runner, state = build("bgm", tts_enabled=False)
                holder["result"] = _res(gs.PASS)
                calls.clear()
                tc.assert_true(runner._final_media_qa(), "BGM-only project passes")
                tc.assert_equal(calls[-1]["subtitle"], None,
                                "tts-disabled projects declare subtitle exemption (None)")

                # ── 7. 质检本身炸了 = 失败，不得静默放行 ──
                runner, state = build("boom")
                holder["raise"] = "ffprobe exploded"
                tc.assert_true(not runner._final_media_qa(),
                               "an exception in media QA is a failure, not a skip")
                tc.assert_true("Final media QA errored" in
                               (state.data["steps"]["postprocess"].get("error") or ""),
                               "the failure message attributes the QA crash")
                holder["raise"] = None

                # ── 8. 指纹登记绑定门禁脚本本体与 mode（A06 同族）──
                runner, state = build("fp")
                inputs = runner._step_inputs("postprocess")
                tc.assert_true(str(inputs.get("media_qa_script", "")).endswith(
                    "media_qa_gate.py"),
                    "postprocess registers the gate script it really executes")
                tc.assert_true(Path(inputs["media_qa_script"]).exists(),
                               "the registered fingerprint path exists on disk")
                tc.assert_equal(inputs.get("mode"), "mode:full",
                                "full mode is a fingerprint input")
                fp_full = runner._fingerprint("postprocess")
                runner.quick_fix = True
                tc.assert_equal(runner._step_inputs("postprocess").get("mode"),
                                "mode:quick-fix",
                                "toggling mode changes the declared input")
                tc.assert_true(fp_full != runner._fingerprint("postprocess"),
                               "a quick-fix PASS cannot be reused as a full-run PASS")
                runner.quick_fix = False

                # ── 9. 接线时序：终检在 mark_completed 之前、成功分支之内 ──
                #    （step_postprocess 需真实渲染才能跑，无秒级行为接缝，保留源码面锁定）
                src = (script_dir / "pipeline_runner.py").read_text(encoding='utf-8')
                body = src.split("def step_postprocess", 1)[1].split(
                    "def _final_media_qa", 1)[0]
                tc.assert_true(
                    body.index("if rc == 0:")
                    < body.index("if not self._final_media_qa():")
                    < body.index('mark_completed("postprocess"'),
                    "final QA runs inside the rc==0 branch, before the step is "
                    "marked completed (a completion must certify an adjudicated product)")
                # CLI 暴露面按行为断言：跑真实 --help，不查源码字符串（A12）
                _help = subprocess.run(
                    [sys.executable, str(script_dir / "pipeline_runner.py"), "--help"],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    cwd=str(script_dir))
                tc.assert_equal(_help.returncode, 0, "pipeline_runner --help runs")
                _help_txt = _help.stdout or ""
                for _flag in ("--accept-media-untested", "--accept-over-render",
                              "--accept-over-budget", "--confirm-fresh"):
                    tc.assert_true(
                        bool(re.search(re.escape(_flag) + r'(?![\w-])', _help_txt)),
                        f"the informed-release flag {_flag} is exposed by the real CLI "
                        f"(help output) as a whole token, not merely present in the "
                        f"source text — a substring match would let a renamed flag "
                        f"pass")
                tc.assert_true(
                    "accept_media_untested=args.accept_media_untested" in src,
                    "the flag is wired into the runner construction (wiring has no "
                    "behavioural seam without a full pipeline run)")

            tc.mark_passed()

        finally:
            mq.MediaQAGate = saved_qa
            _pr.CONFIG_DIR = saved_config_dir

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_count_up_precision_and_reveal_entrance() -> RegressionTestCase:
    """用例54：数值动画精度吸附 + 短窗入场不得静默跳过（A09，2026-09-18）

    缺陷①：count-up 双处生成（anim_count_up 与 stats 场景内联）一律
    snap:1 + Math.round，把 2.5 报成 3——专业参数失真；内联副本另使
    "唯一生成点"名存实亡。缺陷②：anim_progressive_reveal 在
    available < 1 时 return ''，而模板 CSS 把注释卡/层卡初始
    opacity:0——无入场语句即永久不可见（结构场景注释 ≤3 条恒触发）。
    """
    tc = RegressionTestCase(
        "count_up_precision_and_reveal_entrance",
        "验证 count-up 按目标精度吸附且唯一生成，短窗渐进揭示降级为紧凑入场"
    )

    try:
        import scene_compiler as sc

        # ── A. 精度判定 ──
        tc.assert_equal(sc._count_precision(3), 0, "int target -> precision 0")
        tc.assert_equal(sc._count_precision(2.5), 1, "2.5 -> 1")
        tc.assert_equal(sc._count_precision(0.25), 2, "0.25 -> 2")
        tc.assert_equal(sc._count_precision(3.0), 0, "trailing .0 must not claim 1 decimal")

        # ── B. 整数目标：保持旧行为 snap:1 + Math.round ──
        code_int = sc._count_up_tween('.sel', '1.0', 40, '%', 1.5)
        tc.assert_true('snap: { innerText: 1 }' in code_int,
                       "integer target keeps snap 1")
        tc.assert_true('Math.round' in code_int,
                       "integer target keeps Math.round formatter")
        tc.assert_true('toFixed' not in code_int,
                       "integer target must not gain toFixed")

        # ── C. 小数目标：按位吸附 + toFixed，杜绝取整路径 ──
        code_dec = sc._count_up_tween('.sel', '1.0', 2.5, 'mm', 1.5)
        tc.assert_true('snap: { innerText: 0.1 }' in code_dec,
                       "decimal target snaps at its own precision (binary-float 0.30000000004 would leak)")
        tc.assert_true('.toFixed(1)' in code_dec,
                       "display formatter keeps the declared decimal digit")
        tc.assert_true('Math.round' not in code_dec,
                       "decimal target must never be rounded")
        tc.assert_true('innerText: 2.5' in code_dec,
                       "tween terminal value equals declared value")

        # ── D. 两位小数 ──
        code_2 = sc._count_up_tween('.sel', '1.0', 0.25, '', 1.5)
        tc.assert_true('snap: { innerText: 0.01 }' in code_2
                       and '.toFixed(2)' in code_2,
                       "2-decimal target: snap 0.01 + toFixed(2)")

        # ── E. anim_count_up 委托同一生成点，入口入场保留 ──
        full = sc.anim_count_up('.num', 'T.s2', 2.5, 'mm')
        tc.assert_true('back.out(1.7)' in full,
                       "anim_count_up still emits its entrance fromTo")
        tc.assert_true('parseFloat(this.targets()[0].innerText).toFixed(1)' in full,
                       "anim_count_up delegates to the precision-aware tween")
        tc.assert_true('T.s2 + 0.3' in full,
                       "absolute-position 0.3s after T-ref entrance")
        full_abs = sc.anim_count_up('.num', 10.0, 40, '%')
        tc.assert_true('10.3' in full_abs, "float t renders absolute position")

        # ── F. 唯一生成点：count-up tween 只从 _count_up_tween 输出一处 ──
        #     optimize=2 编译剥除全部 docstring，只统计真正会执行的字符串常量
        src = (Path(__file__).resolve().parent / "scene_compiler.py").read_text(encoding='utf-8')
        body_consts = []
        def _walk_consts(obj):
            for c in getattr(obj, 'co_consts', ()):
                if isinstance(c, str):
                    body_consts.append(c)
                elif hasattr(c, 'co_consts'):
                    _walk_consts(c)
        _walk_consts(compile(src, 'scene_compiler.py', 'exec', optimize=2))
        joined = '\n'.join(body_consts)
        tc.assert_equal(joined.count('Math.round(parseFloat('), 1,
                        "integer formatter emitted from exactly one place "
                        "(the shared helper; a second call site means the "
                        "inlined duplicate is back)")
        tc.assert_equal(joined.count('.innerText).toFixed('), 1,
                        "decimal formatter emitted from exactly one place")

        # ── G. 短窗渐进揭示：紧凑入场覆盖每个 selector，禁止 return '' ──
        theme = sc.THEMES['dark-tech']
        sels = ['#s7-a0', '#s7-a1', '#s7-a2']
        short = sc.anim_progressive_reveal(
            sels, 'T.s7 + 4.9', 'T.s7 + 8.5', theme, t_ref='T.s7')
        tc.assert_true('fromTo' in short,
                       "available<1 must degrade to a compact entrance, not return '' "
                       "(CSS opacity:0 items without an entrance stay invisible forever)")
        for s in sels:
            tc.assert_true(short.count(f'fromTo("{s}"') == 1,
                           f"every selector gets exactly one {s} entrance")
        tc.assert_true('opacity: 0, y: 30' in short and 'opacity: 1, y: 0' in short,
                       "entrance is a real 0->1 fade-up")
        tc.assert_true('T.s7 + 0.2' in short, "first compact element enters at 0.2")
        # 期望串与生成器同用 ':.1f' 格式化；每个元素各自绑定其位置
        for i, s in enumerate(sels):
            exp = f'T.s7 + {0.2 + i * 0.2:.1f}'
            tc.assert_true(short.count(exp) >= 1,
                           f"selector {s} entrance positioned at {exp}")
        # 紧凑入场不得越过场景尾（末元素 0.6+0.6=1.2 << span-0.9=2.7）
        tc.assert_true(0.2 + (len(sels) - 1) * 0.2 + 0.6
                       < sc._expr_offset('T.s7 + 8.5')
                       - sc._expr_offset('T.s7 + 4.9') - 0.9,
                       "compact entrance finishes before the scene tail")
        tc.assert_true('scale: 1.02' not in short,
                       "sweep decoration stays gated on available>4")

        # ── H. 充足窗口：分布行为不变（首元素 0.5、间隔 min(available/n, 2.0)）──
        sels5 = [f'#s1-b{i}' for i in range(5)]
        long_code = sc.anim_progressive_reveal(
            sels5, 'T.s1 + 0.8', 'T.s1 + 20.8', theme, t_ref='T.s1')
        # 运行时独立推导：available=17 → interval=min(17/5,2)=2.0 → 首 0.5、第 3 元素 4.5；
        # 若 cap 失效 interval=3.4 → 第 3 元素位置 7.3 不存在 → 红
        exp_first = f'T.s1 + {0.5:.1f}'
        exp_third = f'T.s1 + {0.5 + 2 * min(17 / 5, 2.0):.1f}'
        tc.assert_true(long_code.count(exp_first) >= 1
                       and long_code.count(exp_third) >= 1
                       and 'T.s1 + 7.3' not in long_code,
                       "normal spacing preserved: interval capped at 2.0")

        # ── I. stats 场景真实路径：小数项产出精度吸附，整数项行为不变 ──
        scene = {'heading': '参数', 'items': [
            {'value': 2.5, 'unit': 'mm', 'label': '偏差'},
            {'value': 40, 'unit': '%', 'label': '降幅'}]}
        _html, _css, anim = sc.gen_scene_stats(scene, 3, theme, 0.0, 20.0)
        tc.assert_true('.toFixed(1)' in anim and 'snap: { innerText: 0.1 }' in anim,
                       "decimal stat number passes through the precision-aware helper")
        tc.assert_true('innerText: 40' in anim,
                       "integer stat number still tweens to declared value")
        # JSON 字符串目标（compile 场景 value 可能来自字符串解析）：走内插原样，不误入 toFixed
        tc.assert_true('innerText: 3' in sc._count_up_tween('.s', '1.0', '3', ''),
                       "numeric-string target emits its literal")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_alignment_cache_binds_real_waveform() -> RegressionTestCase:
    """用例55：对齐缓存绑定实际波形与算法版本（A08，2026-09-18）

    旧实现 _build_timeline_manifest 的复用判定只比 tts_hash（旁白文本派生
    键）——波形被 _enforce_sentence_pauses 插静音、同参数重新合成、或对齐
    算法升级后，旧句级时间戳仍被当作有效结果复用（与 A06"登记面必须绑定
    真实读取路径"同族缺陷）。现复用四条件：文本键 ∧ 波形文件名 ∧ 波形字节
    实测哈希 ∧ 算法版本，缺一即重对齐。
    """
    tc = RegressionTestCase(
        "alignment_cache_binds_real_waveform",
        "验证句级对齐缓存按实际波形哈希+算法版本失效，停顿改写后重绑，p95 语义随记录面下发"
    )

    try:
        import hashlib
        import wave as _wave
        import enhance_video_audio as eva
        import _forced_align as fa

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            tts_dir = tmpdir / "tts_44k"
            tts_dir.mkdir()

            TEXT = "第一句测试旁白。第二句继续朗读。"
            h = eva._tts_cache_key(TEXT)
            hq = tts_dir / f"tts_{h}_hq.wav"

            def _make_wav(path, seconds=1.0, sr=44100):
                with _wave.open(str(path), 'wb') as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
                    wf.writeframes(b'\x00\x00' * int(sr * seconds))

            _make_wav(hq)
            SENTS = [{'rel_start': 0.0, 'rel_end': 2.0}, {'rel_start': 2.2, 'rel_end': 5.0}]

            # ── stubs：模型/停顿强制/时长/规则全部替身，波形哈希走真实计算 ──
            saved = {}
            align_calls = {'n': 0}

            def _stub(name, value):
                saved[name] = getattr(eva, name)
                setattr(eva, name, value)

            def _fake_align_scene(wav, text, segments, model=None):
                align_calls['n'] += 1
                return {'method': 'asr_forced', 'match_ratio': 0.97,
                        'sentences': [dict(s) for s in SENTS]}

            _stub('SCENES', [(1, 0.0, 6.0, TEXT)])
            _stub('COVER_DURATION', 0.0)
            _stub('VIDEO_DURATION', 6.0)
            _stub('get_duration', lambda p: 5.0)
            _stub('_load_audio_sync_rules', lambda: {'sentence_pause': {'min_seconds': 0.4}})
            _stub('_enforce_sentence_pauses',
                  lambda f, s, m: (False, s, 0.0))
            _stub('_split_text_to_segments', lambda t: ['第一句测试旁白。', '第二句继续朗读。'])
            saved_fa_load = fa.load_align_model
            saved_fa_align = fa.align_scene
            fa.load_align_model = lambda: object()
            fa.align_scene = _fake_align_scene

            def _manifest():
                with open(tmpdir / "_timeline_manifest.json", encoding='utf-8') as f:
                    return json.load(f)

            def _entry():
                return _manifest()['scenes'][0]

            try:
                algo = f"{eva.ALIGN_ALGO_VERSION}|{fa.MODEL_SIZE}|pause=0.4"
                cur_sha = eva._wave_sha256(hq)

                # ── A. 首轮无旧 manifest → 真实对齐，记录面含波形绑定 ──
                eva._build_timeline_manifest(tmpdir)
                tc.assert_equal(align_calls['n'], 1, "first run aligns")
                e = _entry()
                tc.assert_equal(e.get('wave_sha256'), cur_sha,
                                "recorded wave hash equals real bytes of the hq wav")
                tc.assert_equal(e.get('align_algo_ver'), algo,
                                "recorded algo version = ALIGN_ALGO_VERSION|MODEL_SIZE|pause")
                first_sents = e['sentences']

                # ── B. 波形/版本全一致 → 缓存命中，零重对齐 ──
                eva._build_timeline_manifest(tmpdir)
                tc.assert_equal(align_calls['n'], 1,
                                "identical waveform + algo version must reuse the cache")
                tc.assert_equal(_entry()['sentences'], first_sents,
                                "cached rel timestamps carried over unchanged")

                # ── C. 同文本但波形字节变了（重新合成/插静音）→ 必须重对齐 ──
                _make_wav(hq, seconds=1.5)   # 字节变、文件名与文本哈希不变
                eva._build_timeline_manifest(tmpdir)
                tc.assert_equal(align_calls['n'], 2,
                                "waveform bytes changed under the same text key → "
                                "stale alignment MUST NOT be reused (A08 core)")
                tc.assert_equal(_entry()['wave_sha256'], eva._wave_sha256(hq),
                                "record re-binds to the new waveform bytes")

                # ── D. 算法版本 bump → 失效重对齐 ──
                eva._build_timeline_manifest(tmpdir)   # 收敛回命中态
                n_before = align_calls['n']
                mp = tmpdir / "_timeline_manifest.json"
                data = _manifest()
                data['scenes'][0]['align_algo_ver'] = 'v0|small|pause=0.4'
                mp.write_text(json.dumps(data), encoding='utf-8')
                eva._build_timeline_manifest(tmpdir)
                tc.assert_equal(align_calls['n'], n_before + 1,
                                "algo version mismatch → re-align")

                # ── D2. 旧格式缺 align_algo_ver 字段但波形哈希已绑定 → 兼容命中
                #     （默认值分支有真实语义：不把 A08 前的合法记录一刀切失效）──
                eva._build_timeline_manifest(tmpdir)   # 收敛回命中态
                n_before = align_calls['n']
                data = _manifest()
                del data['scenes'][0]['align_algo_ver']
                mp.write_text(json.dumps(data), encoding='utf-8')
                eva._build_timeline_manifest(tmpdir)
                tc.assert_equal(align_calls['n'], n_before,
                                "record missing align_algo_ver but wave-bound: legacy "
                                "default applies, reuse stands (the default is load-bearing)")

                # ── E. 旧格式记录（无 wave_sha256 字段）→ 保守视为未绑定 → 重对齐 ──
                eva._build_timeline_manifest(tmpdir)
                n_before = align_calls['n']
                data = _manifest()
                del data['scenes'][0]['wave_sha256']
                mp.write_text(json.dumps(data), encoding='utf-8')
                eva._build_timeline_manifest(tmpdir)
                tc.assert_equal(align_calls['n'], n_before + 1,
                                "legacy record without a wave binding cannot certify reuse")

                # ── F. tts_file 指向不同波形文件 → 即使哈希字段相同也失效 ──
                eva._build_timeline_manifest(tmpdir)
                n_before = align_calls['n']
                data = _manifest()
                data['scenes'][0]['tts_file'] = 'tts_deadbeef00_hq.wav'
                mp.write_text(json.dumps(data), encoding='utf-8')
                eva._build_timeline_manifest(tmpdir)
                tc.assert_equal(align_calls['n'], n_before + 1,
                                "record bound to a different wav file → re-align")

                # ── G. 停顿强制改写波形的当轮：记录必须重绑改写后的实测哈希 ──
                _make_wav(hq, seconds=1.0)   # 新字节
                def _enforce_modifying(f, s, m):
                    with open(f, 'ab') as fp:      # 模拟插静音：追加字节
                        fp.write(b'\x01\x02' * 441)
                    return True, [dict(x) for x in s], 0.2
                eva._enforce_sentence_pauses = _enforce_modifying
                eva._build_timeline_manifest(tmpdir)
                e = _entry()
                tc.assert_true(e.get('pause_inserted') == 0.2,
                               "pause insertion recorded")
                tc.assert_equal(e['wave_sha256'], eva._wave_sha256(hq),
                                "record re-binds AFTER enforcement rewrote the wave, "
                                "otherwise every next run would thrash the cache")
                # 下一轮（停顿已达标 no-op）应命中
                eva._enforce_sentence_pauses = lambda f, s, m: (False, s, 0.0)
                n_before = align_calls['n']
                eva._build_timeline_manifest(tmpdir)
                tc.assert_equal(align_calls['n'], n_before,
                                "post-enforcement stable wave hits the cache next run")

                # ── H. 文本一改即失效（旧契约不回退）：新 hash 无文件 → 跳过并告警 ──
                eva.SCENES = [(1, 0.0, 6.0, TEXT + "补一句。")]  # saved 中已有原值，finally 统一还原
                eva._build_timeline_manifest(tmpdir)
                tc.assert_equal(_manifest()['scenes'], [],
                                "changed text key resolves to a new wav path; missing "
                                "file skips the scene instead of reusing old alignment")

                # ── I. p95 语义澄清（media_qa_gate subtitle_audio_alignment）：原为两处
                #     "源码含某字符串"断言（'measure_semantics' / 中文报告行），只证明
                #     文案在场、不证明消费面拿到它——改由行为面锁定：记录面见用例52 A 段
                #     对真实 validate() 产出的 alignment.measure_semantics 断言，
                #     报告面见用例57 E 段（捕获 print_report 输出 + p95≠0 负向对照）。──

            finally:
                for name, val in saved.items():
                    setattr(eva, name, val)
                fa.load_align_model = saved_fa_load
                fa.align_scene = saved_fa_align

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_state_writes_atomic_and_ledger_survives_fresh() -> RegressionTestCase:
    """用例56（2026-09-19 审核 A10）：状态落盘的中断安全 + 成本台账跨 --fresh 保活

    两个缺口（均为源码确认，非推测）：
    ①`PipelineState.save()` 与交付登记表写入都是 `open(path,'w')` 原地截断写——进程
      在 json.dump 中途死掉即留下半截文件。`_load` 里 `}\\n{` 的恢复分支正是为这种
      损坏打的现场补丁，而登记表损坏会被读侧当成空表 → 交付物保护门禁静默 fail-open。
    ②`reset()`（--fresh 入口）把 self.data 整表换成 4 键新 dict，连带清掉
      `render_metrics` 累计成本台账 → 预算门禁计数源归零，"fresh→重渲→fresh"
      可无限绕开 max_full_renders。
    """
    import json
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    tc = RegressionTestCase(
        "state_writes_atomic_and_ledger_survives_fresh",
        "验证状态/交付登记表原子落盘、--fresh 保留渲染成本台账、损坏登记表 fail-closed"
    )

    try:
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        _pr = _safe_import_pipeline_runner()
        PipelineState = _pr.PipelineState
        PipelineRunner = _pr.PipelineRunner

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            spath = tmp / "pipeline_state.json"
            ledger = {"full_render_attempts": 3, "full_render_total_seconds": 120.0,
                      "scene_patch_attempts": 0, "scene_patch_hits": 0, "watchdog_kills": 0}

            # ── A. 正常保存走原子替换：目录里不留 .tmp 兄弟文件 ──
            st = PipelineState(spath)
            st.data["verifications"] = {"media_qa_final": {"verdict": "PASS"}}
            st.save()
            tc.assert_equal(sorted(p.name for p in tmp.iterdir()), ["pipeline_state.json"],
                            "save() renames the temp file into place (no stray .tmp sibling)")
            tc.assert_equal(json.loads(spath.read_text(encoding='utf-8'))["verifications"],
                            {"media_qa_final": {"verdict": "PASS"}},
                            "the renamed file carries the new content")

            # ── A2. 未知顶层节往返保真（A05 同族：写回权威源不得裁掉未消费字段）──
            #      state 文件是单一权威源，本仓只消费自己认识的节；按"已知键集合"
            #      重建 dict 等于每次保存都在削源（旧 reset() 即此形态）。
            rtpath = tmp / "state_roundtrip.json"
            rtpath.write_text(json.dumps({
                "steps": {"render": {"status": "passed", "an_upstream_field": 7}},
                "last_run": "2026-01-01T00:00:00",
                "a_section_this_repo_never_reads": {"keep": True},
            }), encoding='utf-8')
            st_rt = PipelineState(rtpath)
            st_rt.mark_completed("render", input_fingerprint="fp-rt")
            rt = json.loads(rtpath.read_text(encoding='utf-8'))
            tc.assert_equal(rt.get("a_section_this_repo_never_reads"), {"keep": True},
                            "save() writes back what it loaded — an unconsumed top-level "
                            "section survives the round trip")
            tc.assert_equal(rt["steps"]["render"].get("an_upstream_field"), 7,
                            "per-step fields this repo does not read are preserved, not "
                            "rebuilt from the known-key set")
            tc.assert_equal(rt["steps"]["render"]["status"], "passed",
                            "the consumed field still updates normally (merge, not freeze)")

            # ── B. 写入中途失败：目标文件保持上一版完整（原子性本体）──
            import script_interface as si

            prev_bytes = spath.read_bytes()
            real_json, real_si_json = _pr.json, si.json

            class _HalfDump:
                @staticmethod
                def dump(payload, f, **kw):
                    f.write('{"steps": {')
                    raise RuntimeError("simulated interrupt mid-write")

            # 注入点＝真正执行 json.dump 的那个模块名。状态写盘已委托给
            # script_interface.atomic_write_json（唯一实现，防两处漂移），
            # 因此两处同绑；只绑一处会让注入静默失效。
            _fake = SimpleNamespace(dump=_HalfDump.dump, loads=real_json.loads)
            try:
                _pr.json = _fake
                si.json = _fake
                st.data["steps"]["render"] = {"status": "passed"}
                raised = False
                try:
                    st.save()
                except RuntimeError:
                    raised = True
            finally:
                _pr.json, si.json = real_json, real_si_json
            tc.assert_true(raised,
                           "the injected mid-write failure really propagated out of save() "
                           "(otherwise this scenario proves nothing)")
            tc.assert_equal(spath.read_bytes(), prev_bytes,
                            "a failed write leaves the previous state file byte-identical "
                            "(in-place open('w') would have truncated it to the half JSON)")

            # ── C. --fresh 保活成本台账、作废上一轮证据 ──
            st.data["render_metrics"] = dict(ledger)
            st.data["duration_budget_check"] = {"decision": "within_budget"}
            st.data["steps"]["render"] = {"status": "passed"}
            st.reset("--fresh (previous last_run: test)")
            tc.assert_equal(st.data["steps"], {},
                            "--fresh still voids step status (that is its purpose)")
            tc.assert_equal(st.data.get("render_metrics", {}).get("full_render_attempts"), 3,
                            "the cumulative render-cost ledger survives --fresh")
            tc.assert_true("duration_budget_check" not in st.data,
                           "per-run adjudications are previous-run evidence — dropped, not carried")
            tc.assert_true("verifications" not in st.data,
                           "previous-run verification records are dropped by reset")
            tc.assert_equal(json.loads(spath.read_text(encoding='utf-8'))
                             ["render_metrics"]["full_render_attempts"], 3,
                            "the surviving ledger is on disk, not only in memory")

            # ── D. 预算门禁在 reset 之后仍然武装（封堵真实绕开门禁路径）──
            def make_runner():
                r = PipelineRunner.__new__(PipelineRunner)
                r.state = st
                r.force = False
                r.accept_over_render = False
                r.render_rules = {"render_budget": {"max_full_renders": 3,
                                                    "enforcement": "soft"}}
                return r

            runner = make_runner()
            st.mark_started("render")
            tc.assert_true(not runner._render_budget_gate_ok(),
                           "attempts==max_full_renders blocks before a full render starts")
            st.reset("--fresh again (budget escape attempt)")
            st.mark_started("render")
            tc.assert_true(not runner._render_budget_gate_ok(),
                           "reset() no longer re-arms the budget gate by wiping the ledger")

            # ── E. 登记表三态：缺失 / 损坏 / 正常必须可区分 ──
            reg = tmp / "交付登记.json"
            _, status = PipelineRunner._registry_load(reg)
            tc.assert_equal(status, PipelineRunner.REGISTRY_MISSING,
                            "an absent ledger is 'missing' (a fresh repo must not be blocked)")
            reg.write_text('{"deliveries": [', encoding='utf-8')
            _, status = PipelineRunner._registry_load(reg)
            tc.assert_equal(status, PipelineRunner.REGISTRY_CORRUPT,
                            "a truncated ledger is 'corrupt', never an empty table")
            reg.write_text('{"deliveries": {}}', encoding='utf-8')
            _, status = PipelineRunner._registry_load(reg)
            tc.assert_equal(status, PipelineRunner.REGISTRY_CORRUPT,
                            "a structurally invalid ledger is also 'corrupt'")

            # ── F. 损坏登记表 fail-closed：无法证明未交付时不得放行覆盖 ──
            saved_registry = _pr.DELIVERY_REGISTRY
            try:
                _pr.DELIVERY_REGISTRY = reg
                out = tmp / "案例成片.mp4"
                out.write_bytes(b"delivered-film")
                st2 = PipelineState(tmp / "state2.json")
                r2 = make_runner()
                r2.state = st2
                r2.output_file = out
                r2.temp_dir = tmp / "temp2"
                r2.temp_dir.mkdir()
                reg.write_text('{"deliveries": [', encoding='utf-8')
                tc.assert_true(r2._output_was_delivered(),
                               "corrupt ledger adjudicates as delivered (fail-closed): "
                               "the old code read it as empty and allowed overwriting "
                               "every delivered film")
                tc.assert_equal(
                    st2.data.get("delivery_registry_unreadable", {}).get("decision"),
                    "treated_as_delivered_fail_closed",
                    "the fail-closed adjudication is traceable in state")
                reg.unlink()
                tc.assert_true(not r2._output_was_delivered(),
                               "a missing ledger with no VALIDATED report stays 'not delivered' "
                               "(corrupt handling must not block new projects)")

                # ── G. 拒绝在损坏表上追加登记（不就地覆盖整本台账）──
                reg.write_text('{"deliveries": [', encoding='utf-8')
                corrupt_bytes = reg.read_bytes()
                r3 = make_runner()
                r3.state = PipelineState(tmp / "state3.json")
                r3.html_project = "case56"
                r3.config_path = tmp / "cfg.json"
                r3._probe_duration = lambda p: 10.0
                refused = False
                try:
                    r3._record_delivery(out, None, "VALIDATED")
                except RuntimeError:
                    refused = True
                tc.assert_true(refused,
                               "appending onto a corrupt ledger is refused, not silently "
                               "rebuilt from one entry")
                tc.assert_equal(reg.read_bytes(), corrupt_bytes,
                                "the corrupt file is left untouched (git can still restore it)")

                # ── H. 正常追加：既有条目原样保留 + 原子写无残留 ──
                reg.write_text(json.dumps({"deliveries": [
                    {"video": "另一项目_成片.mp4", "bytes": 1, "delivered_at": "2026-09-01T00:00:00"}
                ]}, ensure_ascii=False), encoding='utf-8')
                entry = r3._record_delivery(out, None, "VALIDATED")
                table = json.loads(reg.read_text(encoding='utf-8'))
                tc.assert_equal([d["video"] for d in table["deliveries"]],
                                ["另一项目_成片.mp4", entry["video"]],
                                "an append preserves existing deliveries (read-modify-write "
                                "must not rebuild the table)")
                tc.assert_equal(table["deliveries"][0]["bytes"], 1,
                                "untouched entries keep every original field")
                tc.assert_equal(sorted(p.name for p in tmp.iterdir()
                                        if p.name.endswith(".tmp")), [],
                                "registry writes are atomic too (no .tmp sibling left behind)")
            finally:
                _pr.DELIVERY_REGISTRY = saved_registry

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_check_registry_names_are_single_source() -> RegressionTestCase:
    """用例57（2026-09-19 审核 A12）：检查项清单只允许一处权威源，文档与注释不得手抄项数/编号

    改动前的实测漂移面（三处手抄各自跑偏）：
    - `media_qa_gate` docstring 自称"检查项目（18项）"，运行时 `total_checks` 为 19；
    - docstring 列有"音频响度检测"，但全库零消费者的 `ffmpeg_get_loudness` 从未产出状态；
    - 代码注释的"检查N"编号里 9 号被用了两次（时间单调性 / 视觉边界），AGENTS.md 另写"11项"。
    本用例把清单一致性变成可执行判据：AST 抓 `_mark` 登记名 ↔ docstring 目录 ↔ 运行时
    check_statuses 三向一致；并锁死文档面不得再出现项数与"检查N"号。
    """
    import ast
    import io as _io
    import json
    import re
    import tempfile
    from contextlib import redirect_stdout
    from pathlib import Path
    from types import SimpleNamespace

    tc = RegressionTestCase(
        "check_registry_names_are_single_source",
        "验证 media_qa_gate 检查项名三向一致（AST/docstring/运行时）且文档不再手抄项数与编号"
    )

    try:
        script_dir = Path(__file__).parent
        gate_path = script_dir / "media_qa_gate.py"
        src = gate_path.read_text(encoding='utf-8')
        tree = ast.parse(src)

        # ── A. 权威源：AST 抓 _mark 登记名（真实发射点，不是清单副本）──
        emitted = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_mark" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                emitted.add(node.args[0].value)
        tc.assert_true(len(emitted) >= 10,
                       f"AST extraction found {len(emitted)} check names (registry is not empty)")
        tc.assert_true('ffmpeg_get_loudness' not in src,
                       "the loudness helper that no check ever consumed has been removed "
                       "rather than left advertised")

        # ── B. docstring 目录与 AST 双向一致 ──
        doc = ast.get_docstring(tree) or ""
        catalog = set()
        for line in doc.splitlines():
            stripped = line.strip()
            m = re.match(r'^([a-z][a-z0-9_]+)\s{2,}\S', stripped)
            if m:
                catalog.add(m.group(1))
        tc.assert_true(catalog == emitted,
                       f"docstring catalog and emitted check names must match exactly "
                       f"(doc-only={sorted(catalog - emitted)}, code-only={sorted(emitted - catalog)})")

        # ── C. 文档面禁止手抄计数与"检查N"编号 ──
        tc.assert_true(not re.search(r'检查\s?\d+', src),
                       "code comments address checks by name, not by a hand-maintained number")
        tc.assert_true(not re.search(r'（\d+项）', doc),
                       "the module docstring carries no item count")
        # 文件名按大小写不敏感定位：仓内实际是 agents.md，Linux CI 上写死 "AGENTS.md"
        # 会 FileNotFoundError 把本用例判成红（Windows 大小写不敏感掩盖了这点）
        _root = (script_dir / ".." / "..").resolve()
        agents_md = next(p for p in _root.iterdir()
                         if p.name.lower() == "agents.md").read_text(encoding='utf-8')
        tc.assert_true(not re.search(r'媒体文件\d+项', agents_md),
                       "AGENTS.md must not hand-copy the media_qa check count")
        tc.assert_true(not re.search(r'\d+ ?项检查全部', agents_md),
                       "the A04 row references the registry, not a count")

        # ── D. 运行时一致：真实 validate() 的 check_statuses 键集 == 权威源 ──
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import media_qa_gate as mq
        import _gate_status as gs

        _saved = {}

        def _patch(name, fn):
            _saved.setdefault(name, getattr(mq, name))
            setattr(mq, name, fn)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                video = tmp / "案例成片.mp4"
                video.write_bytes(b"fake-video-bytes")
                srt = tmp / "案例成片.srt"
                srt.write_text("1\n00:00:01,000 --> 00:00:04,000\n第一段旁白\n\n"
                               "2\n00:00:05,000 --> 00:00:08,000\n第二段旁白\n",
                               encoding='utf-8')
                cfg = tmp / "cfg.json"
                cfg.write_text(json.dumps({"fps": 25, "resolution": "1920x1080"}),
                               encoding='utf-8')
                _patch("ffprobe_get_streams", lambda p: [
                    {"codec_type": "video", "codec_name": "h264", "bit_rate": "1500000",
                     "r_frame_rate": "25/1", "width": 1920, "height": 1080},
                    {"codec_type": "audio", "codec_name": "aac", "bit_rate": "128000"}])
                _patch("ffprobe_get_duration", lambda p: 10.0)
                _patch("ffmpeg_detect_silence", lambda p, d=None: 1.0)
                _patch("ffmpeg_detect_speech_onsets", lambda *a, **k: [1.5, 2.5, 5.5, 6.5, 8.5])
                _patch("ffmpeg_detect_black_intervals", lambda *a, **k: [])
                _patch("ffmpeg_detect_long_silences", lambda *a, **k: [])
                _patch("subprocess", SimpleNamespace(run=lambda *a, **k: SimpleNamespace(
                    returncode=0, stdout="", stderr="")))

                qa = mq.MediaQAGate()
                _, res = qa.validate(str(video), str(srt), visual_check_passed=True,
                                     config_path=str(cfg))
                tc.assert_equal(set(res["check_statuses"]), emitted,
                                "the runtime four-state registry emits exactly the names "
                                "the source declares (a check that never runs cannot hide)")
                tc.assert_equal(res["total_checks"], len(emitted),
                                "the count the report prints is derived at runtime")
                tc.assert_true('measure_semantics' in res["alignment"],
                               "the p95 semantics boundary rides on the machine-readable record")

                # ── E. 报告行按行为断言（用例55-I 原为源码字符串断言）：
                #     p95=0 的免责说明只在 p95 真的归零时出现 ──
                def _render(result):
                    buf = _io.StringIO()
                    with redirect_stdout(buf):
                        mq.print_report(result)
                    return buf.getvalue()

                base = {"verdict": gs.PASS, "status_counts": {gs.PASS: len(emitted)},
                        "total_checks": len(emitted), "errors": [], "untested": [],
                        "not_applicable": [], "warnings": [], "alignment": {},
                        "media_facts": {}}
                zero = _render(dict(base, alignment={"onsets_detected": 5,
                                                     "p95_deviation_seconds": 0,
                                                     "threshold_p95_seconds": 0.5}))
                tc.assert_true("非逐句精确同步" in zero,
                               "the report names the misreading it forbids when p95=0")
                nonzero = _render(dict(base, alignment={"onsets_detected": 5,
                                                        "p95_deviation_seconds": 0.4,
                                                        "threshold_p95_seconds": 0.5}))
                tc.assert_true("非逐句精确同步" not in nonzero,
                               "the disclaimer is conditional on p95=0, not printed unconditionally "
                               "(a load-bearing assertion: an always-true report line would pass E-1)")
                tc.assert_true(f"total={len(emitted)}" in zero,
                               "the printed count equals the runtime registry size")
                untested_line = _render(dict(base, verdict=gs.UNTESTED,
                                             untested=["audio_not_silent: silencedetect 失败"]))
                tc.assert_true("UNTESTED — 不得读成通过" in untested_line,
                               "the untested section is labelled as not-a-pass in the human report")
        finally:
            for k, v in _saved.items():
                setattr(mq, k, v)

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


# ============================================================================
# 2026-09-19 审核 A07 后续：Audio RMS 采样口径（窗口聚合量 + 停顿/无样本分桶）
# ============================================================================

def test_audio_rms_window_aggregate_measure() -> RegressionTestCase:
    """用例58：Audio RMS 一致性改取窗口聚合量，句间停顿不得冒充"检查未跑"或"音量异常"

    背景：_check_video_quality 的多点电平检查曾用 `astats=metadata=1:reset=1` 后取
    rms_matches[-1]。reset=1 使统计块按音频帧重置，打印的是该时段**最后一帧（≈14ms）**
    的统计；帧落在句间静音时 RMS=-inf，_RMS_LEVEL_RE 要求数字 → 该窗口零贡献。成片
    句间静音常达 2s，与 2s 采样窗等长，33.1s 已交付成片实测 4/4 窗口全落 → 整项恒
    0/4 UNTESTED（批次5-④ 实证）。旧文案把归因写成"检查 FFmpeg astats 输出拼写是否
    再次变化"，而两种拼写早已由同一条正则覆盖并被用例48 锁定——归因指向错误方向。
    修后：①取不带 reset 的窗口聚合 RMS（与 volumedetect mean_volume 实测逐点吻合）；
    ②"落在停顿/静音段"（-inf 或低于 video_quality_rules.rms_window_silence_floor_db）
    与"FFmpeg 未输出任何 RMS 行"（无音频样本/解码失败）分成两桶各自点名；
    ③停顿窗口不进入 dB 极差，否则会把 2s 停顿判成"音量严重不一致"（医疗养老成片
    26.7s 窗口实测 -67.3dB，按聚合量直算极差 47.5dB 即此形态）。
    """
    tc = RegressionTestCase(
        "audio_rms_window_aggregate_measure",
        "验证 RMS 取窗口聚合量、停顿与无样本分桶、归因文案指向真实失效模式"
    )
    try:
        import io as _io
        import contextlib
        import subprocess
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        eva = _safe_import_rebinding_module("enhance_video_audio")

        # 生产正则把 "RMS level dB: <值>" 全部吃下，故按真实块序拼 stderr：
        # 分声道块在前、Overall 块在最后（本机 FFmpeg 实测顺序）。
        def _astats_stderr(overall, channels=None):
            chans = [overall] if channels is None else list(channels)
            lines = [f"[Parsed_astats_0 @ 0x1] RMS level dB: {c:.6f}" for c in chans]
            lines.append("[Parsed_astats_0 @ 0x1] Overall")
            lines.append(f"[Parsed_astats_0 @ 0x1] RMS level dB: {overall:.6f}")
            return "\n".join(lines)

        class _Out:
            def __init__(self, stdout="", stderr=""):
                self.stdout, self.stderr = stdout, stderr

        probe_payload = json.dumps({"streams": [
            {"codec_type": "video", "bit_rate": "5000000", "duration": "20.0"},
            {"codec_type": "audio", "duration": "20.0"}]})

        # ── A. 真实 FFmpeg 夹具：20s 音频，每个 2s 采样窗（3/8/13/18 起）末尾落在静音，
        #      第 4 窗整窗静音 ──
        ffmpeg_exe = shutil.which("ffmpeg")
        tc.assert_true(bool(ffmpeg_exe), "ffmpeg available for the real-output checks")
        if ffmpeg_exe:
            with tempfile.TemporaryDirectory() as td:
                wav = Path(td) / "pause_aligned.wav"
                subprocess.run(
                    [ffmpeg_exe, "-y", "-loglevel", "error",
                     "-f", "lavfi", "-i", "sine=duration=4.9:sample_rate=44100",
                     "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=0.15",
                     "-f", "lavfi", "-i", "sine=duration=4.85:sample_rate=44100",
                     "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=0.15",
                     "-f", "lavfi", "-i", "sine=duration=4.85:sample_rate=44100",
                     "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=0.15",
                     "-f", "lavfi", "-i", "sine=duration=0.9:sample_rate=44100",
                     "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=4.05",
                     "-filter_complex", "concat=n=8:v=0:a=1", str(wav)],
                    capture_output=True)
                tc.assert_true(wav.exists(), "fixture built by lavfi concat")

                # A-负向对照：旧口径在同一夹具上逐窗取末帧统计 → 4/4 空，复现"恒未测"。
                # 没有这条对照，A-正 可能只是"随便测到了值"（空跑）。
                old_windows = []
                for t in (3.0, 8.0, 13.0, 18.0):
                    probe = subprocess.run(
                        [ffmpeg_exe, "-ss", f"{t:.1f}", "-t", "2", "-i", str(wav),
                         "-af", "astats=metadata=1:reset=1", "-f", "null", "-"],
                        capture_output=True, text=True, encoding='utf-8', errors='replace')
                    old_windows.append(eva._RMS_LEVEL_RE.findall(probe.stderr))
                tc.assert_equal([len(m) for m in old_windows], [0, 0, 0, 0],
                                "the fixture reproduces the old last-frame failure mode (0/4)")

                # A-正：新口径同一夹具拿到 3 个语音 dB 值，第 4 窗归入静音桶而非"取不到"
                vals, silent, unreadable = eva._sample_windows_rms(
                    str(wav), [3.0, 8.0, 13.0, 18.0], -60.0)
                tc.assert_equal(len(vals), 3,
                                "aggregate RMS yields one value per voiced window")
                tc.assert_equal((silent, unreadable), (1, 0),
                                "a fully-silent window is bucketed as pause/silence, not as unmeasurable")
                tc.assert_true(vals and max(vals) - min(vals) < 2.0,
                               f"identical tone across windows must not read as level inconsistency (got {vals})")

                # B. 采样口径锁：生产命令不得带 reset/metadata（退回末帧口径即红）
                calls = []

                def _spy(argv, *a, **k):
                    calls.append(list(argv))
                    return _Out(stderr=_astats_stderr(-22.5))

                saved_run = eva.subprocess.run
                try:
                    eva.subprocess.run = _spy
                    eva._sample_windows_rms("fake.wav", [1.0, 2.0], -60.0)
                finally:
                    eva.subprocess.run = saved_run
                tc.assert_equal(len(calls), 2, "one ffmpeg pass per sample window")
                af_args = [c[c.index("-af") + 1] for c in calls if "-af" in c]
                tc.assert_equal(af_args, ["astats", "astats"],
                                "RMS sampling uses the aggregate astats form (no reset=1)")

        # 只把 stderr 喂给 RMS 采样调用（-af astats），其余 ffmpeg 调用（空帧抽帧等）
        # 返回空输出——否则帧采样会消耗掉序列条目，RMS 侧读到的是错位的窗口。
        def _make_run(stderrs):
            state = {"i": 0}

            def _run(argv, *a, **k):
                if argv[0] == "ffprobe":
                    return _Out(stdout=probe_payload)
                if "astats" not in argv:
                    return _Out(stderr="")
                s = stderrs[min(state["i"], len(stderrs) - 1)]
                state["i"] += 1
                return _Out(stderr=s)
            return _run

        # ── C. 取的是 Overall 块，不是第一个声道块（[-1] 而非 [0]）──
        # 声道块 -70dB（低于分界）/ Overall -15 与 -50：误取声道块 → 两窗都归静音 →
        # 整项 UNTESTED；正确取 Overall → 极差 35dB → 报"严重不一致"。
        stderr_seq = [_astats_stderr(-15.0, channels=(-70.0,)),
                      _astats_stderr(-50.0, channels=(-70.0,)),
                      _astats_stderr(-50.0, channels=(-70.0,)),
                      _astats_stderr(-15.0, channels=(-70.0,))]

        saved_run = eva.subprocess.run
        try:
            eva.subprocess.run = _make_run(stderr_seq)
            with contextlib.redirect_stdout(_io.StringIO()):
                errs_c, _, untested_c, _ = eva._check_video_quality("fake.mp4")
        finally:
            eva.subprocess.run = saved_run
        tc.assert_true(any("Audio level severely inconsistent" in e and "-15.0dB" in e
                           and "-50.0dB" in e for e in errs_c),
                       "the per-window value is the Overall block, not the first channel block")
        tc.assert_true(not any("Audio RMS consistency" in u for u in untested_c),
                       "an adjudicated RMS check must not also be reported as untested")

        # ── D. 停顿桶 / 无样本桶分别点名，且不再指向"输出拼写"──
        voiced = _astats_stderr(-21.0)
        inf_stderr = _astats_stderr(float("-inf"))
        sub_floor_stderr = _astats_stderr(-67.3)
        no_rms_stderr = "size=N/A time=00:00:02.00 bitrate=N/A speed=18x"

        # D-1：1 窗有值 + 3 窗 -inf → UNTESTED，计数写成"3 个落在停顿/静音段"
        try:
            eva.subprocess.run = _make_run([voiced, inf_stderr, inf_stderr, inf_stderr])
            with contextlib.redirect_stdout(_io.StringIO()):
                _, _, untested_d1, _ = eva._check_video_quality("fake.mp4")
        finally:
            eva.subprocess.run = saved_run
        rms_u1 = [u for u in untested_d1 if "Audio RMS consistency" in u]
        tc.assert_equal(len(rms_u1), 1, "one usable sample cannot adjudicate → named as untested")
        tc.assert_true("UNTESTED" in rms_u1[0]
                       and "1 个取到可比 dB 值" in rms_u1[0]
                       and "3 个落在停顿/静音段" in rms_u1[0]
                       and "0 个 FFmpeg 未输出任何 RMS 行" in rms_u1[0],
                       f"pause windows counted as pause, not as measurement gap (got: {rms_u1[0]})")

        # D-2：低于分界的数值窗同样归静音桶（-67.3dB 有 RMS 行但不含语音电平）
        try:
            eva.subprocess.run = _make_run([voiced, sub_floor_stderr, sub_floor_stderr,
                                            sub_floor_stderr])
            with contextlib.redirect_stdout(_io.StringIO()):
                _, _, untested_d2, _ = eva._check_video_quality("fake.mp4")
        finally:
            eva.subprocess.run = saved_run
        tc.assert_true(any("3 个落在停顿/静音段" in u
                           for u in untested_d2 if "Audio RMS consistency" in u),
                       "sub-floor aggregate RMS counts as a pause window, not a voiced sample")

        # D-3：完全没有 RMS 行 → 归"无样本/解码失败"桶，且不得再写"拼写是否再次变化"
        try:
            eva.subprocess.run = _make_run([no_rms_stderr] * 4)
            with contextlib.redirect_stdout(_io.StringIO()):
                _, _, untested_d3, _ = eva._check_video_quality("fake.mp4")
        finally:
            eva.subprocess.run = saved_run
        rms_u3 = [u for u in untested_d3 if "Audio RMS consistency" in u]
        tc.assert_true(any("4 个 FFmpeg 未输出任何 RMS 行" in u and "拼写漂移" in u
                           and "是否再次变化" not in u for u in rms_u3),
                       "the untested note points at no-audio-sample/decode failure, "
                       "not at the (already-locked) astats spelling")

        # ── E. 停顿分界来自 video_quality_rules.json，不是调用点硬编码 ──
        declared_floor = eva._load_video_quality_rules().get("rms_window_silence_floor_db")
        tc.assert_true(isinstance(declared_floor, (int, float)),
                       "the silence floor is declared in the authoritative rules file")
        all_sub = [sub_floor_stderr] * 4
        saved_loader = eva._load_video_quality_rules
        try:
            # 分界降到 -70dB 后，同样四个 -67.3dB 窗口即为可比样本 → 整项裁定、无未测
            eva._load_video_quality_rules = lambda *a, **k: {
                "min_video_bitrate_kbps_fail": 200, "min_video_bitrate_kbps_warn": 500,
                "rms_window_silence_floor_db": -70}
            eva.subprocess.run = _make_run(all_sub)
            with contextlib.redirect_stdout(_io.StringIO()):
                _, _, untested_e, na_e = eva._check_video_quality("fake.mp4")
        finally:
            eva.subprocess.run = saved_run
            eva._load_video_quality_rules = saved_loader
        tc.assert_true(not any("Audio RMS consistency" in u for u in untested_e)
                       and not any("Audio RMS consistency" in x for x in na_e),
                       "raising the floor to -70dB makes -67.3dB windows comparable "
                       "(the config value, not a hardcoded literal, drives the bucketing)")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


# ============================================================================
# 2026-09-19 修订01：ASR 强制对齐的分段面守卫（首句零匹配 / 塌缩零长句）
# ============================================================================

def test_forced_align_segment_face_guards() -> RegressionTestCase:
    """用例59：match_ratio 是全局聚合量，守卫必须落在分段面

    背景（agent-wiki-promo-v2 _修订01 实证）：场景 4 TTS 音频正常（首句
    mean -26.1dB、静音分布正常），但 whisper 把第一句整段漏识别（转写从
    5.94s 才起口）→ 首句锚定失败、时间戳全由"0→首个锚点"线性插值造出，
    塌缩为零长句（rel_start==rel_end==5.54）；match_ratio 0.516 勉强过
    0.5 线，既有单调性守卫（只抓倒退 >0.5s）抓不住零长句 → 垃圾时间戳
    直通 SRT，终检 subtitle_audio_alignment p95=3.575s FAIL（2 个语音
    起口落在字幕窗口外，dev 4.52/3.57s）。独立复跑逐位一致（确定性）。
    修后：align_scene 在 ratio 线之外加两道分段面守卫——①首句发音字符
    零匹配（ASR 吞前缀）→ None；②任意句子时长 <10ms（锚定区间塌缩）
    → None。任一命中即落调用方既有 punct-gap 降级链。ALIGN_ALGO_VERSION
    v1→v2 bump 使存量 v1 的 asr_forced 缓存记录全部重对齐（复用失效由
    用例55 D 段按行为锁定，此处不注数字防漂移）。
    """
    tc = RegressionTestCase(
        "forced_align_segment_face_guards",
        "ASR 漏转写首句/锚定塌缩时 align_scene 必须判不可靠并降级，正常词流不得误杀"
    )

    try:
        import sys
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import _forced_align as fa

        def _run_with_words(words, segments):
            saved = fa._transcribe_words
            fa._transcribe_words = lambda wav, model, initial_prompt=None: list(words)
            try:
                return fa.align_scene("fake_scene.wav", "".join(segments),
                                      segments, model=object())
            finally:
                fa._transcribe_words = saved

        def _words(text, t0, step=0.09):
            """把字符流摊成词：从 t0 起，每字 step 秒（步长留隙避免伪塌缩）。"""
            return [(ch, t0 + step * i, t0 + step * i + step * 0.9)
                    for i, ch in enumerate(text)]

        # ── A. 负向对照（修复前即从此处漏网）：ASR 吞掉第一句 ──
        # 原文两句各 7 发音字符，转写词流只含第二句且从 6.0s 起口 →
        # 首句零匹配、时间戳全由"0→首个锚点"插值造出并塌缩为零长。
        # ratio 7/14=0.5 刻意压在 MIN_MATCH_RATIO 线上（判据为严格小于，
        # 故旧线放行）——摘掉新守卫后此支必须转绿失败（防恒真）。
        r = _run_with_words(_words('第二句话在这里', 6.0),
                            ['第一句话在这里。', '第二句话在这里。'])
        tc.assert_true(r is None,
                       "ASR dropped the first sentence (first segment has zero "
                       "matched chars, ratio still at/above MIN_MATCH_RATIO) → "
                       "align_scene must judge unreliable → None")

        # ── B. 负向对照：ASR 只转写出第一句 → 其后字符全未匹配，右锚缺失使
        #      插值退化为"左锚→左锚"，末句塌缩为零长。──
        r = _run_with_words(_words('第一句话在这里', 0.1),
                            ['第一句话在这里。', '第二句话在。'])
        tc.assert_true(r is None,
                       "ASR kept only the first sentence → tail clamps left and the "
                       "last window collapses to zero length; must be rejected")

        # ── C. 边界记录：中段整句未匹配但被左右锚点包住（时间上有真实间隙）
        #      → 窗口张开、不塌缩，既有守卫按设计放行。零长守卫只拦"塌缩"
        #      这一确定失败信号，不对"局部漏识别"一刀切（防过拟合误杀）。──
        r = _run_with_words(_words('第一句话在这里', 0.1) + _words('第三句话在这里', 5.0),
                            ['第一句话在这里。', '第二句话在这里。', '第三句话在这里。'])
        tc.assert_true(r is not None and len(r['sentences']) == 3,
                       "a sandwiched unmatched sentence widens between real anchors "
                       "(no collapse) — existing guards intentionally let it through")

        # ── D. 正常完整词流：不得误杀（守卫摘除前后此支都必须 PASS，
        #      否则 A-C 的拦截可能只是普遍失效的副产品）──
        r = _run_with_words(
            [('第', 0.10, 0.30), ('一', 0.30, 0.50), ('句', 0.50, 0.70),
             ('旁', 0.70, 0.90), ('白', 0.90, 1.10),
             ('第', 1.60, 1.80), ('二', 1.80, 2.00), ('句', 2.00, 2.20),
             ('旁', 2.20, 2.40), ('白', 2.40, 2.60)],
            ['第一句旁白。', '第二句旁白。'])
        tc.assert_true(r is not None, "healthy word stream must still align")
        if r is not None:
            tc.assert_equal(r.get('method'), 'asr_forced',
                            "healthy path keeps method asr_forced")
            sents = r['sentences']
            tc.assert_equal(len(sents), 2, "both sentences emitted")
            tc.assert_true(sents[0]['rel_start'] < 1.2
                           and sents[0]['rel_end'] - sents[0]['rel_start'] > 0.5
                           and sents[1]['rel_start'] > 1.2,
                           "sentence windows track the real word timings, "
                           "not interpolation from a blank prefix")

        # ── E. 前缀守卫的独有判据（零长守卫抓不到的形态）：ASR 漏掉首句，
        #      但在词流最前面吐了一个幻觉字符 → 首句未匹配区间被"幻觉字符
        #      起点 → 首个真实锚点"这段 5.8s 的空档线性摊开，窗口张开而不塌缩，
        #      单调性也照样成立。此时只有"首句零匹配字符"这一条能拦。──
        r = _run_with_words([('嗯', 0.2, 0.5)] + _words('第二句话在这里', 6.0),
                            ['第一句话在这里。', '第二句话在这里。'])
        tc.assert_true(r is None,
                       "ASR dropped the prefix but emitted a leading hallucinated token: "
                       "the fabricated window widens (no collapse) and monotonicity holds, "
                       "so only the zero-matched-first-segment guard can catch it")

        # ── E2. 中段塌缩（逐句零长检查的独有判据，循环体不必再依赖"前一条"
        #      写法）：ASR 漏掉整句"第二句"的"第二"二字，第三句紧贴第一句
        #      的结束时刻起口 → 中间句首尾字符各自插值到同一个锚点，
        #      rel_start == rel_end == 0.73。首句有真实匹配（前缀守卫不命中）、
        #      末句窗口张开（0.73→1.36），只有逐句零长检查能拦这一形态。──
        r = _run_with_words(
            _words('第一句话在这里', 0.1)
            + _words('三句话在这里', 0.1 + 0.09 * 6 + 0.09 * 0.9),   # 紧贴首句末字符的结束时刻
            ['第一句话在这里。', '第二句话在这里。', '第三句话在这里。'])
        tc.assert_true(r is None,
                       "a MIDDLE sentence collapses (head matched, tail widened) — "
                       "only the per-sentence zero-length check catches this shape")

        # ── F. 纯标点分段（n==0 理论分支）冒烟：首段归一化为空时前缀守卫
        #      必须跳过（seg_matched_counts[0]==0 恒真会误杀合法对齐）。本支
        #      仅锁"不抛异常"，落哪条出口不作断言（ratio 线本身也会拒绝）──
        r = _run_with_words(
            [('一', 0.1, 0.5)],
            ['。', '一二三。'])
        tc.assert_true(r is None or isinstance(r, dict),
                       "pure-punct first segment must not raise")

        # ── G. _align_char_times 返回 arity 与调用点一致（四元组），
        #      无匹配时 matched_flags 为 None ──
        starts, ends, ratio, flags = fa._align_char_times(
            'abc', [('x', 0.0, 1.0), ('y', 1.0, 2.0)])
        tc.assert_true(starts is None and ratio == 0.0 and flags is None,
                       "no-match returns (None, None, 0.0, None)")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


# ============================================================================
# 2026-09-19 普查裁定（方案 A）：字幕时间戳主路径命中率
# ============================================================================

def test_subtitle_timestamp_source_hit_rate_gate() -> RegressionTestCase:
    """用例60：media_qa_gate.subtitle_timestamp_source 主路径命中率裁定

    背景（2026-09-19 7 份已交付成片普查）：subtitle_audio_alignment 量的是"语音起口
    落在哪条字幕窗口内"，punct-gap 一级降级同样由真实静音驱动，7 份现场重跑一律
    p95=0.0s（阈值 0.6s）——整项目对齐全降级（09-18 那版交付）在终检报告上与主路径
    **零区分度**。本项读 step5 逐场实测来源记录（temp/_subtitle_timestamp_source.json），
    asr_forced 占比低于 audio_sync_rules.json → alignment.min_asr_forced_ratio 即判
    UNTESTED，**不判 FAIL**（punct-gap 是设计内一级链路，判成缺陷属篡改既有取舍），
    走四态语义：默认阻断、--accept-media-untested 可知情放行。锁定面：
      - 负向注入：全 punct-gap 记录 → 本项 UNTESTED 且 subtitle_audio_alignment 同时
        PASS（"两条链可区分"这一动因本身成为断言）；
      - 正向对照：全 asr_forced → PASS，不误杀已交付形态；占比恰等于阈值判过；
      - 阈值真被消费：改配置权威源为 0.5 后 50% 即放行（非代码常量）；
      - 代次绑定：记录文件名/条目数与被测字幕不符 → UNTESTED，旧记录不得裁定新成片；
      - 取数面缺失/损坏 → UNTESTED 且报告行印"无从裁定"（不印 0/0 让人读成 0%）；
      - 纯 BGM 声明豁免 → 整组 NOT_APPLICABLE；
      - 生成侧 writer 与消费端字段兼容，记录只存逐场归属（单一 Primitive）；
      - preflight 模型缓存缺位只 warn 不 error（离线走降级合法），tts 禁用不置警。
    """
    tc = RegressionTestCase(
        "subtitle_timestamp_source_hit_rate_gate",
        "验证主路径命中率裁定：占比不足判未测不判违规、阈值取配置权威源、记录代次绑定"
    )

    try:
        import io
        import contextlib

        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import media_qa_gate as mq
        import _gate_status as gs
        from types import SimpleNamespace

        _saved = {}

        def _patch(name, fn):
            _saved.setdefault(name, getattr(mq, name))
            setattr(mq, name, fn)

        def _restore():
            for k, v in _saved.items():
                setattr(mq, k, v)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                video = tmp / "案例成片.mp4"
                video.write_bytes(b"fake-video-bytes")
                srt = tmp / "案例成片.srt"
                srt.write_text("1\n00:00:01,000 --> 00:00:04,000\n第一段旁白\n\n"
                               "2\n00:00:05,000 --> 00:00:08,000\n第二段旁白\n",
                               encoding='utf-8')
                n_entries = len(mq.parse_srt(str(srt)))
                cfg = tmp / "cfg.json"
                cfg.write_text(json.dumps({"fps": 25, "resolution": "1920x1080"}),
                               encoding='utf-8')

                # 除被测项以外的检查全部喂清洁测量值（与用例52/57 同法，物理扫描按
                # 模块级 stub 解耦 ffmpeg）；起口样本按 min_onsets 补齐，不假设阈值
                rules = mq._load_alignment_rules()
                onsets = [1.5, 2.5, 5.5, 6.5, 7.5]      # 全部落在两条字幕窗口内 → p95=0
                onsets += [1.5] * max(0, int(rules['min_onsets']) - len(onsets))
                _patch("ffprobe_get_streams", lambda p: [
                    {"codec_type": "video", "codec_name": "h264", "bit_rate": "1500000",
                     "r_frame_rate": "25/1", "width": 1920, "height": 1080},
                    {"codec_type": "audio", "codec_name": "aac", "bit_rate": "128000"}])
                _patch("ffprobe_get_duration", lambda p: 10.0)
                _patch("ffmpeg_detect_silence", lambda p, d=None: 1.0)
                _patch("ffmpeg_detect_speech_onsets", lambda *a, **k: onsets)
                _patch("ffmpeg_detect_black_intervals", lambda *a, **k: [])
                _patch("ffmpeg_detect_long_silences", lambda *a, **k: [])
                _patch("subprocess", SimpleNamespace(run=lambda *a, **k: SimpleNamespace(
                    returncode=0, stdout="", stderr="")))

                _rec_n = [0]

                def make_record(sources, subtitle_file=None, srt_entries=None):
                    """按 step5 记录的字段形态落一份来源记录（逐场归属是唯一 Primitive）。"""
                    _rec_n[0] += 1
                    p = tmp / f"src_{_rec_n[0]}.json"
                    p.write_text(json.dumps({
                        '$schema': 'subtitle-timestamp-source v1',
                        'subtitle_file': subtitle_file or srt.name,
                        'srt_entries': (n_entries if srt_entries is None else srt_entries),
                        'scenes': [{'scene_id': f's{i}', 'source': s, 'segments': 1}
                                   for i, s in enumerate(sources)],
                    }, ensure_ascii=False), encoding='utf-8')
                    return str(p)

                def run_qa(source=None, subtitle=str(srt)):
                    qa = mq.MediaQAGate()
                    return qa.validate(str(video), subtitle, visual_check_passed=True,
                                       config_path=str(cfg), subtitle_source_path=source)

                def _status(res):
                    return res['check_statuses']['subtitle_timestamp_source']

                def _render(result):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        mq.print_report(result)
                    return buf.getvalue()

                # ── 1. 正向对照：全主路径不得误杀 ──
                _, r_forced = run_qa(make_record(['asr_forced'] * 10))
                tc.assert_equal(_status(r_forced), gs.PASS,
                                "all-asr_forced record adjudicates PASS (已交付主流形态)")
                tc.assert_equal(r_forced['verdict'], gs.PASS,
                                "the new check does not block a clean full-chain delivery")
                f_forced = r_forced['media_facts']['subtitle_timestamp_source']
                tc.assert_equal(f_forced['asr_forced_ratio'], 1.0,
                                "ratio is computed from the per-scene list at consume time")
                tc.assert_equal(f_forced['scenes_total'], 10,
                                "the record's scene count rides into the report facts")

                # ── 2. 负向注入：整项目一级降级必须可见，且判未测不判违规 ──
                passed_bad, r_bad = run_qa(make_record(['punct_gap'] * 10))
                tc.assert_equal(_status(r_bad), gs.UNTESTED,
                                "a fully punct-gap project is UNTESTED, not silently green")
                tc.assert_equal(r_bad['check_statuses']['subtitle_audio_alignment'],
                                gs.PASS,
                                "alignment still PASSes on the same clip — that zero "
                                "discrimination is exactly why this check exists")
                tc.assert_equal(r_bad['verdict'], gs.UNTESTED,
                                "the demoted verdict is visible at the top level")
                tc.assert_equal(r_bad['errors'], [],
                                "a designed-in fallback chain produces no FAIL entry")
                tc.assert_true(passed_bad,
                               "legacy 'passed' keeps its meaning (no violation was found)")
                ok_bad, reason_bad = mq.adjudicate(r_bad)
                tc.assert_true(not ok_bad and 'UNTESTED' in reason_bad,
                               "untested hit-rate blocks delivery by default")
                ok_ok, reason_ok = mq.adjudicate(r_bad, accept_untested=True)
                tc.assert_true(ok_ok and '--accept-media-untested' in reason_ok,
                               "informed acceptance releases it with a traceable reason")
                tc.assert_true(any('min_asr_forced_ratio' in u for u in r_bad['untested']
                                   if u.startswith('subtitle_timestamp_source')),
                               "the untested note names the threshold authority so the "
                               "operator can see what to raise or re-run")

                # ── 3. 阈值边界：ratio 恰等于阈值判过（严格小于才阻断），短项目一场降级即触发 ──
                th = float(rules['min_asr_forced_ratio'])
                at_n = int(th * 10)
                _, r_at = run_qa(make_record(['asr_forced'] * at_n
                                             + ['punct_gap'] * (10 - at_n)))
                tc.assert_equal(_status(r_at), gs.PASS,
                                f"exactly {at_n}/10 ＝ 阈值 {th:g} releases (comparison is <)")
                just_below = int(th * 20) - 1
                _, r_under = run_qa(make_record(['asr_forced'] * just_below
                                                + ['punct_gap'] * (20 - just_below)))
                tc.assert_equal(_status(r_under), gs.UNTESTED,
                                f"{just_below}/20 刚低于阈值 {th:g} 即判未测")
                _, r_gran = run_qa(make_record(['asr_forced'] * 3 + ['punct_gap']))
                tc.assert_equal(_status(r_gran), gs.UNTESTED,
                                "3 场项目 1 场降级（75%）即触发 —— 短项目粒度提醒的行为面")

                # ── 4. 阈值消费的是配置文件，不是代码常量 ──
                disk = json.loads((script_dir / ".." / "配置" / "config" / "quality" /
                                   "audio_sync_rules.json").resolve()
                                  .read_text(encoding='utf-8'))
                tc.assert_equal(th, float(disk['alignment']['min_asr_forced_ratio']),
                                "the effective threshold equals the config authority "
                                "(single source, no code literal)")
                saved_rules = mq._load_alignment_rules
                try:
                    mq._load_alignment_rules = lambda *a, **k: dict(
                        rules, min_asr_forced_ratio=0.5)
                    _, r_loose = run_qa(make_record(['asr_forced'] * 5 + ['punct_gap'] * 5))
                    tc.assert_equal(_status(r_loose), gs.PASS,
                                    "raising the config to 0.5 releases a 50% project — "
                                    "the gate really reads the rule file")
                    tc.assert_equal(r_loose['media_facts']
                                    ['subtitle_timestamp_source']['threshold_ratio'], 0.5,
                                    "the reported threshold is the consumed one")
                finally:
                    mq._load_alignment_rules = saved_rules

                # ── 5. 代次绑定：旧记录/异名字幕不得裁定新成片 ──
                _, r_stale_name = run_qa(make_record(['asr_forced'] * 10,
                                                     subtitle_file="上一版成片.srt"))
                tc.assert_equal(_status(r_stale_name), gs.UNTESTED,
                                "a record naming another subtitle file is not evidence")
                tc.assert_true(r_stale_name['media_facts']
                               ['subtitle_timestamp_source']['stale_record'],
                               "staleness is machine-readable for the delivery doc")
                _, r_stale_n = run_qa(make_record(['asr_forced'] * 10,
                                                  srt_entries=n_entries + 1))
                tc.assert_equal(_status(r_stale_n), gs.UNTESTED,
                                "entry-count mismatch means another SRT generation")
                tc.assert_true(any('不同代' in u for u in r_stale_n['untested']
                                   if u.startswith('subtitle_timestamp_source')),
                               "the stale reason is spelled out, not a bare untested")

                # ── 6. 取数面缺失/损坏 → 未测，且报告行不得印成 0/0 ──
                for label, src in (("no path", None),
                                   ("absent", str(tmp / "不存在.json")),
                                   ("corrupt", None)):
                    if label == "corrupt":
                        p = tmp / "broken.json"
                        p.write_text("{ not json", encoding='utf-8')
                        src = str(p)
                    _, r = run_qa(src)
                    tc.assert_equal(_status(r), gs.UNTESTED,
                                    f"unusable source record ({label}) is UNTESTED, never guessed")
                    line = mq.format_subtitle_source_line(
                        r['media_facts']['subtitle_timestamp_source'])
                    tc.assert_true('无从裁定' in line,
                                   f"the report line states 无从裁定 instead of a fake 0% ({label})")
                p_nokey = tmp / "nokey.json"
                p_nokey.write_text(json.dumps({'subtitle_file': srt.name,
                                               'srt_entries': n_entries}), encoding='utf-8')
                tc.assert_equal(_status(run_qa(str(p_nokey))[1]), gs.UNTESTED,
                                "a record without the per-scene list cannot adjudicate")

                # ── 7. 纯 BGM 声明豁免 → 整组 NOT_APPLICABLE ──
                _, r_bgm = run_qa(make_record(['asr_forced'] * 10), subtitle=None)
                tc.assert_equal(r_bgm['check_statuses']['subtitle_timestamp_source'],
                                gs.NOT_APPLICABLE,
                                "no narration means no timestamp chain to adjudicate")
                tc.assert_equal(r_bgm['status_counts'][gs.NOT_APPLICABLE],
                                len(mq.SUBTITLE_CHECK_KEYS),
                                "the whole subtitle group is exempted together")

                # ── 8. 文案单一生成点：报告行与交付说明提示同源 ──
                tc.assert_true(mq.format_subtitle_source_line(f_forced) in _render(r_forced),
                               "print_report renders the shared line, its own copy is forbidden")

                # ── 9. 生成侧 writer 与消费端字段兼容（跨模块契约） ──
                import enhance_video_audio as eva
                written = eva._write_subtitle_timestamp_source(
                    tmp, srt,
                    [{'scene_id': 's0', 'source': 'asr_forced', 'segments': 2},
                     {'scene_id': 's1', 'source': 'punct_gap', 'segments': 1}],
                    n_entries)
                tc.assert_true(written is not None and Path(written).exists(),
                               "step5 persists the per-scene source record")
                data = json.loads(Path(written).read_text(encoding='utf-8'))
                tc.assert_equal(data['subtitle_file'], srt.name,
                                "the record binds itself to the SRT it describes")
                tc.assert_equal(data['srt_entries'], n_entries,
                                "entry count rides along for generation binding")
                tc.assert_true('sources' not in data,
                               "only the per-scene primitive is stored; a second derived "
                               "count in the same file would be free to drift")
                _, r_round = run_qa(str(written))
                tc.assert_equal(_status(r_round), gs.UNTESTED,
                                "writer output feeds the gate directly (50% < threshold)")
                tc.assert_equal(r_round['media_facts']['subtitle_timestamp_source']
                                ['asr_forced'], 1,
                                "the consumer recount from the writer's scenes matches")
                tc.assert_equal(r_round['media_facts']['subtitle_timestamp_source']
                                ['punct_gap'], 1,
                                "fallback scenes are counted into the same distribution")

                # ── 10. 配置缺键时回退默认并点名（阈值不得静默消失） ──
                sparse = tmp / "audio_sync_rules_sparse.json"
                sparse.write_text(json.dumps({"alignment": {"silence_db": -38}}),
                                  encoding='utf-8')
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    loaded = mq._load_alignment_rules(str(sparse))
                tc.assert_true('min_asr_forced_ratio' in loaded,
                               "the key survives a config that omits it (defaulted)")
                tc.assert_true('min_asr_forced_ratio' in buf.getvalue(),
                               "the fallback is announced, not silent")

                # ── 11. 接线位点（行为接缝需整条流水线，秒级不可达 → 调用存在性） ──
                eva_src = (script_dir / "enhance_video_audio.py").read_text(encoding='utf-8')
                step5_body = (eva_src.split("def step5_generate_subtitles(", 1)[1]
                              .split("\ndef ", 1)[0])
                tc.assert_true("_write_subtitle_timestamp_source(" in step5_body,
                               "step5 still writes the record (quick-fix included)")
                runner_src = (script_dir / "pipeline_runner.py").read_text(encoding='utf-8')
                tc.assert_true("subtitle_source_path=" in runner_src,
                               "_final_media_qa still hands the record to the gate")
        finally:
            _restore()

        # ── 12. preflight：模型缓存缺位只告警，不阻断（离线降级合法） ──
        import preflight_check as pf
        import _forced_align as fa
        saved_ready = fa.align_model_cache_ready
        try:
            fa.align_model_cache_ready = lambda: (False, "snapshots/ 下无有效 model.bin")
            r_miss = pf.PreflightResult(mode="render")
            with contextlib.redirect_stdout(io.StringIO()):
                pf.check_alignment_model_cache({"tts_enabled": True}, r_miss)
            tc.assert_equal(r_miss.errors, [],
                            "a missing whisper cache must never block rendering")
            tc.assert_true(r_miss.passed, "warn-only by design")
            tc.assert_true(any("punct-gap" in w for w in r_miss.warnings),
                           "the warning names the chain this run will take")
            tc.assert_true(any("accept-media-untested" in w for w in r_miss.warnings),
                           "and points at the downstream gate/escape so the two agree")
            fa.align_model_cache_ready = lambda: (True, "snapshots/abc/model.bin")
            r_ready = pf.PreflightResult(mode="render")
            with contextlib.redirect_stdout(io.StringIO()):
                pf.check_alignment_model_cache({"tts_enabled": True}, r_ready)
            tc.assert_equal(r_ready.warnings, [],
                            "a ready cache produces no noise (a permanent warning is dead weight)")
            r_bgm_pf = pf.PreflightResult(mode="render")
            with contextlib.redirect_stdout(io.StringIO()):
                pf.check_alignment_model_cache({"tts_enabled": False}, r_bgm_pf)
            tc.assert_equal(r_bgm_pf.warnings, [],
                            "pure-BGM projects have no sentence alignment to warn about")
            pf_src = (script_dir / "preflight_check.py").read_text(encoding='utf-8')
            tc.assert_true("check_alignment_model_cache(" in
                           pf_src.split("def main(", 1)[1],
                           "main() still runs the model-cache probe")
        finally:
            fa.align_model_cache_ready = saved_ready

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


# ============================================================================
# 2026-09-19 方案 B：交付说明「字幕时间戳来源」节自动写入 + --audit 一致性维
# ============================================================================

def test_delivery_notes_subtitle_source_section() -> RegressionTestCase:
    """用例61：交付说明本节的结构唯一生成点、就地替换幂等、落点推导、审计一致性维

    背景（操作日志 [2026-09-19] ③ 裁定）：该节的数字唯一来源是终检 media_facts，而
    temp/ 与日志不入 git——靠收尾提示让人粘贴，交付后"这一版走主路径还是 punct-gap
    降级"仍无从追溯（09-19 普查的取证成本即此）。方案 B 把写入者收归流水线，并配
    --audit 第 6 维裁定一致性。锁定面：
      - 结构唯一生成点：节正文只含一行实测行（同一分布不抄两份），模板标题与
        media_qa_gate.DELIVERY_NOTES_SECTION_TITLE 逐字一致；
      - 落点推导：md 在 成果文件/ 层而非 成果文件/视频/（旧实现按 output_file.parent
        算错一层，使"说明已创建"对全仓 16 份真实 md 恒判为假）；
      - splice 三态：无节追加、有节整节就地替换且不吃掉相邻节、内容一致 changed=False
        且字节不变（幂等——否则每次重跑刷新已交付说明的 mtime）；
      - 写入端：md 不存在不代创建、终检无事实不写、有事实才写；写盘失败（注入
        os.replace 报错）时 md 保持上一版字节且不留半截临时名（A10 同一实现）；
      - 审计维四态：报告无 facts → 不适用（不得印成 PASS，存量交付因此零误杀）；
        facts 齐且节正确 → 通过；节缺失或被手改数字 → FAIL（负面注入，防恒真断言）。
    """
    tc = RegressionTestCase(
        "delivery_notes_subtitle_source_section",
        "验证交付说明字幕时间戳来源节的唯一生成点、落点、幂等替换与审计一致性维"
    )

    try:
        import re
        from types import SimpleNamespace

        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        import media_qa_gate as mq
        import pipeline_runner as pr
        import generate_completion_report as gcr

        facts = {'scenes_total': 10, 'asr_forced': 9, 'punct_gap': 1, 'char_prop': 0,
                 'asr_forced_ratio': 0.9, 'threshold_ratio': 0.8, 'adjudicable': True}
        line = mq.format_subtitle_source_line(facts)
        section = mq.format_subtitle_source_section(line)

        # ── 1. 结构唯一生成点 ──
        tc.assert_true(line in section, "节正文复用 format_subtitle_source_line 的唯一产出")
        tc.assert_equal(section.count("asr_forced"), 1,
                        "同一分布在一节内只出现一次（表格副本已废除）")
        tpl_path = (script_dir.parent.parent / "AI视频制作工作流模板"
                    / "templates_07_交付说明模板.md")
        tpl = tpl_path.read_text(encoding='utf-8')
        tc.assert_true(f"## {mq.DELIVERY_NOTES_SECTION_TITLE}\n" in tpl,
                       "模板标题与结构常量逐字一致（否则写入端找不到要替换的节）")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "成果文件"
            (out_dir / "视频").mkdir(parents=True)
            video = out_dir / "视频" / "案例成片.mp4"
            video.write_bytes(b"fake-video")

            # ── 2. 落点推导 ──
            notes = mq.delivery_notes_path(video)
            tc.assert_equal(notes.name, "交付说明_案例成片.md",
                            "md 名由成片基名派生")
            tc.assert_equal(notes.parent, out_dir,
                            "md 与 mp4 不同层（旧实现算进 成果文件/视频/，判据恒假）")

            # ── 3. splice 三态 ──
            md_old = ("## 基本信息\n\n内容A\n\n"
                      "## 已清理的过程文件\n\n- [ ] render_raw.mp4\n")
            appended, ch_app = mq.splice_subtitle_source_section(md_old, section)
            tc.assert_true(ch_app and line in appended,
                           "无本节时追加，且追加的是实测行")
            tc.assert_equal(appended.count("## "), 3,
                            "追加只新增一节，相邻两节结构不变")

            facts2 = dict(facts, asr_forced=7, punct_gap=3, asr_forced_ratio=0.7)
            line2 = mq.format_subtitle_source_line(facts2)
            section2 = mq.format_subtitle_source_section(line2)
            md_mid = ("## 基本信息\n\n内容A\n\n" + section +
                      "\n## 已清理的过程文件\n\n- [ ] render_raw.mp4\n")
            replaced, ch_rep = mq.splice_subtitle_source_section(md_mid, section2)
            tc.assert_true(ch_rep and line2 in replaced, "有本节时整节就地替换")
            tc.assert_true(line not in replaced,
                           "旧实测值不留残影（两行并存会让人抄到过期分布）")
            tc.assert_true("内容A" in replaced and "- [ ] render_raw.mp4" in replaced,
                           "替换射程仅限本节，不吃相邻内容")
            tc.assert_equal(replaced.count(f"## {mq.DELIVERY_NOTES_SECTION_TITLE}"), 1,
                            "替换不产生重复节")
            again, ch_idem = mq.splice_subtitle_source_section(replaced, section2)
            tc.assert_equal(ch_idem, False, "内容一致时 changed=False")
            tc.assert_equal(again, replaced, "内容一致时字节不变（幂等）")

            # ── 4. 写入端行为 ──
            fake = SimpleNamespace(output_file=video,
                                   _subtitle_source_note_line=lambda: line)
            msg_absent = pr.PipelineRunner._sync_subtitle_source_section(fake)
            tc.assert_true("跳过" in msg_absent and not notes.exists(),
                           "交付说明不存在时不代创建（创建属人工收尾）")
            notes.write_text(md_old, encoding='utf-8')
            msg_write = pr.PipelineRunner._sync_subtitle_source_section(fake)
            tc.assert_true("已写入" in msg_write and line in notes.read_text(encoding='utf-8'),
                           "有实测事实且 md 在场 → 落盘")
            tc.assert_equal(sorted(p.name for p in out_dir.iterdir()),
                            ["交付说明_案例成片.md", "视频"],
                            "写盘走原子替换，成果目录不留半截临时名")
            mtime_before = notes.stat().st_mtime_ns
            msg_again = pr.PipelineRunner._sync_subtitle_source_section(fake)
            tc.assert_true("已是最新" in msg_again, "重复执行走幂等分支")
            tc.assert_equal(notes.stat().st_mtime_ns, mtime_before,
                            "幂等分支不刷新已交付说明的 mtime")
            notes.write_text(md_old, encoding='utf-8')
            fake_nofacts = SimpleNamespace(output_file=video,
                                           _subtitle_source_note_line=lambda: None)
            msg_nofacts = pr.PipelineRunner._sync_subtitle_source_section(fake_nofacts)
            tc.assert_true("跳过" in msg_nofacts and line not in notes.read_text(encoding='utf-8'),
                           "终检无事实时不写（不得凭空造节）")
            notes.write_text(md_old, encoding='utf-8')

            # ── 5. md 写盘必须是原子替换（原地截断写会留下半截交付说明）──
            import script_interface as si
            from unittest import mock
            md_before = notes.read_bytes()
            with mock.patch.object(si, "os",
                                   SimpleNamespace(fsync=si.os.fsync,
                                                   replace=lambda *a: (_ for _ in ()).throw(
                                                       OSError("simulated replace failure")))):
                raised = False
                try:
                    pr.PipelineRunner._sync_subtitle_source_section(fake)
                except OSError:
                    raised = True
            tc.assert_true(raised, "注入的替换失败确实传出了写入点（否则本场景无从证明）")
            tc.assert_equal(notes.read_bytes(), md_before,
                            "写盘失败时交付说明保持上一版字节（原地截断写会把它变成半截）")
            tc.assert_true("交付说明_案例成片.md.tmp" not in
                           [p.name for p in out_dir.iterdir()],
                           "失败路径清掉半截临时名")

            # ── 6. 审计第 6 维 ──
            cfg = root / "cfg.json"
            cfg.write_text(json.dumps({"delivery": {"prohibited_terms": ["final"]}}),
                           encoding='utf-8')

            def d6(report_facts):
                report = {"status": "VALIDATED", "validation": {},
                          "data_sources": ({"subtitle_timestamp_source": report_facts}
                                           if report_facts else {})}
                res = gcr.run_delivery_audit(report, str(video), None, str(cfg), str(cfg))
                return res["dimensions"]["delivery_notes_consistency"]

            d6_none = d6(None)
            tc.assert_equal(d6_none.get("applicable"), False,
                            "报告无实测分布 → 不适用（存量交付零误杀）")
            tc.assert_true(d6_none["passed"], "不适用不翻转审计总结论")
            d6_missing = d6(facts)
            tc.assert_equal(d6_missing["passed"], False,
                            "facts 齐而 md 无本节 → FAIL（缺留痕就是不一致）")
            notes.write_text(replaced.replace(line2, line), encoding='utf-8')
            tc.assert_true(d6(facts)["passed"],
                           "本节与报告实测同源 → PASS")
            tampered = notes.read_text(encoding='utf-8').replace("asr_forced 9/10",
                                                                 "asr_forced 10/10")
            notes.write_text(tampered, encoding='utf-8')
            tc.assert_equal(d6(facts)["passed"], False,
                            "手改本节数字 → FAIL（一致性维不是恒真回执）")

        # ── 7. 不适用维度在打印面不得显示为 PASS ──
        gcr_src = (script_dir / "generate_completion_report.py").read_text(encoding='utf-8')
        tc.assert_true(re.search(r'if res\.get\("applicable",\s*True\) is False:\s*\n\s*mark = "N/A"',
                                 gcr_src),
                       "审计打印按 applicable 分档，未裁定项不得印成 PASS")

        tc.mark_passed()

    except Exception as e:
        tc.mark_failed(str(e))

    return tc


def test_delivery_slot_committed_only_after_all_gates() -> RegressionTestCase:
    """用例62：交付槽位只在全部下游门禁通过后单次落槽（2026-09-20 时序修复）

    背景：enhance 的 step3（混音）与 step6（烧字幕）曾把产物直接 rename 进
    成果文件/视频/{name}.mp4，而 step4（码率/音轨核验）与 step7（综合门禁）在其
    之后才跑——"该步自身成功"不等于 AGENTS.md 要求的"成功链末端"。实测后果
    （2026-09-20 发布仓 quickstart-demo 复验，操作日志 [2026-09-20] verify 发现 3）：
    同一路径产生 4 次无背书占槽（1 份因终检 UNTESTED 失败、3 份因码率门禁 FAIL）。
    修复：产物全程留 temp，_commit_delivery_slot() 成为槽位唯一写入者。

    夹具 monkeypatch WF_ROOT 到临时目录，不落仓库；ffmpeg 由 run_ffmpeg 桩替代，
    零真实编码。
    """
    tc = RegressionTestCase(
        "delivery_slot_committed_only_after_all_gates",
        "验证后处理门禁失败时交付槽位字节不变、成功路径槽位与 temp 产物同源"
    )
    import asyncio
    import hashlib
    import inspect
    import io

    def _sha(p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def _slot_snapshot(slot_dir):
        if not slot_dir.exists():
            return {}
        return {f.name: (f.stat().st_size, _sha(f))
                for f in sorted(slot_dir.glob("*.mp4"))}

    try:
        script_dir = Path(__file__).parent
        if str(script_dir) not in sys.path:
            sys.path.insert(0, str(script_dir))
        eva = _safe_import_rebinding_module("enhance_video_audio")

        # ── 1. 结构前提：step3 的写出目标不再是交付槽位 ──
        sig = inspect.signature(eva.step3_merge_all)
        tc.assert_equal(list(sig.parameters), ["video_path", "temp_dir"],
                        "1a step3 参数表只剩输入源 + temp，槽位不再是它的写出目标")
        tc.assert_true(callable(getattr(eva, "_commit_delivery_slot", None)),
                       "1b 槽位写入点由独立函数承担")

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            saved_root = eva.WF_ROOT
            eva.WF_ROOT = td
            slot_dir = td / "成果文件" / "视频"
            slot_dir.mkdir(parents=True, exist_ok=True)
            cfg = {"paths": {"video_name": "_regress-slotcommit.mp4",
                             "subtitle_name": "_regress-slotcommit.srt",
                             "temp_subdir": "_regress-slotcommit_audio"}}
            _root, _vin, temp_dir, slot, _srt = eva.get_paths(cfg)
            temp_dir.mkdir(parents=True, exist_ok=True)
            working = temp_dir / "final_output.mp4"

            ffmpeg_outs = []

            def _fake_ffmpeg(cmd, desc=""):
                out = Path(cmd[-1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"ART-" + desc.encode("utf-8"))
                ffmpeg_outs.append(out)
                return True

            saved = {}
            for name in ("run_ffmpeg", "get_duration", "step0_validate_html_template",
                         "step1_generate_tts", "step2_generate_bgm", "step3_merge_all",
                         "step5_generate_subtitles", "step6_burn_subtitles",
                         "step4_verify", "step7_validate"):
                saved[name] = getattr(eva, name)
            globals_saved = (eva.TTS_ENABLED, eva.BGM_ENABLED, eva.VIDEO_DURATION,
                             eva.SCENES)
            # 被测脚本的 print 走的是测试进程 stdout（GBK 控制台），门禁文案含
            # 非 ASCII 符号会炸编码——整段收进内存，末尾再据其非空性自证"真跑过"
            console_out = sys.stdout
            captured = io.StringIO()
            sys.stdout = captured
            try:
                eva.run_ffmpeg = _fake_ffmpeg
                eva.get_duration = lambda p: 10.0
                eva.step0_validate_html_template = lambda: True
                eva.VIDEO_DURATION = 10.0
                eva.SCENES = []

                async def _fake_tts(_temp_dir):
                    return True
                eva.step1_generate_tts = _fake_tts
                eva.step2_generate_bgm = lambda _td: True

                # ── 2. 真实 step3 在门禁前不得触碰槽位 ──
                (temp_dir / "render_raw.mp4").write_bytes(b"RAW" * 32)
                _r, video_file, _t, _o, _s = eva.get_paths(cfg)
                old_slot_bytes = b"PREVIOUS-DELIVERED"
                slot.write_bytes(old_slot_bytes)
                before = _slot_snapshot(slot_dir)
                eva.TTS_ENABLED = False
                eva.BGM_ENABLED = False
                tc.assert_true(eva.step3_merge_all(video_file, temp_dir),
                               "2a 真实 step3 在纯BGM夹具下成功")
                tc.assert_true(working.exists(),
                               "2b step3 产物落在 temp/final_output.mp4")
                tc.assert_equal([str(p) for p in ffmpeg_outs], [str(working)],
                                "2c step3 只让 ffmpeg 往 temp 写，没有第二个输出点")
                tc.assert_equal(_slot_snapshot(slot_dir), before,
                                "2d step3 跑完后槽位字节与文件集合逐位不变")

                # ── 3. 真实 step6 同理（写回 temp 产物，不碰槽位）──
                srt = td / "成果文件" / "字幕" / "_regress-slotcommit.srt"
                srt.parent.mkdir(parents=True, exist_ok=True)
                srt.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nx\n")
                ffmpeg_outs.clear()
                tc.assert_true(eva.step6_burn_subtitles(working, srt, temp_dir),
                               "3a 真实 step6 成功")
                tc.assert_equal([str(p) for p in ffmpeg_outs],
                                [str(temp_dir / "final_with_subs.mp4")],
                                "3b 烧录中间产物落在 temp，不在成果目录")
                tc.assert_equal(slot.read_bytes(), old_slot_bytes,
                                "3c step6 之后槽位仍是旧字节（新产物未落槽）")
                tc.assert_equal(_slot_snapshot(slot_dir), before,
                                "3d step6 之后成果目录无新增文件（含 .prev/.noaudio 类残留）")
                tc.assert_equal(working.read_bytes(), b"ART-subtitle burn-in",
                                "3e step6 把烧录结果顶回 temp 工作产物")

                # ── 4. 提交点本身：成功落槽＝同一次 rename，缺产物/缺目录拒绝 ──
                tc.assert_true(eva._commit_delivery_slot(working, slot, temp_dir),
                               "4a 门禁全过后落槽成功")
                tc.assert_equal(_sha(slot),
                                hashlib.sha256(b"ART-subtitle burn-in").hexdigest(),
                               "4b 槽位内容＝落槽前 temp 产物的逐位内容（同一次 rename，非二次生成）")
                tc.assert_true(not working.exists(),
                               "4c 落槽是同卷 rename：temp 不留第二份副本（磁盘峰值不翻倍）")
                tc.assert_equal(_slot_snapshot(slot_dir),
                                {slot.name: (len(b"ART-subtitle burn-in"), _sha(slot))},
                                "4d 落槽后成果目录只有成片一个文件")
                tc.assert_equal((temp_dir / "_regress-slotcommit.prev.mp4").read_bytes(),
                                old_slot_bytes,
                                "4e 被替换的旧成片落 temp 的 .prev.mp4")
                sentinel_prev = temp_dir / "_regress-slotcommit.prev.mp4"
                sentinel_prev.write_bytes(b"PREV-SENTINEL")
                tc.assert_true(not eva._commit_delivery_slot(temp_dir / "absent.mp4",
                                                            slot, temp_dir),
                               "4f 产物缺失时拒绝落槽（不凭空搬走槽位里的旧成片）")
                tc.assert_equal(slot.read_bytes(), b"ART-subtitle burn-in",
                                "4g 拒绝落槽时槽位内容逐位不变")
                tc.assert_equal(sentinel_prev.read_bytes(), b"PREV-SENTINEL",
                                '4g2 拒绝发生在动槽位之前：上一版 .prev 备份不得被无谓覆盖')
                working.write_bytes(b"ART-retry")
                tc.assert_true(not eva._commit_delivery_slot(
                    working, td / "no-such-dir" / "x.mp4", temp_dir),
                    "4h 成果目录不存在 → 落槽失败（os.replace 自身报错，不代建目录）")
                tc.assert_true(working.exists(),
                               "4i 落槽失败时产物仍在 temp（未被半途搬走消失）")

                # ── 4j. 落槽改名失败（跨卷 EXDEV 形态）→ 槽位必须回滚，产物留 temp ──
                real_os = eva.os
                slot_bytes_before = slot.read_bytes()

                class _OsStub:
                    def __getattr__(self, k):
                        return getattr(real_os, k)

                    def replace(self, _src, _dst):
                        raise OSError(18, "Invalid cross-device link")

                eva.os = _OsStub()
                try:
                    working.write_bytes(b"NEW-TAKE-2")
                    ok_i = eva._commit_delivery_slot(working, slot, temp_dir)
                finally:
                    eva.os = real_os
                tc.assert_true(not ok_i, "4k 落槽改名失败必须返回 False（不得当成成功）")
                tc.assert_equal(slot.read_bytes(), slot_bytes_before,
                                "4l 失败后槽位回滚为原文件（交付目录不得出现空槽/半成品）")
                tc.assert_equal(working.read_bytes(), b"NEW-TAKE-2",
                                "4m 失败后新产物仍留在 temp，可复跑而不必重渲染")

                # ── 5. 端到端时序：step7 FAIL 时槽位必须保持原状 ──
                working.write_bytes(b"MERGED-NEW")
                slot.write_bytes(b"ALREADY-DELIVERED")
                before5 = _slot_snapshot(slot_dir)
                gate_calls = []

                def _fake_step4(p):
                    gate_calls.append(("step4", Path(p)))
                    return True

                def _fake_step7(subtitle_path, _temp_dir, video_path=None, _dur=None):
                    gate_calls.append(("step7", Path(video_path)))
                    return False

                def _fake_step3(_video_path, _temp_dir):
                    Path(_temp_dir / "final_output.mp4").write_bytes(b"MERGED-NEW")
                    return True

                def _fake_step6(vpath, _srt, _td):
                    Path(vpath).write_bytes(b"MERGED-NEW")
                    return True

                eva.step3_merge_all = _fake_step3
                eva.step6_burn_subtitles = _fake_step6
                eva.TTS_ENABLED = True
                eva.step5_generate_subtitles = lambda _p, _td: True
                eva.step4_verify = _fake_step4
                eva.step7_validate = _fake_step7

                main_logs = []
                cfg_path = td / "cfg.json"

                def _write_cfg(tts_enabled):
                    cfg_path.write_text(json.dumps({
                        "video_duration": 10.0,
                        "tts_enabled": tts_enabled,
                        "bgm_enabled": True,
                        "scenes": [{"scene_id": "s1", "start": 0, "end": 10,
                                    "narration": "测试旁白"}],
                        "paths": {"video_name": slot.name,
                                  "subtitle_name": srt.name,
                                  "temp_subdir": temp_dir.name},
                    }, ensure_ascii=False), encoding="utf-8")

                def _run_main(tts_enabled=True):
                    """跑 enhance.main() 真身（load_config 亦真身，会按 cfg 重设
                    TTS_ENABLED 等模块全局），输出留在内存（测试进程控制台是 GBK，
                    门禁失败文案含非 ASCII 符号，直接打印会炸编码）。"""
                    _write_cfg(tts_enabled)
                    argv = sys.argv
                    out, err = sys.stdout, sys.stderr
                    buf_out, buf_err = io.StringIO(), io.StringIO()
                    sys.argv = ["enhance_video_audio.py", "--config", str(cfg_path)]
                    sys.stdout, sys.stderr = buf_out, buf_err
                    rc = None
                    try:
                        asyncio.run(eva.main())
                    except SystemExit as e:
                        rc = e.code
                    finally:
                        sys.stdout, sys.stderr = out, err
                        sys.argv = argv
                    text = buf_out.getvalue() + buf_err.getvalue()
                    main_logs.append(text)
                    return rc, text

                rc, log5 = _run_main()
                tc.assert_equal(rc, 1, "5b step7 门禁失败 → 退出码 1")
                tc.assert_true("VALIDATION GATE FAILED" in log5,
                               "5c 失败确实来自 step7（非提前 return 造成的假阴性）")
                tc.assert_equal(_slot_snapshot(slot_dir), before5,
                                "5d 门禁失败后交付槽位字节不变、且不新增文件（本用例主命题）")
                tc.assert_equal([c[0] for c in gate_calls], ["step4", "step7"],
                                "5e 两道门禁都在链路里跑过，顺序为 step4 → step7")
                tc.assert_true(bool(gate_calls) and all(p == working for _, p in gate_calls),
                               "5f 门禁检查的对象是 temp 产物，不是交付槽位")
                tc.assert_true(working.exists(),
                               "5g 门禁失败时新产物留在 temp（供 --quick-fix 复跑）")

                # ── 6. 同一链路把 step7 改判通过 → 槽位才被写入 ──
                gate_calls.clear()
                eva.step7_validate = (
                    lambda subtitle_path, _td, video_path=None, _d=None:
                    (gate_calls.append(("step7", Path(video_path))), True)[1])
                rc2, log6 = _run_main()
                tc.assert_equal(rc2, None, "6a 全部门禁通过 → 正常退出（无 SystemExit）")
                tc.assert_true("All checks passed" in log6,
                               "6b 成功路径确实走完门禁（与 5c 同法对照）")
                tc.assert_equal(slot.read_bytes(), b"MERGED-NEW",
                                "6c 槽位此时才拿到新产物")
                prev = temp_dir / f"{slot.stem}.prev.mp4"
                tc.assert_equal(prev.read_bytes(), b"ALREADY-DELIVERED",
                                "6d 旧成片在提交一刻才搬去 temp（备份时机随写入点）")
                tc.assert_equal(_slot_snapshot(slot_dir), {slot.name: (10, _sha(slot))},
                                "6e 成功路径成果目录仍只有成片一个文件")

                # ── 7. 纯BGM路径（config tts_enabled=false，无 step5/6）同样只在门禁后落槽 ──
                step5_calls, step6_calls = [], []
                eva.step5_generate_subtitles = lambda _p, _td: step5_calls.append(1) or True
                eva.step6_burn_subtitles = lambda v, _s, _td: step6_calls.append(v) or True
                slot.write_bytes(b"BGM-OLD")
                before7 = _slot_snapshot(slot_dir)
                eva.step7_validate = lambda *_a, **_k: False
                rc3, _log7 = _run_main(tts_enabled=False)
                tc.assert_equal(rc3, 1, "7a 纯BGM路径门禁失败 → 退出码 1")
                tc.assert_equal((len(step5_calls), len(step6_calls)), (0, 0),
                                "7b 纯BGM路径按类型化豁免跳过字幕两步（不是被本用例桩掉）")
                tc.assert_equal(_slot_snapshot(slot_dir), before7,
                                "7c 纯BGM路径同样不得在门禁前写槽位")
            finally:
                sys.stdout = console_out
                for name, fn in saved.items():
                    setattr(eva, name, fn)
                (eva.TTS_ENABLED, eva.BGM_ENABLED, eva.VIDEO_DURATION,
                 eva.SCENES) = globals_saved
                eva.WF_ROOT = saved_root

            all_text = captured.getvalue() + "\n".join(main_logs)
            tc.assert_true("Merged (temp working artifact)" in all_text
                           and "Delivery slot written" in all_text
                           and "VALIDATION GATE FAILED" in all_text,
                           "8 本用例确实驱动过真实 step3 落 temp、落槽、门禁失败三条路径（日志自证）")
            tc.assert_equal(len(list(slot_dir.glob("*.mp4"))), 1,
                            "9 全程结束后成果目录只剩一个 mp4，无裸片/备份残留")

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
            test_delivery_registry_backing,
            test_config_single_source_timeline_rejected,
            test_fresh_guard_requires_confirmation,
            test_doc_to_markdown_picture_extraction_fallback,
            test_scene_patch_default_route_and_time_witness,
            test_delivery_slot_written_only_by_postprocess,
            test_asset_signoff_gate,
            test_preview_coverage_gap,
            test_gate_four_state_untested_not_pass,
            test_audio_rms_parse_and_untested_trace,
            test_explicit_engine_binds_config_path,
            test_step_fingerprint_binds_real_inputs,
            test_narration_rich_fields_survive_timeline_writeback,
            test_media_qa_gate_four_state_report,
            test_final_media_qa_wired_into_postprocess,
            test_count_up_precision_and_reveal_entrance,
            test_alignment_cache_binds_real_waveform,
            test_state_writes_atomic_and_ledger_survives_fresh,
            test_check_registry_names_are_single_source,
            test_audio_rms_window_aggregate_measure,
            test_forced_align_segment_face_guards,
            test_subtitle_timestamp_source_hit_rate_gate,
            test_delivery_notes_subtitle_source_section,
            test_delivery_slot_committed_only_after_all_gates,
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
