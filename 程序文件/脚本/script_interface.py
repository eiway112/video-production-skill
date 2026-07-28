#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
脚本接口规范化基础模块

统一所有工作流脚本的：
1. 入参格式（Config Object Pattern）
2. 出参格式（Result Object Pattern）
3. 错误处理（标准 exit code）
4. 日志格式（统一 Logger）

用法：
    from script_interface import Config, Result, Logger, EXIT_CODE
    
    config = Config.from_file("config.json")
    logger = Logger("my_script", config.log_level)
    
    try:
        result = Result.success(data={"processed": 100})
    except Exception as e:
        result = Result.failure(code=EXIT_CODE.RUNTIME_ERROR, message=str(e))
    
    result.print_to_stdout()
    sys.exit(result.code)
"""

import json
import sys
import os
import time
import io
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import hashlib


# === Exit Code 标准定义 ===
class EXIT_CODE:
    """标准 exit code 定义"""
    SUCCESS = 0
    RECOVERABLE_ERROR = 1
    CONFIG_ERROR = 2
    MISSING_DEPENDENCY = 3
    GATE_FAILURE = 4  # HARD_GATES 失败
    RUNTIME_ERROR = 5
    INTERNAL_ERROR = 6


# === Logger 类 ===
class Logger:
    """统一日志记录器
    
    输出格式：
    [TIMESTAMP] [LEVEL] [MODULE] - MESSAGE
    """
    
    LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
    
    def __init__(self, module_name: str, level: str = "INFO"):
        self.module_name = module_name
        self.level = level
        self.min_level = self.LEVELS.get(level, 1)
        
        # 配置 stderr 编码为 UTF-8
        if sys.platform == "win32":
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding='utf-8', errors='replace'
            )
    
    def _format_message(self, level: str, message: str) -> str:
        """格式化日志消息"""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return f"[{ts}] [{level:5s}] [{self.module_name}] - {message}"
    
    def debug(self, message: str):
        if self.LEVELS.get("DEBUG", 0) >= self.min_level:
            print(self._format_message("DEBUG", message), file=sys.stderr)
    
    def info(self, message: str):
        if self.LEVELS.get("INFO", 1) >= self.min_level:
            print(self._format_message("INFO", message), file=sys.stderr)
    
    def warn(self, message: str):
        if self.LEVELS.get("WARN", 2) >= self.min_level:
            print(self._format_message("WARN", message), file=sys.stderr)
    
    def error(self, message: str):
        if self.LEVELS.get("ERROR", 3) >= self.min_level:
            print(self._format_message("ERROR", message), file=sys.stderr)


# === Config 类 ===
class Config:
    """标准配置对象
    
    结构：
    {
        "meta": {
            "script_version": "1.0.0",
            "timestamp": "2026-07-15T10:30:00Z",
            "input_fingerprint": "sha256_hash",
            "retry_policy": "exponential_backoff"
        },
        "input": { ... },
        "environment": {
            "log_level": "info",
            "output_format": "json",
            "working_dir": "/path"
        },
        "quality_gates": { ... }
    }
    """
    
    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.meta = data.get("meta", {})
        self.input = data.get("input", {})
        self.environment = data.get("environment", {})
        self.quality_gates = data.get("quality_gates", {})
    
    @staticmethod
    def from_file(config_path: str) -> 'Config':
        """从 JSON 文件读取配置"""
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return Config(data)
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'Config':
        """从字典创建配置"""
        return Config(data)
    
    def get_log_level(self) -> str:
        """获取日志级别"""
        return self.environment.get("log_level", "INFO").upper()
    
    def get_output_format(self) -> str:
        """获取输出格式"""
        return self.environment.get("output_format", "json")
    
    def get_working_dir(self) -> Path:
        """获取工作目录"""
        work_dir = self.environment.get("working_dir", ".")
        return Path(work_dir)
    
    def get_input_fingerprint(self) -> str:
        """获取输入指纹"""
        return self.meta.get("input_fingerprint", "")
    
    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return self.data


# === Result 类 ===
class Result:
    """标准返回值对象
    
    结构：
    {
        "status": "success|failure|partial",
        "code": 0,
        "message": "...",
        "data": { ... },
        "metadata": {
            "execution_time_ms": 1234,
            "memory_peak_mb": 256,
            "version": "1.0.0"
        },
        "diagnostics": {
            "warnings": [],
            "errors": []
        }
    }
    """
    
    def __init__(
        self,
        status: str,
        code: int,
        message: str,
        data: Dict[str, Any] = None,
        version: str = "1.0.0",
        execution_time_ms: int = 0,
    ):
        self.status = status
        self.code = code
        self.message = message
        self.data = data or {}
        self.version = version
        self.execution_time_ms = execution_time_ms
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.memory_peak_mb = 0
    
    @staticmethod
    def success(
        data: Dict[str, Any] = None,
        message: str = "Success",
        execution_time_ms: int = 0,
    ) -> 'Result':
        """创建成功结果"""
        return Result(
            status="success",
            code=EXIT_CODE.SUCCESS,
            message=message,
            data=data,
            execution_time_ms=execution_time_ms,
        )
    
    @staticmethod
    def failure(
        code: int = EXIT_CODE.RUNTIME_ERROR,
        message: str = "Failure",
        data: Dict[str, Any] = None,
    ) -> 'Result':
        """创建失败结果"""
        return Result(
            status="failure",
            code=code,
            message=message,
            data=data,
        )
    
    @staticmethod
    def partial(
        code: int = EXIT_CODE.RECOVERABLE_ERROR,
        message: str = "Partial completion",
        data: Dict[str, Any] = None,
        warnings: List[str] = None,
    ) -> 'Result':
        """创建部分成功结果"""
        result = Result(
            status="partial",
            code=code,
            message=message,
            data=data,
        )
        if warnings:
            result.warnings = warnings
        return result
    
    def add_warning(self, warning: str):
        """添加警告"""
        self.warnings.append(warning)
    
    def add_error(self, error: str):
        """添加错误诊断"""
        self.errors.append(error)
    
    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return {
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "data": self.data,
            "metadata": {
                "execution_time_ms": self.execution_time_ms,
                "memory_peak_mb": self.memory_peak_mb,
                "version": self.version,
            },
            "diagnostics": {
                "warnings": self.warnings,
                "errors": self.errors,
            },
        }
    
    def to_json(self) -> str:
        """转为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    def print_to_stdout(self):
        """打印到 stdout（JSON 格式）"""
        output = self.to_json()
        # 在 Windows 下直接写入 buffer，避免重复包装 sys.stdout 导致句柄关闭
        if sys.platform == "win32" and hasattr(sys.stdout, 'buffer'):
            try:
                sys.stdout.buffer.write(output.encode('utf-8', errors='replace'))
                sys.stdout.buffer.write(b'\n')
                sys.stdout.buffer.flush()
                return
            except Exception:
                pass
        print(output)


# === 辅助函数 ===
def compute_fingerprint(config: Config) -> str:
    """计算输入指纹（用于缓存验证）"""
    input_json = json.dumps(config.input, sort_keys=True)
    return hashlib.sha256(input_json.encode('utf-8')).hexdigest()


def get_minimal_config(log_level: str = "INFO") -> Config:
    """获取最小配置（仅指定日志级别）"""
    return Config({
        "meta": {
            "script_version": "1.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "input": {},
        "environment": {
            "log_level": log_level,
            "output_format": "json",
            "working_dir": ".",
        },
        "quality_gates": {},
    })


import subprocess


# === V2 Wrapper 基类 ===
class V2WrapperBase:
    """V2 脚本公共基类 - subprocess 包装模式（模板方法模式）

    子类只需实现差异化的三个方法即可获得完整的：
    - Config 验证
    - 命令行构建
    - subprocess 执行（带超时）
    - 输出解析与 Result 封装
    - 错误处理与日志记录

    用法::

        class MyScriptV2(V2WrapperBase):
            SCRIPT_NAME = "my_script_v2"
            TARGET_SCRIPT = "my_script.py"

            def _validate_config(self) -> bool: ...
            def _build_command(self) -> list: ...
            def _map_exit_code(self, exit_code: int) -> tuple: ...
    """

    SCRIPT_NAME: str = ""
    TARGET_SCRIPT: str = ""
    TIMEOUT_SECONDS: int = 3600

    def __init__(self, config: Config):
        self.config = config
        self.logger = Logger(
            self.SCRIPT_NAME or self.__class__.__name__,
            config.get_log_level(),
        )
        self.start_time = time.time()

    def run(self) -> Result:
        """模板方法：validate → build_command → execute → parse → Result"""
        try:
            self.logger.info(f"{self.SCRIPT_NAME} started")

            # 1. 验证配置
            if not self._validate_config():
                return Result.failure(
                    code=EXIT_CODE.CONFIG_ERROR,
                    message="Configuration validation failed",
                )

            # 2. 构建命令
            cmd = self._build_command()
            self.logger.info(f"Executing: {' '.join(cmd)}")

            # 3. 执行子进程
            exit_code, stdout, stderr = self._execute(cmd)
            execution_time_ms = int((time.time() - self.start_time) * 1000)

            # 4. 映射退出码
            result_code, status = self._map_exit_code(exit_code)

            # 5. 构建 Result
            message = f"{self.SCRIPT_NAME} completed with exit code {exit_code}"
            if status == "success":
                result = Result.success(message=message, execution_time_ms=execution_time_ms)
            elif status == "partial":
                result = Result.partial(code=result_code, message=message)
            else:
                result = Result.failure(code=result_code, message=message)

            # 6. 解析 stdout 数据
            parsed_data = self._parse_stdout_data(stdout)
            if parsed_data:
                result.data = parsed_data

            # 7. 提取诊断信息
            diag = self._extract_diagnostics(stdout, stderr)
            for err in diag.get("errors", []):
                result.add_error(err)
            for warn in diag.get("warnings", []):
                result.add_warning(warn)

            result.execution_time_ms = execution_time_ms
            self.logger.info(
                f"Completed: status={result.status}, code={result.code}, "
                f"time={execution_time_ms}ms"
            )
            return result

        except NotImplementedError as e:
            self.logger.error(f"Subclass method not implemented: {e}")
            return Result.failure(
                code=EXIT_CODE.INTERNAL_ERROR,
                message=f"Not implemented: {e}",
            )
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
            return Result.failure(
                code=EXIT_CODE.RUNTIME_ERROR,
                message=f"Unexpected error: {e}",
            )

    def _execute(self, cmd: list) -> tuple:
        """公共：subprocess 执行，返回 (exit_code, stdout, stderr)"""
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self._get_scripts_dir()),
                env=os.environ.copy(),
            )
            stdout, stderr = proc.communicate(timeout=self.TIMEOUT_SECONDS)
            return proc.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.logger.error(f"Execution timeout ({self.TIMEOUT_SECONDS}s)")
            return EXIT_CODE.RUNTIME_ERROR, "", "Timeout expired"

    def _get_python_exe(self) -> str:
        """公共：获取 Python 解释器路径（优先 venv）"""
        venv_python = (
            Path(__file__).resolve().parent.parent
            / "运行环境" / "venv" / "Scripts" / "python.exe"
        )
        if venv_python.exists():
            return str(venv_python)
        return sys.executable

    def _get_scripts_dir(self) -> Path:
        """公共：获取脚本目录路径"""
        return Path(__file__).resolve().parent

    # === 子类必须实现 ===
    def _validate_config(self) -> bool:
        """子类实现：验证 config 是否包含必要字段"""
        raise NotImplementedError("_validate_config")

    def _build_command(self) -> list:
        """子类实现：构建 subprocess 命令行参数列表"""
        raise NotImplementedError("_build_command")

    def _map_exit_code(self, exit_code: int) -> tuple:
        """子类实现：将 exit_code 映射为 (EXIT_CODE枚举值, status字符串)

        status 取值: 'success' | 'partial' | 'failure'
        """
        raise NotImplementedError("_map_exit_code")

    # === 子类可选重写 ===
    def _parse_stdout_data(self, stdout: str) -> dict:
        """可选重写：从 stdout 提取结构化数据，默认返回空 dict"""
        return {}

    def _extract_diagnostics(self, stdout: str, stderr: str) -> dict:
        """可选重写：提取诊断信息，默认提取 [ERROR]/[WARN] 行"""
        errors = []
        warnings = []
        for line in (stderr or "").split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if "[ERROR]" in stripped or "ERROR" in stripped:
                errors.append(stripped)
            elif "[WARN]" in stripped or "WARN" in stripped:
                warnings.append(stripped)
        return {"errors": errors, "warnings": warnings}


if __name__ == "__main__":
    # 测试
    logger = Logger("test_script", "INFO")
    logger.info("Script interface module loaded")
    
    # 测试 Config
    config = get_minimal_config()
    logger.info(f"Config: {config.to_dict()}")
    
    # 测试 Result
    result = Result.success(data={"processed": 100}, message="Test completed")
    result.add_warning("This is a test warning")
    logger.info("Result created, printing to stdout...")
    result.print_to_stdout()
