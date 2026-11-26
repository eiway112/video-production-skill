"""完工报告六维技术审计的唯一结构契约，无进程副作用。"""

from __future__ import annotations

from typing import Any

import _gate_status as gs

END_TO_END_AUTHENTICITY = "end_to_end_authenticity"
GATE_INTEGRITY = "gate_integrity"
DELIVERY_COMPLIANCE = "delivery_compliance"
STORYBOARD_FIDELITY = "storyboard_fidelity"
COMPLETION_INTEGRITY = "completion_integrity"
DELIVERY_NOTES_CONSISTENCY = "delivery_notes_consistency"

AUDIT_DIMENSIONS = (
    END_TO_END_AUTHENTICITY,
    GATE_INTEGRITY,
    DELIVERY_COMPLIANCE,
    STORYBOARD_FIDELITY,
    COMPLETION_INTEGRITY,
    DELIVERY_NOTES_CONSISTENCY,
)
REPORT_PASS_STATES = ("VALIDATED", "VALIDATED_WITH_ISSUES")


def adjudicate_audit(audit: Any) -> tuple[str, list[str]]:
    if not isinstance(audit, dict):
        return gs.UNTESTED, ["audit 必须是对象"]

    errors: list[str] = []
    failed = False
    top_level = audit.get("passed")
    if top_level is False:
        failed = True
        errors.append("audit.passed=false")
    elif top_level is not True:
        errors.append("audit.passed 必须为布尔值")

    dimensions = audit.get("dimensions")
    if not isinstance(dimensions, dict):
        errors.append("audit.dimensions 必须是对象")
    else:
        missing = [name for name in AUDIT_DIMENSIONS if name not in dimensions]
        unexpected = sorted(set(dimensions) - set(AUDIT_DIMENSIONS))
        if missing:
            errors.append("缺少审计维度: " + ", ".join(missing))
        if unexpected:
            errors.append("未知审计维度: " + ", ".join(unexpected))
        for name in AUDIT_DIMENSIONS:
            item = dimensions.get(name)
            if not isinstance(item, dict):
                errors.append(f"审计维度 {name} 必须是对象")
            elif item.get("passed") is False:
                failed = True
                errors.append(f"{name}.passed=false")
            elif item.get("passed") is not True:
                errors.append(f"{name}.passed 必须为布尔值")

    if failed:
        return gs.FAIL, ["六维技术审计未通过"] + errors
    if errors:
        return gs.UNTESTED, ["六维技术审计结构不完整"] + errors
    return gs.PASS, []
