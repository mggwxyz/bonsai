from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from bonsai.errors import BonsaiConfigError
from bonsai.models import AgentContext

AGENT_GUIDE_SCHEMA = "bonsai.agent-guide.v1"
AGENT_CONTEXT_SCHEMA = "bonsai.context.v1"

AGENT_RULES = (
    "Do not guess ports or localhost URLs.",
    "Run `bonsai context --format json` from inside a Bonsai worktree before choosing "
    "URLs, ports, or start commands.",
    "Use `bonsai start` instead of raw project dev commands when `[commands].start` is configured.",
    "Use `bonsai open` or the primary service URL from `bonsai context` for the current "
    "worktree URL.",
    "Run `bonsai sync --apply` when generated `.env.local` or Caddy files are missing or stale.",
    "Run `bonsai doctor` before debugging port conflicts, Caddy routing, or generated file issues.",
    "Ports are defined by `.bonsai.toml` `[[services]]` entries and rendered into each "
    "worktree's generated `.env.local`.",
)

AGENT_COMMANDS = {
    "context": "bonsai context --format json",
    "start": "bonsai start",
    "open": "bonsai open",
    "sync": "bonsai sync --apply",
    "doctor": "bonsai doctor",
    "help": "bonsai --help",
}


def validate_agent_format(output_format: str) -> str:
    normalized = output_format.lower()
    if normalized not in {"text", "json"}:
        raise BonsaiConfigError(f"Unsupported format: {output_format}")
    return normalized


def agent_guide_payload() -> dict[str, Any]:
    return {
        "schema": AGENT_GUIDE_SCHEMA,
        "rules": list(AGENT_RULES),
        "commands": dict(AGENT_COMMANDS),
    }


def render_agent_guide(output_format: str) -> str:
    output_format = validate_agent_format(output_format)
    payload = agent_guide_payload()
    if output_format == "json":
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    lines = [
        "Bonsai agent guide",
        "",
        "Rules:",
        *[f"  - {rule}" for rule in AGENT_RULES],
        "",
        "Commands:",
        *[f"  {name}: {command}" for name, command in AGENT_COMMANDS.items()],
        "",
    ]
    return "\n".join(lines)


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
