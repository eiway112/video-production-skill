#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline 状态指纹管理模块

功能：
  为 Pipeline 的每个步骤生成和验证输入指纹（SHA256 哈希）
  
  当输入（配置、旁白、HTML）发生变化时，自动使相关步骤及其下游失效
  防止旧状态被应用到新输入的"缓存中毒"问题

用法：
  from pipeline_state_fingerprint import PipelineStateFingerprint
  
  pstate = PipelineStateFingerprint(state_file='pipeline_state.json')
  
  # 记录步骤开始
  pstate.start_step('tts', 
      inputs={
          'config': 'wall-crack-remedy.json',
          'narration': 'narration.json',
          'compiled_scenes': '_compiled_scenes.json'
      },
      script_version='git-hash-abc123'
  )
  
  # 记录步骤完成
  pstate.complete_step('tts', passed=True, outputs={'manifest': 'tts_manifest.json'})
  
  # 检查步骤是否应重新执行（输入指纹不一致）
  should_rerun = pstate.should_rerun_step('tts', inputs={...})
"""

import json
import hashlib
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

def compute_file_hash(filepath: str) -> str:
    """计算文件的 SHA256 哈希"""
    try:
        with open(filepath, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        return f"error:{str(e)}"

def compute_string_hash(content: str) -> str:
    """计算字符串的 SHA256 哈希"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

def compute_inputs_fingerprint(inputs: Dict[str, str]) -> str:
    """计算一组输入文件的联合指纹（模块级，单一哈希实现）

    pipeline_runner 的跳步判定与本模块的 should_rerun_step 共用此函数，
    保证两处指纹算法永远一致。

    参数：
      inputs: {名称 -> 文件路径} 的字典

    返回：
      输入指纹的 SHA256 哈希
    """
    hashes = {}
    for name, path in sorted(inputs.items()):
        if os.path.isabs(str(path)) and os.path.exists(path):
            hashes[name] = compute_file_hash(path)
        else:
            # 相对路径或不存在的文件
            hashes[name] = f"notfound:{path}"

    combined = json.dumps(hashes, sort_keys=True)
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()

class PipelineStateFingerprint:
    """管理 Pipeline 执行状态和输入指纹"""
    
    def __init__(self, state_file: str):
        """初始化状态管理器
        
        参数：
          state_file: pipeline_state.json 的路径
        """
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
    
    def _load_state(self) -> Dict[str, Any]:
        """加载现有状态文件"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] Failed to load state file: {e}")
        
        # 默认状态结构
        return {
            "version": "2.0",  # 指纹版本
            "created_at": datetime.now(timezone.utc).isoformat(),
            "steps": {}
        }
    
    def _save_state(self):
        """保存状态文件"""
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)
    
    def _compute_input_fingerprint(self, inputs: Dict[str, str]) -> str:
        """
        计算输入指纹

        参数：
          inputs: {名称 -> 文件路径} 的字典

        返回：
          输入指纹的 SHA256 哈希
        """
        return compute_inputs_fingerprint(inputs)
    
    def start_step(self, 
                   step_name: str, 
                   inputs: Optional[Dict[str, str]] = None,
                   script_version: Optional[str] = None) -> None:
        """记录步骤开始执行
        
        参数：
          step_name: 步骤名称（如 'tts', 'render'）
          inputs: 输入文件清单 {名称 -> 相对或绝对路径}
          script_version: 执行脚本版本（如 Git commit hash）
        """
        input_fingerprint = ""
        if inputs:
            input_fingerprint = self._compute_input_fingerprint(inputs)
        
        self._state["steps"][step_name] = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "input_fingerprint": input_fingerprint,
            "input_files": inputs or {},
            "script_version": script_version,
            "expected_outputs": {},
            "actual_outputs": {},
            "passed": None,
            "error": None
        }
        self._save_state()
    
    def complete_step(self, 
                     step_name: str, 
                     passed: bool, 
                     expected_outputs: Optional[Dict[str, str]] = None,
                     actual_outputs: Optional[Dict[str, str]] = None,
                     error: Optional[str] = None) -> None:
        """记录步骤完成
        
        参数：
          step_name: 步骤名称
          passed: 是否通过
          expected_outputs: 预期输出清单
          actual_outputs: 实际输出清单
          error: 如果失败，记录错误信息
        """
        if step_name not in self._state["steps"]:
            self.start_step(step_name)
        
        self._state["steps"][step_name].update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "passed": passed,
            "expected_outputs": expected_outputs or {},
            "actual_outputs": actual_outputs or {},
            "error": error
        })
        
        # 如果此步骤失败，标记所有下游步骤为失效
        if not passed:
            self._invalidate_downstream(step_name)
        
        self._save_state()
    
    def _invalidate_downstream(self, step_name: str) -> None:
        """标记所有下游步骤为失效（需要重新执行）"""
        # 简单的依赖关系：按步骤顺序
        step_order = ["preflight", "tts", "timeline", "preview", "render", 
                     "verify", "visual_check", "postprocess"]
        
        if step_name not in step_order:
            return
        
        idx = step_order.index(step_name)
        downstream = step_order[idx + 1:]
        
        for downstream_step in downstream:
            if downstream_step in self._state["steps"]:
                self._state["steps"][downstream_step]["status"] = "invalidated"
                self._state["steps"][downstream_step]["passed"] = False
    
    def should_rerun_step(self, step_name: str, inputs: Dict[str, str]) -> bool:
        """判断步骤是否应重新执行
        
        重新执行的条件：
        1. 步骤未曾执行过
        2. 步骤状态为失败或失效
        3. 输入指纹与之前不一致（输入已变化）
        
        返回：
          True 表示应重新执行，False 表示可以使用缓存
        """
        if step_name not in self._state["steps"]:
            # 从未执行过
            return True
        
        step_record = self._state["steps"][step_name]
        
        # 检查状态
        if step_record.get("status") == "invalidated":
            return True
        
        if not step_record.get("passed", False):
            return True
        
        # 检查输入指纹
        old_fingerprint = step_record.get("input_fingerprint", "")
        new_fingerprint = self._compute_input_fingerprint(inputs)
        
        if old_fingerprint != new_fingerprint:
            print(f"[INFO] Input fingerprint changed for {step_name}")
            print(f"       Old: {old_fingerprint[:16]}...")
            print(f"       New: {new_fingerprint[:16]}...")
            return True
        
        return False
    
    def get_step_status(self, step_name: str) -> Dict[str, Any]:
        """获取步骤的完整状态信息"""
        return self._state["steps"].get(step_name, {})
    
    def get_all_steps(self) -> Dict[str, Any]:
        """获取所有步骤的状态"""
        return self._state["steps"]
    
    def reset_step(self, step_name: str) -> None:
        """重置步骤状态（用于手动重新执行）"""
        if step_name in self._state["steps"]:
            del self._state["steps"][step_name]
            self._invalidate_downstream(step_name)
            self._save_state()
    
    def mark_production_ready(self) -> None:
        """标记整个流水线为生产就绪（所有必要步骤都通过）"""
        self._state["production_ready"] = True
        self._state["ready_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
    
    def is_production_ready(self) -> bool:
        """检查流水线是否已生产就绪"""
        return self._state.get("production_ready", False)

def main():
    """示例用法"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Pipeline state fingerprint management')
    parser.add_argument('--state-file', required=True, help='Path to pipeline_state.json')
    parser.add_argument('--show', action='store_true', help='Display current state')
    parser.add_argument('--reset-step', help='Reset a specific step')
    
    args = parser.parse_args()
    
    pstate = PipelineStateFingerprint(args.state_file)
    
    if args.show:
        print(json.dumps(pstate.get_all_steps(), indent=2, ensure_ascii=False))
    
    if args.reset_step:
        pstate.reset_step(args.reset_step)
        print(f"[OK] Step '{args.reset_step}' and downstream steps invalidated")

if __name__ == '__main__':
    main()
