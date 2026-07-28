#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一验证框架

从 preflight_check.py / verify_tts_product(_v2).py / regression_test.py /
architecture_self_check.py 中提取的公共验证规则的统一实现：
  1. 规则加载 —— 配置/validation_rules.yaml（PyYAML 缺失时支持同名 .json）
  2. 标准化检查执行 —— 检查原语（primitives）+ 规则引擎
  3. 统一结果输出 —— script_interface.Result（Result Object Pattern）

两种用法：

  作为库（preflight_check.py 等脚本接入公共原语，消息措辞由调用方控制）::

      import unified_validator as uv
      outcome = uv.check_json_file(config_path)
      if outcome.ok:
          cfg = outcome.data["content"]

  作为 CLI（独立运行规则集）::

      python unified_validator.py --list-rules
      python unified_validator.py --context config_path=../配置/config/wall-crack-remedy.json
      python unified_validator.py --category media_params --context image_path=xxx.png
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from script_interface import Result, EXIT_CODE

DEFAULT_RULES_PATH = SCRIPT_DIR.parent / "配置" / "validation_rules.yaml"


# ============================================================================
# 检查结果对象（库模式返回值）
# ============================================================================

class CheckOutcome:
    """单项检查的结构化结果。

    ok:   检查是否通过（布尔判定）
    data: 结构化细节（大小、解析内容、问题列表等），供调用方自行格式化消息
    """

    __slots__ = ("ok", "data")

    def __init__(self, ok, data=None):
        self.ok = bool(ok)
        self.data = data if data is not None else {}

    def __repr__(self):
        return f"CheckOutcome(ok={self.ok}, data={self.data})"


# ============================================================================
# 检查原语（公共规则的唯一实现处）
# ============================================================================

def check_path_exists(path, kind="any") -> CheckOutcome:
    """文件/目录存在性检查。kind: file | dir | any"""
    p = Path(path)
    exists = p.exists()
    if exists and kind == "file":
        exists = p.is_file()
    elif exists and kind == "dir":
        exists = p.is_dir()
    return CheckOutcome(exists, {"exists": exists, "path": str(p)})


def check_utf8_readable(path) -> CheckOutcome:
    """UTF-8 可读性检查（Windows/GBK 环境的高频故障点）。"""
    p = Path(path)
    if not p.is_file():
        return CheckOutcome(False, {"exists": False, "error": "file not found"})
    try:
        p.read_text(encoding="utf-8")
        return CheckOutcome(True, {"exists": True})
    except UnicodeDecodeError as e:
        return CheckOutcome(False, {"exists": True, "error": str(e)})


def check_json_file(path) -> CheckOutcome:
    """JSON 文件解析检查。通过时 data['content'] 为解析结果。"""
    p = Path(path)
    if not p.is_file():
        return CheckOutcome(False, {"exists": False, "error": "file not found"})
    try:
        content = json.loads(p.read_text(encoding="utf-8"))
        return CheckOutcome(True, {"exists": True, "content": content})
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return CheckOutcome(False, {"exists": True, "error": str(e)})


def check_required_fields(obj, fields) -> CheckOutcome:
    """字典必填字段检查。"""
    if not isinstance(obj, dict):
        return CheckOutcome(False, {"missing": list(fields), "error": "not a dict"})
    missing = [f for f in fields if f not in obj]
    return CheckOutcome(not missing, {"missing": missing})


def check_scene_bounds(cfg, continuity_tolerance=0.01) -> CheckOutcome:
    """场景边界检查：start/end 存在、end > start、场景间连续。

    返回 data['issues'] 列表，每项为：
      {"kind": "missing_bounds", "scene_id": ..., "index": i}
      {"kind": "inverted", "scene_id": ..., "start": .., "end": ..}
      {"kind": "gap", "prev_id": .., "prev_end": .., "curr_id": .., "curr_start": ..}
    消息措辞由调用方决定（保持既有脚本输出不变）。
    """
    issues = []
    scenes = cfg.get("scenes", []) if isinstance(cfg, dict) else []
    for i, scene in enumerate(scenes):
        if "start" not in scene or "end" not in scene:
            issues.append({
                "kind": "missing_bounds",
                "scene_id": scene.get("scene_id", i + 1),
                "index": i,
            })
            continue
        if scene["end"] <= scene["start"]:
            issues.append({
                "kind": "inverted",
                "scene_id": scene.get("scene_id", i + 1),
                "start": scene["start"],
                "end": scene["end"],
            })
    for i in range(1, len(scenes)):
        prev, curr = scenes[i - 1], scenes[i]
        if "end" not in prev or "start" not in curr:
            continue  # 边界缺失已在上面记录
        prev_end, curr_start = prev["end"], curr["start"]
        if abs(prev_end - curr_start) > continuity_tolerance:
            issues.append({
                "kind": "gap",
                "prev_id": prev.get("scene_id", i),
                "prev_end": prev_end,
                "curr_id": curr.get("scene_id", i + 1),
                "curr_start": curr_start,
            })
    return CheckOutcome(not issues, {"issues": issues, "scene_count": len(scenes)})


def check_backup_pair(path, bak_name=None, validate="exists") -> CheckOutcome:
    """备份配对检查。

    bak_name: 备份文件名；缺省按 sibling 规则推导 {stem}.bak{suffix}
              （config.json -> config.bak.json）
    validate: exists | json
    返回 data['state']: valid | invalid | missing
    """
    p = Path(path)
    if bak_name:
        bak_path = p.with_name(bak_name)
    else:
        bak_path = p.with_name(p.stem + ".bak" + p.suffix)

    if not bak_path.exists():
        state = "missing"
    elif validate == "json":
        state = "valid" if check_json_file(bak_path).ok else "invalid"
    else:
        state = "valid"
    return CheckOutcome(state == "valid", {"bak_path": bak_path, "state": state})


def check_file_size_range(path, min_bytes=None, max_bytes=None) -> CheckOutcome:
    """文件大小区间检查（媒体产物防占位图/假成功的公共规则）。

    data: exists / size_bytes / too_small / too_large
    """
    p = Path(path)
    if not p.is_file():
        return CheckOutcome(False, {
            "exists": False, "size_bytes": 0,
            "too_small": False, "too_large": False,
        })
    size = p.stat().st_size
    too_small = min_bytes is not None and size < min_bytes
    too_large = max_bytes is not None and size > max_bytes
    return CheckOutcome(not (too_small or too_large), {
        "exists": True, "size_bytes": size,
        "too_small": too_small, "too_large": too_large,
    })


_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def check_srt_file(path) -> CheckOutcome:
    """SRT 字幕格式检查：分块结构、时间码解析、start<end、时序单调。"""
    p = Path(path)
    if not p.is_file():
        return CheckOutcome(False, {"exists": False, "errors": ["file not found"]})
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        return CheckOutcome(False, {"exists": True, "errors": [f"encoding: {e}"]})

    errors = []
    entries = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = _SRT_TIME.search(lines[1] if lines[0].isdigit() else lines[0])
        if not m:
            errors.append(f"invalid timecode in block: {lines[0][:40]}")
            continue
        g = [int(x) for x in m.groups()]
        start_ms = (g[0] * 3600 + g[1] * 60 + g[2]) * 1000 + g[3]
        end_ms = (g[4] * 3600 + g[5] * 60 + g[6]) * 1000 + g[7]
        if end_ms <= start_ms:
            errors.append(f"end <= start at {lines[0][:40]}")
        entries.append({"start_ms": start_ms, "end_ms": end_ms})

    if not entries:
        errors.append("no valid subtitle entries")
    for i in range(1, len(entries)):
        if entries[i]["start_ms"] < entries[i - 1]["end_ms"]:
            errors.append(f"overlap between entry {i} and {i + 1}")
    return CheckOutcome(not errors, {
        "exists": True, "entry_count": len(entries), "errors": errors,
    })


RESULT_REQUIRED_KEYS = ("status", "code", "message", "data", "metadata", "diagnostics")


def check_result_shape(path) -> CheckOutcome:
    """Result Object 结构检查（script_interface.Result 序列化完整性）。

    path: dict 或 JSON 文件路径（与 _CHECK_DISPATCH/规则配置的 path 参数对齐）。
    """
    obj = path
    if not isinstance(obj, dict):
        loaded = check_json_file(obj)
        if not loaded.ok:
            return CheckOutcome(False, {"missing": list(RESULT_REQUIRED_KEYS),
                                        "error": loaded.data.get("error")})
        obj = loaded.data["content"]
    missing = [k for k in RESULT_REQUIRED_KEYS if k not in obj]
    for sub_key, sub_fields in (("metadata", ("execution_time_ms", "version")),
                                ("diagnostics", ("warnings", "errors"))):
        if isinstance(obj.get(sub_key), dict):
            missing.extend(f"{sub_key}.{f}" for f in sub_fields
                           if f not in obj[sub_key])
    return CheckOutcome(not missing, {"missing": missing})


# ============================================================================
# 规则引擎（CLI 模式）
# ============================================================================

# 规则 check 字段 -> (原语函数, 接受的参数名)
_CHECK_DISPATCH = {
    "path_exists": (check_path_exists, ("path", "kind")),
    "utf8_readable": (check_utf8_readable, ("path",)),
    "json_valid": (check_json_file, ("path",)),
    "json_required_fields": (None, ("path", "fields")),  # 特殊处理：先加载 JSON
    "scene_bounds": (None, ("path", "continuity_tolerance")),  # 同上
    "backup_pair": (check_backup_pair, ("path", "bak_name", "validate")),
    "file_size_range": (check_file_size_range, ("path", "min_bytes", "max_bytes")),
    "srt_valid": (check_srt_file, ("path",)),
    "result_shape": (check_result_shape, ("path",)),
}

_VAR_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def load_rules(rules_path=None):
    """加载规则配置。优先 YAML；PyYAML 缺失时尝试同名 .json。"""
    path = Path(rules_path) if rules_path else DEFAULT_RULES_PATH
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            json_alt = path.with_suffix(".json")
            if json_alt.exists():
                path = json_alt
            else:
                raise RuntimeError(
                    f"PyYAML 不可用且未找到 JSON 规则文件: {json_alt}")
    if not path.exists():
        raise FileNotFoundError(f"规则文件不存在: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        import yaml
        doc = yaml.safe_load(text)
    else:
        doc = json.loads(text)
    return doc.get("rules", []), doc.get("version", "?")


def _resolve_params(params, context):
    """替换参数中的 {var} 上下文变量。存在未解析变量时返回 (None, 缺失变量名)。"""
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str):
            missing = [v for v in _VAR_PATTERN.findall(value) if v not in context]
            if missing:
                return None, missing[0]
            for var, val in context.items():
                value = value.replace("{" + var + "}", str(val))
        resolved[key] = value
    return resolved, None


def run_rule(rule, context):
    """执行单条规则。返回 dict：{id, category, severity, status, detail}

    status: pass | fail | skip
    """
    entry = {
        "id": rule.get("id", "?"),
        "category": rule.get("category", "?"),
        "severity": rule.get("severity", "error"),
        "status": "skip",
        "detail": "",
    }
    check_name = rule.get("check")
    if check_name not in _CHECK_DISPATCH:
        entry["detail"] = f"未知检查类型: {check_name}"
        return entry

    params, missing_var = _resolve_params(dict(rule.get("params", {})), context)
    if params is None:
        entry["detail"] = f"上下文变量未提供: {{{missing_var}}}"
        return entry

    skip_if_missing = params.pop("skip_if_missing", False)
    target = params.get("path")
    if skip_if_missing and target and not Path(target).exists():
        entry["detail"] = f"目标不存在，按规则跳过: {target}"
        return entry

    # 需要先加载 JSON 的复合检查
    if check_name == "json_required_fields":
        loaded = check_json_file(params["path"])
        outcome = (check_required_fields(loaded.data["content"], params["fields"])
                   if loaded.ok else loaded)
    elif check_name == "scene_bounds":
        loaded = check_json_file(params["path"])
        outcome = (check_scene_bounds(
            loaded.data["content"],
            continuity_tolerance=params.get("continuity_tolerance", 0.01))
            if loaded.ok else loaded)
    else:
        func, accepted = _CHECK_DISPATCH[check_name]
        kwargs = {k: v for k, v in params.items() if k in accepted}
        outcome = func(**kwargs)

    entry["status"] = "pass" if outcome.ok else "fail"
    if not outcome.ok:
        detail = {k: v for k, v in outcome.data.items() if k != "content"}
        entry["detail"] = rule.get("message", "") + " | " + json.dumps(
            detail, ensure_ascii=False, default=str)
    return entry


def run_rules(rules, context, category=None, rule_id=None):
    """批量执行规则，返回 (entries, error_count, warn_count, skip_count)。"""
    entries = []
    error_count = warn_count = skip_count = 0
    for rule in rules:
        if category and rule.get("category") != category:
            continue
        if rule_id and rule.get("id") != rule_id:
            continue
        entry = run_rule(rule, context)
        entries.append(entry)
        if entry["status"] == "skip":
            skip_count += 1
        elif entry["status"] == "fail":
            if entry["severity"] == "warn":
                warn_count += 1
            else:
                error_count += 1
    return entries, error_count, warn_count, skip_count


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="统一验证框架 - 规则化检查执行器")
    parser.add_argument("--rules", default=None,
                        help=f"规则文件路径（默认 {DEFAULT_RULES_PATH}）")
    parser.add_argument("--context", nargs="*", default=[], metavar="KEY=VALUE",
                        help="上下文变量，如 config_path=xxx.json html_path=xxx.html")
    parser.add_argument("--category", default=None, help="只执行指定类别的规则")
    parser.add_argument("--rule-id", default=None, help="只执行指定 id 的规则")
    parser.add_argument("--list-rules", action="store_true", help="列出规则清单后退出")
    args = parser.parse_args()

    start_time = time.time()
    try:
        rules, version = load_rules(args.rules)
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        result = Result.failure(code=EXIT_CODE.CONFIG_ERROR, message=str(e))
        result.print_to_stdout()
        return result.code

    if args.list_rules:
        print(f"规则配置 v{version}，共 {len(rules)} 条：")
        for rule in rules:
            print(f"  [{rule.get('severity', 'error'):5}] "
                  f"{rule.get('category', '?'):16} {rule.get('id', '?')}")
        return EXIT_CODE.SUCCESS

    context = {}
    for item in args.context:
        if "=" not in item:
            result = Result.failure(code=EXIT_CODE.CONFIG_ERROR,
                                    message=f"--context 参数格式应为 KEY=VALUE: {item}")
            result.print_to_stdout()
            return result.code
        key, _, value = item.partition("=")
        context[key] = value

    entries, error_count, warn_count, skip_count = run_rules(
        rules, context, category=args.category, rule_id=args.rule_id)

    for entry in entries:
        icon = {"pass": "✓", "fail": "✗", "skip": "-"}[entry["status"]]
        line = f"  {icon} [{entry['status']:4}] {entry['id']}"
        if entry["detail"]:
            line += f" — {entry['detail']}"
        print(line)

    execution_time_ms = int((time.time() - start_time) * 1000)
    executed = len(entries) - skip_count
    data = {
        "rules_version": version,
        "executed": executed,
        "passed": executed - error_count - warn_count,
        "failed_error": error_count,
        "failed_warn": warn_count,
        "skipped": skip_count,
        "entries": entries,
    }
    if error_count:
        result = Result.failure(
            code=EXIT_CODE.GATE_FAILURE,
            message=f"Validation failed: {error_count} error(s), {warn_count} warning(s)",
            data=data)
    else:
        result = Result.success(
            data=data,
            message=f"Validation passed ({executed} executed, "
                    f"{warn_count} warning(s), {skip_count} skipped)",
            execution_time_ms=execution_time_ms)
    result.print_to_stdout()
    return result.code


if __name__ == "__main__":
    sys.exit(main())
