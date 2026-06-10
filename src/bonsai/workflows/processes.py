from __future__ import annotations

import json
import os
import shlex
import signal
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from bonsai.env import parse_env_content
from bonsai.errors import BonsaiConfigError, BonsaiWorkspaceError
from bonsai.logs import latest_command_log, next_command_log_path
from bonsai.models import (
    AppDownPlan,
    AppUpPlan,
    BonsaiConfig,
    BonsaiState,
    CommandLogPlan,
    PortOwner,
    StopProcessItem,
    StopProcessPlan,
    WorkspacePort,
    WorktreeTarget,
)
from bonsai.process import Runner
from bonsai.state import load_state
from bonsai.workflows import probes
from bonsai.workflows.inspection import (
    plan_workspace_ports,
)
from bonsai.workflows.shared import (
    _configured_worktree_targets,
    _resolve_current_worktree,
    load_workspace_config,
    resolve_start_target,
    run_lifecycle_command,
)


def _stop_targets(
    state: BonsaiState,
    workspace_root: Path,
    current_path: Path,
    name: str | None,
    all_worktrees: bool,
) -> tuple[WorktreeTarget, ...]:
    if all_worktrees and name is not None:
        raise BonsaiWorkspaceError("Use either a worktree name or --all, not both")
    if all_worktrees:
        return _configured_worktree_targets(state, workspace_root)
    if name is not None:
        return (resolve_start_target(workspace_root, name, current_path),)

    branch, worktree, worktree_path = _resolve_current_worktree(
        state,
        workspace_root,
        current_path,
    )
    return (WorktreeTarget(branch=branch, worktree=worktree, worktree_path=worktree_path),)


def _stop_item_for_owner(
    port: WorkspacePort,
    owner: PortOwner,
    *,
    force: bool,
) -> StopProcessItem:
    if force or owner.worktree_branch == port.branch:
        return StopProcessItem(
            action="stop",
            branch=port.branch,
            worktree_path=port.worktree_path,
            service_name=port.service_name,
            port_env=port.port_env,
            port=port.port,
            owner=owner,
            reason="selected worktree owner" if not force else "forced",
        )
    return StopProcessItem(
        action="skip",
        branch=port.branch,
        worktree_path=port.worktree_path,
        service_name=port.service_name,
        port_env=port.port_env,
        port=port.port,
        owner=owner,
        reason="owner is outside selected worktree; use --force to stop it",
    )


def plan_stop_processes(
    runner: Runner,
    workspace_root: Path,
    current_path: Path,
    name: str | None = None,
    all_worktrees: bool = False,
    force: bool = False,
) -> StopProcessPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    target_branches = {
        target.branch
        for target in _stop_targets(state, workspace_root, current_path, name, all_worktrees)
    }
    seen_pids: set[int] = set()
    items: list[StopProcessItem] = []
    for port in plan_workspace_ports(runner, workspace_root).ports:
        if port.branch not in target_branches:
            continue
        for owner in port.owners:
            if owner.pid in seen_pids:
                continue
            seen_pids.add(owner.pid)
            items.append(_stop_item_for_owner(port, owner, force=force))
    return StopProcessPlan(items=tuple(items))


def execute_stop_processes(
    runner: Runner,
    workspace_root: Path,
    current_path: Path,
    name: str | None = None,
    all_worktrees: bool = False,
    force: bool = False,
    terminate_timeout: float = 5.0,
) -> StopProcessPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    apps: list[AppDownPlan] = []
    stopped_pids: set[int] = set()
    for target in _stop_targets(state, workspace_root, current_path, name, all_worktrees):
        app = _stop_tracked_app(workspace_root, target, terminate_timeout)
        if app.action == "not-running":
            continue
        apps.append(app)
        if app.pid is not None:
            stopped_pids.add(app.pid)

    plan = plan_stop_processes(
        runner,
        workspace_root,
        current_path=current_path,
        name=name,
        all_worktrees=all_worktrees,
        force=force,
    )
    applied: list[StopProcessItem] = []
    for item in plan.items:
        if item.owner.pid in stopped_pids:
            continue
        if item.action != "stop":
            applied.append(item)
            continue
        reason = "terminated"
        try:
            os.kill(item.owner.pid, signal.SIGTERM)
        except ProcessLookupError:
            reason = "process already exited"
        applied.append(replace(item, action="stopped", reason=reason))
    return StopProcessPlan(items=tuple(applied), apps=tuple(apps))


def _app_process_record_path(workspace_root: Path, worktree_slug: str) -> Path:
    return workspace_root / ".bonsai" / "pids" / f"{worktree_slug}.json"


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_app_process_record(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _record_pid(record: dict[str, object]) -> int | None:
    try:
        return int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return None


def _record_log_path(record: dict[str, object]) -> Path | None:
    value = record.get("log_path")
    if not isinstance(value, str) or not value:
        return None
    return Path(value)


def _write_app_process_record(
    path: Path,
    *,
    branch: str,
    worktree_path: Path,
    pid: int,
    argv: list[str],
    log_path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "branch": branch,
                "worktree_path": str(worktree_path),
                "pid": pid,
                "command": argv,
                "log_path": str(log_path),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _remove_process_record(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _terminate_process_id(pid: int, timeout: float) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if timeout <= 0:
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
            return
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    if _process_is_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _start_environment(target: WorktreeTarget) -> Mapping[str, str]:
    env_path = target.worktree_path / ".env.local"
    if not env_path.exists():
        raise BonsaiWorkspaceError(
            f"Missing generated env file at {env_path}. Run: bonsai sync --apply"
        )
    return parse_env_content(env_path.read_text(encoding="utf-8"))


def _readiness_ports(config: BonsaiConfig, target: WorktreeTarget) -> tuple[int, ...]:
    try:
        service = config.primary_service()
    except ValueError:
        return ()
    return (service.base_port + target.worktree.slot,)


def _wait_for_ready(ports: tuple[int, ...], timeout: float) -> tuple[int, ...]:
    if not ports:
        return ()

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        ready = tuple(port for port in ports if probes._check_port_listening(port))
        if len(ready) == len(ports):
            return ready
        if time.monotonic() >= deadline:
            return ready
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def execute_start(
    runner: Runner,
    workspace_root: Path,
    name: str | None,
    current_path: Path,
) -> int:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    if config.commands.start is None:
        raise BonsaiConfigError("Missing config key commands.start")

    target = resolve_start_target(workspace_root, name, current_path)
    env = _start_environment(target)
    if config.commands.prestart:
        run_lifecycle_command(
            runner,
            workspace_root=workspace_root,
            worktree_slug=target.worktree.slug,
            kind="prestart",
            command=config.commands.prestart,
            cwd=target.worktree_path,
            env=env,
        )
    exit_code = run_lifecycle_command(
        runner,
        workspace_root=workspace_root,
        worktree_slug=target.worktree.slug,
        kind="start",
        command=config.commands.start,
        cwd=target.worktree_path,
        env=env,
        check=False,
    )
    if config.commands.poststart:
        post_exit_code = run_lifecycle_command(
            runner,
            workspace_root=workspace_root,
            worktree_slug=target.worktree.slug,
            kind="poststart",
            command=config.commands.poststart,
            cwd=target.worktree_path,
            env=env,
            check=False,
        )
        if exit_code == 0:
            return post_exit_code
    return exit_code


def execute_up(
    runner: Runner,
    workspace_root: Path,
    name: str | None,
    current_path: Path,
    readiness_timeout: float = 30.0,
) -> AppUpPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    if config.commands.start is None:
        raise BonsaiConfigError("Missing config key commands.start")

    target = resolve_start_target(workspace_root, name, current_path)
    env = _start_environment(target)
    record_path = _app_process_record_path(workspace_root, target.worktree.slug)
    stale_pid: int | None = None
    record = _read_app_process_record(record_path)
    if record is not None:
        existing_pid = _record_pid(record)
        if existing_pid is not None and _process_is_alive(existing_pid):
            raise BonsaiWorkspaceError(
                f"{target.branch} is already running with pid {existing_pid}. Run: bonsai stop"
            )
        stale_pid = existing_pid
        _remove_process_record(record_path)

    if config.commands.prestart:
        run_lifecycle_command(
            runner,
            workspace_root=workspace_root,
            worktree_slug=target.worktree.slug,
            kind="prestart",
            command=config.commands.prestart,
            cwd=target.worktree_path,
            env=env,
        )

    argv = shlex.split(config.commands.start)
    log_path = next_command_log_path(workspace_root, target.worktree.slug, "start")
    pid = runner.run_detached_logged(
        argv,
        cwd=target.worktree_path,
        env=env,
        log_path=log_path,
        label="start",
    )
    _write_app_process_record(
        record_path,
        branch=target.branch,
        worktree_path=target.worktree_path,
        pid=pid,
        argv=argv,
        log_path=log_path,
    )

    expected_ports = _readiness_ports(config, target)
    ready_ports = _wait_for_ready(expected_ports, readiness_timeout)
    if expected_ports and ready_ports != expected_ports:
        _terminate_process_id(pid, timeout=0.0)
        _remove_process_record(record_path)
        expected_text = ", ".join(str(port) for port in expected_ports)
        raise BonsaiWorkspaceError(
            f"{target.branch} did not become ready on port(s): {expected_text}. Log: {log_path}"
        )

    if config.commands.poststart:
        run_lifecycle_command(
            runner,
            workspace_root=workspace_root,
            worktree_slug=target.worktree.slug,
            kind="poststart",
            command=config.commands.poststart,
            cwd=target.worktree_path,
            env=env,
        )

    return AppUpPlan(
        branch=target.branch,
        worktree_path=target.worktree_path,
        pid=pid,
        log_path=log_path,
        ready_ports=ready_ports,
        stale_pid=stale_pid,
    )


def _stop_tracked_app(
    workspace_root: Path,
    target: WorktreeTarget,
    terminate_timeout: float = 5.0,
) -> AppDownPlan:
    record_path = _app_process_record_path(workspace_root, target.worktree.slug)
    record = _read_app_process_record(record_path)
    if record is None:
        return AppDownPlan(
            branch=target.branch,
            worktree_path=target.worktree_path,
            pid=None,
            action="not-running",
        )

    pid = _record_pid(record)
    log_path = _record_log_path(record)
    if pid is None or not _process_is_alive(pid):
        _remove_process_record(record_path)
        return AppDownPlan(
            branch=target.branch,
            worktree_path=target.worktree_path,
            pid=pid,
            action="stale",
            log_path=log_path,
        )

    _terminate_process_id(pid, timeout=terminate_timeout)
    _remove_process_record(record_path)
    return AppDownPlan(
        branch=target.branch,
        worktree_path=target.worktree_path,
        pid=pid,
        action="stopped",
        log_path=log_path,
    )


def plan_command_log(
    workspace_root: Path,
    name: str | None,
    current_path: Path,
    kind: str | None,
) -> CommandLogPlan:
    target = resolve_start_target(workspace_root, name, current_path)
    log_path = latest_command_log(workspace_root, target.worktree.slug, kind)
    return CommandLogPlan(
        branch=target.branch,
        worktree_path=target.worktree_path,
        log_path=log_path,
        content=log_path.read_text(encoding="utf-8"),
    )
