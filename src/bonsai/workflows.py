from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from bonsai.caddy import caddy_reload_plan
from bonsai.compose import detect_compose_project, teardown_compose_project
from bonsai.config import load_config
from bonsai.env import parse_env_content
from bonsai.errors import BonsaiCommandError, BonsaiConfigError, BonsaiWorkspaceError
from bonsai.git import (
    add_existing_worktree,
    add_new_worktree,
    clone_default_branch,
    current_branch,
    discover_default_branch,
    fetch_origin,
    is_git_worktree,
    remote_branch_exists,
    worktree_has_changes,
)
from bonsai.git import (
    remove_worktree as git_remove_worktree,
)
from bonsai.logs import LogKind, latest_command_log, next_command_log_path
from bonsai.models import (
    AddFilesPlan,
    AgentContext,
    AgentServiceContext,
    BonsaiConfig,
    BonsaiState,
    CheckoutWorktreePlan,
    CleanupItem,
    CleanupPlan,
    CloneWorkspacePlan,
    CommandLogPlan,
    CommandSpec,
    DoctorCheck,
    DoctorReport,
    FileSymlink,
    FileWrite,
    ManagedWorktree,
    OpenUrlPlan,
    RemoveWorktreePlan,
    RepairItem,
    RepairPlan,
    ResolvedWorktree,
    SyncFileAction,
    SyncPlan,
    WorkspaceServiceSummary,
    WorkspaceStatus,
    WorkspaceSummary,
    WorktreeSummary,
    WorktreeTarget,
)
from bonsai.ports import allocate_slot
from bonsai.process import Runner, format_command
from bonsai.rendering import (
    GENERATED_FILE_HEADER,
    render_caddy_snippets,
    render_env_local,
    render_root_caddyfile,
    template_values,
)
from bonsai.slug import branch_slug
from bonsai.state import load_state, remove_worktree, save_state, update_worktree
from bonsai.templates import render_template

ConfigInitializer = Callable[[Path, str, str, Path], None]


@dataclass(frozen=True)
class _PullRequestInfo:
    state: str
    merged_at: str | None
    url: str | None


def workspace_config_path(workspace_root: Path) -> Path:
    return workspace_root / ".bonsai.toml"


def repo_config_path(workspace_root: Path, default_worktree: str) -> Path:
    return workspace_root / default_worktree / ".bonsai.toml"


def resolve_workspace_config_path(workspace_root: Path, default_worktree: str) -> Path:
    root_config = workspace_config_path(workspace_root)
    if root_config.exists():
        return root_config

    fallback_config = repo_config_path(workspace_root, default_worktree)
    if fallback_config.exists():
        return fallback_config

    raise BonsaiConfigError(
        f"Missing .bonsai.toml at {root_config} or {fallback_config}"
    )


def load_workspace_config(workspace_root: Path, state: BonsaiState) -> BonsaiConfig:
    return load_config(resolve_workspace_config_path(workspace_root, state.default_worktree))


def _safe_path_segment(value: str, label: str) -> str:
    path = Path(value)
    if (
        value == ""
        or value in {".", ".."}
        or path.is_absolute()
        or len(path.parts) != 1
        or "/" in value
        or "\\" in value
    ):
        raise BonsaiWorkspaceError(f"Invalid {label}: {value!r}")
    return value


def _check_port_listening(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def plan_clone_workspace(
    git_url: str,
    name: str,
    default_branch: str,
    config: BonsaiConfig,
    parent: Path,
) -> CloneWorkspacePlan:
    name = _safe_path_segment(name, "workspace name")
    root_caddyfile = _safe_path_segment(config.caddy.root_caddyfile, "caddy root_caddyfile")
    snippets_dir_name = _safe_path_segment(config.caddy.snippets_dir, "caddy snippets_dir")
    workspace_root = parent / name
    default_worktree = workspace_root / default_branch
    snippets_dir = workspace_root / snippets_dir_name
    state = BonsaiState(
        version=1,
        name=name,
        default_branch=default_branch,
        default_worktree=default_branch,
        repo_url=git_url,
        worktrees={},
    )
    files = (
        FileWrite(
            path=workspace_root / root_caddyfile,
            content=render_root_caddyfile(snippets_dir),
        ),
        *generated_worktree_files(
            config,
            branch=default_branch,
            slot=0,
            workspace_root=workspace_root,
            worktree_path=default_worktree,
        ),
    )
    return CloneWorkspacePlan(
        workspace_root=workspace_root,
        default_worktree=default_worktree,
        state=state,
        files=files,
    )


def generated_worktree_files(
    config: BonsaiConfig,
    branch: str,
    slot: int,
    workspace_root: Path,
    worktree_path: Path,
) -> tuple[FileWrite, ...]:
    snippets_dir_name = _safe_path_segment(config.caddy.snippets_dir, "caddy snippets_dir")
    slug = branch_slug(branch)
    if slug == "":
        raise BonsaiWorkspaceError(f"Invalid branch slug: {branch!r}")
    snippets_dir = workspace_root / snippets_dir_name
    files = [
        FileWrite(
            path=worktree_path / ".env.local",
            content=render_env_local(config, branch, slot, worktree_path),
        )
    ]
    for service_name, content in render_caddy_snippets(config, branch, slot, worktree_path).items():
        service_name = _safe_path_segment(service_name, "service name")
        files.append(FileWrite(path=snippets_dir / f"{slug}-{service_name}.caddy", content=content))
    return tuple(files)


def plan_add_files(
    config: BonsaiConfig,
    state: BonsaiState,
    workspace_root: Path,
    branch: str,
) -> AddFilesPlan:
    slug = branch_slug(branch)
    if slug == "":
        raise BonsaiWorkspaceError(f"Invalid branch slug: {branch!r}")
    existing_worktree = state.worktrees.get(branch)
    if existing_worktree is None:
        slot = allocate_slot(state.worktrees)
    else:
        if existing_worktree.path != slug or existing_worktree.slug != slug:
            raise BonsaiWorkspaceError(f"Branch worktree path conflicts with slug: {branch!r}")
        slot = existing_worktree.slot
    worktree_path = workspace_root / slug
    default_worktree_path = workspace_root / state.default_worktree
    files = list(generated_worktree_files(config, branch, slot, workspace_root, worktree_path))
    symlinks: list[FileSymlink] = []
    for shared_file in config.shared_files:
        source = _safe_path_segment(shared_file.source, "shared file source")
        target = _safe_path_segment(shared_file.target, "shared file target")
        symlinks.append(
            FileSymlink(
                source=default_worktree_path / source,
                target=worktree_path / target,
            )
        )

    updated_state = update_worktree(
        state,
        branch,
        ManagedWorktree(path=slug, slug=slug, slot=slot),
    )
    return AddFilesPlan(
        branch=branch,
        worktree_path=worktree_path,
        slot=slot,
        files=tuple(files),
        symlinks=tuple(symlinks),
        updated_state=updated_state,
    )


def resolve_managed_worktree(state: BonsaiState, name: str) -> ResolvedWorktree | None:
    worktree = state.worktrees.get(name)
    if worktree is not None:
        return ResolvedWorktree(branch=name, worktree=worktree)
    for branch, candidate in state.worktrees.items():
        if name in {candidate.path, candidate.slug}:
            return ResolvedWorktree(branch=branch, worktree=candidate)
    return None


def _remove_generated_snippets(
    workspace_root: Path,
    config: BonsaiConfig,
    slug: str,
) -> tuple[Path, ...]:
    snippets_dir_name = _safe_path_segment(config.caddy.snippets_dir, "caddy snippets_dir")
    snippets_dir = workspace_root / snippets_dir_name
    removed: list[Path] = []
    if not snippets_dir.exists():
        return ()
    for path in sorted(snippets_dir.glob(f"{slug}-*.caddy")):
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed.append(path)
    return tuple(removed)


def _github_cli_error(message: str) -> BonsaiWorkspaceError:
    return BonsaiWorkspaceError(f"{message}. Install gh if needed, then run: gh auth login")


def _require_github_cli(runner: Runner, repo: Path) -> None:
    try:
        version = runner.run(["gh", "--version"], check=False)
    except FileNotFoundError as exc:
        raise _github_cli_error("GitHub CLI is required for cleanup") from exc
    if version.returncode != 0:
        raise _github_cli_error("GitHub CLI is required for cleanup")

    try:
        auth = runner.run(["gh", "auth", "status"], cwd=repo, check=False)
    except FileNotFoundError as exc:
        raise _github_cli_error("GitHub CLI is required for cleanup") from exc
    if auth.returncode != 0:
        raise BonsaiWorkspaceError("GitHub CLI is not authenticated. Run: gh auth login")


def _github_prs_for_branch(runner: Runner, repo: Path, branch: str) -> tuple[_PullRequestInfo, ...]:
    result = runner.run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "all",
            "--json",
            "state,mergedAt,url",
            "--limit",
            "20",
        ],
        cwd=repo,
    )
    try:
        raw_items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise BonsaiWorkspaceError(f"Unable to parse GitHub PR data for branch: {branch}") from exc
    if not isinstance(raw_items, list):
        raise BonsaiWorkspaceError(f"Unable to parse GitHub PR data for branch: {branch}")

    pull_requests: list[_PullRequestInfo] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise BonsaiWorkspaceError(f"Unable to parse GitHub PR data for branch: {branch}")
        state = raw_item.get("state")
        merged_at = raw_item.get("mergedAt")
        url = raw_item.get("url")
        pull_requests.append(
            _PullRequestInfo(
                state=str(state or "").lower(),
                merged_at=str(merged_at) if merged_at else None,
                url=str(url) if url else None,
            )
        )
    return tuple(pull_requests)


def _pr_cleanup_decision(
    runner: Runner,
    default_worktree: Path,
    branch: str,
    worktree: ManagedWorktree,
    workspace_root: Path,
) -> CleanupItem:
    worktree_path = workspace_root / worktree.path
    pull_requests = _github_prs_for_branch(runner, default_worktree, branch)
    if not pull_requests:
        return CleanupItem(branch, worktree_path, "skip", "no pull request found")

    open_pr = next(
        (pull_request for pull_request in pull_requests if pull_request.state == "open"),
        None,
    )
    if open_pr is not None:
        return CleanupItem(branch, worktree_path, "skip", "pull request is open", open_pr.url)

    merged_pr = next(
        (
            pull_request
            for pull_request in pull_requests
            if pull_request.merged_at is not None or pull_request.state == "merged"
        ),
        None,
    )
    if merged_pr is None:
        pr = pull_requests[0]
        reason = (
            "pull request is closed but not merged"
            if pr.state == "closed"
            else "pull request is not merged"
        )
        return CleanupItem(branch, worktree_path, "skip", reason, pr.url)

    return CleanupItem(branch, worktree_path, "remove", "pull request is merged", merged_pr.url)


def _branch_sort_key(item: tuple[str, ManagedWorktree]) -> tuple[str, str]:
    branch = item[0]
    return (branch.lower(), branch)


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


def _resolve_current_worktree(
    state: BonsaiState,
    workspace_root: Path,
    current_path: Path,
) -> tuple[str, ManagedWorktree, Path]:
    current_path = current_path.resolve()
    default_worktree = ManagedWorktree(
        path=state.default_worktree,
        slug=branch_slug(state.default_branch),
        slot=0,
    )
    candidates = [(state.default_branch, default_worktree), *state.worktrees.items()]
    resolved_candidates = [
        ((workspace_root / worktree.path).resolve(), branch, worktree)
        for branch, worktree in candidates
    ]

    for worktree_path, branch, worktree in sorted(
        resolved_candidates,
        key=lambda candidate: len(candidate[0].parts),
        reverse=True,
    ):
        if current_path == worktree_path or current_path.is_relative_to(worktree_path):
            return branch, worktree, worktree_path

    raise BonsaiWorkspaceError(f"Current directory is not inside a Bonsai worktree: {current_path}")


def _configured_worktree_targets(
    state: BonsaiState,
    workspace_root: Path,
) -> tuple[WorktreeTarget, ...]:
    default = WorktreeTarget(
        branch=state.default_branch,
        worktree=ManagedWorktree(
            path=state.default_worktree,
            slug=branch_slug(state.default_branch),
            slot=0,
        ),
        worktree_path=workspace_root / state.default_worktree,
    )
    managed = tuple(
        WorktreeTarget(
            branch=branch,
            worktree=worktree,
            worktree_path=workspace_root / worktree.path,
        )
        for branch, worktree in state.worktrees.items()
    )
    return (default, *managed)


def _desired_sync_files(
    config: BonsaiConfig,
    state: BonsaiState,
    workspace_root: Path,
) -> dict[Path, str]:
    snippets_dir_name = _safe_path_segment(config.caddy.snippets_dir, "caddy snippets_dir")
    root_caddyfile = _safe_path_segment(config.caddy.root_caddyfile, "caddy root_caddyfile")
    snippets_dir = workspace_root / snippets_dir_name
    desired: dict[Path, str] = {
        workspace_root / root_caddyfile: render_root_caddyfile(snippets_dir),
    }
    for target in _configured_worktree_targets(state, workspace_root):
        desired[target.worktree_path / ".env.local"] = render_env_local(
            config,
            target.branch,
            target.worktree.slot,
            target.worktree_path,
        )
        for service_name, content in render_caddy_snippets(
            config,
            target.branch,
            target.worktree.slot,
            target.worktree_path,
        ).items():
            service_name = _safe_path_segment(service_name, "service name")
            desired[snippets_dir / f"{target.worktree.slug}-{service_name}.caddy"] = content
    return desired


def _stale_generated_snippet_actions(
    config: BonsaiConfig,
    workspace_root: Path,
    desired_paths: set[Path],
) -> tuple[SyncFileAction, ...]:
    snippets_dir_name = _safe_path_segment(config.caddy.snippets_dir, "caddy snippets_dir")
    snippets_dir = workspace_root / snippets_dir_name
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
    workspace_root: Path,
    actions: list[SyncFileAction],
) -> bool:
    root_caddyfile = workspace_root / _safe_path_segment(
        config.caddy.root_caddyfile,
        "caddy root_caddyfile",
    )
    snippets_dir = workspace_root / _safe_path_segment(
        config.caddy.snippets_dir,
        "caddy snippets_dir",
    )
    return any(
        action.path == root_caddyfile
        or (action.path.parent == snippets_dir and action.path.suffix == ".caddy")
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
    actions.extend(
        _stale_generated_snippet_actions(
            config,
            workspace_root,
            set(desired),
        )
    )
    return SyncPlan(
        actions=tuple(actions),
        reload_caddy=bool(config.public_services())
        or _sync_actions_affect_caddy(config, workspace_root, actions),
    )


def execute_sync(runner: Runner, workspace_root: Path, apply: bool = False) -> SyncPlan:
    plan = plan_sync(workspace_root)
    if not apply:
        return plan
    for action in plan.actions:
        if action.kind == "write" and action.content is not None:
            action.path.parent.mkdir(parents=True, exist_ok=True)
            action.path.write_text(action.content, encoding="utf-8")
        elif action.kind == "remove":
            action.path.unlink(missing_ok=True)
    if plan.reload_caddy:
        state = load_state(workspace_root / ".bonsai" / "state.json")
        config = load_workspace_config(workspace_root, state)
        reload_workspace_caddy(runner, config, workspace_root)
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
                ),
            )
        )

    state = load_state(state_path)
    config = load_workspace_config(workspace_root, state)
    checks.append(DoctorCheck("workspace state", "ok", str(state_path)))
    checks.append(DoctorCheck("config", "ok", str(config.path)))

    git_result = runner.run(["git", "--version"], check=False)
    checks.append(
        DoctorCheck(
            "git",
            "ok" if git_result.returncode == 0 else "fail",
            git_result.stdout.strip() or "git command failed",
        )
    )

    for target in _configured_worktree_targets(state, workspace_root):
        if not target.worktree_path.exists():
            checks.append(
                DoctorCheck(
                    f"worktree {target.branch}",
                    "fail",
                    f"Missing {target.worktree_path}",
                )
            )
            continue
        if not is_git_worktree(runner, target.worktree_path):
            checks.append(
                DoctorCheck(
                    f"worktree {target.branch}",
                    "fail",
                    f"Not a git worktree: {target.worktree_path}",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    f"worktree {target.branch}",
                    "ok",
                    str(target.worktree_path),
                )
            )

        env_path = target.worktree_path / ".env.local"
        if env_path.exists():
            checks.append(DoctorCheck(f"env {target.branch}", "ok", str(env_path)))
        else:
            checks.append(
                DoctorCheck(
                    f"env {target.branch}",
                    "fail",
                    f"Missing {env_path}",
                    "Run: bonsai sync --apply",
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
                )
            )

    if config.public_services():
        root_caddyfile = workspace_root / _safe_path_segment(
            config.caddy.root_caddyfile,
            "caddy root_caddyfile",
        )
        checks.append(
            DoctorCheck(
                "root Caddyfile",
                "ok" if root_caddyfile.exists() else "fail",
                str(root_caddyfile),
                None if root_caddyfile.exists() else "Run: bonsai sync --apply",
            )
        )
        caddy_result = runner.run(["caddy", "version"], check=False)
        checks.append(
            DoctorCheck(
                "caddy",
                "ok" if caddy_result.returncode == 0 else "fail",
                caddy_result.stdout.strip() or "caddy command failed",
            )
        )

    for target in _configured_worktree_targets(state, workspace_root):
        for service in config.services:
            port = service.base_port + target.worktree.slot
            if _check_port_listening(port):
                checks.append(
                    DoctorCheck(
                        f"port {port}",
                        "fail",
                        f"{service.name} port is already in use",
                    )
                )
            else:
                checks.append(DoctorCheck(f"port {port}", "ok", service.name))

    return DoctorReport(checks=tuple(checks))


def resolve_start_target(
    workspace_root: Path,
    name: str | None,
    current_path: Path,
) -> WorktreeTarget:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    if name is None:
        branch, worktree, worktree_path = _resolve_current_worktree(
            state,
            workspace_root,
            current_path,
        )
        return WorktreeTarget(branch=branch, worktree=worktree, worktree_path=worktree_path)

    for target in _configured_worktree_targets(state, workspace_root):
        if name in {target.branch, target.worktree.path, target.worktree.slug}:
            return target

    raise BonsaiWorkspaceError(f"Unknown Bonsai worktree: {name}")


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
    env_path = target.worktree_path / ".env.local"
    if not env_path.exists():
        raise BonsaiWorkspaceError(
            f"Missing generated env file at {env_path}. Run: bonsai sync --apply"
        )
    env = parse_env_content(env_path.read_text(encoding="utf-8"))
    return run_lifecycle_command(
        runner,
        workspace_root=workspace_root,
        worktree_slug=target.worktree.slug,
        kind="start",
        command=config.commands.start,
        cwd=target.worktree_path,
        env=env,
        check=False,
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


def _plan_primary_open_url(
    config: BonsaiConfig,
    branch: str,
    worktree: ManagedWorktree,
    worktree_path: Path,
) -> OpenUrlPlan:
    try:
        service = config.primary_service()
    except ValueError as exc:
        raise BonsaiConfigError("No primary public service configured") from exc
    if service.url is None:
        raise BonsaiConfigError("Primary public service does not have a URL")

    values = template_values(config, branch, worktree.slot, worktree_path)
    try:
        url = render_template(service.url, values)
    except KeyError as exc:
        key = exc.args[0]
        raise BonsaiConfigError(f"Primary URL uses unknown template key: {key}") from exc
    except ValueError as exc:
        raise BonsaiConfigError(f"Invalid primary URL template: {exc}") from exc

    return OpenUrlPlan(branch=branch, worktree_path=worktree_path, url=url)


def plan_open_url(workspace_root: Path, current_path: Path) -> OpenUrlPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    branch, worktree, worktree_path = _resolve_current_worktree(state, workspace_root, current_path)
    return _plan_primary_open_url(config, branch, worktree, worktree_path)


def plan_open_url_for_worktree(workspace_root: Path, name: str) -> OpenUrlPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    target = resolve_start_target(workspace_root, name, workspace_root)
    return _plan_primary_open_url(
        config,
        target.branch,
        target.worktree,
        target.worktree_path,
    )


def _workspace_summary_commands() -> dict[str, str]:
    return {
        "context": "bonsai context --format json",
        "status": "bonsai status",
        "list": "bonsai list",
        "start": "bonsai start",
        "open": "bonsai open",
        "sync": "bonsai sync --apply",
        "doctor": "bonsai doctor",
    }


def _env_file_status(config: BonsaiConfig, target: WorktreeTarget) -> str:
    desired_env = render_env_local(
        config,
        target.branch,
        target.worktree.slot,
        target.worktree_path,
    )
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
    branch: str,
    slot: int,
    worktree_path: Path,
) -> tuple[WorkspaceServiceSummary, ...]:
    values = template_values(config, branch, slot, worktree_path)
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


def _worktree_summary(
    config: BonsaiConfig,
    branch: str,
    worktree: ManagedWorktree,
    worktree_path: Path,
    kind: str,
) -> WorktreeSummary:
    target = WorktreeTarget(branch=branch, worktree=worktree, worktree_path=worktree_path)
    return WorktreeSummary(
        branch=branch,
        worktree_path=worktree_path,
        relative_path=worktree.path,
        slug=worktree.slug,
        slot=worktree.slot,
        kind=kind,
        env_file_path=worktree_path / ".env.local",
        env_file_status=_env_file_status(config, target),
        services=_service_summaries(config, branch, worktree.slot, worktree_path),
    )


def plan_workspace_summary(workspace_root: Path) -> WorkspaceSummary:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    config_path = config.path or resolve_workspace_config_path(
        workspace_root,
        state.default_worktree,
    )
    targets = _configured_worktree_targets(state, workspace_root)
    default_target = targets[0]
    managed_targets = sorted(targets[1:], key=lambda target: target.branch.lower())
    worktrees = [
        _worktree_summary(
            config,
            default_target.branch,
            default_target.worktree,
            default_target.worktree_path,
            "default",
        )
    ]
    worktrees.extend(
        _worktree_summary(
            config,
            target.branch,
            target.worktree,
            target.worktree_path,
            "managed",
        )
        for target in managed_targets
    )
    return WorkspaceSummary(
        workspace_name=state.name,
        workspace_root=workspace_root,
        default_branch=state.default_branch,
        default_worktree=state.default_worktree,
        config_path=config_path,
        worktrees=tuple(worktrees),
        commands=_workspace_summary_commands(),
    )


def plan_current_worktree_status(
    workspace_root: Path,
    current_path: Path,
) -> WorkspaceStatus:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    config_path = config.path or resolve_workspace_config_path(
        workspace_root,
        state.default_worktree,
    )
    branch, worktree, worktree_path = _resolve_current_worktree(
        state,
        workspace_root,
        current_path,
    )
    kind = "default" if branch == state.default_branch else "managed"
    return WorkspaceStatus(
        workspace_name=state.name,
        workspace_root=workspace_root,
        default_branch=state.default_branch,
        default_worktree=state.default_worktree,
        config_path=config_path,
        current=_worktree_summary(config, branch, worktree, worktree_path, kind),
        commands=_workspace_summary_commands(),
    )


def plan_agent_context(workspace_root: Path, current_path: Path) -> AgentContext:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    branch, worktree, worktree_path = _resolve_current_worktree(state, workspace_root, current_path)
    desired_env = render_env_local(config, branch, worktree.slot, worktree_path)
    env_file_path = worktree_path / ".env.local"
    if not env_file_path.exists():
        env_file_status = "missing"
    elif env_file_path.read_text(encoding="utf-8") == desired_env:
        env_file_status = "current"
    else:
        env_file_status = "stale"

    values = template_values(config, branch, worktree.slot, worktree_path)
    services: list[AgentServiceContext] = []
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
            AgentServiceContext(
                name=service.name,
                port_env=service.port_env,
                port=int(values[service.port_env]),
                public=service.public,
                primary=service.primary,
                url=url,
            )
        )

    return AgentContext(
        workspace_name=state.name,
        workspace_root=workspace_root,
        default_branch=state.default_branch,
        default_worktree=state.default_worktree,
        config_path=config.path
        or resolve_workspace_config_path(workspace_root, state.default_worktree),
        branch=branch,
        worktree_path=worktree_path,
        slug=worktree.slug,
        slot=worktree.slot,
        env_file_path=env_file_path,
        env_file_status=env_file_status,
        generated_env=parse_env_content(desired_env),
        services=tuple(services),
        commands={
            "context": "bonsai context --format json",
            "start": "bonsai start",
            "open": "bonsai open",
            "sync": "bonsai sync --apply",
            "doctor": "bonsai doctor",
        },
    )


def write_files(files: tuple[FileWrite, ...]) -> None:
    for file in files:
        file.path.parent.mkdir(parents=True, exist_ok=True)
        file.path.write_text(file.content, encoding="utf-8")


def apply_symlinks(symlinks: tuple[FileSymlink, ...]) -> None:
    for symlink in symlinks:
        if not symlink.source.exists():
            raise BonsaiWorkspaceError(f"Shared file source does not exist: {symlink.source}")
        if symlink.target.is_symlink():
            if symlink.target.resolve() == symlink.source.resolve():
                continue
            raise BonsaiWorkspaceError(
                f"Shared file target already exists with a different symlink: {symlink.target}"
            )
        if symlink.target.exists():
            raise BonsaiWorkspaceError(f"Shared file target already exists: {symlink.target}")
        symlink.target.parent.mkdir(parents=True, exist_ok=True)
        symlink.target.symlink_to(symlink.source)


def command_summary(command: CommandSpec) -> str:
    return format_command(command.argv, cwd=command.cwd)


def run_command_specs(runner: Runner, commands: list[CommandSpec]) -> None:
    for command in commands:
        runner.run(list(command.argv), cwd=command.cwd, env=dict(command.env))


def generated_worktree_env(files: tuple[FileWrite, ...]) -> dict[str, str]:
    for file in files:
        if file.path.name == ".env.local":
            return parse_env_content(file.content)
    return {}


def run_worktree_command(
    runner: Runner,
    command: str,
    cwd: Path,
    env: Mapping[str, str],
) -> None:
    runner.run(shlex.split(command), cwd=cwd, env=env)


def run_lifecycle_command(
    runner: Runner,
    workspace_root: Path,
    worktree_slug: str,
    kind: LogKind,
    command: str,
    cwd: Path,
    env: Mapping[str, str],
    check: bool = True,
) -> int:
    argv = shlex.split(command)
    log_path = next_command_log_path(workspace_root, worktree_slug, kind)
    exit_code = runner.run_stream_logged(argv, cwd=cwd, env=env, log_path=log_path, label=kind)

    if check and exit_code != 0:
        raise BonsaiCommandError(
            f"Command failed ({exit_code}): {format_command(argv, cwd=cwd)}\nLog: {log_path}"
        )
    return exit_code


def reload_workspace_caddy(runner: Runner, config: BonsaiConfig, workspace_root: Path) -> None:
    root_caddyfile = _safe_path_segment(config.caddy.root_caddyfile, "caddy root_caddyfile")
    command = caddy_reload_plan(workspace_root / root_caddyfile)
    runner.run(list(command.argv), cwd=command.cwd)


def execute_clone(
    runner: Runner,
    git_url: str,
    name: str,
    parent: Path,
    config_initializer: ConfigInitializer | None = None,
) -> CloneWorkspacePlan:
    safe_name = _safe_path_segment(name, "workspace name")
    workspace_root = parent / safe_name
    if workspace_root.exists():
        raise BonsaiWorkspaceError(f"Target workspace already exists: {workspace_root}")

    default_branch = discover_default_branch(runner, git_url)
    default_worktree = workspace_root / default_branch
    clone_default_branch(runner, git_url, default_branch, default_worktree)
    root_config = workspace_config_path(workspace_root)
    fallback_config = repo_config_path(workspace_root, default_branch)
    if not root_config.exists() and not fallback_config.exists() and config_initializer is not None:
        config_initializer(root_config, safe_name, default_branch, default_worktree)
    config = load_config(resolve_workspace_config_path(workspace_root, default_branch))
    plan = plan_clone_workspace(git_url, safe_name, default_branch, config, parent)
    write_files(plan.files)
    save_state(workspace_root / ".bonsai" / "state.json", plan.state)
    command_env = generated_worktree_env(plan.files)
    default_worktree_slug = branch_slug(plan.state.default_branch)
    if config.commands.install:
        run_lifecycle_command(
            runner,
            workspace_root=plan.workspace_root,
            worktree_slug=default_worktree_slug,
            kind="install",
            command=config.commands.install,
            cwd=plan.default_worktree,
            env=command_env,
        )
    if config.commands.setup:
        run_lifecycle_command(
            runner,
            workspace_root=plan.workspace_root,
            worktree_slug=default_worktree_slug,
            kind="setup",
            command=config.commands.setup,
            cwd=plan.default_worktree,
            env=command_env,
        )
    return plan


def execute_add(
    runner: Runner,
    branch: str,
    workspace_root: Path,
) -> AddFilesPlan:
    state_path = workspace_root / ".bonsai" / "state.json"
    state = load_state(state_path)
    default_worktree = workspace_root / state.default_worktree
    config = load_workspace_config(workspace_root, state)
    plan = plan_add_files(config, state, workspace_root, branch)
    if plan.worktree_path.exists() and not plan.worktree_path.is_dir():
        raise BonsaiWorkspaceError(f"Branch worktree path is not a directory: {plan.worktree_path}")
    if plan.worktree_path.exists():
        if not is_git_worktree(runner, plan.worktree_path):
            raise BonsaiWorkspaceError(
                f"Branch worktree path is not a git worktree: {plan.worktree_path}"
            )
        existing_branch = current_branch(runner, plan.worktree_path)
        if existing_branch != branch:
            raise BonsaiWorkspaceError(
                f"Branch worktree path has branch {existing_branch}, expected {branch}"
            )
    else:
        base_branch = config.base_branch or state.default_branch
        fetch_origin(runner, default_worktree)
        if remote_branch_exists(runner, default_worktree, branch):
            add_existing_worktree(runner, default_worktree, branch, plan.worktree_path)
        else:
            add_new_worktree(runner, default_worktree, branch, plan.worktree_path, base_branch)
    apply_symlinks(plan.symlinks)
    write_files(plan.files)
    save_state(state_path, plan.updated_state)
    reload_workspace_caddy(runner, config, workspace_root)
    command_env = generated_worktree_env(plan.files)
    worktree_slug = plan.updated_state.worktrees[branch].slug
    if config.commands.install:
        run_lifecycle_command(
            runner,
            workspace_root=workspace_root,
            worktree_slug=worktree_slug,
            kind="install",
            command=config.commands.install,
            cwd=plan.worktree_path,
            env=command_env,
        )
    if config.commands.setup:
        run_lifecycle_command(
            runner,
            workspace_root=workspace_root,
            worktree_slug=worktree_slug,
            kind="setup",
            command=config.commands.setup,
            cwd=plan.worktree_path,
            env=command_env,
        )
    return plan


def execute_checkout(
    runner: Runner,
    name: str,
    workspace_root: Path,
) -> CheckoutWorktreePlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    if name in {state.default_branch, state.default_worktree}:
        return CheckoutWorktreePlan(
            worktree_path=workspace_root / state.default_worktree,
            created=False,
        )

    resolved = resolve_managed_worktree(state, name)
    if resolved is not None:
        return CheckoutWorktreePlan(
            worktree_path=workspace_root / resolved.worktree.path,
            created=False,
        )

    add_plan = execute_add(runner, name, workspace_root)
    return CheckoutWorktreePlan(worktree_path=add_plan.worktree_path, created=True)


def execute_remove(
    runner: Runner,
    name: str,
    workspace_root: Path,
    force: bool = False,
) -> RemoveWorktreePlan:
    state_path = workspace_root / ".bonsai" / "state.json"
    state = load_state(state_path)
    if name in {state.default_branch, state.default_worktree}:
        raise BonsaiWorkspaceError("Cannot remove the default worktree")

    resolved = resolve_managed_worktree(state, name)
    if resolved is None:
        raise BonsaiWorkspaceError(f"Unknown worktree: {name}")

    worktree_path = workspace_root / resolved.worktree.path
    default_worktree = workspace_root / state.default_worktree
    config = load_workspace_config(workspace_root, state)
    if not force and worktree_has_changes(runner, worktree_path):
        raise BonsaiWorkspaceError(
            f"Worktree has uncommitted changes: {worktree_path}. Use --force to remove it."
        )

    compose_project = detect_compose_project(worktree_path)
    if compose_project is not None:
        teardown_compose_project(runner, compose_project)

    git_remove_worktree(runner, default_worktree, worktree_path, force=force)
    removed_snippets = _remove_generated_snippets(workspace_root, config, resolved.worktree.slug)
    updated_state = remove_worktree(state, resolved.branch)
    save_state(state_path, updated_state)
    reload_workspace_caddy(runner, config, workspace_root)
    return RemoveWorktreePlan(
        branch=resolved.branch,
        worktree_path=worktree_path,
        removed_snippets=removed_snippets,
        updated_state=updated_state,
        compose_project_name=compose_project.project_name if compose_project is not None else None,
    )


def execute_cleanup(
    runner: Runner,
    workspace_root: Path,
    apply: bool = False,
    force: bool = False,
) -> CleanupPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    default_worktree = workspace_root / state.default_worktree
    _require_github_cli(runner, default_worktree)

    items: list[CleanupItem] = []
    for branch, worktree in sorted(state.worktrees.items(), key=lambda item: item[0].lower()):
        item = _pr_cleanup_decision(runner, default_worktree, branch, worktree, workspace_root)
        if item.action != "remove":
            items.append(item)
            continue

        if not force and worktree_has_changes(runner, item.worktree_path):
            items.append(
                CleanupItem(
                    branch=item.branch,
                    worktree_path=item.worktree_path,
                    action="skip",
                    reason="worktree has uncommitted changes",
                    pr_url=item.pr_url,
                )
            )
            continue

        if not apply:
            items.append(item)
            continue

        execute_remove(runner, branch, workspace_root, force=force)
        items.append(
            CleanupItem(
                branch=item.branch,
                worktree_path=item.worktree_path,
                action="removed",
                reason=item.reason,
                pr_url=item.pr_url,
            )
        )

    return CleanupPlan(items=tuple(items))
