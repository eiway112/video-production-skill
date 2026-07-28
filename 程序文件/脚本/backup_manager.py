#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
项目备份管理器
支持全量备份和增量备份，重点保护配置文件和核心源码
"""

import os
import sys
import json
import shutil
from pathlib import Path
from datetime import datetime

from config_manager import ConfigManager
from _script_env import ROOT as WF_ROOT


class BackupManager:
    """备份管理器"""

    # 备份优先级（越高越重要）
    BACKUP_PRIORITIES = {
        "配置": 10,       # 最高优先级
        "脚本": 8,
        "源码/hyperframes": 7,
        "源码/doc2video": 7,
        "源码/openmontage": 3,  # 可从 git 恢复，低优先级
        "成果文件": 9,
        "素材文件": 6,
    }

    def __init__(self, backup_root: Path = None):
        self.cm = ConfigManager()
        if backup_root is None:
            # 默认备份到工作流根目录的上级 _备份文件/（避免硬编码盘符）
            backup_root = WF_ROOT.parent / "_备份文件"
        self.backup_root = backup_root

    def create_backup(self, label: str = None, mode: str = "selective") -> Path:
        """
        创建备份
        mode: "full" 全量 | "selective" 选择性（仅核心文件）
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if label is None:
            label = f"backup_{timestamp}"

        backup_dir = self.backup_root / f"视频制作工作流_{label}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        print(f"备份目标: {backup_dir}")
        print(f"备份模式: {mode}")

        manifest = {
            "timestamp": timestamp,
            "mode": mode,
            "source": str(self.cm.paths.root),
            "items": []
        }

        if mode == "selective":
            # 仅备份核心文件（配置、脚本、自研源码）
            targets = [
                ("程序文件/配置", "config"),
                ("程序文件/脚本", "scripts"),
                ("程序文件/源码/hyperframes", "hyperframes_projects"),
                ("成果文件", "output"),
            ]
        else:
            # 全量备份（跳过 openmontage 和 node_modules）
            targets = [
                ("程序文件/配置", "config"),
                ("程序文件/脚本", "scripts"),
                ("程序文件/源码", "source"),
                ("成果文件", "output"),
                ("素材文件", "assets"),
            ]

        for src_rel, dst_name in targets:
            src_path = self.cm.paths.root / src_rel
            dst_path = backup_dir / dst_name
            if src_path.exists():
                shutil.copytree(src_path, dst_path, dirs_exist_ok=True,
                               ignore=shutil.ignore_patterns(
                                   "node_modules", "__pycache__", ".git", "*.pyc"))
                file_count = len(list(dst_path.rglob("*")))
                manifest["items"].append({
                    "source": src_rel,
                    "destination": dst_name,
                    "files": file_count,
                    "size_mb": round(
                        sum(f.stat().st_size for f in dst_path.rglob("*")
                            if f.is_file()) / 1024 / 1024, 2)
                })
                print(f"  [OK] {src_rel} -> {dst_name} ({file_count} 文件)")

        # 保存备份清单
        manifest_path = backup_dir / "manifest.json"
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        print(f"\n备份完成: {backup_dir}")
        print(f"清单: {manifest_path}")
        return backup_dir


def main():
    import argparse
    parser = argparse.ArgumentParser(description="项目备份管理器")
    parser.add_argument("--mode", choices=["full", "selective"],
                        default="selective", help="备份模式")
    parser.add_argument("--label", default=None, help="备份标签")
    args = parser.parse_args()

    bm = BackupManager()
    bm.create_backup(label=args.label, mode=args.mode)


if __name__ == "__main__":
    main()
