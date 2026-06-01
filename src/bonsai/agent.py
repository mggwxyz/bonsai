from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from bonsai.errors import BonsaiConfigError
from bonsai.models import AgentContext

AGENT_CONTEXT_SCHEMA = "bonsai.context.v1"


def validate_agent_format(output_format: str) -> str:
    normalized = output_format.lower()
    if normalized not in {"text", "json"}:
        raise BonsaiConfigError(f"Unsupported format: {output_format}")
    return normalized


def agent_context_payload(context: AgentContext) -> dict[str, Any]:
    return {
        "schema": AGENT_CONTEXT_SCHEMA,
        "workspace": {
            "name": context.workspace_name,
            "root": str(context.workspace_root),
            "default_branch": context.default_branch,
            "default_worktree": context.default_worktree,
            "config": str(context.config_path),
        },
        "current": {
            "branch": context.branch,
            "worktree": context.worktree_path.name,
            "path": str(context.worktree_path),
            "slug": context.slug,
            "slot": context.slot,
        },
        "env_file": {
            "path": str(context.env_file_path),
            "status": context.env_file_status,
        },
        "generated_env": dict(context.generated_env),
        "services": [asdict(service) for service in context.services],
        "commands": dict(context.commands),
    }


def render_agent_context(context: AgentContext, output_format: str) -> str:
    output_format = validate_agent_format(output_format)
    payload = agent_context_payload(context)
    if output_format == "json":
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    lines = [
        "Bonsai context",
        "",
        f"Workspace: {context.workspace_name}",
        f"Root: {context.workspace_root}",
        f"Config: {context.config_path}",
        f"Branch: {context.branch}",
        f"Worktree: {context.worktree_path}",
        f"Slot: {context.slot}",
        f"Env file: {context.env_file_path} ({context.env_file_status})",
        "",
        "Services:",
    ]
    for service in context.services:
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
            f"  Start current worktree: {context.commands['start']}",
            f"  Open primary URL: {context.commands['open']}",
            f"  Repair generated files: {context.commands['sync']}",
            f"  Diagnose workspace: {context.commands['doctor']}",
            "",
        ]
    )
    return "\n".join(lines)
