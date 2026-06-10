from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from bonsai.compose import (
    detect_compose_project,
    find_compose_published_ports,
)
from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import (
    PortOwner,
    WorkspacePort,
    WorkspacePortsPlan,
    WorktreeTarget,
)
from bonsai.ports import inspect_port_owners
from bonsai.process import Runner
from bonsai.state import load_state
from bonsai.workflows import probes
from bonsai.workflows.shared import (
    _configured_worktree_targets,
    load_workspace_config,
)


def _annotate_owner_worktree(
    owner: PortOwner,
    targets: tuple[WorktreeTarget, ...],
) -> PortOwner:
    if owner.cwd is None:
        return owner
    owner_cwd = owner.cwd.resolve()
    for target in sorted(targets, key=lambda item: len(item.worktree_path.parts), reverse=True):
        worktree_path = target.worktree_path.resolve()
        if owner_cwd == worktree_path or owner_cwd.is_relative_to(worktree_path):
            return replace(
                owner,
                worktree_branch=target.branch,
                worktree_path=target.worktree_path,
            )
    return owner


def _compose_host_ports_by_branch(
    runner: Runner,
    targets: tuple[WorktreeTarget, ...],
) -> dict[str, set[int]]:
    project_by_branch: dict[str, str] = {}
    for target in targets:
        project = detect_compose_project(target.worktree_path)
        if project is not None:
            project_by_branch[target.branch] = project.project_name
    if not project_by_branch:
        return {}

    try:
        published_ports = find_compose_published_ports(
            runner,
            tuple(project_by_branch.values()),
        )
    except BonsaiWorkspaceError:
        return {}

    ports_by_project: dict[str, set[int]] = {}
    for published in published_ports:
        ports_by_project.setdefault(published.project_name, set()).add(published.host_port)
    return {
        branch: ports_by_project.get(project_name, set())
        for branch, project_name in project_by_branch.items()
    }


def _annotate_compose_owner(
    owner: PortOwner,
    target: WorktreeTarget,
    port: int,
    compose_host_ports: dict[str, set[int]],
) -> PortOwner:
    if not owner.command.startswith("com.docker.backend"):
        return owner
    if port not in compose_host_ports.get(target.branch, set()):
        return owner
    return replace(
        owner,
        worktree_branch=target.branch,
        worktree_path=target.worktree_path,
    )


def _workspace_port_status(
    target: WorktreeTarget,
    owners: tuple[PortOwner, ...],
    port: int,
) -> str:
    if not owners:
        return "unknown" if probes._check_port_listening(port) else "free"
    if all(owner.worktree_branch == target.branch for owner in owners):
        return "owned"
    return "conflict"


def plan_workspace_ports(runner: Runner, workspace_root: Path) -> WorkspacePortsPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    targets = _configured_worktree_targets(state, workspace_root)
    compose_host_ports = _compose_host_ports_by_branch(runner, targets)
    ports: list[WorkspacePort] = []
    for target in targets:
        for service in config.services:
            port = service.base_port + target.worktree.slot
            owners = tuple(
                _annotate_compose_owner(
                    _annotate_owner_worktree(owner, targets),
                    target,
                    port,
                    compose_host_ports,
                )
                for owner in inspect_port_owners(runner, port)
            )
            ports.append(
                WorkspacePort(
                    branch=target.branch,
                    worktree_path=target.worktree_path,
                    service_name=service.name,
                    port_env=service.port_env,
                    port=port,
                    status=_workspace_port_status(target, owners, port),
                    owners=owners,
                )
            )
    return WorkspacePortsPlan(workspace_root=workspace_root, ports=tuple(ports))


def _port_owner_label(owner: PortOwner) -> str:
    label = f"{owner.command or 'process'}[{owner.pid}]"
    if owner.worktree_branch is not None:
        return f"{label} in {owner.worktree_branch}"
    if owner.cwd is not None:
        return f"{label} at {owner.cwd}"
    return label


def _port_owner_detail(port: WorkspacePort) -> str:
    if not port.owners:
        return f"{port.service_name} port is already in use by an unknown process"
    return (
        f"{port.service_name} port is already in use by "
        + ", ".join(_port_owner_label(owner) for owner in port.owners)
    )
