from __future__ import annotations

from rich.table import Table

from bonsai.models import (
    CleanupPlan,
    DoctorApplyPlan,
    DoctorReport,
    PortRepairPlan,
    RepairPlan,
    StopProcessPlan,
    SyncPlan,
)

CommandRenderable = str | Table


def _join_lines(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def render_sync_result(plan: SyncPlan, apply: bool) -> str:
    mode = "apply" if apply else "dry run"
    lines = [f"sync {mode}"]
    if not plan.actions:
        lines.append("No sync changes")
    for action in plan.actions:
        lines.append(f"{action.kind} {action.path}")
    if apply and plan.reload_caddy:
        lines.append("reload Caddy")
    elif not apply and plan.reload_caddy and plan.actions:
        lines.append("reload Caddy after apply")
    return _join_lines(lines)


def _repair_action_label(action: str, apply: bool) -> str:
    if not apply:
        return action
    if action == "remove":
        return "removed"
    if action == "repack":
        return "repacked"
    return action


def render_repair_result(plan: RepairPlan, apply: bool) -> str:
    mode = "apply" if apply else "dry run"
    lines = [f"repair {mode}"]
    if not plan.items:
        lines.append("No state repairs needed")
    for item in plan.items:
        action = _repair_action_label(item.action, apply)
        lines.append(f"{action} {item.branch} - {item.reason}")
    if plan.state_changed:
        lines.append("Run: bonsai sync --apply")
    return _join_lines(lines)


def render_port_repair_result(plan: PortRepairPlan, apply: bool) -> str:
    mode = "apply" if apply else "dry run"
    lines = [f"repair-ports {mode}"]
    if not plan.items:
        lines.append("No port repairs needed")
        return _join_lines(lines)

    for item in plan.items:
        lines.append(f"{item.branch} slot {item.current_slot} -> {item.proposed_slot}")
        for service in item.services:
            lines.append(f"  {service.port_env} {service.old_port} -> {service.new_port}")
            for owner in getattr(service, "owners", ()):
                owner_label = f"{owner.command or 'process'}[{owner.pid}]"
                if owner.worktree_branch is not None:
                    owner_label = f"{owner_label} in {owner.worktree_branch}"
                elif owner.cwd is not None:
                    owner_label = f"{owner_label} at {owner.cwd}"
                lines.append(f"    owner {owner_label}")
    if apply:
        lines.append("Updated state and regenerated files")
    else:
        lines.append("No files changed")
    return _join_lines(lines)


def _owner_label(owner) -> str:
    label = f"{owner.command or 'process'}[{owner.pid}]"
    if owner.worktree_branch is not None:
        return f"{label} in {owner.worktree_branch}"
    if owner.cwd is not None:
        return f"{label} at {owner.cwd}"
    return label


def render_stop_result(plan: StopProcessPlan) -> str:
    lines = ["stop"]
    if not plan.items:
        lines.append("No listener processes matched")
        return _join_lines(lines)
    for item in plan.items:
        line = (
            f"{item.action} {item.branch} {item.service_name} "
            f"{item.port_env}={item.port} {_owner_label(item.owner)}"
        )
        if item.action == "skip":
            line = f"{line} - {item.reason}"
        lines.append(line)
    return _join_lines(lines)


def render_cleanup_result(plan: CleanupPlan, apply: bool) -> str:
    mode = "apply" if apply else "dry run"
    lines = [f"cleanup {mode}"]
    if not plan.items:
        lines.append("No managed worktrees")
    for item in plan.items:
        suffix = item.reason
        if item.pr_url is not None:
            suffix = f"{suffix} ({item.pr_url})"
        lines.append(f"{item.action} {item.branch} - {suffix}")
    return _join_lines(lines)


def _doctor_table(report: DoctorReport) -> Table:
    table = Table(title="Bonsai doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    table.add_column("Hint")
    for check in report.checks:
        table.add_row(check.name, check.status, check.detail, check.hint or "")
    return table


def render_doctor_result(
    report: DoctorReport,
    apply: bool,
    apply_plan: DoctorApplyPlan | None = None,
) -> tuple[CommandRenderable, ...]:
    rendered: list[CommandRenderable] = []
    if apply:
        rendered.append("doctor apply")
        if apply_plan is not None and apply_plan.actions:
            rendered.extend(
                f"{action.kind} {action.detail}" for action in apply_plan.actions
            )
        else:
            rendered.append("No repairs applied")
    rendered.append(_doctor_table(report))
    return tuple(rendered)
