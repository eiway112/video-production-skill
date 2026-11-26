"""视频交付、技术验证、治理验收、业务验收的四轴状态投影。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import _gate_status as gs
import completion_audit_contract as audit_contract
from _script_env import ROOT as EVIDENCE_ROOT
from script_interface import atomic_write_json

PRODUCTION = "production"
ENGINEERING = "engineering"
ACCEPTANCE_MODES = (PRODUCTION, ENGINEERING)
DEFAULT_ACCEPTANCE_MODE = PRODUCTION
CONFIG_FIELDS = ("acceptance_mode", "governance_evidence_dir",
                 "business_acceptance_file")
REPORT_PASS_STATES = audit_contract.REPORT_PASS_STATES

DELIVERED = "DELIVERED"
NOT_DELIVERED = "NOT_DELIVERED"
PENDING_USER = "PENDING_USER"
USER_ACCEPTED = "USER_ACCEPTED"
NOT_RUN = "NOT_RUN"

GOVERNANCE_FILES = {
    "workspace_health": "workspace_health.json",
    "independent_review": "independent_review.json",
    "governance_checks": "governance_checks.json",
}
REVIEW_ROLES = ("critic", "evaluator")
GOVERNANCE_CHECKS = ("ledger", "provenance", "version_trace")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_EVIDENCE_POINTER_RE = re.compile(
    r"^(?P<path>.+?):(?P<start>\d+)"
    r"(?:-(?P<end>\d+))?(?:\s*[（(].*[）)])?\s*$"
)


class AcceptanceInputError(ValueError):
    pass


def resolve_acceptance_mode(explicit: str | None = None,
                            config: dict[str, Any] | None = None) -> str:
    if explicit is not None:
        mode = explicit
    elif config is not None and "acceptance_mode" in config:
        mode = config["acceptance_mode"]
    else:
        mode = DEFAULT_ACCEPTANCE_MODE
    if mode not in ACCEPTANCE_MODES:
        raise AcceptanceInputError(
            f"acceptance_mode 非法：{mode!r}；只接受 {', '.join(ACCEPTANCE_MODES)}"
        )
    return mode


def resolve_evidence_path(value: str | Path | None,
                          base: str | Path) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path(base) / path


def _required_path(value: str | Path, name: str, base: Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise AcceptanceInputError(f"{name} 不能为空")
    return resolve_evidence_path(value, base)


def _normalized_path(value: str | Path, base: Path) -> Path:
    path = resolve_evidence_path(value, base)
    return path.resolve(strict=False)


def _validate_project(project: Any) -> None:
    if not isinstance(project, str) or not project.strip():
        raise AcceptanceInputError("project 不能为空")
    if project != project.strip() or Path(project).name != project or project in (".", ".."):
        raise AcceptanceInputError("project 必须是单段非空目录名")


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"unreadable: {exc}"
    if not isinstance(data, dict):
        return None, "not-an-object"
    return data, None


def _valid_timestamp(value: Any) -> bool:
    return (
        isinstance(value, str)
        and _TIMESTAMP_RE.fullmatch(value) is not None
        and _parse_timestamp(value) is not None
    )


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _evidence_pointer_error(pointer: Any, evidence_root: Path) -> str | None:
    if not isinstance(pointer, str) or not pointer.strip():
        return "证据指针缺失"
    match = _EVIDENCE_POINTER_RE.fullmatch(pointer)
    if not match:
        return "证据指针须为 视频工作流根相对路径:行号[-行号]"
    raw_path = Path(match.group("path"))
    if raw_path.is_absolute() or raw_path.drive:
        return "证据路径须为视频工作流根相对路径"
    path = _normalized_path(raw_path, evidence_root)
    try:
        path.relative_to(evidence_root.resolve())
    except ValueError:
        return "证据路径越出视频工作流根"
    if not path.is_file():
        return f"证据文件不存在: {path}"
    try:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeError) as exc:
        return f"证据文件不可读: {path}: {exc}"
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if start < 1 or end < start or end > line_count:
        return (
            f"证据行号越界: {path.name} 请求 {start}-{end}，"
            f"实际 1-{line_count}"
        )
    return None


def _project_delivery(registry_path: Path, video_path: Path,
                      project: str) -> dict[str, Any]:
    data, error = _read_json(registry_path)
    base = {
        "status": NOT_DELIVERED,
        "delivered_at": None,
        "video": video_path.name,
        "subtitle": None,
        "source": "delivery_registry",
        "evidence": str(registry_path),
        "artifact_present": video_path.is_file(),
    }
    if error:
        base["reason"] = f"交付登记不可用：{error}"
        return base
    entries = data.get("deliveries")
    if not isinstance(entries, list):
        base["reason"] = "交付登记缺少 deliveries 列表"
        return base
    matches = [
        entry for entry in entries
        if isinstance(entry, dict)
        and entry.get("video") == video_path.name
        and not entry.get("retired")
        and entry.get("project") == project
    ]
    if len(matches) != 1:
        base["reason"] = f"当前视频的有效交付条目数为 {len(matches)}，要求恰为 1"
        return base
    entry = matches[0]
    registered_bytes = entry.get("bytes")
    registered_sha = entry.get("sha256")
    if "bytes" in entry and (
        type(registered_bytes) is not int or registered_bytes <= 0
    ):
        base["reason"] = "交付条目 bytes 非法"
        return base
    if "sha256" in entry and not _valid_sha256(registered_sha):
        base["reason"] = "交付条目 sha256 非法"
        return base
    if video_path.is_file():
        if registered_bytes is None and registered_sha is None:
            base["reason"] = "实物存在但登记缺少 bytes/sha256，无法确认交付身份"
            return base
        try:
            actual_bytes = video_path.stat().st_size
            actual_sha256 = hashlib.sha256(video_path.read_bytes()).hexdigest()
        except OSError as exc:
            base["reason"] = f"当前实物不可核对：{exc}"
            return base
        if registered_bytes is not None and registered_bytes != actual_bytes:
            base["reason"] = (
                f"登记字节 {registered_bytes} 与当前实物字节 {actual_bytes} 不一致"
            )
            return base
        if registered_sha and registered_sha.lower() != actual_sha256:
            base["reason"] = "登记 SHA256 与当前实物不一致"
            return base
    delivered_at = entry.get("delivered_at")
    report_status = entry.get("report_status")
    if not _valid_timestamp(delivered_at):
        base["reason"] = "交付条目缺少合法 delivered_at"
        return base
    if report_status not in REPORT_PASS_STATES:
        base["reason"] = f"交付条目 report_status 非通过态：{report_status!r}"
        return base
    base.update({
        "status": DELIVERED,
        "delivered_at": delivered_at,
        "subtitle": entry.get("subtitle"),
        "bytes": registered_bytes,
        "duration_seconds": entry.get("duration_seconds"),
        "sha256": registered_sha.lower() if registered_sha else None,
    })
    return base


def _video_identity_error(video_src: dict[str, Any],
                          video_path: Path) -> tuple[str | None, str | None]:
    reported_size = video_src.get("size_bytes")
    if type(reported_size) is not int or reported_size < 0:
        return "完工报告缺少合法 video_file.size_bytes", None
    modified_at = video_src.get("modified_at")
    if (not _valid_timestamp(modified_at)
            or _parse_timestamp(modified_at).tzinfo is not None):
        return "完工报告缺少合法本地时间 video_file.modified_at", None

    has_sha256 = "sha256" in video_src
    if has_sha256 and not _valid_sha256(video_src.get("sha256")):
        return "完工报告 video_file.sha256 非法", None
    identity_mode = "sha256" if has_sha256 else "legacy_size_mtime"
    try:
        before = video_path.stat()
        if not video_path.is_file():
            return "当前视频实物不存在，无法核对完工报告身份", identity_mode
        digest = hashlib.sha256()
        with video_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = video_path.stat()
    except OSError as exc:
        return f"当前视频实物不可核对：{exc}", identity_mode
    if (before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns):
        return "当前视频在身份核对期间发生变化", identity_mode
    if reported_size != after.st_size:
        return (
            f"完工报告字节 {reported_size} 与当前实物字节 {after.st_size} 不一致"
        ), identity_mode
    try:
        reported_mtime = _parse_timestamp(modified_at)
        actual_mtime = datetime.fromtimestamp(after.st_mtime)
    except (OverflowError, OSError, ValueError):
        return "当前视频修改时间无法核对", identity_mode
    if reported_mtime != actual_mtime:
        return "完工报告 modified_at 与当前实物修改时间不一致", identity_mode
    if has_sha256 and video_src["sha256"].lower() != digest.hexdigest():
        return "完工报告 SHA256 与当前实物不一致", identity_mode
    return None, identity_mode


def _project_technical(report_path: Path, project: str,
                       video_path: Path, root: Path) -> dict[str, Any]:
    base = {
        "status": gs.UNTESTED,
        "report_status": None,
        "source": "completion_report",
        "evidence": str(report_path),
    }
    data, error = _read_json(report_path)
    if error:
        base["reason"] = f"完工报告不可用：{error}"
        return base
    if data.get("project_name") != project:
        base["reason"] = "完工报告 project_name 与当前项目不一致"
        return base
    sources = data.get("data_sources")
    video_src = sources.get("video_file") if isinstance(sources, dict) else None
    reported_video = video_src.get("path") if isinstance(video_src, dict) else None
    if not isinstance(reported_video, str) or not reported_video.strip():
        base["reason"] = "完工报告缺少 video_file.path，无法绑定当前视频"
        return base
    if _normalized_path(reported_video, root) != _normalized_path(video_path, root):
        base["reason"] = "完工报告 video_file.path 与当前视频不一致"
        return base
    identity_error, identity_mode = _video_identity_error(video_src, video_path)
    if identity_error:
        base["reason"] = identity_error
        return base
    base["video_identity_mode"] = identity_mode

    report_status = data.get("status")
    base["report_status"] = report_status
    summary = data.get("validation_summary")
    audit = data.get("audit")
    if report_status == "FAILED":
        base["status"] = gs.FAIL
        base["reason"] = "完工报告状态为 FAILED"
        return base
    if isinstance(summary, dict):
        failed = summary.get("failed_checks")
        if type(failed) is int and failed > 0:
            base["status"] = gs.FAIL
            base["reason"] = f"完工报告有 {failed} 项失败检查"
            return base
    audit_status, audit_reasons = audit_contract.adjudicate_audit(audit)
    if audit_status == gs.FAIL:
        base["status"] = gs.FAIL
        base["reason"] = "; ".join(audit_reasons)
        return base
    if audit_status != gs.PASS:
        base["reason"] = "; ".join(audit_reasons)
        return base
    if report_status not in REPORT_PASS_STATES:
        base["reason"] = f"完工报告状态无法裁定：{report_status!r}"
        return base
    failed = summary.get("failed_checks") if isinstance(summary, dict) else None
    if type(failed) is not int or failed < 0:
        base["reason"] = "完工报告缺少合法 validation_summary.failed_checks"
        return base
    base["status"] = gs.PASS
    base["validation_summary"] = summary
    base["audit_present"] = True
    return base


def technical_evidence_usable(*, report_path: str | Path,
                              video_file: str | Path, project: str,
                              root: str | Path = EVIDENCE_ROOT) -> tuple[bool, list[str]]:
    _validate_project(project)
    root_path = Path(root)
    report = _required_path(report_path, "completion_report", root_path)
    video = _required_path(video_file, "video_file", root_path)
    result = _project_technical(report, project, video, root_path)
    reasons = [] if result["status"] == gs.PASS else [result.get("reason", "技术证据不可用")]
    return result["status"] == gs.PASS, reasons


def _normalize_governance_status(value: Any) -> str | None:
    if value == "OK":
        return "PASS"
    if value in ("PASS", "WARN", "FAIL", "UNTESTED"):
        return value
    return None


def _health_verdict(data: dict[str, Any], workspace_root: Path,
                    evidence_root: Path) -> tuple[str, list[str]]:
    status = _normalize_governance_status(data.get("overall_status"))
    errors = []
    if status is None:
        errors.append("workspace_health.overall_status 非法")
    if data.get("scope") != "workspace":
        errors.append("workspace_health.scope 必须为 workspace")
    subject = data.get("subject")
    if (not isinstance(subject, str)
            or _normalized_path(subject, evidence_root) != workspace_root):
        errors.append("workspace_health.subject 不是当前工程工作区")
    if _normalize_governance_status(data.get("workspace_status")) != status:
        errors.append("workspace_status 与 overall_status 不一致")
    if type(data.get("can_proceed")) is not bool:
        errors.append("workspace_health.can_proceed 缺失或非法")
    elif status is not None and data["can_proceed"] != (status != "FAIL"):
        errors.append("workspace_health.can_proceed 与总体状态不一致")
    if not _valid_timestamp(data.get("timestamp")):
        errors.append("workspace_health.timestamp 缺失或非法")
    if status == "FAIL":
        return "FAIL", errors
    if errors or status in (None, "UNTESTED"):
        return "UNTESTED", errors
    return status, errors


def _review_verdict(data: dict[str, Any],
                    evidence_root: Path) -> tuple[str, list[str]]:
    statuses = []
    errors = []
    for role in REVIEW_ROLES:
        item = data.get(role)
        if not isinstance(item, dict):
            errors.append(f"independent_review.{role} 缺失")
            continue
        status = _normalize_governance_status(item.get("status"))
        if status is None:
            errors.append(f"independent_review.{role}.status 非法")
        else:
            statuses.append(status)
        pointer_error = _evidence_pointer_error(item.get("evidence"), evidence_root)
        if pointer_error:
            errors.append(f"independent_review.{role}.evidence: {pointer_error}")
    if "FAIL" in statuses:
        return "FAIL", errors
    if errors or "UNTESTED" in statuses:
        return "UNTESTED", errors
    if "WARN" in statuses:
        return "WARN", errors
    return "PASS", errors


def _checks_verdict(data: dict[str, Any],
                    evidence_root: Path) -> tuple[str, list[str]]:
    statuses = []
    errors = []
    for name in GOVERNANCE_CHECKS:
        item = data.get(name)
        if not isinstance(item, dict):
            errors.append(f"governance_checks.{name} 缺失")
            continue
        status = _normalize_governance_status(item.get("status"))
        if status is None:
            errors.append(f"governance_checks.{name}.status 非法")
        else:
            statuses.append(status)
        pointer_error = _evidence_pointer_error(item.get("evidence"), evidence_root)
        if pointer_error:
            errors.append(f"governance_checks.{name}.evidence: {pointer_error}")
    if "FAIL" in statuses:
        return "FAIL", errors
    if errors or "UNTESTED" in statuses:
        return "UNTESTED", errors
    if "WARN" in statuses:
        return "WARN", errors
    return "PASS", errors


def _project_governance(mode: str, evidence_dir: Path,
                        evidence_root: Path, workspace_root: Path) -> dict[str, Any]:
    paths = {name: evidence_dir / filename for name, filename in GOVERNANCE_FILES.items()}
    present = {name: path.exists() for name, path in paths.items()}
    base = {
        "status": NOT_RUN,
        "can_proceed": mode == PRODUCTION,
        "source": "governance_evidence",
        "evidence": [str(paths[name]) for name in paths if present[name]],
        "missing_required": [],
        "missing_evidence": [str(path) for name, path in paths.items() if not present[name]],
        "errors": [],
    }
    if not any(present.values()):
        if mode == ENGINEERING:
            base["missing_required"] = [str(path) for path in paths.values()]
        return base

    verdicts: dict[str, str] = {}
    if present["workspace_health"]:
        health, health_error = _read_json(paths["workspace_health"])
        if health_error:
            base["errors"].append(f"workspace_health: {health_error}")
        else:
            verdict, errors = _health_verdict(
                health, workspace_root, evidence_root)
            verdicts["workspace_health"] = verdict
            base["errors"].extend(errors)
    if present["independent_review"]:
        review, review_error = _read_json(paths["independent_review"])
        if review_error:
            base["errors"].append(f"independent_review: {review_error}")
        else:
            verdict, errors = _review_verdict(review, evidence_root)
            verdicts["independent_review"] = verdict
            base["errors"].extend(errors)
    if present["governance_checks"]:
        checks, checks_error = _read_json(paths["governance_checks"])
        if checks_error:
            base["errors"].append(f"governance_checks: {checks_error}")
        else:
            verdict, errors = _checks_verdict(checks, evidence_root)
            verdicts["governance_checks"] = verdict
            base["errors"].extend(errors)
    base["evidence_status"] = verdicts

    if "FAIL" in verdicts.values():
        base["status"] = "FAIL"
    elif mode == ENGINEERING:
        base["missing_required"] = [
            str(path) for name, path in paths.items() if not present[name]
        ]
        if base["missing_required"] or base["errors"] or "UNTESTED" in verdicts.values():
            base["status"] = "FAIL"
        elif "WARN" in verdicts.values():
            base["status"] = "WARN"
        else:
            base["status"] = "PASS"
    elif (base["missing_evidence"] or base["errors"]
          or "UNTESTED" in verdicts.values()):
        base["status"] = "WARN"
    elif "WARN" in verdicts.values():
        base["status"] = "WARN"
    else:
        base["status"] = "PASS"
    base["can_proceed"] = base["status"] != "FAIL"
    return base


def _project_business(evidence_path: Path | None, project: str,
                      video_file: Path, delivery: dict[str, Any],
                      evidence_root: Path) -> dict[str, Any]:
    base = {
        "status": PENDING_USER,
        "accepted_at": None,
        "source": "explicit_user_confirmation" if evidence_path else "none",
        "evidence": str(evidence_path) if evidence_path else None,
    }
    if evidence_path is None:
        base["reason"] = "没有用户明确验收证据"
        return base
    data, error = _read_json(evidence_path)
    if error:
        base["reason"] = f"业务验收证据不可用：{error}"
        return base
    if data.get("status") == "PENDING_USER":
        return base
    if data.get("status") != "USER_ACCEPTED":
        base["reason"] = "业务验收证据未声明 USER_ACCEPTED"
        return base
    if delivery["status"] != DELIVERED:
        base["reason"] = "交付事实未成立，不能记录用户验收"
        return base
    if data.get("project") != project or data.get("video") != video_file.name:
        base["reason"] = "业务验收证据与当前项目或视频身份不一致"
        return base
    accepted_at = data.get("accepted_at")
    if not _valid_timestamp(accepted_at):
        base["reason"] = "USER_ACCEPTED 缺少合法 accepted_at"
        return base
    if data.get("delivered_at") != delivery["delivered_at"]:
        base["reason"] = "业务验收证据未绑定当前 delivered_at"
        return base
    try:
        accepted_dt = _parse_timestamp(accepted_at)
        delivered_dt = _parse_timestamp(delivery["delivered_at"])
        if accepted_dt < delivered_dt:
            base["reason"] = "accepted_at 早于 delivered_at"
            return base
    except TypeError:
        base["reason"] = "业务验收时间无法与交付时间比较"
        return base
    if delivery.get("sha256"):
        if data.get("sha256") != delivery["sha256"]:
            base["reason"] = "业务验收证据未绑定当前 SHA256"
            return base
    elif delivery.get("bytes") is not None:
        if data.get("bytes") != delivery["bytes"]:
            base["reason"] = "业务验收证据未绑定当前交付字节数"
            return base
    else:
        base["reason"] = "交付身份不足，不能升级用户验收"
        return base
    pointer_error = _evidence_pointer_error(data.get("evidence"), evidence_root)
    if pointer_error:
        base["reason"] = f"用户确认证据不可定位：{pointer_error}"
        return base
    base.update({
        "status": USER_ACCEPTED,
        "accepted_at": accepted_at,
        "confirmation": data["evidence"],
    })
    return base


def build_acceptance_status(*, registry_file: str | Path,
                            video_file: str | Path,
                            completion_report: str | Path,
                            project: str,
                            acceptance_mode: str | None = None,
                            config: dict[str, Any] | None = None,
                            governance_evidence_dir: str | Path | None = None,
                            business_acceptance_file: str | Path | None = None,
                            root: str | Path = EVIDENCE_ROOT,
                            workspace_root: str | Path | None = None) -> dict[str, Any]:
    _validate_project(project)
    mode = resolve_acceptance_mode(acceptance_mode, config)
    root_path = Path(root)
    workspace_path = _normalized_path(
        workspace_root if workspace_root is not None else root_path.parent,
        root_path)
    registry_path = _required_path(registry_file, "registry_file", root_path)
    video_path = _required_path(video_file, "video_file", root_path)
    report_path = _required_path(completion_report, "completion_report", root_path)
    governance_dir = (
        resolve_evidence_path(governance_evidence_dir, root_path)
        or resolve_evidence_path((config or {}).get("governance_evidence_dir"), root_path)
        or report_path.parent / "governance"
    )
    business_path = (
        resolve_evidence_path(business_acceptance_file, root_path)
        or resolve_evidence_path((config or {}).get("business_acceptance_file"), root_path)
    )
    delivery = _project_delivery(registry_path, video_path, project)
    return {
        "schema_version": 1,
        "project": project,
        "acceptance_mode": mode,
        "video_delivery": delivery,
        "technical_validation": _project_technical(
            report_path, project, video_path, root_path),
        "governance_acceptance": _project_governance(
            mode, governance_dir, root_path, workspace_path),
        "business_acceptance": _project_business(
            business_path, project, video_path, delivery, root_path),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def acceptance_satisfied(status: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    if status["video_delivery"]["status"] != DELIVERED:
        reasons.append("视频未交付")
    if status["technical_validation"]["status"] != gs.PASS:
        reasons.append("技术验证未通过")
    if status["acceptance_mode"] == ENGINEERING:
        if status["governance_acceptance"]["status"] not in ("PASS", "WARN"):
            reasons.append("工程治理验收未通过")
    return not reasons, reasons


def format_status_lines(status: dict[str, Any]) -> list[str]:
    delivery = status["video_delivery"]
    delivered_at = f" delivered_at={delivery['delivered_at']}" if delivery["delivered_at"] else ""
    governance = status["governance_acceptance"]
    return [
        f"视频交付: {delivery['status']}{delivered_at}",
        f"技术验证: {status['technical_validation']['status']}",
        f"治理验收: {governance['status']} can_proceed={str(governance['can_proceed']).lower()}",
        f"业务验收: {status['business_acceptance']['status']}",
    ]


def write_acceptance_status(path: str | Path, status: dict[str, Any]) -> None:
    atomic_write_json(path, status)


def main() -> int:
    parser = argparse.ArgumentParser(description="投影视频四轴验收状态（只读证据源）")
    parser.add_argument("--registry-file", required=True)
    parser.add_argument("--video-file", required=True)
    parser.add_argument("--completion-report", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--config-file")
    parser.add_argument("--acceptance-mode", choices=ACCEPTANCE_MODES)
    parser.add_argument("--governance-evidence-dir")
    parser.add_argument("--business-acceptance-file")
    parser.add_argument("--output-file")
    parser.add_argument("--require-acceptance", action="store_true")
    args = parser.parse_args()
    try:
        config = None
        if args.config_file:
            config_path = Path(args.config_file)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise AcceptanceInputError("config 必须是 JSON 对象")
        status = build_acceptance_status(
            registry_file=args.registry_file,
            video_file=args.video_file,
            completion_report=args.completion_report,
            project=args.project_name,
            acceptance_mode=args.acceptance_mode,
            config=config,
            governance_evidence_dir=args.governance_evidence_dir,
            business_acceptance_file=args.business_acceptance_file,
        )
        if args.output_file:
            write_acceptance_status(args.output_file, status)
    except (AcceptanceInputError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"[ACCEPTANCE] ERROR: {exc}", file=sys.stderr)
        return 2
    print("[ACCEPTANCE]")
    for line in format_status_lines(status):
        print(f"  {line}")
    if args.require_acceptance:
        satisfied, reasons = acceptance_satisfied(status)
        if not satisfied:
            for reason in reasons:
                print(f"  BLOCKED: {reason}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
