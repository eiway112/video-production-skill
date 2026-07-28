#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志聚合器 - 将所有脚本的结构化日志集中输出到单一文件

用法：
  from log_aggregator import LogAggregator
  agg = LogAggregator("pipeline.log")
  agg.log_event("preflight_check", "PASS", {"mode": "audit"})
  agg.generate_report()
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import threading
import time

class LogAggregator:
    """结构化日志聚合器"""
    
    def __init__(self, log_file_path: str, workspace_root: str = None):
        """
        初始化日志聚合器
        
        Args:
            log_file_path: 日志文件输出路径
            workspace_root: 工作区根目录（用于相对路径计算）
        """
        self.log_file = Path(log_file_path)
        self.workspace_root = Path(workspace_root) if workspace_root else Path.cwd()
        self.events: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        
        # 创建日志目录
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 初始化日志文件
        self.log_file.write_text(json.dumps(
            {
                "pipeline_session": {
                    "start_time": self._timestamp(),
                    "events": []
                }
            },
            ensure_ascii=False,
            indent=2
        ))
    
    def _timestamp(self) -> str:
        """ISO 8601 时间戳"""
        return datetime.now(timezone.utc).isoformat()
    
    def log_event(self, 
                  stage: str,
                  status: str,
                  data: Dict[str, Any] = None,
                  duration_ms: int = None,
                  errors: List[str] = None) -> None:
        """
        记录一个事件
        
        Args:
            stage: 阶段名称（preflight_check, render, verify_tts等）
            status: 状态（PASS, FAIL, SKIP, PARTIAL）
            data: 业务数据
            duration_ms: 执行耗时
            errors: 错误列表
        """
        event = {
            "timestamp": self._timestamp(),
            "stage": stage,
            "status": status,
            "data": data or {},
            "duration_ms": duration_ms,
            "errors": errors or []
        }
        
        with self.lock:
            self.events.append(event)
            self._append_to_file(event)
    
    def _append_to_file(self, event: Dict[str, Any]) -> None:
        """将事件追加到日志文件"""
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                log_data = json.load(f)
            
            log_data['pipeline_session']['events'].append(event)
            
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] Failed to append to log file: {str(e)}")
    
    def log_script_result(self, script_name: str, result: Dict[str, Any]) -> None:
        """
        从 Result Object 记录脚本执行结果
        
        Args:
            script_name: 脚本名称
            result: Result Object (dict)
        """
        status = "PASS" if result.get("status") == "success" else "FAIL"
        self.log_event(
            stage=f"script:{script_name}",
            status=status,
            data={
                "code": result.get("code"),
                "message": result.get("message"),
                "output": result.get("data", {})
            },
            duration_ms=result.get("metadata", {}).get("execution_time_ms")
        )
    
    def get_summary(self) -> Dict[str, Any]:
        """生成日志摘要"""
        total = len(self.events)
        passed = len([e for e in self.events if e["status"] == "PASS"])
        failed = len([e for e in self.events if e["status"] == "FAIL"])
        total_duration_ms = sum(e.get("duration_ms") or 0 for e in self.events)
        
        return {
            "total_events": total,
            "passed": passed,
            "failed": failed,
            "success_rate": f"{passed/total*100:.1f}%" if total > 0 else "N/A",
            "total_duration_ms": total_duration_ms,
            "stages": list(set(e["stage"] for e in self.events))
        }
    
    def generate_report(self) -> None:
        """生成完整的日志报告"""
        summary = self.get_summary()
        
        report = {
            "session": {
                "start_time": self.events[0]["timestamp"] if self.events else None,
                "end_time": self.events[-1]["timestamp"] if self.events else None,
                "summary": summary
            },
            "events": self.events
        }
        
        # 保存完整报告
        report_file = self.log_file.parent / f"pipeline_report_{int(time.time())}.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"\n[LOG REPORT] {report_file}")
        print(f"  总事件数: {summary['total_events']}")
        print(f"  成功: {summary['passed']} | 失败: {summary['failed']}")
        print(f"  成功率: {summary['success_rate']}")
        print(f"  总耗时: {summary['total_duration_ms']}ms")
        print(f"  涉及阶段: {', '.join(summary['stages'])}")


def create_aggregator(workspace_root: str = None) -> LogAggregator:
    """
    创建日志聚合器实例（便利函数）
    
    Args:
        workspace_root: 工作区根目录
    
    Returns:
        LogAggregator 实例
    """
    if workspace_root is None:
        workspace_root = str(Path.cwd())
    
    log_dir = Path(workspace_root) / "过程产物" / "日志"
    log_file = log_dir / "pipeline.log"
    
    return LogAggregator(str(log_file), workspace_root)


if __name__ == "__main__":
    # 测试
    agg = LogAggregator("/tmp/test_pipeline.log")
    
    agg.log_event("preflight_check", "PASS", {"mode": "audit"}, 1250)
    agg.log_event("adjust_timeline", "PASS", {"scenes": 7}, 850)
    agg.log_event("render", "FAIL", {"error": "Duration mismatch"}, 0, ["Duration mismatch: 146s vs 313s"])
    
    agg.generate_report()
