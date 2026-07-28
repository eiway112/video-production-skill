#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ImageGen 能力探测与正式执行层（P1-09 整改）

功能：
  1. 探测 Qoder ImageGen 是否可编程调用
  2. 为需要图片的场景创建请求并记录
  3. 保存请求参数、错误码、产物关联
  4. 建立失败降级和重试机制（指数退避）
  5. 基于 prompt SHA256 的请求去重 / 缓存命中
  6. 集成 pipeline_state_fingerprint 产物追踪
  
  如果 ImageGen 不可编程调用，则：
  - 建模为"人工关卡"
  - 在 manifest 中保留待审核标记
  - 交付报告中明确披露
  - 返回结构化降级 Result（不阻断流水线）

用法：
  from imagegen_executor import ImageGenExecutor, probe_imagegen_capability
  
  # 探测能力
  available, error = probe_imagegen_capability()
  if not available:
      print(f"ImageGen 不可用: {error}")
  
  # 执行请求
  executor = ImageGenExecutor(
      manifest_path='imagegen_manifest.json',
      output_dir='素材文件/图片',
      capability_available=available,
      retry_config={'max_retries': 5, 'base_delay': 2, 'backoff_factor': 2}
  )
  
  result_record = executor.request_image(
      scene_id='s1',
      prompt='Technical diagram showing stress distribution',
      language='zh'
  )
"""

import json
import os
import sys
import time
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List

# 本地模块导入
from script_interface import Result, Logger, EXIT_CODE
from pipeline_state_fingerprint import PipelineStateFingerprint


# === 默认重试配置 ===
DEFAULT_RETRY_CONFIG: Dict[str, Any] = {
    "max_retries": 5,
    "base_delay": 2,       # 秒
    "backoff_factor": 2,   # 退避因子
}

# === 连续失败降级阈值 ===
DEGRADATION_THRESHOLD = 3


def probe_imagegen_capability() -> Tuple[bool, Optional[str]]:
    """探测 Qoder ImageGen 是否可编程调用
    
    返回：
      (available, error_message)
      如果 available=True，error_message 为 None
      如果 available=False，error_message 说明失败原因
    """
    
    print("[INFO] Probing ImageGen capability...")
    
    # 方案1：尝试通过 env 变量或配置文件检查工具可用性
    qoder_home = os.getenv("QODER_HOME")
    if not qoder_home:
        return False, "QODER_HOME environment variable not set"
    
    # 方案2：尝试调用一个简单的测试请求（如果 Qoder 提供 CLI）
    try:
        result = subprocess.run(
            ["qoder", "capabilities", "--show", "imagegen"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and "imagegen" in result.stdout.lower():
            return True, None
        else:
            return False, "ImageGen tool not found in capabilities"
    except FileNotFoundError:
        return False, "Qoder CLI not found in PATH"
    except Exception as e:
        return False, str(e)


class ImageGenExecutor:
    """ImageGen 请求执行器
    
    支持：
      - 基于 prompt SHA256 的请求去重（缓存命中直接返回）
      - 可配置的指数退避重试策略
      - pipeline_state_fingerprint 产物追踪集成
      - 降级策略：连续失败超阈值时不阻断流水线
    """
    
    def __init__(self, 
                 manifest_path: str,
                 output_dir: str,
                 capability_available: bool = False,
                 retry_config: Optional[Dict[str, Any]] = None,
                 cache_dir: Optional[str] = None,
                 pipeline_state_file: Optional[str] = None):
        """初始化执行器
        
        参数：
          manifest_path: imagegen_manifest.json 的路径
          output_dir: 生成图片的输出目录
          capability_available: ImageGen 是否可用
          retry_config: 重试策略配置 {'max_retries': int, 'base_delay': float, 'backoff_factor': float}
                        传 None 则使用默认值（5次/2s/2x）
          cache_dir: 缓存目录路径（存放 imagegen_cache.json），默认为 manifest 同级的临时目录
          pipeline_state_file: pipeline_state.json 路径，传入则启用指纹追踪
        """
        self.manifest_path = Path(manifest_path)
        self.output_dir = Path(output_dir)
        self.capability_available = capability_available
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 重试配置（可外部覆盖）
        self.retry_config = {**DEFAULT_RETRY_CONFIG, **(retry_config or {})}
        
        # 日志
        self.logger = Logger("imagegen_executor", "INFO")
        
        # 缓存目录与缓存文件
        if cache_dir:
            self._cache_dir = Path(cache_dir)
        else:
            self._cache_dir = self.manifest_path.parent
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / "imagegen_cache.json"
        self._cache = self._load_cache()
        
        # 指纹系统集成
        self._pipeline_state: Optional[PipelineStateFingerprint] = None
        if pipeline_state_file:
            self._pipeline_state = PipelineStateFingerprint(pipeline_state_file)
        
        # 连续失败计数器（用于降级判断）
        self._consecutive_failures = 0
        
        # manifest
        self.manifest = self._load_manifest()
    
    # ------------------------------------------------------------------
    # 缓存管理
    # ------------------------------------------------------------------
    
    def _load_cache(self) -> Dict[str, str]:
        """加载 prompt 去重缓存（prompt_hash → image_path）"""
        if self._cache_file.exists():
            try:
                with open(self._cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
    
    def _save_cache(self):
        """持久化缓存映射"""
        with open(self._cache_file, 'w', encoding='utf-8') as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=2)
    
    @staticmethod
    def _hash_prompt(prompt: str) -> str:
        """计算 prompt 的 SHA256 哈希（用于去重）"""
        return hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    
    def _cache_lookup(self, prompt_hash: str) -> Optional[str]:
        """查找缓存：仅当图片文件实际存在时命中
        
        返回：
          命中时返回图片绝对路径字符串，否则 None
        """
        cached_path = self._cache.get(prompt_hash)
        if cached_path and Path(cached_path).exists():
            return cached_path
        # 路径不存在，清除无效缓存
        if cached_path:
            del self._cache[prompt_hash]
            self._save_cache()
        return None
    
    def _cache_store(self, prompt_hash: str, image_path: str):
        """写入缓存"""
        self._cache[prompt_hash] = image_path
        self._save_cache()
    
    # ------------------------------------------------------------------
    # Manifest 管理
    # ------------------------------------------------------------------
    
    def _load_manifest(self) -> Dict[str, Any]:
        """加载现有 manifest"""
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        
        # 默认结构
        return {
            "version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "capability_available": self.capability_available,
            "requests": []
        }
    
    def _save_manifest(self):
        """保存 manifest（排除运行时内部对象如 _result）"""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 深拷贝 manifest 并剔除以 '_' 开头的运行时键（如 _result: Result 对象）
        def _sanitize(obj):
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items() if not k.startswith('_')}
            elif isinstance(obj, list):
                return [_sanitize(item) for item in obj]
            return obj
        
        serializable = _sanitize(self.manifest)
        with open(self.manifest_path, 'w', encoding='utf-8') as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    
    # ------------------------------------------------------------------
    # 指纹系统集成
    # ------------------------------------------------------------------
    
    def _record_artifact_for_fingerprint(self, image_path: str, prompt_hash: str):
        """将 ImageGen 产物记录到 pipeline_state_fingerprint 系统
        
        在成功生成图片后调用，写入 manifest 记录供指纹系统追踪：
          - 图片路径
          - prompt hash
          - 生成时间戳
        """
        if not self._pipeline_state:
            return
        
        # 通过 complete_step 的 actual_outputs 记录产物
        self._pipeline_state.complete_step(
            step_name="imagegen",
            passed=True,
            actual_outputs={
                "image_path": image_path,
                "prompt_hash": prompt_hash,
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
        )
        self.logger.info(f"Artifact recorded in pipeline fingerprint: {image_path}")
    
    # ------------------------------------------------------------------
    # 降级策略
    # ------------------------------------------------------------------
    
    def _build_degradation_result(self, scene_id: str, prompt: str, reason: str) -> Result:
        """构建降级 Result 对象（不阻断流水线）
        
        参数：
          scene_id: 场景 ID
          prompt: 原始 prompt
          reason: 降级原因
        
        返回：
          Result 对象，status='partial'，标记 manual_review_required
        """
        result = Result.partial(
            code=EXIT_CODE.RECOVERABLE_ERROR,
            message=f"ImageGen degraded for scene '{scene_id}': {reason}",
            data={
                "scene_id": scene_id,
                "prompt_hash": self._hash_prompt(prompt),
                "status": "manual_review_required",
                "reason": reason,
                "action_required": "人工介入生成或替换图片素材"
            },
            warnings=[
                f"ImageGen 降级：{reason}",
                "流水线未阻断，该步骤标记为 manual_review_required"
            ]
        )
        self.logger.warn(f"[DEGRADATION] scene={scene_id} | reason={reason} | 需要人工介入")
        return result
    
    # ------------------------------------------------------------------
    # 核心执行
    # ------------------------------------------------------------------
    
    def request_image(self,
                     scene_id: str,
                     prompt: str,
                     language: str = 'en',
                     max_retries: Optional[int] = None) -> Dict[str, Any]:
        """请求生成一张图片
        
        流程：
          1. 计算 prompt SHA256 哈希
          2. 缓存命中检查 → 命中直接返回
          3. 降级检查 → 连续失败超阈值则降级
          4. 指数退避重试执行
          5. 成功后写入缓存 + 指纹追踪
        
        参数：
          scene_id: 场景 ID
          prompt: 英文 prompt
          language: 图片标题语言（'en' 或 'zh'）
          max_retries: 重试次数（传 None 使用构造时配置）
        
        返回：
          请求记录（dict），降级时包含 '_result' 键存放 Result 对象
        """
        effective_max_retries = max_retries if max_retries is not None else self.retry_config["max_retries"]
        base_delay = self.retry_config["base_delay"]
        backoff_factor = self.retry_config["backoff_factor"]
        
        prompt_hash = self._hash_prompt(prompt)
        
        # --- 去重：缓存命中检查 ---
        cached_path = self._cache_lookup(prompt_hash)
        if cached_path:
            self.logger.info(f"Cache HIT for scene={scene_id}, hash={prompt_hash[:12]}..., path={cached_path}")
            cache_record = {
                "scene_id": scene_id,
                "prompt": prompt,
                "prompt_hash": prompt_hash,
                "language": language,
                "status": "cache_hit",
                "output_file": cached_path,
                "resolved_at": datetime.now(timezone.utc).isoformat()
            }
            self.manifest["requests"].append(cache_record)
            self._save_manifest()
            return cache_record
        
        # --- 构建请求记录 ---
        request_record: Dict[str, Any] = {
            "scene_id": scene_id,
            "prompt": prompt,
            "prompt_hash": prompt_hash,
            "language": language,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "attempt": 0,
            "errors": []
        }
        
        # --- 降级判断：能力不可用 ---
        if not self.capability_available:
            request_record["status"] = "manual_review_required"
            request_record["reason"] = "ImageGen tool not available - requires manual image creation"
            request_record["_result"] = self._build_degradation_result(
                scene_id, prompt, "ImageGen 探测不可用"
            )
            self.manifest["requests"].append(request_record)
            self._save_manifest()
            return request_record
        
        # --- 降级判断：连续失败超过阈值 ---
        if self._consecutive_failures >= DEGRADATION_THRESHOLD:
            request_record["status"] = "manual_review_required"
            request_record["reason"] = (
                f"Consecutive failures ({self._consecutive_failures}) exceeded threshold ({DEGRADATION_THRESHOLD})"
            )
            request_record["_result"] = self._build_degradation_result(
                scene_id, prompt,
                f"连续失败 {self._consecutive_failures} 次，超过降级阈值 {DEGRADATION_THRESHOLD}"
            )
            self.manifest["requests"].append(request_record)
            self._save_manifest()
            return request_record
        
        # --- 指数退避重试执行 ---
        for attempt in range(effective_max_retries):
            request_record["attempt"] = attempt + 1
            wait_time = base_delay * (backoff_factor ** attempt)
            
            try:
                output_file = self.output_dir / f"imagegen_{scene_id}_{datetime.now(timezone.utc).timestamp():.0f}.png"
                
                result = subprocess.run(
                    [
                        "qoder", "generate", "image",
                        "--prompt", prompt,
                        "--output", str(output_file),
                        "--timeout", "60"
                    ],
                    capture_output=True,
                    text=True,
                    timeout=90
                )
                
                if result.returncode == 0 and output_file.exists():
                    # === 成功 ===
                    with open(output_file, 'rb') as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()
                    
                    abs_path = str(output_file.resolve())
                    
                    request_record["status"] = "generated"
                    request_record["output_file"] = abs_path
                    request_record["file_size"] = output_file.stat().st_size
                    request_record["file_hash"] = file_hash
                    request_record["generated_at"] = datetime.now(timezone.utc).isoformat()
                    
                    self.manifest["requests"].append(request_record)
                    self._save_manifest()
                    
                    # 写入去重缓存
                    self._cache_store(prompt_hash, abs_path)
                    
                    # 记录产物到指纹系统
                    self._record_artifact_for_fingerprint(abs_path, prompt_hash)
                    
                    # 重置连续失败计数
                    self._consecutive_failures = 0
                    
                    self.logger.info(f"Generated image for scene={scene_id}, path={abs_path}")
                    return request_record
                else:
                    # 调用失败
                    error_msg = result.stderr or result.stdout or "Unknown error"
                    request_record["errors"].append({
                        "attempt": attempt + 1,
                        "error": error_msg[:200],
                        "wait_before_retry": wait_time,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                    self.logger.warn(
                        f"Attempt {attempt + 1}/{effective_max_retries} failed | "
                        f"wait={wait_time}s | error={error_msg[:80]}"
                    )
                    
                    if attempt < effective_max_retries - 1:
                        time.sleep(wait_time)
                    
            except subprocess.TimeoutExpired:
                request_record["errors"].append({
                    "attempt": attempt + 1,
                    "error": "Timeout (> 90s)",
                    "wait_before_retry": wait_time,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                self.logger.warn(
                    f"Attempt {attempt + 1}/{effective_max_retries} timed out | wait={wait_time}s"
                )
                if attempt < effective_max_retries - 1:
                    time.sleep(wait_time)
                    
            except Exception as e:
                request_record["errors"].append({
                    "attempt": attempt + 1,
                    "error": str(e)[:200],
                    "wait_before_retry": wait_time,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                self.logger.warn(
                    f"Attempt {attempt + 1}/{effective_max_retries} exception | "
                    f"wait={wait_time}s | error={str(e)[:80]}"
                )
                if attempt < effective_max_retries - 1:
                    time.sleep(wait_time)
        
        # === 所有重试都失败 ===
        self._consecutive_failures += 1
        request_record["status"] = "failed"
        request_record["reason"] = f"Failed after {effective_max_retries} attempts"
        self.manifest["requests"].append(request_record)
        self._save_manifest()
        
        self.logger.error(
            f"All {effective_max_retries} attempts failed for scene={scene_id} | "
            f"consecutive_failures={self._consecutive_failures}"
        )
        
        # 如果连续失败达到阈值，后续请求将自动降级
        if self._consecutive_failures >= DEGRADATION_THRESHOLD:
            self.logger.error(
                f"[DEGRADATION TRIGGERED] consecutive_failures={self._consecutive_failures} >= "
                f"threshold={DEGRADATION_THRESHOLD}, subsequent requests will degrade"
            )
        
        return request_record
    
    def get_request_status(self, scene_id: str) -> Optional[Dict[str, Any]]:
        """获取场景的 ImageGen 请求状态"""
        for req in self.manifest.get("requests", []):
            if req["scene_id"] == scene_id:
                return req
        return None
    
    def get_summary(self) -> Dict[str, Any]:
        """获取 manifest 摘要"""
        requests = self.manifest.get("requests", [])
        return {
            "total_requests": len(requests),
            "generated": sum(1 for r in requests if r.get("status") == "generated"),
            "cache_hit": sum(1 for r in requests if r.get("status") == "cache_hit"),
            "failed": sum(1 for r in requests if r.get("status") == "failed"),
            "manual_review": sum(1 for r in requests if r.get("status") == "manual_review_required"),
            "pending": sum(1 for r in requests if r.get("status") == "pending"),
            "capability_available": self.manifest.get("capability_available", False),
            "cache_entries": len(self._cache),
            "consecutive_failures": self._consecutive_failures
        }
    
    def reset_degradation(self):
        """手动重置降级状态（例如在外部修复后重新启用）"""
        self._consecutive_failures = 0
        self.logger.info("Degradation counter reset to 0")


def main():
    """示例用法"""
    import argparse
    
    parser = argparse.ArgumentParser(description='ImageGen execution layer')
    parser.add_argument('--probe', action='store_true', help='Probe ImageGen capability')
    parser.add_argument('--manifest', help='Path to imagegen_manifest.json')
    parser.add_argument('--summary', action='store_true', help='Show manifest summary')
    
    args = parser.parse_args()
    
    if args.probe:
        available, error = probe_imagegen_capability()
        if available:
            print("[OK] ImageGen is available")
        else:
            print(f"[INFO] ImageGen not available: {error}")
        return
    
    if args.manifest and args.summary:
        executor = ImageGenExecutor(args.manifest, ".")
        summary = executor.get_summary()
        print(json.dumps(summary, indent=2))
        return


if __name__ == '__main__':
    main()
