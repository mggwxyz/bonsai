from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bonsai.errors import BonsaiConfigError
from bonsai.models import DoctorApplyPlan, DoctorReport

DOCTOR_SCHEMA = "bonsai.doctor.v1"


def validate_doctor_format(output_format: str) -> str:
    normalized = output_format.lower()
    if normalized not in {"text", "json"}:
        raise BonsaiConfigError(f"Unsupported format: {output_format}")
    return normalized


def _fallback_check_id(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return normalized or "check"


def doctor_report_payload(
    report: DoctorReport,
    workspace_root: Path,
    apply_plan: DoctorApplyPlan | None = None,
) -> dict[str, Any]:
    return {
        "schema": DOCTOR_SCHEMA,
        "workspace": {"root": str(workspace_root)},
        "failed": report.failed,
        "checks": [
            {
                "id": getattr(check, "id", None) or _fallback_check_id(check.name),
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "hint": check.hint,
                "repair": getattr(check, "repair", None),
            }
            for check in report.checks
        ],
        "applied": [
            {"kind": action.kind, "detail": action.detail}
            for action in (apply_plan.actions if apply_plan is not None else ())
        ],
    }


def render_doctor_json(
    report: DoctorReport,
    workspace_root: Path,
    apply_plan: DoctorApplyPlan | None = None,
) -> str:
    return json.dumps(
        doctor_report_payload(report, workspace_root, apply_plan),
        indent=2,
        sort_keys=True,
    ) + "\n"
