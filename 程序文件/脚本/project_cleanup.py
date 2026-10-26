#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
项目清理工具 — 事务式过程产物管理

清理维度:
  1. 渲染可再生缓存(render-scratch) — work-* 目录、中间视频、调试截帧、.noaudio 备份
  2. TTS 缓存(tts-cache)      — tts_44k、scene_*.mp3、scene_*_hq.wav
                                **扣费面**：删掉后重跑要重新向 TTS 服务合成，故须
                                `--confirm-tts-cache` 显式确认（2026-10-26 拆类）
  3. 过期缓存    — 过程产物/缓存/ 中超过指定天数的文件
  4. 空目录      — 过程产物下的空子目录
  5. 增量基线    — render_raw.mp4 + scene_fingerprints.json 为 --scene-patch
                  增量渲染基线，保留最近一次，仅超过 stale-days 才列入清理
  6. 散落取证产物 — 临时产物根目录的取证/调试文件 + _取证/YYYYMMDD 日期子目录，
                  按目录归属归类、按 forensic_artifact_rules.json 的 retention_days
                  裁定可清理/保留期未到；受保护名与 .md 文档不在扫描面

安全机制:
  - 默认预览模式（dry-run），不删除任何文件
  - --execute 执行实际删除
  - **类别按「重新生成是否扣费」分**：`render-scratch` 只付时间，`tts-cache` 要重新
    扣费合成；后者不给确认就不删（单类请求直接拒绝、全量清理时跳过并点名）
  - 永不触碰 成果文件/ 中的正式交付物
  - 删除前生成完整清单供确认
  - 删除后运行 project_audit 验证
  - 目录遍历不穿越重解析点（Windows junction / 符号链接）：穿越会把目录外
    资产的体积计入本目录（虚增），也会把目录外路径列入删除候选（越界）

用法:
  python project_cleanup.py                    # 预览所有可清理项
  python project_cleanup.py --execute          # 执行（TTS 缓存类默认跳过）
  python project_cleanup.py --category render-scratch --execute  # 仅删可再生缓存
  python project_cleanup.py --category tts-cache --execute --confirm-tts-cache  # 扣费面
  python project_cleanup.py --stale-days 7     # 将过期阈值调为7天
"""

import sys
import io
import shutil
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List

# 换包装保活对：持旧防旧包装析构关底层 buffer，持新防本轮新代被下个重绑点顶掉
# 后引用归零（无需 gc.collect 即 finalize）。理由详见 script_interface 内同名表。
# 用例105 锁定。
_streams_prev = (sys.stdout, sys.stderr)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
_streams_kept = (sys.stdout, sys.stderr)

from config_manager import ConfigManager
from _fs_walk import dir_size as _dir_size, rglob as _rglob_no_reparse, walk as _walk
from _script_env import load_forensic_rules

# 过期缓存天数阈值（可通过 --stale-days 覆盖）
STALE_DAYS = 30


@dataclass
class CleanupItem:
    """单个可清理项"""
    path: Path
    category: str
    reason: str
    size_bytes: int = 0


@dataclass
class CleanupReport:
    """清理报告"""
    items: List[CleanupItem] = field(default_factory=list)
    deleted: List[CleanupItem] = field(default_factory=list)
    failed: List[CleanupItem] = field(default_factory=list)
    retained: List[CleanupItem] = field(default_factory=list)  # 保留的增量基线（仅展示占用）
    skipped_tts: List[CleanupItem] = field(default_factory=list)  # 未确认而跳过的扣费面
    freed_bytes: int = 0

    def add(self, item: CleanupItem):
        self.items.append(item)

    def total_size_mb(self) -> float:
        return sum(i.size_bytes for i in self.items) / 1024 / 1024

    def by_category(self) -> dict:
        result = {}
        for item in self.items:
            result.setdefault(item.category, []).append(item)
        return result


# ─────────────────────────────────────────────────────────
#  清理规则定义
# ─────────────────────────────────────────────────────────

# 渲染缓存按「重新生成是否扣费」拆两档（2026-10-26 批 2③）：
#   旧实现把两类混在同一个 `render` 类别里，`--category render --execute` 一次就把
#   TTS 缓存一起删掉；本项目为此在一轮里付过 11 次重新合成。拆类后唯一的区分轴是
#   「重跑要不要再向 TTS 服务付费」，不是目录形态。
RENDER_SCRATCH_DIRS = [
    "work-*",       # HyperFrames 渲染工作目录
]
TTS_CACHE_DIRS = [
    "tts_44k",      # TTS 采样转换缓存
]
RENDER_SCRATCH_FILES = [
    "bgm.wav",          # 合成 BGM（本地 ffmpeg 重新生成，不扣费）
    "render_new.mp4",
    "final_output.mp4",
    "final_with_subs.mp4",
    "frame_*.png",      # 调试截帧
    "scene*_*.png",     # 场景调试截帧
    "verify_*.png",
    "video-only.mp4",   # HyperFrames 无音频中间产物
    "*noaudio*",        # 无音频备份文件（render_noaudio_backup.mp4 等）
]
TTS_CACHE_FILES = [
    "scene_*.mp3",      # 场景 TTS 原始文件（重新合成扣费）
    "scene_*_hq.wav",   # 重采样成品（由 mp3 派生，源件删了同样要重合成）
]

# 增量渲染基线（scene-patch）：不再立即清理。render_raw.mp4 与同目录的
# scene_fingerprints.json 是 --scene-patch 段渲染的拼接基线，删掉就只能全量
# 重渲染（~11 分钟）。保留最近一次，仅当超过 stale-days 未更新才列入清理；
# 保留期间在预览中显示占用量。
PATCH_BASELINE_NAMES = ["render_raw.mp4", "scene_fingerprints.json"]

# 散落在临时产物根目录的取证/调试产物：归类与保留期不在本文件硬编码，
# 而是读自单一权威源 config/quality/forensic_artifact_rules.json（与产生侧
# preflight_check 共用同一份约定）。判据是『目录归属』而非文件名枚举——
# 见 scan_scattered_artifacts。旧的 25 名精确匹配白名单 SCATTERED_SCRIPT_NAMES
# 已删除：它与真实散文件交集长期为 0（结构性空转），且每次取证都制造新的漏网对象。



def scan_render_cache(cm: ConfigManager, report: CleanupReport):
    """扫描渲染缓存（递归查找，支持嵌套在音频子目录下的 work-* 目录）。

    两档分开登记：`render-scratch`（重新生成只付时间）与 `tts-cache`（重新合成扣费）。
    类别由**命中的模式组**决定，不按目录形态猜——`tts_44k` 与 `work-*` 都在 temp 下、
    都是目录，唯一区别是重跑要不要再付费。
    """
    temp_dir = cm.paths.process_temp
    if not temp_dir.exists():
        return

    cache_dirs = []
    for pattern in RENDER_SCRATCH_DIRS:
        for d in _rglob_no_reparse(temp_dir, pattern):
            if d.is_dir():
                cache_dirs.append(d)
                report.add(CleanupItem(d, "render-scratch",
                                       "HyperFrames 渲染工作目录（可再生）", _dir_size(d)))
    for pattern in TTS_CACHE_DIRS:
        for d in _rglob_no_reparse(temp_dir, pattern):
            if d.is_dir():
                cache_dirs.append(d)
                report.add(CleanupItem(d, "tts-cache",
                                       "TTS 采样缓存（重新合成扣费）", _dir_size(d)))

    # 递归查找渲染中间文件（跳过已位于缓存目录内的文件，避免重复计数）
    for category, patterns in (("render-scratch", RENDER_SCRATCH_FILES),
                               ("tts-cache", TTS_CACHE_FILES)):
        for pattern in patterns:
            for f in _rglob_no_reparse(temp_dir, pattern):
                if f.is_file():
                    if any(f.is_relative_to(d) for d in cache_dirs):
                        continue
                    why = ("TTS 产物（重新合成扣费）" if category == "tts-cache"
                           else "渲染中间文件")
                    report.add(CleanupItem(
                        f, category,
                        f"{why} ({pattern})",
                        f.stat().st_size
                    ))


def scan_patch_baseline(cm: ConfigManager, report: CleanupReport, stale_days: int):
    """扫描增量渲染基线（render_raw.mp4 + scene_fingerprints.json）。

    保留策略：未超过 stale-days 的基线不列入清理（记入 retained 展示占用），
    超过 stale-days 才作为过期基线清理（删后可由全量渲染重建）。
    """
    temp_dir = cm.paths.process_temp
    if not temp_dir.exists():
        return

    threshold = datetime.now() - timedelta(days=stale_days)
    for name in PATCH_BASELINE_NAMES:
        for f in _rglob_no_reparse(temp_dir, name):
            if not f.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                size = f.stat().st_size
            except (OSError, PermissionError):
                continue
            age = (datetime.now() - mtime).days
            if mtime < threshold:
                report.add(CleanupItem(
                    f, "render-scratch",
                    f"过期增量渲染基线 ({age}天未更新, 阈值{stale_days}天)",
                    size
                ))
            else:
                report.retained.append(CleanupItem(
                    f, "render-scratch",
                    f"增量渲染基线（保留 {age}/{stale_days} 天, 供 --scene-patch 拼接）",
                    size
                ))


def _match_glob(filename: str, pattern: str) -> bool:
    """简单的 glob 匹配（支持 * 通配符）"""
    import re
    regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
    return bool(re.match(regex, filename))


def scan_stale_cache(cm: ConfigManager, report: CleanupReport, stale_days: int):
    """扫描过期缓存"""
    cache_dir = cm.paths.process_cache
    if not cache_dir.exists():
        return

    threshold = datetime.now() - timedelta(days=stale_days)

    for item in cache_dir.iterdir():
        try:
            mtime = datetime.fromtimestamp(item.stat().st_mtime)
            if mtime < threshold:
                if item.is_dir():
                    size = _dir_size(item)
                else:
                    size = item.stat().st_size
                age = (datetime.now() - mtime).days
                report.add(CleanupItem(
                    item, "stale",
                    f"过期缓存 ({age}天)",
                    size
                ))
        except (OSError, PermissionError):
            pass


def scan_backup_videos(cm: ConfigManager, report: CleanupReport):
    """扫描 .noaudio.mp4 备份文件"""
    video_dir = cm.paths.output_video
    if not video_dir.exists():
        return

    for f in video_dir.glob("*.noaudio.mp4"):
        # 确认对应的有声版存在
        main_name = f.name.replace(".noaudio.mp4", ".mp4")
        main_file = video_dir / main_name
        if main_file.exists():
            report.add(CleanupItem(
                f, "backup",
                f"备份文件（有声版已存在: {main_name}）",
                f.stat().st_size
            ))


def scan_empty_dirs(cm: ConfigManager, report: CleanupReport):
    """扫描过程产物中的空目录"""
    process_dir = cm.paths.root / "过程产物"
    if not process_dir.exists():
        return

    # 自底向上检查（先处理深层空目录）
    for dirpath, dirnames, filenames in sorted(
        _walk(process_dir), key=lambda x: len(x[0].parts), reverse=True
    ):
        dp = dirpath
        if dp == process_dir:
            continue
        if not any(dp.iterdir()):
            report.add(CleanupItem(dp, "empty", "空目录", 0))


def scan_scattered_artifacts(cm: ConfigManager, report: CleanupReport):
    """扫描临时产物根目录散落的取证/调试产物 + _取证 日期子目录。

    归类判据是『目录归属』而非文件名枚举：临时产物根目录不承载任何持久文件，
    持久产物只住 *_audio/ 项目子目录或 _取证/ 日期子目录。故根目录任一文件，
    只要不在 protect_names 且扩展名不在 protect_extensions，即按散落取证产物归类，
    再按 mtime 与配置 retention_days 裁定三态：
      - 可清理（mtime 早于阈值）       → report.items（category=scattered）
      - 保留期未到（mtime 在阈值内）   → report.retained（仅展示占用，不删）
      - 不在扫描面（受保护名/.md/子目录）→ 不报、不动
    _取证/YYYYMMDD/ 日期子目录按目录名日期整目录裁定（自带日期，无需逐文件 mtime）。
    保留期与归类规则读自单一权威源 forensic_artifact_rules.json，与产生侧共用。
    """
    temp_dir = cm.paths.process_temp
    if not temp_dir.exists():
        return

    rules = load_forensic_rules()
    retention_days = int(rules["retention_days"])
    forensic_sub = rules["forensic_subdir"]
    protect_names = set(rules["protect_names"])
    protect_exts = set(rules["protect_extensions"])

    threshold = datetime.now() - timedelta(days=retention_days)

    # (a) 根目录散落文件（非递归；*_audio/ 等子目录天然不在扫描面）
    for f in temp_dir.iterdir():
        if not f.is_file():
            continue
        if f.name in protect_names or f.suffix in protect_exts:
            continue  # 不在扫描面：受保护名 / 受保护扩展名
        try:
            st = f.stat()
        except (OSError, PermissionError):
            continue
        mtime = datetime.fromtimestamp(st.st_mtime)
        age = (datetime.now() - mtime).days
        if mtime < threshold:
            report.add(CleanupItem(
                f, "scattered",
                f"散落取证产物 ({age}天未更新, 阈值{retention_days}天)",
                st.st_size))
        else:
            report.retained.append(CleanupItem(
                f, "scattered",
                f"散落取证产物（保留 {age}/{retention_days} 天）",
                st.st_size))

    # (b) _取证/YYYYMMDD/ 日期子目录（按目录名日期整目录裁定）
    forensic_root = temp_dir / forensic_sub
    if forensic_root.is_dir():
        for d in forensic_root.iterdir():
            if not d.is_dir():
                continue
            try:
                day_dt = datetime.strptime(d.name, "%Y%m%d")
            except ValueError:
                # 非日期命名子目录：按 mtime 兜底（仍受同一保留期治理）
                day_dt = datetime.fromtimestamp(d.stat().st_mtime)
            size = _dir_size(d)
            if day_dt < threshold:
                report.add(CleanupItem(
                    d, "scattered",
                    f"过期取证日志目录 ({d.name}, 阈值{retention_days}天)",
                    size))
            else:
                report.retained.append(CleanupItem(
                    d, "scattered",
                    f"取证日志目录（保留期内 {d.name}, 阈值{retention_days}天）",
                    size))



def scan_cdrive_residuals(report: CleanupReport):
    """扫描 C:\\temp 中工作流产生的残留文件（Chrome profile、预览截图等）

        工作流临时文件应使用 _script_env.PREVIEW_TEMP（默认在项目内 `过程产物/临时产物/chrome_preview`），既不应堆积在 C 盘，也不应落盘根。
    """
    c_temp = Path(r"C:\temp")
    if not c_temp.exists():
        return

    # 已知工作流产物模式（精确匹配，不泛扫 C 盘）
    workflow_patterns = [
        "chrome_pv_*",       # Chrome user-data-dir（旧版）
        "preview_captures",  # 预览截图（旧版）
        "preview_test",      # 预览测试（旧版）
        "temp_chrome_preview",  # Chrome 临时数据（若意外残留）
    ]

    import glob as glob_mod
    for pattern in workflow_patterns:
        for match in glob_mod.glob(str(c_temp / pattern)):
            p = Path(match)
            if p.is_dir():
                report.add(CleanupItem(p, "cdrive", "工作流临时文件残留于C盘", _dir_size(p)))
            elif p.is_file():
                report.add(CleanupItem(p, "cdrive", "工作流临时文件残留于C盘", p.stat().st_size))


# ─────────────────────────────────────────────────────────
#  执行引擎
# ─────────────────────────────────────────────────────────

CATEGORY_NAMES = {
    "render-scratch": "渲染可再生缓存（重跑只付时间）",
    "tts-cache": "TTS 缓存（重新合成扣费，须显式确认）",
    "stale": "过期缓存",
    "backup": "备份文件",
    "scattered": "散落取证产物",
    "empty": "空目录",
    "cdrive": "C盘残留（工作流）",
}


def print_preview(report: CleanupReport, category_filter: str = None):
    """打印预览报告"""
    by_cat = report.by_category()

    print("\n" + "=" * 60)
    print("项目清理预览")
    print("=" * 60)

    if not report.items:
        print("\n没有可清理的项目。")
        _print_retained(report)
        return

    total_count = 0
    for cat, items in sorted(by_cat.items()):
        if category_filter and cat != category_filter:
            continue

        cat_name = CATEGORY_NAMES.get(cat, cat)
        cat_size = sum(i.size_bytes for i in items) / 1024 / 1024
        print(f"\n[{cat_name}] ({len(items)} 项, {cat_size:.1f} MB)")

        for item in items:
            rel = item.path.name
            if item.path.parent.name != "临时产物" and item.path.parent.name != "缓存":
                rel = f"{item.path.parent.name}/{rel}"
            size_str = ""
            if item.size_bytes > 0:
                mb = item.size_bytes / 1024 / 1024
                size_str = f" ({mb:.1f} MB)" if mb >= 1 else f" ({item.size_bytes // 1024} KB)"
            print(f"  - {rel}{size_str}")
            print(f"    {item.reason}")

        total_count += len(items)

    print(f"\n{'─' * 60}")
    print(f"合计: {total_count} 项, {report.total_size_mb():.1f} MB")
    print(f"{'─' * 60}")
    _print_retained(report)
    tts = [i for i in report.items if i.category == "tts-cache"]
    if tts:
        mb = sum(i.size_bytes for i in tts) / 1024 / 1024
        print(f"\n⚠ 其中 TTS 缓存 {len(tts)} 项 / {mb:.1f} MB 属**扣费面**：删掉后重跑要"
              f"重新向 TTS 服务合成。`--execute` 默认跳过；确要删须显式加 "
              f"`--category tts-cache --confirm-tts-cache`")
    print(f"\n确认无误后运行: python project_cleanup.py --execute")


def _print_retained(report: CleanupReport):
    """展示保留期内、不列入清理的项（增量渲染基线 + 散落取证产物）占用量"""
    if not report.retained:
        return
    total_mb = sum(i.size_bytes for i in report.retained) / 1024 / 1024
    print(f"\n[保留期内 — 不清理] ({len(report.retained)} 项, 占用 {total_mb:.1f} MB)")
    for item in report.retained:
        mb = item.size_bytes / 1024 / 1024
        size_str = f" ({mb:.1f} MB)" if mb >= 1 else f" ({item.size_bytes // 1024} KB)"
        print(f"  = {item.path.parent.name}/{item.path.name}{size_str}")
        print(f"    {item.reason}")


def execute_cleanup(report: CleanupReport, category_filter: str = None,
                    confirm_tts_cache: bool = False) -> CleanupReport:
    """执行清理。

    `tts-cache` 是**扣费面**：删掉后重跑要重新向 TTS 服务合成（本项目为此付过 11 次
    重新合成）。故三档：
      · `--category tts-cache` 未带确认 → **一件不删**（连同类以外的也不删：那本来
        就不在请求面内），记入 skipped_tts，由 main 判退出码 2；
      · 不限类别（全量清理）未带确认 → 跳过该类并点名跳过了什么，其余照删；
      · 带确认 → 与其它类同样删。
    """
    print("\n" + "=" * 60)
    print("执行清理")
    print("=" * 60)

    tts_sel = [i for i in report.items
               if i.category == "tts-cache"
               and (category_filter is None or category_filter == "tts-cache")]
    if tts_sel and not confirm_tts_cache:
        report.skipped_tts = list(tts_sel)
        mb = sum(i.size_bytes for i in tts_sel) / 1024 / 1024
        if category_filter == "tts-cache":
            print(f"[REFUSED] 请求的 tts-cache 属扣费面（{len(tts_sel)} 项 / {mb:.1f} MB）："
                  f"删除后重跑要重新向 TTS 服务合成。")
            print("  确认要删就再加 --confirm-tts-cache；本次一件未删。")
            return report
        print(f"[SKIP] tts-cache {len(tts_sel)} 项 / {mb:.1f} MB 未删（扣费面，需 "
              f"--confirm-tts-cache）；其余类别照常清理。")

    for item in report.items:
        if category_filter and item.category != category_filter:
            continue
        if item.category == "tts-cache" and not confirm_tts_cache:
            continue

        try:
            if item.path.is_dir():
                shutil.rmtree(item.path)
            elif item.path.is_file():
                item.path.unlink()
            report.deleted.append(item)
            report.freed_bytes += item.size_bytes
            print(f"  [OK] {item.path.name}")
        except (OSError, PermissionError) as e:
            report.failed.append(item)
            print(f"  [FAIL] {item.path.name}: {e}")

    print(f"\n{'─' * 60}")
    print(f"已删除: {len(report.deleted)} 项")
    print(f"释放空间: {report.freed_bytes / 1024 / 1024:.1f} MB")
    if report.failed:
        print(f"失败: {len(report.failed)} 项")
    if report.skipped_tts:
        mb = sum(i.size_bytes for i in report.skipped_tts) / 1024 / 1024
        print(f"跳过 TTS 缓存: {len(report.skipped_tts)} 项 / {mb:.1f} MB（扣费面，未确认）")
    print(f"{'─' * 60}")

    return report


# ─────────────────────────────────────────────────────────
#  主入口
# ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="项目清理工具")
    parser.add_argument("--execute", action="store_true",
                        help="执行实际删除（默认仅预览）")
    parser.add_argument("--category", choices=list(CATEGORY_NAMES.keys()),
                        help="仅处理指定类别")
    parser.add_argument("--stale-days", type=int, default=STALE_DAYS,
                        help=f"过期缓存天数阈值 (默认 {STALE_DAYS})")
    parser.add_argument("--confirm-tts-cache", action="store_true",
                        help="显式确认删除 TTS 缓存（扣费面：删后重跑要重新合成）")
    parser.add_argument("--no-verify", action="store_true",
                        help="清理后不运行审计验证")
    args = parser.parse_args()

    cm = ConfigManager()
    report = CleanupReport()

    # 扫描所有类别
    scan_render_cache(cm, report)
    scan_patch_baseline(cm, report, args.stale_days)
    scan_stale_cache(cm, report, args.stale_days)
    scan_backup_videos(cm, report)
    scan_scattered_artifacts(cm, report)
    scan_empty_dirs(cm, report)
    scan_cdrive_residuals(report)

    if args.execute:
        result = execute_cleanup(report, args.category, args.confirm_tts_cache)

        # 清理后运行审计验证（使用子进程，避免 sys.stdout 包装冲突）
        if not args.no_verify and result.deleted:
            print("\n运行审计验证...", flush=True)
            sys.stdout.flush()
            import subprocess
            audit_script = Path(__file__).parent / "project_audit.py"
            if audit_script.exists():
                subprocess.run(
                    [sys.executable, str(audit_script)],
                    cwd=str(cm.paths.root)
                )
            else:
                print("  (project_audit 不可用，跳过验证)")

        # 单类请求被扣费面挡下 → 退 2：让"一件没删"与"删完了"在退出码上可区分
        if args.category == "tts-cache" and result.skipped_tts:
            sys.exit(2)
    else:
        print_preview(report, args.category)


if __name__ == "__main__":
    main()

