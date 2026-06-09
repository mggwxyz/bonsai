from __future__ import annotations

import json
from typing import Any

from rich.table import Table
from rich.text import Text

from bonsai.errors import BonsaiConfigError
from bonsai.models import (
    PortOwner,
    WorkspacePort,
    WorkspacePortsPlan,
    WorkspaceServiceSummary,
    WorkspaceStatus,
    WorkspaceSummary,
    WorkspaceUrl,
    WorkspaceUrlsPlan,
    WorktreeSummary,
)

LIST_SCHEMA = "bonsai.list.v1"
STATUS_SCHEMA = "bonsai.status.v1"
PORTS_SCHEMA = "bonsai.ports.v1"
URLS_SCHEMA = "bonsai.urls.v1"


def validate_status_format(output_format: str) -> str:
    normalized = output_format.lower()
    if normalized not in {"text", "json"}:
        raise BonsaiConfigError(f"Unsupported format: {output_format}")
    return normalized


def _workspace_payload(summary: WorkspaceSummary | WorkspaceStatus) -> dict[str, str]:
    return {
        "name": summary.workspace_name,
        "root": str(summary.workspace_root),
        "default_branch": summary.default_branch,
        "default_worktree": summary.default_worktree,
        "config": str(summary.config_path),
    }


def _service_payload(service: WorkspaceServiceSummary) -> dict[str, Any]:
    return {
        "name": service.name,
        "port_env": service.port_env,
        "port": service.port,
        "public": service.public,
        "primary": service.primary,
        "url": service.url,
    }


def _worktree_payload(worktree: WorktreeSummary) -> dict[str, Any]:
    return {
        "branch": worktree.branch,
        "path": str(worktree.worktree_path),
        "relative_path": worktree.relative_path,
        "slug": worktree.slug,
        "slot": worktree.slot,
        "kind": worktree.kind,
        "env_file": {
            "path": str(worktree.env_file_path),
            "status": worktree.env_file_status,
        },
        "services": [_service_payload(service) for service in worktree.services],
    }


def _port_owner_payload(owner: PortOwner) -> dict[str, Any]:
    return {
        "pid": owner.pid,
        "command": owner.command,
        "user": owner.user,
        "cwd": str(owner.cwd) if owner.cwd is not None else None,
        "worktree_branch": owner.worktree_branch,
        "worktree_path": str(owner.worktree_path) if owner.worktree_path is not None else None,
    }


def _workspace_port_payload(port: WorkspacePort) -> dict[str, Any]:
    return {
        "branch": port.branch,
        "worktree_path": str(port.worktree_path),
        "service": port.service_name,
        "port_env": port.port_env,
        "port": port.port,
        "status": port.status,
        "owners": [_port_owner_payload(owner) for owner in port.owners],
    }


def workspace_list_payload(summary: WorkspaceSummary) -> dict[str, Any]:
    return {
        "schema": LIST_SCHEMA,
        "workspace": _workspace_payload(summary),
        "worktrees": [_worktree_payload(worktree) for worktree in summary.worktrees],
        "commands": dict(summary.commands),
    }


def workspace_status_payload(status: WorkspaceStatus) -> dict[str, Any]:
    location_path = (
        status.location_path
        or (status.current.worktree_path if status.current is not None else status.workspace_root)
    )
    return {
        "schema": STATUS_SCHEMA,
        "workspace": _workspace_payload(status),
        "location": {
            "kind": status.location_kind,
            "path": str(location_path),
        },
        "current": _worktree_payload(status.current) if status.current is not None else None,
        "commands": dict(status.commands),
    }


def workspace_ports_payload(
    plan: WorkspacePortsPlan,
    *,
    only_busy: bool = False,
) -> dict[str, Any]:
    ports = _filtered_workspace_ports(plan.ports, only_busy=only_busy)
    return {
        "schema": PORTS_SCHEMA,
        "workspace": {"root": str(plan.workspace_root)},
        "ports": [_workspace_port_payload(port) for port in ports],
    }


def _url_check_payload(check) -> dict[str, Any]:
    return {
        "name": check.name,
        "status": check.status,
        "detail": check.detail,
        "hint": check.hint,
    }


def _workspace_url_payload(item: WorkspaceUrl) -> dict[str, Any]:
    return {
        "branch": item.branch,
        "worktree_path": str(item.worktree_path),
        "service": item.service_name,
        "port_env": item.port_env,
        "port": item.port,
        "primary": item.primary,
        "url": item.url,
        "caddy_snippet_path": str(item.caddy_snippet_path),
        "checks": [_url_check_payload(check) for check in item.checks],
    }


def workspace_urls_payload(plan: WorkspaceUrlsPlan) -> dict[str, Any]:
    return {
        "schema": URLS_SCHEMA,
        "workspace": {"root": str(plan.workspace_root)},
        "caddyfile": str(plan.caddyfile),
        "urls": [_workspace_url_payload(item) for item in plan.urls],
    }


def _filtered_workspace_ports(
    ports,
    *,
    only_busy: bool,
) -> tuple[WorkspacePort, ...]:
    ports = tuple(ports)
    if not only_busy:
        return ports
    return tuple(port for port in ports if port.status != "free")


def _owner_label(owner: PortOwner) -> str:
    label = f"{owner.command or 'process'}[{owner.pid}]"
    if owner.worktree_branch is not None:
        return f"{label} in {owner.worktree_branch}"
    if owner.cwd is not None:
        return f"{label} at {owner.cwd}"
    return label


def _format_port_owners(port: WorkspacePort) -> str:
    if not port.owners:
        return ""
    return "\n".join(_owner_label(owner) for owner in port.owners)


def _workspace_ports_table(plan: WorkspacePortsPlan, *, only_busy: bool = False) -> Table:
    table = Table(title="Bonsai ports")
    table.add_column("Branch")
    table.add_column("Service")
    table.add_column("Port", justify="right")
    table.add_column("Status")
    table.add_column("Owners")
    for port in _filtered_workspace_ports(plan.ports, only_busy=only_busy):
        table.add_row(
            port.branch,
            f"{port.service_name}\n{port.port_env}",
            str(port.port),
            port.status,
            _format_port_owners(port),
        )
    return table


def _workspace_list_lines(summary: WorkspaceSummary) -> str:
    lines = [f"Worktrees for {summary.workspace_name}", ""]
    if not summary.worktrees:
        lines.append("  (no worktrees)")
        return "\n".join(lines) + "\n"
    branch_width = max(len(worktree.branch) for worktree in summary.worktrees)
    path_width = max(len(f"./{worktree.relative_path}") for worktree in summary.worktrees)
    for worktree in summary.worktrees:
        path = f"./{worktree.relative_path}"
        lines.append(
            f"  {worktree.branch.ljust(branch_width)}  {path.ljust(path_width)}  {worktree.kind}"
        )
    return "\n".join(lines) + "\n"


def render_workspace_list(summary: WorkspaceSummary, output_format: str) -> str:
    output_format = validate_status_format(output_format)
    if output_format == "json":
        return json.dumps(workspace_list_payload(summary), indent=2, sort_keys=True) + "\n"
    return _workspace_list_lines(summary)


def render_workspace_ports(
    plan: WorkspacePortsPlan,
    output_format: str,
    *,
    only_busy: bool = False,
) -> str | Table:
    output_format = validate_status_format(output_format)
    if output_format == "json":
        return json.dumps(
            workspace_ports_payload(plan, only_busy=only_busy),
            indent=2,
            sort_keys=True,
        ) + "\n"
    return _workspace_ports_table(plan, only_busy=only_busy)


def render_workspace_urls(plan: WorkspaceUrlsPlan, output_format: str) -> str:
    output_format = validate_status_format(output_format)
    if output_format == "json":
        return json.dumps(workspace_urls_payload(plan), indent=2, sort_keys=True) + "\n"

    lines = ["Bonsai URLs"]
    if not plan.urls:
        lines.append("No URLs matched")
        return "\n".join(lines) + "\n"
    for item in plan.urls:
        lines.append("")
        lines.append(f"{item.branch} / {item.service_name}")
        lines.append(item.url)
        lines.append(f"{item.port_env}={item.port}")
        lines.append(f"Caddy: {item.caddy_snippet_path}")
        for check in item.checks:
            lines.append(f"[{check.status}] {check.name}: {check.detail}")
            if check.hint:
                lines.append(f"  {check.hint}")
    return "\n".join(lines) + "\n"


def _workspace_status_lines(status: WorkspaceStatus) -> list[str]:
    current = status.current
    lines = [
        "Bonsai status",
        "",
        f"Workspace: {status.workspace_name}",
        f"Root: {status.workspace_root}",
        f"Config: {status.config_path}",
        f"Default branch: {status.default_branch}",
    ]

    if current is None:
        location_path = status.location_path or status.workspace_root
        lines.extend(
            [
                "Location: workspace root (parent folder)",
                f"Path: {location_path}",
                "",
                "Recommended commands:",
                f"  List worktrees: {status.commands['list']}",
                f"  Repair generated files: {status.commands['sync']}",
                f"  Diagnose workspace: {status.commands['doctor']}",
                "",
            ]
        )
        return lines

    lines.extend(
        [
            f"Branch: {current.branch}",
            f"Worktree: {current.worktree_path}",
            f"Path: {current.relative_path}",
            f"Slug: {current.slug}",
            f"Slot: {current.slot}",
            f"Kind: {current.kind}",
            f"Env file: {current.env_file_path} ({current.env_file_status})",
            "",
            "Services:",
        ]
    )
    for service in current.services:
        lines.append(f"  {service.name}")
        lines.append(f"    port: {service.port_env}={service.port}")
        if service.url is not None:
            lines.append(f"    url: {service.url}")
        lines.append(f"    public: {'yes' if service.public else 'no'}")
        lines.append(f"    primary: {'yes' if service.primary else 'no'}")

    lines.extend(
        [
            "",
            "Recommended commands:",
            f"  Start current worktree: {status.commands['start']}",
            f"  Open primary URL: {status.commands['open']}",
            f"  List worktrees: {status.commands['list']}",
            f"  Repair generated files: {status.commands['sync']}",
            f"  Diagnose workspace: {status.commands['doctor']}",
            "",
        ]
    )
    return lines


def _env_status_style(status: str) -> str:
    if status == "current":
        return "green"
    if status == "stale":
        return "yellow"
    if status == "missing":
        return "red"
    return "white"


def _yes_no_style(value: bool) -> str:
    return "green" if value else "dim"


def _append_label_value(
    text: Text,
    label: str,
    value: object,
    *,
    value_style: str | None = None,
) -> None:
    text.append(f"{label}:", style="bold cyan")
    text.append(" ")
    text.append(str(value), style=value_style)
    text.append("\n")


def _append_command(text: Text, label: str, command: str) -> None:
    text.append(f"  {label}:")
    text.append(" ")
    text.append(command, style="bold green")
    text.append("\n")


def _render_workspace_status_text(status: WorkspaceStatus) -> Text:
    current = status.current
    text = Text()
    text.append("Bonsai status", style="bold green")
    text.append("\n\n")
    _append_label_value(text, "Workspace", status.workspace_name, value_style="bold")
    _append_label_value(text, "Root", status.workspace_root, value_style="cyan")
    _append_label_value(text, "Config", status.config_path, value_style="cyan")
    _append_label_value(text, "Default branch", status.default_branch, value_style="magenta")

    if current is None:
        location_path = status.location_path or status.workspace_root
        _append_label_value(
            text,
            "Location",
            "workspace root (parent folder)",
            value_style="yellow",
        )
        _append_label_value(text, "Path", location_path, value_style="cyan")
        text.append("\n")
        text.append("Recommended commands:", style="bold")
        text.append("\n")
        _append_command(text, "List worktrees", status.commands["list"])
        _append_command(text, "Repair generated files", status.commands["sync"])
        _append_command(text, "Diagnose workspace", status.commands["doctor"])
        return text

    _append_label_value(text, "Branch", current.branch, value_style="bold magenta")
    _append_label_value(text, "Worktree", current.worktree_path, value_style="cyan")
    _append_label_value(text, "Path", current.relative_path, value_style="cyan")
    _append_label_value(text, "Slug", current.slug, value_style="magenta")
    _append_label_value(text, "Slot", current.slot, value_style="yellow")
    _append_label_value(text, "Kind", current.kind, value_style="yellow")
    text.append("Env file:", style="bold cyan")
    text.append(f" {current.env_file_path} (", style="cyan")
    text.append(current.env_file_status, style=_env_status_style(current.env_file_status))
    text.append(")\n", style="cyan")
    text.append("\n")
    text.append("Services:", style="bold")
    text.append("\n")
    for service in current.services:
        text.append(f"  {service.name}", style="bold magenta")
        text.append("\n")
        text.append("    port:", style="bold cyan")
        text.append(" ")
        text.append(service.port_env, style="yellow")
        text.append("=")
        text.append(str(service.port), style="yellow")
        text.append("\n")
        if service.url is not None:
            text.append("    url:", style="bold cyan")
            text.append(" ")
            text.append(service.url, style="blue underline")
            text.append("\n")
        text.append("    public:", style="bold cyan")
        text.append(" ")
        text.append("yes" if service.public else "no", style=_yes_no_style(service.public))
        text.append("\n")
        text.append("    primary:", style="bold cyan")
        text.append(" ")
        text.append("yes" if service.primary else "no", style=_yes_no_style(service.primary))
        text.append("\n")

    text.append("\n")
    text.append("Recommended commands:", style="bold")
    text.append("\n")
    _append_command(text, "Start current worktree", status.commands["start"])
    _append_command(text, "Open primary URL", status.commands["open"])
    _append_command(text, "List worktrees", status.commands["list"])
    _append_command(text, "Repair generated files", status.commands["sync"])
    _append_command(text, "Diagnose workspace", status.commands["doctor"])
    return text


def render_workspace_status(
    status: WorkspaceStatus,
    output_format: str,
    *,
    color: bool = False,
) -> str | Text:
    output_format = validate_status_format(output_format)
    if output_format == "json":
        return json.dumps(workspace_status_payload(status), indent=2, sort_keys=True) + "\n"
    if color:
        return _render_workspace_status_text(status)
    return "\n".join(_workspace_status_lines(status))
