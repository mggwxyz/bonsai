from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from bonsai.compose import detect_compose_project
from bonsai.models import CommandResult, DoctorApplyPlan, DoctorCheck, DoctorReport

DOCTOR_SCHEMA = "bonsai.doctor.v1"

PREFLIGHT_SHELL_INTEGRATION_MARKER = "# >>> bonsai shell integration >>>"


class _Runner(Protocol):
    def run(
        self,
        argv: list[str],
        cwd: Path | None = ...,
        check: bool = ...,
        env: Any | None = ...,
    ) -> CommandResult: ...


def _command_available(runner: _Runner, argv: list[str]) -> bool:
    try:
        result = runner.run(argv, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def preflight_report(
    runner: _Runner,
    repo_path: Path | None = None,
    home: Path | None = None,
) -> DoctorReport:
    home_dir = home if home is not None else Path.home()
    checks: list[DoctorCheck] = []

    git_ok = _command_available(runner, ["git", "--version"])
    checks.append(
        DoctorCheck(
            "git",
            "ok" if git_ok else "fail",
            "git is available" if git_ok else "git command not found",
            id="git",
            hint=None if git_ok else "Install Git: https://git-scm.com/downloads",
        )
    )

    caddy_ok = _command_available(runner, ["caddy", "version"])
    checks.append(
        DoctorCheck(
            "caddy",
            "ok" if caddy_ok else "fail",
            "caddy is available" if caddy_ok else "caddy command not found",
            id="caddy",
            hint=None
            if caddy_ok
            else "Optional: run `brew install caddy` for pretty URLs; "
            "Bonsai falls back to direct ports without it",
        )
    )

    brew_ok = _command_available(runner, ["brew", "--version"])
    checks.append(
        DoctorCheck(
            "brew",
            "ok" if brew_ok else "fail",
            "brew is available" if brew_ok else "brew command not found",
            id="brew",
            hint=None
            if brew_ok
            else "Install Homebrew so Bonsai can auto-install Caddy: https://brew.sh",
        )
    )

    zshrc = home_dir / ".zshrc"
    shell_ok = zshrc.exists() and PREFLIGHT_SHELL_INTEGRATION_MARKER in zshrc.read_text(
        encoding="utf-8"
    )
    checks.append(
        DoctorCheck(
            "shell integration",
            "ok" if shell_ok else "fail",
            f"Integration present in {zshrc}"
            if shell_ok
            else f"Integration missing from {zshrc}",
            id="shell-integration",
            hint=None
            if shell_ok
            else 'Add to ~/.zshrc: eval "$(bonsai shell-init zsh)" '
            "(or run `bonsai install-shell zsh`)",
        )
    )

    if repo_path is not None and detect_compose_project(repo_path) is not None:
        docker_ok = _command_available(runner, ["docker", "--version"])
        checks.append(
            DoctorCheck(
                "docker",
                "ok" if docker_ok else "fail",
                "docker is available" if docker_ok else "docker command not found",
                id="docker",
                hint=None
                if docker_ok
                else "Install Docker: https://docs.docker.com/get-docker/",
            )
        )

    return DoctorReport(checks=tuple(checks))


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
                "id": check.id or _fallback_check_id(check.name),
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "hint": check.hint,
                "repair": check.repair,
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
