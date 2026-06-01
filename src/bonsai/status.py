from __future__ import annotations

import json
from typing import Any

from rich.table import Table

from bonsai.errors import BonsaiConfigError
from bonsai.models import (
    WorkspaceServiceSummary,
    WorkspaceStatus,
    WorkspaceSummary,
    WorktreeSummary,
)

LIST_SCHEMA = "bonsai.list.v1"
STATUS_SCHEMA = "bonsai.status.v1"


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


def _format_ports(services: tuple[WorkspaceServiceSummary, ...]) -> str:
    return "\n".join(f"{service.port_env}={service.port}" for service in services)


def _format_urls(services: tuple[WorkspaceServiceSummary, ...]) -> str:
    return "\n".join(service.url for service in services if service.url is not None)


def _workspace_list_table(summary: WorkspaceSummary) -> Table:
    table = Table(title=f"Worktrees for {summary.workspace_name}")
    table.add_column("Branch")
    table.add_column("Path")
    table.add_column("Slot", justify="right")
    table.add_column("Kind")
    table.add_column("Env")
    table.add_column("Ports")
    table.add_column("URLs")
    for worktree in summary.worktrees:
        table.add_row(
            worktree.branch,
            worktree.relative_path,
            str(worktree.slot),
            worktree.kind,
            worktree.env_file_status,
            _format_ports(worktree.services),
            _format_urls(worktree.services),
        )
    return table


def render_workspace_list(summary: WorkspaceSummary, output_format: str) -> str | Table:
    output_format = validate_status_format(output_format)
    if output_format == "json":
        return json.dumps(workspace_list_payload(summary), indent=2, sort_keys=True) + "\n"
    return _workspace_list_table(summary)


def render_workspace_status(status: WorkspaceStatus, output_format: str) -> str:
    output_format = validate_status_format(output_format)
    if output_format == "json":
        return json.dumps(workspace_status_payload(status), indent=2, sort_keys=True) + "\n"

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
        return "\n".join(lines)

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
    return "\n".join(lines)
