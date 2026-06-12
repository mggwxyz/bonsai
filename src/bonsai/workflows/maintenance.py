from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from bonsai.compose import (
    StaleComposeContainer,
    detect_compose_project,
    find_stale_compose_containers,
    remove_stopped_stale_compose_containers,
)
from bonsai.errors import BonsaiWorkspaceError
from bonsai.git import (
    is_git_worktree,
)
from bonsai.models import (
    BonsaiConfig,
    BonsaiState,
    DoctorApplyAction,
    DoctorApplyPlan,
    DoctorCheck,
    DoctorReport,
    ManagedWorktree,
    PortRepairItem,
    PortRepairPlan,
    PortRepairServiceChange,
    RepairItem,
    RepairPlan,
    SyncFileAction,
    SyncPlan,
    WorkspacePort,
)
from bonsai.process import Runner
from bonsai.rendering import (
    GENERATED_FILE_HEADER,
    render_caddy_snippets,
    render_env_local,
)
from bonsai.state import load_state, save_state
from bonsai.workflows import probes
from bonsai.workflows.caddy_ops import (
    _run_caddy_setup,
    reload_workspace_caddy,
)
from bonsai.workflows.inspection import (
    _port_owner_detail,
    _port_owner_label,
    plan_workspace_ports,
)
from bonsai.workflows.shared import (
    _branch_sort_key,
    _configured_worktree_targets,
    _safe_path_segment,
    app_snippets_dir,
    global_caddy_paths,
    load_workspace_config,
    worktreeinclude_file_copies,
)


def plan_repair(runner: Runner, workspace_root: Path) -> RepairPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    items: list[RepairItem] = []
    healthy_worktrees: dict[str, ManagedWorktree] = {}
    warning_worktrees: dict[str, ManagedWorktree] = {}
    state_changed = False

    for branch, worktree in sorted(state.worktrees.items(), key=_branch_sort_key):
        worktree_path = workspace_root / worktree.path
        if not worktree_path.exists():
            items.append(
                RepairItem(
                    branch=branch,
                    worktree_path=worktree_path,
                    action="remove",
                    reason=f"missing {worktree_path}",
                    old_slot=worktree.slot,
                    new_slot=None,
                )
            )
            state_changed = True
            continue
        if not is_git_worktree(runner, worktree_path):
            items.append(
                RepairItem(
                    branch=branch,
                    worktree_path=worktree_path,
                    action="warn",
                    reason=f"not a git worktree {worktree_path}",
                    old_slot=worktree.slot,
                    new_slot=worktree.slot,
                )
            )
            warning_worktrees[branch] = worktree
            continue
        healthy_worktrees[branch] = worktree

    repaired_worktrees: dict[str, ManagedWorktree] = dict(warning_worktrees)
    reserved_slots = {worktree.slot for worktree in warning_worktrees.values()}
    next_slot = 1
    for branch, worktree in sorted(healthy_worktrees.items(), key=_branch_sort_key):
        while next_slot in reserved_slots:
            next_slot += 1
        if worktree.slot == next_slot:
            repaired_worktrees[branch] = worktree
        else:
            items.append(
                RepairItem(
                    branch=branch,
                    worktree_path=workspace_root / worktree.path,
                    action="repack",
                    reason=f"slot {worktree.slot} -> {next_slot}",
                    old_slot=worktree.slot,
                    new_slot=next_slot,
                )
            )
            repaired_worktrees[branch] = replace(worktree, slot=next_slot)
            state_changed = True
        next_slot += 1

    return RepairPlan(
        items=tuple(items),
        updated_state=replace(state, worktrees=repaired_worktrees),
        state_changed=state_changed,
    )


def execute_repair(runner: Runner, workspace_root: Path, apply: bool = False) -> RepairPlan:
    plan = plan_repair(runner, workspace_root)
    if apply and plan.state_changed:
        save_state(workspace_root / ".bonsai" / "state.json", plan.updated_state)
    return plan


def _doctor_repair_action_label(action: str) -> str:
    if action == "remove":
        return "removed"
    if action == "repack":
        return "repacked"
    return action


def _compose_project_names(
    state: BonsaiState,
    workspace_root: Path,
) -> tuple[str, ...]:
    project_names: list[str] = []
    seen_project_names: set[str] = set()
    for target in _configured_worktree_targets(state, workspace_root):
        if not target.worktree_path.exists():
            continue
        project = detect_compose_project(target.worktree_path)
        if project is None or project.project_name in seen_project_names:
            continue
        seen_project_names.add(project.project_name)
        project_names.append(project.project_name)
    return tuple(project_names)


def _stale_compose_container_detail(
    containers: tuple[StaleComposeContainer, ...],
) -> str:
    project_counts: dict[str, int] = {}
    for container in containers:
        project_name = container.project_name or "unknown"
        project_counts[project_name] = project_counts.get(project_name, 0) + 1

    projects = ", ".join(
        f"{project}={count}" for project, count in sorted(project_counts.items())
    )
    examples = ", ".join(container.name for container in containers[:5])
    remaining = len(containers) - 5
    if remaining > 0:
        examples = f"{examples}, +{remaining} more"
    return (
        f"{len(containers)} container(s) across {len(project_counts)} project(s) "
        f"[{projects}]; examples: {examples}"
    )


def _doctor_compose_network_check(
    runner: Runner,
    project_names: tuple[str, ...],
) -> DoctorCheck | None:
    if not project_names:
        return None
    try:
        stale = find_stale_compose_containers(runner, project_names)
    except BonsaiWorkspaceError as exc:
        return DoctorCheck(
            "docker compose networks",
            "fail",
            str(exc),
            "Start Docker and rerun bonsai doctor",
            id="docker-compose-networks",
        )

    if not stale:
        return DoctorCheck(
            "docker compose networks",
            "ok",
            "No stale Docker network references",
            id="docker-compose-networks",
        )

    stopped = tuple(container for container in stale if not container.running)
    running = tuple(container for container in stale if container.running)
    detail_parts: list[str] = []
    if stopped:
        detail_parts.append("stopped: " + _stale_compose_container_detail(stopped))
    if running:
        detail_parts.append("running: " + _stale_compose_container_detail(running))
    hint = (
        "Run: bonsai doctor --apply"
        if stopped
        else "Remove or recreate the listed Docker containers manually"
    )
    return DoctorCheck(
        "docker compose networks",
        "fail",
        "Stale Docker network references; " + "; ".join(detail_parts),
        hint,
        id="docker-compose-networks",
        repair="docker-compose-networks" if stopped else None,
    )


def _run_compose_network_repairs(
    runner: Runner,
    project_names: tuple[str, ...],
) -> tuple[DoctorApplyAction, ...]:
    if not project_names:
        return ()
    try:
        stale = find_stale_compose_containers(runner, project_names)
        removed = remove_stopped_stale_compose_containers(runner, stale)
    except BonsaiWorkspaceError:
        return ()
    return tuple(
        DoctorApplyAction(
            kind="docker",
            detail=f"removed {container.name} stale Docker network reference",
        )
        for container in removed
    )


def execute_doctor_apply(runner: Runner, workspace_root: Path) -> DoctorApplyPlan:
    actions: list[DoctorApplyAction] = []
    repair_plan = execute_repair(runner, workspace_root, apply=True)
    for item in repair_plan.items:
        label = _doctor_repair_action_label(item.action)
        actions.append(
            DoctorApplyAction(
                kind="repair",
                detail=f"{label} {item.branch} - {item.reason}",
            )
        )

    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    actions.extend(
        _run_compose_network_repairs(
            runner,
            _compose_project_names(state, workspace_root),
        )
    )
    caddy_result = _run_caddy_setup(runner, config)
    actions.extend(caddy_result.actions)
    actions.extend(
        DoctorApplyAction(kind="caddy", detail=check.detail)
        for check in caddy_result.checks
    )

    sync_preview = plan_sync(workspace_root)
    if sync_preview.actions:
        sync_plan = execute_sync(runner, workspace_root, apply=True)
        actions.extend(
            DoctorApplyAction(kind="sync", detail=f"{action.kind} {action.path}")
            for action in sync_plan.actions
        )
        if sync_plan.reload_caddy:
            actions.append(DoctorApplyAction(kind="sync", detail="reload Caddy"))

    return DoctorApplyPlan(actions=tuple(actions))


def _slot_has_listening_port(config: BonsaiConfig, slot: int) -> bool:
    return any(
        probes._check_port_listening(service.base_port + slot) for service in config.services
    )






def _doctor_port_check(port: WorkspacePort, default_branch: str) -> DoctorCheck:
    if port.status == "free":
        return DoctorCheck(
            f"port {port.port}",
            "ok",
            port.service_name,
            id=f"port-{port.port}",
        )
    if port.status == "owned":
        return DoctorCheck(
            f"port {port.port}",
            "ok",
            f"owned by {', '.join(_port_owner_label(owner) for owner in port.owners)}",
            id=f"port-{port.port}",
        )
    return DoctorCheck(
        f"port {port.port}",
        "fail",
        _port_owner_detail(port),
        "Run: bonsai repair-ports",
        id=f"port-{port.port}",
        repair="repair-ports" if port.branch != default_branch else None,
    )


def _port_repair_service_changes(
    config: BonsaiConfig,
    current_slot: int,
    proposed_slot: int,
    statuses_by_port: dict[int, WorkspacePort] | None = None,
) -> tuple[PortRepairServiceChange, ...]:
    return tuple(
        _port_repair_service_change(service, current_slot, proposed_slot, statuses_by_port)
        for service in config.services
    )


def _port_repair_service_change(
    service,
    current_slot: int,
    proposed_slot: int,
    statuses_by_port: dict[int, WorkspacePort] | None,
) -> PortRepairServiceChange:
    old_port = service.base_port + current_slot
    status = statuses_by_port.get(old_port) if statuses_by_port is not None else None
    owners = (
        status.owners
        if status is not None and status.status in {"conflict", "unknown"}
        else ()
    )
    return PortRepairServiceChange(
        name=service.name,
        port_env=service.port_env,
        old_port=old_port,
        new_port=service.base_port + proposed_slot,
        owners=owners,
    )


def _next_port_repair_slot(config: BonsaiConfig, reserved_slots: set[int]) -> int:
    slot = 1
    while slot in reserved_slots or _slot_has_listening_port(config, slot):
        slot += 1
    return slot


def plan_port_repairs(workspace_root: Path, runner: Runner | None = None) -> PortRepairPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    reserved_slots = {0, *(worktree.slot for worktree in state.worktrees.values())}
    items: list[PortRepairItem] = []
    port_statuses = plan_workspace_ports(runner, workspace_root).ports if runner is not None else ()
    statuses_by_service = {
        (status.branch, status.service_name): status
        for status in port_statuses
    }
    statuses_by_port = {status.port: status for status in port_statuses}

    for branch, worktree in sorted(state.worktrees.items()):
        if runner is None:
            slot_has_conflict = _slot_has_listening_port(config, worktree.slot)
        else:
            slot_has_conflict = any(
                statuses_by_service[(branch, service.name)].status
                in {"conflict", "unknown"}
                for service in config.services
            )
        if not slot_has_conflict:
            continue
        proposed_slot = _next_port_repair_slot(config, reserved_slots)
        reserved_slots.add(proposed_slot)
        items.append(
            PortRepairItem(
                branch=branch,
                slug=worktree.slug,
                current_slot=worktree.slot,
                proposed_slot=proposed_slot,
                services=_port_repair_service_changes(
                    config,
                    current_slot=worktree.slot,
                    proposed_slot=proposed_slot,
                    statuses_by_port=statuses_by_port if runner is not None else None,
                ),
            )
        )

    return PortRepairPlan(items=tuple(items))


def execute_port_repairs(
    runner: Runner,
    workspace_root: Path,
    apply: bool = False,
) -> PortRepairPlan:
    plan = plan_port_repairs(workspace_root, runner=runner)
    if not apply or not plan.items:
        return plan

    state_path = workspace_root / ".bonsai" / "state.json"
    state = load_state(state_path)
    updated_worktrees = dict(state.worktrees)
    for item in plan.items:
        updated_worktrees[item.branch] = replace(
            updated_worktrees[item.branch],
            slot=item.proposed_slot,
        )
    save_state(state_path, replace(state, worktrees=updated_worktrees))
    execute_sync(runner, workspace_root, apply=True)
    return plan


def _desired_sync_files(
    config: BonsaiConfig,
    state: BonsaiState,
    workspace_root: Path,
) -> dict[Path, str]:
    snippets_dir = app_snippets_dir(config.name)
    desired: dict[Path, str] = {}
    for target in _configured_worktree_targets(state, workspace_root):
        desired[target.worktree_path / ".env.local"] = render_env_local(
            config,
            target.branch,
            target.worktree.slot,
            target.worktree_path,
            workspace_root=workspace_root,
            default_branch=state.default_branch,
        )
        for service_name, content in render_caddy_snippets(
            config,
            target.branch,
            target.worktree.slot,
            target.worktree_path,
            workspace_root=workspace_root,
            default_branch=state.default_branch,
        ).items():
            service_name = _safe_path_segment(service_name, "service name")
            desired[snippets_dir / f"{target.worktree.slug}-{service_name}.caddy"] = content
    return desired


def _shared_copy_actions(
    config: BonsaiConfig,
    state: BonsaiState,
    workspace_root: Path,
) -> tuple[SyncFileAction, ...]:
    default_worktree = workspace_root / state.default_worktree
    actions: list[SyncFileAction] = []
    for shared_file in config.shared_files:
        if shared_file.mode != "copy":
            continue
        source = default_worktree / _safe_path_segment(shared_file.source, "shared file source")
        target_name = _safe_path_segment(shared_file.target, "shared file target")
        for worktree in state.worktrees.values():
            target = workspace_root / worktree.path / target_name
            if target.exists() or target.is_symlink():
                continue
            actions.append(SyncFileAction(kind="copy", path=target, source=source))
    for worktree in state.worktrees.values():
        worktree_path = workspace_root / worktree.path
        for copy in worktreeinclude_file_copies(config, default_worktree, worktree_path):
            if copy.target.exists() or copy.target.is_symlink():
                continue
            actions.append(SyncFileAction(kind="copy", path=copy.target, source=copy.source))
    return tuple(actions)


def _stale_generated_snippet_actions(
    config: BonsaiConfig,
    desired_paths: set[Path],
) -> tuple[SyncFileAction, ...]:
    snippets_dir = app_snippets_dir(config.name)
    if not snippets_dir.exists():
        return ()
    actions: list[SyncFileAction] = []
    for path in sorted(snippets_dir.glob("*.caddy")):
        if not path.is_file():
            continue
        if path in desired_paths:
            continue
        if _is_bonsai_generated_file(path):
            actions.append(SyncFileAction(kind="remove", path=path))
    return tuple(actions)


def _is_bonsai_generated_file(path: Path) -> bool:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    first_line = content.splitlines()[0] if content else ""
    return first_line == GENERATED_FILE_HEADER


def _sync_actions_affect_caddy(
    config: BonsaiConfig,
    actions: list[SyncFileAction],
) -> bool:
    snippets_dir = app_snippets_dir(config.name)
    return any(
        action.path.parent == snippets_dir and action.path.suffix == ".caddy"
        for action in actions
    )


def plan_sync(workspace_root: Path) -> SyncPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    desired = _desired_sync_files(config, state, workspace_root)
    actions: list[SyncFileAction] = []
    for path, content in sorted(desired.items(), key=lambda item: str(item[0])):
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            actions.append(SyncFileAction(kind="write", path=path, content=content))
    actions.extend(_shared_copy_actions(config, state, workspace_root))
    actions.extend(_stale_generated_snippet_actions(config, set(desired)))
    return SyncPlan(
        actions=tuple(actions),
        reload_caddy=bool(config.public_services())
        or _sync_actions_affect_caddy(config, actions),
    )


def execute_sync(runner: Runner, workspace_root: Path, apply: bool = False) -> SyncPlan:
    plan = plan_sync(workspace_root)
    if not apply:
        return plan
    for action in plan.actions:
        if action.kind == "write" and action.content is not None:
            action.path.parent.mkdir(parents=True, exist_ok=True)
            action.path.write_text(action.content, encoding="utf-8")
        elif action.kind == "copy" and action.source is not None:
            action.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(action.source, action.path)
        elif action.kind == "remove":
            action.path.unlink(missing_ok=True)
    if plan.reload_caddy:
        reload_workspace_caddy(runner)
    return plan


def check_workspace_health(runner: Runner, workspace_root: Path) -> DoctorReport:
    checks: list[DoctorCheck] = []
    state_path = workspace_root / ".bonsai" / "state.json"
    if not state_path.exists():
        return DoctorReport(
            checks=(
                DoctorCheck(
                    name="workspace state",
                    status="fail",
                    detail=f"Missing {state_path}",
                    id="workspace-state",
                ),
            )
        )

    state = load_state(state_path)
    config = load_workspace_config(workspace_root, state)
    checks.append(DoctorCheck("workspace state", "ok", str(state_path), id="workspace-state"))
    checks.append(DoctorCheck("config", "ok", str(config.path), id="config"))

    git_result = runner.run(["git", "--version"], check=False)
    checks.append(
        DoctorCheck(
            "git",
            "ok" if git_result.returncode == 0 else "fail",
            git_result.stdout.strip() or "git command failed",
            id="git",
        )
    )

    for target in _configured_worktree_targets(state, workspace_root):
        if not target.worktree_path.exists():
            checks.append(
                DoctorCheck(
                    f"worktree {target.branch}",
                    "fail",
                    f"Missing {target.worktree_path}",
                    id=f"worktree-{target.worktree.slug}",
                    repair="repair",
                )
            )
            continue
        if not is_git_worktree(runner, target.worktree_path):
            checks.append(
                DoctorCheck(
                    f"worktree {target.branch}",
                    "fail",
                    f"Not a git worktree: {target.worktree_path}",
                    id=f"worktree-{target.worktree.slug}",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    f"worktree {target.branch}",
                    "ok",
                    str(target.worktree_path),
                    id=f"worktree-{target.worktree.slug}",
                )
            )

        env_path = target.worktree_path / ".env.local"
        if env_path.exists():
            checks.append(
                DoctorCheck(
                    f"env {target.branch}",
                    "ok",
                    str(env_path),
                    id=f"env-{target.worktree.slug}",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    f"env {target.branch}",
                    "fail",
                    f"Missing {env_path}",
                    "Run: bonsai sync --apply",
                    id=f"env-{target.worktree.slug}",
                    repair="sync",
                )
            )

    expected_sync = plan_sync(workspace_root)
    for action in expected_sync.actions:
        if action.kind == "write" and action.path.name.endswith(".caddy"):
            checks.append(
                DoctorCheck(
                    f"caddy snippet {action.path.name}",
                    "fail",
                    f"Missing or stale {action.path}",
                    "Run: bonsai sync --apply",
                    id=f"caddy-snippet-{action.path.stem}",
                    repair="sync",
                )
            )

    if config.public_services():
        root_caddyfile, _ = global_caddy_paths()
        checks.append(
            DoctorCheck(
                "root Caddyfile",
                "ok" if root_caddyfile.exists() else "fail",
                str(root_caddyfile),
                None if root_caddyfile.exists() else "Run: bonsai sync --apply",
                id="root-caddyfile",
                repair=None if root_caddyfile.exists() else "sync",
            )
        )
        caddy_result = runner.run(["caddy", "version"], check=False)
        checks.append(
            DoctorCheck(
                "caddy",
                "ok" if caddy_result.returncode == 0 else "fail",
                caddy_result.stdout.strip() or "caddy command failed",
                id="caddy",
                repair=None if caddy_result.returncode == 0 else "caddy",
            )
        )

    compose_check = _doctor_compose_network_check(
        runner,
        _compose_project_names(state, workspace_root),
    )
    if compose_check is not None:
        checks.append(compose_check)

    port_plan = plan_workspace_ports(runner, workspace_root)
    checks.extend(_doctor_port_check(port, state.default_branch) for port in port_plan.ports)

    return DoctorReport(checks=tuple(checks))
