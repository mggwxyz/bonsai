from __future__ import annotations

from dataclasses import dataclass

from bonsai.env import parse_env_content
from bonsai.errors import BonsaiConfigError, BonsaiWorkspaceError
from bonsai.models import (
    AgentServiceContext,
    BonsaiConfig,
    WorkspaceServiceSummary,
    WorktreeSummary,
    WorktreeTarget,
)
from bonsai.rendering import render_env_local, template_values
from bonsai.templates import render_template


@dataclass(frozen=True)
class WorktreeFacts:
    summary: WorktreeSummary
    generated_env: dict[str, str]


def _env_file_status(target: WorktreeTarget, desired_env: str) -> str:
    env_file_path = target.worktree_path / ".env.local"
    if not env_file_path.exists():
        return "missing"
    try:
        current_env = env_file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BonsaiWorkspaceError(f"Unable to read generated env file at {env_file_path}") from exc
    if current_env == desired_env:
        return "current"
    return "stale"


def _service_summaries(
    config: BonsaiConfig,
    target: WorktreeTarget,
) -> tuple[WorkspaceServiceSummary, ...]:
    values = template_values(
        config,
        target.branch,
        target.worktree.slot,
        target.worktree_path,
    )
    services: list[WorkspaceServiceSummary] = []
    for service in config.services:
        url = None
        if service.url is not None:
            try:
                url = render_template(service.url, values)
            except KeyError as exc:
                key = exc.args[0]
                raise BonsaiConfigError(
                    f"Service {service.name} URL uses unknown template key: {key}"
                ) from exc
            except ValueError as exc:
                raise BonsaiConfigError(
                    f"Invalid service {service.name} URL template: {exc}"
                ) from exc
        services.append(
            WorkspaceServiceSummary(
                name=service.name,
                port_env=service.port_env,
                port=int(values[service.port_env]),
                public=service.public,
                primary=service.primary,
                url=url,
            )
        )
    return tuple(services)


def build_worktree_facts(
    config: BonsaiConfig,
    target: WorktreeTarget,
    kind: str,
) -> WorktreeFacts:
    desired_env = render_env_local(
        config,
        target.branch,
        target.worktree.slot,
        target.worktree_path,
    )
    summary = WorktreeSummary(
        branch=target.branch,
        worktree_path=target.worktree_path,
        relative_path=target.worktree.path,
        slug=target.worktree.slug,
        slot=target.worktree.slot,
        kind=kind,
        env_file_path=target.worktree_path / ".env.local",
        env_file_status=_env_file_status(target, desired_env),
        services=_service_summaries(config, target),
    )
    return WorktreeFacts(summary=summary, generated_env=parse_env_content(desired_env))


def agent_services_from_facts(facts: WorktreeFacts) -> tuple[AgentServiceContext, ...]:
    return tuple(
        AgentServiceContext(
            name=service.name,
            port_env=service.port_env,
            port=service.port,
            public=service.public,
            primary=service.primary,
            url=service.url,
        )
        for service in facts.summary.services
    )
