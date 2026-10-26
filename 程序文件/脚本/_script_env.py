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

# Chrome 预览临时目录（每轮 mkdtemp 一个 profile，收尾整目录删除），可用环境变量覆盖。
# 2026-10-11 修正两处，各独立：
#  ① 位置耦合 bug：旧写法 `ROOT.parent.parent / "temp_chrome_preview"` 隐含假设"项目根
#     恰住盘符下"；项目层级多一层时，同一表达式落到工作区根，与该常量原注释
#     "避免污染工作区根"的意图正好相反。现引 TEMP_BASE（项目内受管临时区），
#     不依赖磁盘层级，且落在散文件清理与保留期规则的扫描面内——判据：落点若不被任何
#     扫描面覆盖，违规就永不显形。旧落点也不在 `_工具/盘根基线.json` 内，
#     预览中断所留残留会被盘根越界哨兵报成基线外新增。
#  ② 原注释"Chrome 不支持中文 user-data-dir"属成文断言而非实测记录，本仓对照实测已证伪：
#     chrome-headless-shell 152.0.7977.30，ASCII 组与中文组（profile／中文截图文件名／
#     file:// URL 含中文与空格）同为 rc=0、截图 17331 字节逐位相同。
#     读数与复现命令见 `操作日志.md` [2026-10-11] 预览落点 条（取证件有保留期，不作长期指针）。
PREVIEW_TEMP = Path(os.environ.get(
    "HYPERFRAMES_PREVIEW_TEMP", str(TEMP_BASE / "chrome_preview")))


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


def validate_managed_name(value, field: str, source: str = ""):
    """校验"受管目录名"字段（paths.temp_subdir / paths.html_project / --html 等）。

    语义契约：这些字段的值是**单段目录名**，各消费点以 `BASE / value` 直接拼接
    （TEMP_BASE=过程产物/临时产物、HTML_BASE=程序文件/源码/hyperframes）。
    本函数是唯一校验点，fail-closed——拒绝路径分隔符、`.`/`..` 段、绝对路径与
    盘符前缀；不静默截断、不回退默认值（静默回退会把配错的落点洗成"看起来对"）。

    背景（2026-10-15 消费者侧根因修复，2026-10-16 回灌本仓）：使用者克隆里
    config paths.temp_subdir 被带入路径值 '过程产物/临时产物/<项目>'，穿透 6 个
    消费点的 TEMP_BASE/temp_subdir 拼接，落成 过程产物/临时产物/过程产物/临时产物/
    <项目>/ 自我嵌套副本（508MB/2777 文件）。两次巡检只清副本未清源头，7 日内
    同形态再现——正解是堵拼接入口，不是每日清垃圾。

    与 preflight check 2b（check_config_path_fields，用例99）的分工：那一条裁的是
    5 枚**点号路径字段**的扩展名，且住在预检位点；本函数裁的是**目录名字段**的
    单段性，且住在拼接入口，故覆盖不走预检的 CLI 直调路径。两者不互相替代。

    刻意不纳入的同类输入（登记为待办，见 AGENTS.md 同名条）：narration_source 类
    候选路径（其值合法含 `/`，与目录名不是同一契约）、asset_scanner /
    generate_photo_registry / narration_pause_precheck / pipeline_runner 的 SDL
    编译入口（`video.name`）——后四者是同一形态但无既有回归夹具承接，
    翻转前置＝任一处真实触发过一次错值，届时一并接入并补夹具。

    None/空串返回 None——由调用方走各自的"缺省默认值"分支，不在本层代决。
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    where = f"{source} 的 {field}" if source else field
    problems = []
    if '/' in s or '\\' in s:
        problems.append("含路径分隔符")
    segments = s.replace('\\', '/').split('/')
    if any(seg in ('.', '..') for seg in segments):
        problems.append("含 . 或 .. 段")
    if Path(s).is_absolute() or (len(s) >= 2 and s[1] == ':'):
        problems.append("是绝对路径或带盘符")
    if problems:
        raise RuntimeError(
            f"{where} 必须是单段目录名，收到 {s!r}（{'；'.join(problems)}）"
            "——禁止带路径的值：它会穿透 BASE/字段名 拼接，在受管临时区/源码区内"
            "造出自我嵌套目录。请改为纯目录名（如 'demo-project'）")
    return s


def resolve_delivery_names(config, html_project):
    """交付文件名唯一推导点：返回 (video_name, subtitle_name)。

    权威源只有 paths.video_name / paths.subtitle_name（subtitle.file 为兼容别名）。
    delivery.output_video / delivery.output_subtitle / delivery.video 是历史别名，
    **不参与推导**——2026-10-18 的修订轮里两处名字分叉，终检与交付验收实际量的
    是上一版基础片（AGENTS.md 2026-10-24 整改条）。别名与这里不一致时由
    preflight check 2c 点名告警（只报不拦：推导已不读它们，陈旧别名是卫生问题
    而非正确性风险）；本函数不静默采纳，避免同一对象两个真相源。
    """
    cfg = config or {}
    paths = cfg.get("paths") or {}
    base = str(html_project or "").strip()
    video_name = (str(paths.get("video_name") or "").strip()
                  or (f"{base}.mp4" if base else "output.mp4"))
    subtitle_name = (str(paths.get("subtitle_name") or "").strip()
                     or str((cfg.get("subtitle") or {}).get("file") or "").strip()
                     or (f"{base}.srt" if base else "output.srt"))
    return video_name, subtitle_name


def delivery_name_conflicts(config, html_project, root=None):
    """历史交付别名与单一权威源的冲突清单（preflight check 2c 的取数面）。

    返回 ([(key, 声明值, 期望值), ...], video_name, subtitle_name)。root 缺省为
    工作流根；只比较**解析后的完整路径**——`成果文件/视频/x.mp4` 与裸 `x.mp4`
    指向同一对象时不算冲突。
    """
    cfg = config or {}
    rootp = Path(root) if root else ROOT
    video_name, subtitle_name = resolve_delivery_names(cfg, html_project)
    video_dir = rootp / "成果文件" / "视频"
    subtitle_dir = rootp / "成果文件" / "字幕"
    want_video = video_dir / video_name
    want_subtitle = subtitle_dir / subtitle_name
    delivery = cfg.get("delivery") or {}

    def _same(value, want, base_dir):
        p = Path(str(value))
        if not p.is_absolute():
            p = base_dir / p
        try:
            return p.resolve() == want.resolve()
        except OSError:
            return False

    conflicts = []
    for key, base_dir, want in (
        ("video", rootp, want_video),
        ("subtitle", rootp, want_subtitle),
        ("output_video", video_dir, want_video),
        ("output_subtitle", subtitle_dir, want_subtitle),
    ):
        value = delivery.get(key)
        if value and not _same(value, want, base_dir):
            conflicts.append((f"delivery.{key}", value, str(want)))
    return conflicts, video_name, subtitle_name


def revision_base_stem(stem: str) -> str:
    """去掉末尾 `_修订NN` 后的基础名；无该后缀原样返回。

    修订轮的交付惯例：视频必须带修订后缀；字幕与交付说明允许沿用基础名
    （已有 2 支修订交付如此）。审计与写入共用本函数，避免各写一套正则。
    **边界**：交付说明的"沿用基础名"只是**读取侧**宽容——审计据此判"这一版有没有
    留痕"；写入侧不把本版实测写进基础名文档（见 pipeline_runner._format_notes_base_refusal）。
    """
    import re as _re
    m = _re.search(r"_修订\d+$", str(stem or ""))
    return str(stem)[:m.start()] if m else str(stem or "")


def delivery_notes_candidates(video_path):
    """交付说明候选链：修订名优先，基础名回退。

    消费者是**读取侧**（审计第 6 维 resolve_delivery_notes）；流水线写入侧只用它
    识别"同层仅有基础名说明"这一状态并拒绝改写，不把它当写入目标。
    """
    p = Path(video_path)
    exact = p.parents[1] / f"交付说明_{p.stem}.md"
    base = revision_base_stem(p.stem)
    if base != p.stem:
        return [exact, p.parents[1] / f"交付说明_{base}.md"]
    return [exact]


def resolve_delivery_notes(video_path):
    """返回交付说明候选链中第一个在位文件；全不在位返回 None。"""
    for cand in delivery_notes_candidates(video_path):
        if cand.exists():
            return cand
    return None


def subtitle_stem_candidates(video_stem: str):
    """字幕基名候选：视频全名优先；修订轮允许沿用基础名（共享 srt 惯例）。"""
    stem = str(video_stem)
    base = revision_base_stem(stem)
    return [stem] if base == stem else [stem, base]

