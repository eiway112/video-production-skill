# -*- coding: utf-8 -*-
"""视频制作工作流脚本公共环境（重复代码治理，2026-07-25；路径收口改造，2026-07-28）。

提供各校验/预处理脚本共享的：
- GBK 控制台编码修复（import 即生效）
- 工作流公共路径常量（ROOT 自动探测，禁止任何脚本再硬编码盘符路径）
- asset_audit.jsonl 结构化日志追加

ROOT 探测优先级：
1. 环境变量 VIDEO_WORKFLOW_ROOT（显式覆盖，供异机/CI 使用）
2. 从本文件位置上溯：本文件固定位于 ROOT/程序文件/脚本/ 下
两种方式得到的 ROOT 都用 AGENTS.md 标记文件验证，验证失败直接报错，
禁止带错误路径静默运行。

用法：
    import _script_env  # noqa: F401  (GBK 编码修复即生效)
    from _script_env import ROOT, HTML_BASE, LOG_PATH, append_audit_log
"""
import json
import os
import sys
from pathlib import Path

# Fix GBK encoding in PowerShell
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def _detect_root() -> Path:
    """探测工作流根目录，以 AGENTS.md + 程序文件/ 为标记验证。"""
    def _is_root(p: Path) -> bool:
        return (p / "AGENTS.md").is_file() and (p / "程序文件").is_dir()

    env_root = os.environ.get("VIDEO_WORKFLOW_ROOT", "").strip()
    if env_root:
        p = Path(env_root).resolve()
        if _is_root(p):
            return p
        raise RuntimeError(
            f"VIDEO_WORKFLOW_ROOT={env_root} 不是有效的工作流根目录"
            "（缺 AGENTS.md 或 程序文件/）")

    # 本文件位于 ROOT/程序文件/脚本/_script_env.py → 上溯两级
    candidate = Path(__file__).resolve().parents[2]
    if _is_root(candidate):
        return candidate
    raise RuntimeError(
        f"无法定位工作流根目录（从 {candidate} 探测失败），"
        "请设置环境变量 VIDEO_WORKFLOW_ROOT")


ROOT = _detect_root()
SCRIPT_DIR = ROOT / "程序文件" / "脚本"
CONFIG_BASE = ROOT / "程序文件" / "配置"
HTML_BASE = ROOT / "程序文件" / "源码" / "hyperframes"
SOURCE_BASE = ROOT / "素材文件" / "图片"
TEMP_BASE = ROOT / "过程产物" / "临时产物"
LOG_PATH = TEMP_BASE / "asset_audit.jsonl"
QUALITY_DIR = CONFIG_BASE / "config" / "quality"
FORENSIC_RULES_PATH = QUALITY_DIR / "forensic_artifact_rules.json"

# venv 解释器：存在则用项目 venv，否则回退当前解释器（异机/CI 兼容）
VENV_PYTHON = ROOT / "程序文件" / "运行环境" / "venv" / "Scripts" / "python.exe"
if not VENV_PYTHON.exists():
    VENV_PYTHON = Path(sys.executable)

# Chrome 预览专用 ASCII 临时目录（Chrome 不支持中文 user-data-dir）：
# 默认放在工作区根目录的上级（D:\ 盘，避免污染工作区根目录），可用环境变量覆盖
PREVIEW_TEMP = Path(os.environ.get(
    "HYPERFRAMES_PREVIEW_TEMP", str(ROOT.parent.parent / "temp_chrome_preview")))


def resolve_narration_scenes(cfg: dict, config_path, cover_duration=None):
    """P0-03：解析 narration_source 指针，返回 (scenes, source_path)。

    未声明指针时返回 (None, None)。声明了指针即以指针为权威：
    解析/加载失败抛 RuntimeError，禁止静默回退内嵌 scenes（单一权威源原则）。
    解析顺序与 enhance_video_audio.load_config 一致：
    绝对路径 → HTML 项目目录（paths.html_project）→ 配置文件目录。
    给定 cover_duration 时，LEGACY 相对时间自动平移为绝对时间
    （与 _normalize_scenes_to_absolute 同一启发式），cover 占位场景被过滤。
    """
    if 'narration_source' not in cfg:
        return None, None
    raw = str(cfg['narration_source'])
    config_path = Path(config_path)
    candidates = []
    if Path(raw).is_absolute():
        candidates.append(Path(raw))
    else:
        html_project = (cfg.get('paths') or {}).get('html_project', '')
        if html_project:
            candidates.append(HTML_BASE / html_project / raw)
        candidates.append(config_path.parent / raw)
    source_path = next((c for c in candidates if c.is_file()), None)
    if source_path is None:
        tried = '; '.join(str(c) for c in candidates)
        raise RuntimeError(
            f"narration_source='{raw}' 声明了外部旁白源但未找到（尝试：{tried}）"
            "——请修正指针或移除该字段，禁止静默回退")
    try:
        with open(source_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"narration_source 解析失败: {source_path}: {e}")
    raw_scenes = data.get('scenes')
    if not raw_scenes:
        raise RuntimeError(f"narration_source 中无有效 scenes: {source_path}")
    content = []
    for s in raw_scenes:
        if str(s.get('type', '')).lower() == 'cover':
            continue
        if s.get('scene_id') == 0 and not (s.get('narration') or '').strip():
            continue
        content.append(dict(s))
    if cover_duration and content:
        first_start = float(content[0].get('start', 0))
        if first_start < float(cover_duration) * 0.5:
            for s in content:
                s['start'] = float(s.get('start', 0)) + float(cover_duration)
                s['end'] = float(s.get('end', 0)) + float(cover_duration)
    return content, source_path


def append_audit_log(entry: dict, log_path: Path = LOG_PATH):
    """将结构化日志条目追加写入 JSONL（失败静默，不阻断主流程）。"""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


_FORENSIC_REQUIRED_KEYS = (
    "forensic_subdir", "retention_days", "protect_names", "protect_extensions")


def load_forensic_rules() -> dict:
    """取证产物归类与保留期规则（单一权威源 forensic_artifact_rules.json）。

    产生侧（preflight_check 写 forensic_subdir 日期子目录）与清理侧
    （project_cleanup.scan_scattered_artifacts 读 retention_days + 归类）共用
    本函数，禁止任一处另写副本（A06：登记面=真实读取路径）。

    fail-closed：缺文件 / 不可解析 / 缺必需键即 RuntimeError——禁止静默回退
    默认值，否则清理侧会按错误规则裁定删除面（与 A10『损坏配置即报错』同族）。
    """
    try:
        with open(FORENSIC_RULES_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        raise RuntimeError(f"取证规则配置缺失：{FORENSIC_RULES_PATH}")
    except Exception as e:
        raise RuntimeError(f"取证规则配置不可解析：{FORENSIC_RULES_PATH}: {e}")
    if not isinstance(data, dict):
        raise RuntimeError(f"取证规则配置格式非法（应为对象）：{FORENSIC_RULES_PATH}")
    missing = [k for k in _FORENSIC_REQUIRED_KEYS if k not in data]
    if missing:
        raise RuntimeError(
            f"取证规则配置缺必需键 {missing}：{FORENSIC_RULES_PATH}")
    return data

