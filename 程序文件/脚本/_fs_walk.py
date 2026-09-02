#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件系统遍历工具 — 不穿越重解析点（Windows junction / 符号链接）

为什么需要它：Path.rglob 与 os.walk 在 Windows 上都会穿透目录 junction
（lstat 的 st_reparse_tag 非零，而 S_ISLNK 对 junction 为假，is_symlink 检不出来）。
本仓每个临时项目目录下都存在 ffmpeg-bin junction，指向仓外的 FFmpeg 安装，
穿透遍历会把 411.8 MB 的仓外资产计入项目目录体积（虚增），
也会把仓外路径列入删除候选（越界）。

消费者：project_cleanup.py（清理候选枚举与体积）、project_audit.py（过程产物体积审计）
"""

import fnmatch
import os
import stat
from pathlib import Path


def is_reparse_point(path) -> bool:
    """重解析点判定：junction 或符号链接（须用 lstat，stat 会解析到目标）"""
    try:
        st = os.lstat(str(path))
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_reparse_tag", 0))


def walk(root):
    """os.walk 语义，但显式剔除重解析点子目录，且根自身为重解析点时不产出"""
    root = Path(root)
    if is_reparse_point(root):
        return
    for dirpath, dirnames, filenames in os.walk(str(root)):
        base = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_reparse_point(base / d)]
        yield base, dirnames, filenames


def rglob(root, pattern):
    """root.rglob(pattern) 的等价实现，不穿越重解析点（pattern 为名称模式）"""
    for base, dirnames, filenames in walk(root):
        for name in dirnames + filenames:
            if fnmatch.fnmatch(name, pattern):
                yield base / name


def dir_size(path) -> int:
    """目录内常规文件字节数；重解析点指向的目录外资产不计入"""
    total = 0
    for base, _dirs, filenames in walk(path):
        for name in filenames:
            try:
                st = os.lstat(str(base / name))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total
