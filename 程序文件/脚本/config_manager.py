#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
统一配置管理器
管理 Doc2Video / OpenMontage / HyperFrames 三套工具链的配置
"""

import os
import json
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class ProjectPaths:
    """项目标准路径结构（基于项目根目录推导）"""
    root: Path
    output_video: Path = field(init=False)
    output_subtitle: Path = field(init=False)
    output_other: Path = field(init=False)
    source_doc2video: Path = field(init=False)
    source_openmontage: Path = field(init=False)
    source_hyperframes: Path = field(init=False)
    scripts: Path = field(init=False)
    scripts_tests: Path = field(init=False)
    config: Path = field(init=False)
    config_dev: Path = field(init=False)
    runtime: Path = field(init=False)
    assets_images: Path = field(init=False)
    assets_videos: Path = field(init=False)
    assets_docs: Path = field(init=False)
    assets_audio: Path = field(init=False)
    process_cache: Path = field(init=False)
    process_temp: Path = field(init=False)
    process_logs: Path = field(init=False)
    process_archive: Path = field(init=False)

    def __post_init__(self):
        self.output_video = self.root / "成果文件" / "视频"
        self.output_subtitle = self.root / "成果文件" / "字幕"
        self.output_other = self.root / "成果文件" / "其他"
        self.source_doc2video = self.root / "程序文件" / "源码" / "doc2video"
        self.source_openmontage = self.root / "程序文件" / "源码" / "openmontage"
        self.source_hyperframes = self.root / "程序文件" / "源码" / "hyperframes"
        self.scripts = self.root / "程序文件" / "脚本"
        self.scripts_tests = self.root / "程序文件" / "脚本" / "测试"
        self.config = self.root / "程序文件" / "配置"
        self.config_dev = self.root / "程序文件" / "配置" / "config_dev"
        self.runtime = self.root / "程序文件" / "运行环境"
        self.assets_images = self.root / "素材文件" / "图片"
        self.assets_videos = self.root / "素材文件" / "视频"
        self.assets_docs = self.root / "素材文件" / "文档"
        self.assets_audio = self.root / "素材文件" / "音频"
        self.process_cache = self.root / "过程产物" / "缓存"
        self.process_temp = self.root / "过程产物" / "临时产物"
        self.process_logs = self.root / "过程产物" / "日志"
        self.process_archive = self.root / "过程产物" / "归档"

    # Config subdirectory names (searched in order by resolve_config_path)
    _CONFIG_SUBDIRS = ("", "pipelines", "openmontage", "system")

    def resolve_config_path(self, name: str) -> Path:
        """Resolve a config file name to its full path, searching subdirectories.

        Search order: config/config/<name> → config/config/pipelines/<name>
                      → config/config/openmontage/<name> → config/config/system/<name>
        Returns the first match, or config/config/<name> if none found (for error messages).
        """
        base = self.config / "config"
        # If already absolute, return as-is
        p = Path(name)
        if p.is_absolute():
            return p
        # Search subdirectories
        for subdir in self._CONFIG_SUBDIRS:
            candidate = base / subdir / name if subdir else base / name
            if candidate.exists():
                return candidate
        # Not found — return root path for meaningful error message
        return base / name


class ConfigManager:
    """统一配置管理器"""

    def __init__(self, project_root: Optional[Path] = None):
        if project_root is None:
            project_root = Path(__file__).parent.parent.parent
        self.paths = ProjectPaths(root=project_root)

    def ensure_directories(self) -> None:
        """确保所有标准目录存在"""
        for attr in vars(self.paths):
            path = getattr(self.paths, attr)
            if isinstance(path, Path) and attr != 'root':
                path.mkdir(parents=True, exist_ok=True)

    def load_openmontage_env(self) -> Dict[str, str]:
        """加载 OpenMontage .env 配置"""
        env_path = self.paths.config / "openmontage.env"
        config = {}
        if env_path.exists():
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, value = line.split('=', 1)
                            config[key.strip()] = value.strip()
        return config

    def load_doc2video_config(self) -> Dict[str, Any]:
        """加载 Doc2Video 配置"""
        voice_config_path = self.paths.resolve_config_path("voice_config.json")
        if voice_config_path.exists():
            with open(voice_config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def load_hyperframes_config(self) -> Dict[str, Any]:
        """加载 HyperFrames 渲染配置"""
        hf_config_path = self.paths.resolve_config_path("hyperframes_config.json")
        if hf_config_path.exists():
            with open(hf_config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            "browser_path": os.environ.get("HYPERFRAMES_BROWSER_PATH", ""),
            "ffmpeg_path": os.environ.get("HYPERFRAMES_FFMPEG_PATH", ""),
            "default_resolution": "1920x1080",
            "default_fps": 25,  # 与 hyperframes_config.json 权威值一致（渲染器实际输出，2026-08-07 对齐）
            "default_format": "mp4"
        }

    def load_openmontage_config(self, config_name: Optional[str] = None) -> Dict[str, Any]:
        """加载 OpenMontage 项目配置，合并 openmontage.env 环境变量。

        Args:
            config_name: 配置文件名（如 'explainer_demo.json'）。
                         为 None 时仅返回模板配置 + env 合并结果。

        Returns:
            合并后的配置字典，包含 engine/pipeline/input/output/budget/runtime/paths 字段。
        """
        # 加载模板作为基础默认值
        template_path = self.paths.resolve_config_path("openmontage_template.json")
        base_config: Dict[str, Any] = {}
        if template_path.exists():
            with open(template_path, 'r', encoding='utf-8') as f:
                base_config = json.load(f)

        # 加载项目级配置（覆盖模板）
        if config_name:
            project_config_path = self.paths.resolve_config_path(config_name)
            if project_config_path.exists():
                with open(project_config_path, 'r', encoding='utf-8') as f:
                    project_config = json.load(f)
                # 深合并：项目配置覆盖模板
                for key, value in project_config.items():
                    if isinstance(value, dict) and isinstance(base_config.get(key), dict):
                        base_config[key].update(value)
                    else:
                        base_config[key] = value
            else:
                raise FileNotFoundError(f"OpenMontage config not found: {project_config_path}")

        # 合并 openmontage.env 中的运行时参数
        env_config = self.load_openmontage_env()
        if env_config:
            runtime = base_config.setdefault("runtime", {})
            if env_config.get("RENDER_RUNTIME"):
                runtime["compose_engine"] = env_config["RENDER_RUNTIME"]
            budget = base_config.setdefault("budget", {})
            if env_config.get("DEFAULT_BUDGET_CAP"):
                budget["cap_usd"] = float(env_config["DEFAULT_BUDGET_CAP"])
            if env_config.get("ENABLE_COST_TRACKING"):
                budget["cost_tracking"] = env_config["ENABLE_COST_TRACKING"].lower() == "true"

        return base_config

    def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置"""
        return {
            "project_root": str(self.paths.root),
            "doc2video": self.load_doc2video_config(),
            "openmontage": self.load_openmontage_env(),
            "hyperframes": self.load_hyperframes_config(),
            "paths": {k: str(v) for k, v in vars(self.paths).items()
                      if isinstance(v, Path)}
        }

    def validate(self) -> list:
        """验证项目完整性，返回问题列表"""
        issues = []
        critical_dirs = [
            ("程序文件/源码/openmontage", self.paths.source_openmontage),
            ("程序文件/源码/hyperframes", self.paths.source_hyperframes),
            ("程序文件/配置", self.paths.config),
            ("程序文件/脚本/ppt_to_video.py", self.paths.scripts / "ppt_to_video.py"),
        ]
        for name, path in critical_dirs:
            if not path.exists():
                issues.append(f"目录缺失: {name}")
            elif path.is_dir() and not any(path.iterdir()):
                issues.append(f"目录为空: {name}")

        om_env = self.paths.config / "openmontage.env"
        if not om_env.exists():
            issues.append("配置文件缺失: openmontage.env")

        import shutil
        for tool in ["python", "node", "ffmpeg"]:
            if not shutil.which(tool):
                issues.append(f"系统工具缺失: {tool}")

        return issues

    def print_status(self) -> None:
        """打印项目状态摘要"""
        print("=" * 60)
        print("视频制作工作流 - 项目状态")
        print("=" * 60)
        print(f"项目根目录: {self.paths.root}")
        print()

        print("[源码目录]")
        for name, path in [
            ("Doc2Video", self.paths.source_doc2video),
            ("OpenMontage", self.paths.source_openmontage),
            ("HyperFrames", self.paths.source_hyperframes),
        ]:
            status = "OK" if path.exists() and any(path.iterdir()) else "未就绪"
            print(f"  {name}: {status}")

        print("\n[成果文件]")
        videos = list(self.paths.output_video.glob("*.mp4")) if self.paths.output_video.exists() else []
        subtitles = list(self.paths.output_subtitle.glob("*.srt")) if self.paths.output_subtitle.exists() else []
        print(f"  视频: {len(videos)} 个")
        print(f"  字幕: {len(subtitles)} 个")

        issues = self.validate()
        if issues:
            print(f"\n[待处理问题: {len(issues)}]")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("\n[验证] 全部通过")


if __name__ == "__main__":
    manager = ConfigManager()
    manager.ensure_directories()
    manager.print_status()
