from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from bonsai.caddy import (
    caddy_boot_config_path,
    caddy_reload_plan,
    caddy_setup_plan,
    merge_boot_config,
)
from bonsai.compose import (
    StaleComposeContainer,
    detect_compose_project,
    find_compose_published_ports,
    find_stale_compose_containers,
    remove_stopped_stale_compose_containers,
    teardown_compose_project,
)
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
    list_worktrees,
    remote_branch_exists,
    remote_origin_url,
    repair_worktrees,
    worktree_has_changes,
)
from bonsai.git import (
    move_worktree as git_move_worktree,
)
from bonsai.git import (
    remove_worktree as git_remove_worktree,
)
from bonsai.logs import LogKind, command_log_dir, latest_command_log, next_command_log_path
from bonsai.models import (
    AddFilesPlan,
    AppDownPlan,
    AppUpPlan,
    BonsaiConfig,
    BonsaiState,
    CaddySetupResult,
    CheckoutWorktreePlan,
    CleanupItem,
    CleanupPlan,
    CloneWorkspacePlan,
    CommandLogPlan,
    CommandResult,
    CommandSpec,
    DoctorApplyAction,
    DoctorApplyPlan,
    DoctorCheck,
    DoctorReport,
    FileSymlink,
    FileWrite,
    ManagedWorktree,
    MoveWorktreePlan,
    OpenUrlPlan,
    PortOwner,
    PortRepairItem,
    PortRepairPlan,
    PortRepairServiceChange,
    RemoveWorktreePlan,
    RepairItem,
    RepairPlan,
    ResolvedWorktree,
    StopProcessItem,
    StopProcessPlan,
    SyncFileAction,
    SyncPlan,
    UrlCheck,
    WorkspacePort,
    WorkspacePortsPlan,
    WorkspaceStatus,
    WorkspaceSummary,
    WorkspaceUrl,
    WorkspaceUrlsPlan,
    WorktreeTarget,
)
from bonsai.ports import allocate_slot, inspect_port_owners
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
from bonsai.workspace_facts import build_worktree_facts

ConfigInitializer = Callable[[Path, str, str, Path], None]

_PREPARE_COMMAND_KINDS: tuple[LogKind, ...] = (
    "preinstall",
    "install",
    "postinstall",
    "presetup",
    "setup",
    "postsetup",
)


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


def global_caddy_paths() -> tuple[Path, Path]:
    """Return (root Caddyfile, snippets root) under ~/.bonsai."""
    root = Path.home() / ".bonsai"
    return root / "Caddyfile", root / "caddy.d"


def app_snippets_dir(app_name: str) -> Path:
    _, snippets_root = global_caddy_paths()
    return snippets_root / _safe_path_segment(app_name, "workspace name")


def _app_snippet_dirs(snippets_root: Path) -> list[Path]:
    """App subdirectories under the global snippets root that contain snippets."""
    if not snippets_root.exists():
        return []
    return sorted(
        directory
        for directory in snippets_root.iterdir()
        if directory.is_dir() and any(directory.glob("*.caddy"))
    )


def _check_port_listening(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


_CADDY_HTTPS_PORT = 443


def _check_caddy_listening() -> bool:
    """Probe Caddy's local HTTPS listener.

    A plain socket connect confirms Caddy is accepting connections without
    performing a TLS handshake, so a missing ``*.localhost`` certificate trust
    never falsely demotes a healthy Caddy route to its direct port.
    """
    return _check_port_listening(_CADDY_HTTPS_PORT)


def plan_clone_workspace(
    git_url: str,
    name: str,
    default_branch: str,
    config: BonsaiConfig,
    parent: Path,
) -> CloneWorkspacePlan:
    name = _safe_path_segment(name, "workspace name")
    workspace_root = parent / name
    default_worktree = workspace_root / default_branch
    state = BonsaiState(
        version=1,
        name=name,
        default_branch=default_branch,
        default_worktree=default_branch,
        repo_url=git_url,
        worktrees={},
    )
    files = generated_worktree_files(
        config,
        branch=default_branch,
        slot=0,
        worktree_path=default_worktree,
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
    worktree_path: Path,
) -> tuple[FileWrite, ...]:
    slug = branch_slug(branch)
    if slug == "":
        raise BonsaiWorkspaceError(f"Invalid branch slug: {branch!r}")
    snippets_dir = app_snippets_dir(config.name)
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
    files = list(generated_worktree_files(config, branch, slot, worktree_path))
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


def _worktree_name_aliases(branch: str, worktree: ManagedWorktree) -> tuple[str, ...]:
    aliases: list[str] = []
    for alias in (branch, worktree.path, worktree.slug):
        if alias and alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def _normalized_worktree_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _worktree_name_matches(query: str, alias: str) -> bool:
    if query == "":
        return True
    folded_query = query.casefold()
    folded_alias = alias.casefold()
    if folded_query in folded_alias:
        return True
    normalized_query = _normalized_worktree_name(query)
    return bool(normalized_query) and normalized_query in _normalized_worktree_name(alias)


def _fuzzy_worktree_target(
    targets: tuple[WorktreeTarget, ...],
    name: str,
) -> WorktreeTarget | None:
    matches = [
        target
        for target in targets
        if any(
            _worktree_name_matches(name, alias)
            for alias in _worktree_name_aliases(
                target.branch,
                target.worktree,
            )
        )
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        choices = ", ".join(target.branch for target in matches)
        raise BonsaiWorkspaceError(f"Ambiguous Bonsai worktree {name!r}: {choices}")
    return None


def worktree_name_completions(
    workspace_root: Path,
    incomplete: str,
    *,
    include_default: bool = True,
) -> tuple[str, ...]:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    targets = _configured_worktree_targets(state, workspace_root)
    if not include_default:
        targets = tuple(target for target in targets if target.branch != state.default_branch)

    completions: list[str] = []
    for target in targets:
        for alias in _worktree_name_aliases(target.branch, target.worktree):
            if alias in completions or not _worktree_name_matches(incomplete, alias):
                continue
            completions.append(alias)
    return tuple(completions)


def _paths_refer_to_same_existing_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


def _default_worktree_names(state: BonsaiState) -> set[str]:
    return {
        state.default_branch,
        state.default_worktree,
        branch_slug(state.default_branch),
    }


def _validate_move_target(
    state: BonsaiState,
    old_worktree_path: Path,
    new_worktree_path: Path,
    safe_new_folder: str,
    *,
    source_branch: str,
) -> None:
    for branch, worktree in state.worktrees.items():
        if branch == source_branch:
            continue
        if safe_new_folder in {branch, worktree.path, worktree.slug}:
            raise BonsaiWorkspaceError(f"Worktree target already exists: {new_worktree_path}")

    target_is_symlink = new_worktree_path.is_symlink()
    case_only_samefile_move = (
        old_worktree_path.name != new_worktree_path.name
        and old_worktree_path.name.lower() == new_worktree_path.name.lower()
        and not target_is_symlink
        and _paths_refer_to_same_existing_path(old_worktree_path, new_worktree_path)
    )
    if (new_worktree_path.exists() or target_is_symlink) and not case_only_samefile_move:
        raise BonsaiWorkspaceError(f"Worktree target already exists: {new_worktree_path}")


def plan_move_worktree(
    state: BonsaiState,
    workspace_root: Path,
    name: str,
    new_folder: str,
) -> MoveWorktreePlan:
    safe_new_folder = _safe_path_segment(new_folder, "worktree folder")
    if name in _default_worktree_names(state):
        raise BonsaiWorkspaceError("Cannot move the default worktree")

    resolved = resolve_managed_worktree(state, name)
    if resolved is None:
        raise BonsaiWorkspaceError(f"Unknown worktree: {name}")

    if resolved.worktree.path == safe_new_folder:
        raise BonsaiWorkspaceError(f"Worktree already uses folder: {safe_new_folder}")

    old_worktree_path = workspace_root / resolved.worktree.path
    new_worktree_path = workspace_root / safe_new_folder
    if safe_new_folder in _default_worktree_names(state):
        raise BonsaiWorkspaceError(f"Worktree target already exists: {new_worktree_path}")

    _validate_move_target(
        state,
        old_worktree_path,
        new_worktree_path,
        safe_new_folder,
        source_branch=resolved.branch,
    )

    updated_worktree = replace(resolved.worktree, path=safe_new_folder)
    return MoveWorktreePlan(
        branch=resolved.branch,
        old_worktree_path=old_worktree_path,
        new_worktree_path=new_worktree_path,
        updated_state=update_worktree(state, resolved.branch, updated_worktree),
    )


def plan_rename_default(
    state: BonsaiState,
    workspace_root: Path,
    new_folder: str,
) -> MoveWorktreePlan:
    safe_new_folder = _safe_path_segment(new_folder, "worktree folder")
    if safe_new_folder == state.default_worktree:
        raise BonsaiWorkspaceError(f"Worktree already uses folder: {safe_new_folder}")

    old_worktree_path = workspace_root / state.default_worktree
    new_worktree_path = workspace_root / safe_new_folder
    _validate_move_target(
        state,
        old_worktree_path,
        new_worktree_path,
        safe_new_folder,
        source_branch=state.default_branch,
    )

    return MoveWorktreePlan(
        branch=state.default_branch,
        old_worktree_path=old_worktree_path,
        new_worktree_path=new_worktree_path,
        updated_state=replace(state, default_worktree=safe_new_folder),
    )


def _remove_generated_snippets(
    config: BonsaiConfig,
    slug: str,
) -> tuple[Path, ...]:
    snippets_dir = app_snippets_dir(config.name)
    removed: list[Path] = []
    if not snippets_dir.exists():
        return ()
    for path in sorted(snippets_dir.glob(f"{slug}-*.caddy")):
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed.append(path)
    return tuple(removed)


def _remove_worktree_logs(workspace_root: Path, slug: str) -> Path | None:
    log_dir = command_log_dir(workspace_root, slug)
    if log_dir.is_dir() and not log_dir.is_symlink():
        shutil.rmtree(log_dir)
        return log_dir
    if log_dir.is_file() or log_dir.is_symlink():
        log_dir.unlink()
        return log_dir
    return None


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


def _doctor_repair_action_label(action: str) -> str:
    if action == "remove":
        return "removed"
    if action == "repack":
        return "repacked"
    return action


def _command_available(runner: Runner, argv: list[str]) -> bool:
    try:
        result = runner.run(argv, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _run_caddy_setup(runner: Runner, config: BonsaiConfig) -> CaddySetupResult:
    if not config.public_services():
        return CaddySetupResult()

    commands = caddy_setup_plan(
        auto_install=config.caddy.auto_install,
        auto_start=config.caddy.auto_start,
        caddy_exists=_command_available(runner, ["caddy", "version"]),
        brew_exists=_command_available(runner, ["brew", "--version"]),
    )
    actions: list[DoctorApplyAction] = []
    for command in commands:
        result = runner.run(list(command.argv), cwd=command.cwd, check=False)
        if result.returncode != 0:
            check = DoctorCheck(
                name="caddy",
                status="fail",
                detail=f"{command_summary(command)} failed ({result.returncode})",
                hint=(
                    "Caddy install/start failed - Bonsai will use a direct port URL. "
                    "Fix later with `brew install caddy` then `bonsai doctor`."
                ),
                id="caddy-setup",
            )
            return CaddySetupResult(actions=tuple(actions), checks=(check,))
        actions.append(DoctorApplyAction(kind="caddy", detail=command_summary(command)))
    return CaddySetupResult(actions=tuple(actions))


def setup_caddy(runner: Runner, workspace_root: Path) -> CaddySetupResult:
    """Run Caddy install/start for a workspace, loading its config first.

    Thin seam over ``_run_caddy_setup`` for callers that hold a workspace root
    rather than a loaded ``BonsaiConfig`` (e.g. the guided ``start-here`` flow).
    """
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    return _run_caddy_setup(runner, config)


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
    return any(_check_port_listening(service.base_port + slot) for service in config.services)


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
        return "unknown" if _check_port_listening(port) else "free"
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
) -> StopProcessPlan:
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
        if item.action != "stop":
            applied.append(item)
            continue
        reason = "terminated"
        try:
            os.kill(item.owner.pid, signal.SIGTERM)
        except ProcessLookupError:
            reason = "process already exited"
        applied.append(replace(item, action="stopped", reason=reason))
    return StopProcessPlan(items=tuple(applied))


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
        ready = tuple(port for port in ports if _check_port_listening(port))
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
                f"{target.branch} is already running with pid {existing_pid}. Run: bonsai down"
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


def execute_down(
    workspace_root: Path,
    name: str | None,
    current_path: Path,
    terminate_timeout: float = 5.0,
) -> AppDownPlan:
    target = resolve_start_target(workspace_root, name, current_path)
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


def _public_url_service(config: BonsaiConfig, service_name: str | None):
    if service_name is None:
        try:
            return config.primary_service()
        except ValueError as exc:
            raise BonsaiConfigError("No primary public service configured") from exc
    for service in config.public_services():
        if service.name == service_name and service.url is not None:
            return service
    raise BonsaiConfigError(f"No public URL service named {service_name}")


def _plan_service_open_url(
    config: BonsaiConfig,
    branch: str,
    worktree: ManagedWorktree,
    worktree_path: Path,
    service_name: str | None = None,
) -> OpenUrlPlan:
    service = _public_url_service(config, service_name)
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

    return OpenUrlPlan(
        branch=branch,
        worktree_path=worktree_path,
        url=url,
        service_name=service.name,
        port=service.base_port + worktree.slot,
        workspace_name=config.name,
        browser_extension_id=config.browser_extension.extension_id,
    )


def _port_open_plan(plan: OpenUrlPlan) -> OpenUrlPlan:
    return OpenUrlPlan(
        branch=plan.branch,
        worktree_path=plan.worktree_path,
        url=f"http://localhost:{plan.port}",
        service_name=plan.service_name,
        port=plan.port,
        workspace_name=plan.workspace_name,
        browser_extension_id=plan.browser_extension_id,
        via="port",
    )


def resolve_open_target(plan: OpenUrlPlan) -> OpenUrlPlan:
    """Choose between the Caddy route and the direct port for an open target.

    Runner-free, probe-driven, and opt-in: only the open/wizard flow calls this.
    ``bonsai urls`` keeps using the plain Caddy ``OpenUrlPlan`` so its output is
    unaffected. When Caddy's HTTPS listener is up the existing
    ``https://…localhost`` URL is kept (``via="caddy"``). When Caddy is down but
    the app port is live the plan is demoted to ``http://localhost:<port>``
    (``via="port"``). When neither responds the Caddy plan is returned unchanged
    so the caller's liveness gate can report the dead route.
    """
    if _check_caddy_listening():
        return plan
    if _check_port_listening(plan.port):
        return _port_open_plan(plan)
    return plan


def url_liveness_ok(plan: OpenUrlPlan) -> bool:
    """Confirm the chosen open target is actually reachable.

    Runner-free. The Caddy plan requires BOTH Caddy's HTTPS listener AND the
    app's own backend port (the port Caddy reverse-proxies to) to be live, so a
    persistent Caddy service can never greenlight a dead app. The port plan is
    gated on the app port alone; the port probe never greenlights the Caddy URL.
    """
    if plan.via == "caddy":
        return _check_caddy_listening() and _check_port_listening(plan.port)
    return _check_port_listening(plan.port)


def plan_open_url(
    workspace_root: Path,
    current_path: Path,
    service_name: str | None = None,
) -> OpenUrlPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    branch, worktree, worktree_path = _resolve_current_worktree(state, workspace_root, current_path)
    return _plan_service_open_url(
        config,
        branch,
        worktree,
        worktree_path,
        service_name=service_name,
    )


def plan_open_url_for_worktree(
    workspace_root: Path,
    name: str,
    service_name: str | None = None,
) -> OpenUrlPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    target = resolve_start_target(workspace_root, name, workspace_root)
    return _plan_service_open_url(
        config,
        target.branch,
        target.worktree,
        target.worktree_path,
        service_name=service_name,
    )


def _workspace_url_checks(
    runner: Runner,
    workspace_root: Path,
    config: BonsaiConfig,
    target: WorktreeTarget,
    service,
    caddy_snippet_path: Path,
    port_status: WorkspacePort,
) -> tuple[UrlCheck, ...]:
    root_caddyfile, snippets_root = global_caddy_paths()
    expected_root = render_root_caddyfile(_app_snippet_dirs(snippets_root))
    port = service.base_port + target.worktree.slot
    expected_route = render_caddy_snippets(
        config,
        target.branch,
        target.worktree.slot,
        target.worktree_path,
    )[service.name]

    checks: list[UrlCheck] = []
    if not root_caddyfile.exists():
        checks.append(
            UrlCheck(
                "root Caddyfile",
                "fail",
                f"Missing {root_caddyfile}",
                "Run: bonsai sync --apply",
            )
        )
    elif root_caddyfile.read_text(encoding="utf-8") != expected_root:
        checks.append(
            UrlCheck(
                "root Caddyfile",
                "fail",
                f"Stale {root_caddyfile}",
                "Run: bonsai sync --apply",
            )
        )
    else:
        checks.append(
            UrlCheck(
                "root Caddyfile",
                "ok",
                f"imports app snippets under {snippets_root}",
            )
        )

    route_content = None
    if caddy_snippet_path.exists():
        route_content = caddy_snippet_path.read_text(encoding="utf-8")
    if route_content is None:
        checks.append(
            UrlCheck(
                "Caddy route",
                "fail",
                f"Missing {caddy_snippet_path}",
                "Run: bonsai sync --apply",
            )
        )
    elif route_content != expected_route:
        checks.append(
            UrlCheck(
                "Caddy route",
                "fail",
                f"Stale {caddy_snippet_path}",
                "Run: bonsai sync --apply",
            )
        )
    else:
        checks.append(
            UrlCheck(
                "Caddy route",
                "ok",
                f"{caddy_snippet_path} routes to localhost:{port}",
            )
        )

    if not root_caddyfile.exists():
        checks.append(
            UrlCheck(
                "Caddy validate",
                "fail",
                f"Cannot validate missing {root_caddyfile}",
                "Run: bonsai sync --apply",
            )
        )
    else:
        try:
            caddy = runner.run(
                ["caddy", "validate", "--config", str(root_caddyfile)],
                check=False,
            )
        except (FileNotFoundError, OSError):
            caddy = CommandResult(returncode=127, stderr="caddy not found")
        checks.append(
            UrlCheck(
                "Caddy validate",
                "ok" if caddy.returncode == 0 else "fail",
                caddy.stdout.strip() or caddy.stderr.strip() or "caddy validate failed",
                "Run: bonsai doctor --apply" if caddy.returncode != 0 else None,
            )
        )

    checks.append(_workspace_url_app_check(port_status, target.branch))
    if service.url is not None and service.url.startswith("http://"):
        checks.append(
            UrlCheck(
                "TLS",
                "warn",
                "URL uses HTTP; TLS is not configured for this route",
            )
        )
        checks.append(
            UrlCheck(
                "local CA trust",
                "ok",
                "not required for HTTP URLs",
            )
        )
    elif route_content is not None and "\ttls internal" in route_content:
        checks.append(
            UrlCheck(
                "TLS",
                "ok",
                "route uses Caddy internal TLS",
            )
        )
        checks.append(
            UrlCheck(
                "local CA trust",
                "warn",
                "Caddy internal certificates require local CA trust in browsers",
                "Run: caddy trust",
            )
        )
    else:
        checks.append(
            UrlCheck(
                "TLS",
                "fail",
                "route is missing tls internal",
                "Run: bonsai sync --apply",
            )
        )
        checks.append(
            UrlCheck(
                "local CA trust",
                "warn",
                "verify browser trust after TLS is restored",
                "Run: caddy trust",
            )
        )
    return tuple(checks)


def _workspace_url_app_check(port: WorkspacePort, branch: str) -> UrlCheck:
    if port.status == "owned":
        owner_text = ", ".join(_port_owner_label(owner) for owner in port.owners)
        return UrlCheck(
            "app listener",
            "ok",
            f"{port.port_env}={port.port} owned by {owner_text}",
        )
    if port.status == "free":
        return UrlCheck(
            "app listener",
            "warn",
            f"no listener detected on localhost:{port.port}",
            f"Run: bonsai start {branch}",
        )
    if port.status == "unknown":
        return UrlCheck(
            "app listener",
            "fail",
            f"localhost:{port.port} is busy but the owner could not be identified",
            "Run: bonsai ports --busy",
        )
    return UrlCheck(
        "app listener",
        "fail",
        _port_owner_detail(port),
        "Run: bonsai repair-ports",
    )


def plan_workspace_urls(
    runner: Runner,
    workspace_root: Path,
    name: str | None = None,
    service_name: str | None = None,
    diagnose_url: str | None = None,
) -> WorkspaceUrlsPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    snippets_dir = app_snippets_dir(config.name)
    targets = (
        (resolve_start_target(workspace_root, name, workspace_root),)
        if name is not None
        else _configured_worktree_targets(state, workspace_root)
    )
    port_statuses = {
        (port.branch, port.service_name): port
        for port in plan_workspace_ports(runner, workspace_root).ports
    }
    items: list[WorkspaceUrl] = []
    for target in targets:
        services = (
            (_public_url_service(config, service_name),)
            if service_name is not None
            else tuple(service for service in config.public_services() if service.url is not None)
        )
        for service in services:
            plan = _plan_service_open_url(
                config,
                target.branch,
                target.worktree,
                target.worktree_path,
                service_name=service.name,
            )
            if diagnose_url is not None and plan.url != diagnose_url:
                continue
            caddy_snippet_path = snippets_dir / f"{target.worktree.slug}-{service.name}.caddy"
            items.append(
                WorkspaceUrl(
                    branch=target.branch,
                    worktree_path=target.worktree_path,
                    service_name=service.name,
                    port_env=service.port_env,
                    port=service.base_port + target.worktree.slot,
                    primary=service.primary,
                    url=plan.url,
                    caddy_snippet_path=caddy_snippet_path,
                    checks=_workspace_url_checks(
                        runner,
                        workspace_root,
                        config,
                        target,
                        service,
                        caddy_snippet_path,
                        port_statuses[(target.branch, service.name)],
                    ),
                )
            )
    if diagnose_url is not None and not items:
        raise BonsaiWorkspaceError(f"URL is not configured by Bonsai: {diagnose_url}")
    return WorkspaceUrlsPlan(
        workspace_root=workspace_root,
        caddyfile=global_caddy_paths()[0],
        urls=tuple(items),
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
    worktrees = [build_worktree_facts(config, default_target, "default").summary]
    worktrees.extend(
        build_worktree_facts(config, target, "managed").summary
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
    current_path_resolved = current_path.resolve()
    if current_path_resolved == workspace_root.resolve():
        return WorkspaceStatus(
            workspace_name=state.name,
            workspace_root=workspace_root,
            default_branch=state.default_branch,
            default_worktree=state.default_worktree,
            config_path=config_path,
            current=None,
            location_kind="workspace_root",
            location_path=workspace_root,
            commands=_workspace_summary_commands(),
        )

    branch, worktree, worktree_path = _resolve_current_worktree(
        state,
        workspace_root,
        current_path_resolved,
    )
    kind = "default" if branch == state.default_branch else "managed"
    target = WorktreeTarget(branch=branch, worktree=worktree, worktree_path=worktree_path)
    facts = build_worktree_facts(config, target, kind)
    return WorkspaceStatus(
        workspace_name=state.name,
        workspace_root=workspace_root,
        default_branch=state.default_branch,
        default_worktree=state.default_worktree,
        config_path=config_path,
        current=facts.summary,
        location_kind="worktree",
        location_path=worktree_path,
        commands=_workspace_summary_commands(),
        generated_env=facts.generated_env,
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


def run_configured_lifecycle_commands(
    runner: Runner,
    config: BonsaiConfig,
    workspace_root: Path,
    worktree_slug: str,
    kinds: tuple[LogKind, ...],
    cwd: Path,
    env: Mapping[str, str],
) -> None:
    for kind in kinds:
        command = getattr(config.commands, kind)
        if command:
            run_lifecycle_command(
                runner,
                workspace_root=workspace_root,
                worktree_slug=worktree_slug,
                kind=kind,
                command=command,
                cwd=cwd,
                env=env,
            )


def reload_workspace_caddy(runner: Runner) -> None:
    root_caddyfile, snippets_root = global_caddy_paths()
    snippets_root.mkdir(parents=True, exist_ok=True)
    app_dirs = _app_snippet_dirs(snippets_root)
    expected_root = render_root_caddyfile(app_dirs)
    if not root_caddyfile.exists() or root_caddyfile.read_text(encoding="utf-8") != expected_root:
        root_caddyfile.parent.mkdir(parents=True, exist_ok=True)
        root_caddyfile.write_text(expected_root, encoding="utf-8")
    _ensure_caddy_boot_config(runner, app_dirs)
    command = caddy_reload_plan(root_caddyfile)
    runner.run(list(command.argv), cwd=command.cwd)


def _ensure_caddy_boot_config(runner: Runner, app_dirs: list[Path]) -> None:
    if not _command_available(runner, ["caddy", "version"]):
        return
    boot_path = caddy_boot_config_path(runner)
    if boot_path is None:
        return
    existing = boot_path.read_text(encoding="utf-8") if boot_path.exists() else ""
    import_lines = [f"import {directory}/*.caddy" for directory in app_dirs]
    merged = merge_boot_config(existing, import_lines)
    if merged != existing:
        boot_path.parent.mkdir(parents=True, exist_ok=True)
        boot_path.write_text(merged, encoding="utf-8")


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
    run_configured_lifecycle_commands(
        runner,
        config=config,
        workspace_root=plan.workspace_root,
        worktree_slug=default_worktree_slug,
        kinds=_PREPARE_COMMAND_KINDS,
        cwd=plan.default_worktree,
        env=command_env,
    )
    return plan


def execute_init(runner: Runner, checkout_path: Path) -> CloneWorkspacePlan:
    workspace_root = checkout_path.parent
    state_path = workspace_root / ".bonsai" / "state.json"
    if state_path.exists():
        base_state = load_state(state_path)
        config = load_workspace_config(workspace_root, base_state)
        default_worktree = _safe_path_segment(base_state.default_worktree, "default worktree")
        default_branch = base_state.default_branch
        expected_checkout_path = workspace_root / default_worktree
        if checkout_path.resolve() != expected_checkout_path.resolve():
            raise BonsaiWorkspaceError(
                "Existing workspace state default worktree does not match bonsai init path: "
                f"{default_worktree!r} != {checkout_path.name!r}"
            )
    else:
        config_path = checkout_path / ".bonsai.toml"
        config = load_config(config_path)
        default_worktree = _safe_path_segment(checkout_path.name, "default worktree")
        default_branch = current_branch(runner, checkout_path)
        if not default_branch or default_branch == "HEAD":
            raise BonsaiWorkspaceError(f"Unable to determine current branch for {checkout_path}")
        if default_worktree != default_branch:
            raise BonsaiWorkspaceError(
                "Existing checkout directory must match the current branch for bonsai init: "
                f"{checkout_path.name!r} != {default_branch!r}. "
                "Use the Bonsai layout <workspace>/<branch>."
            )
        base_state = BonsaiState(
            version=1,
            name=workspace_root.name,
            default_branch=default_branch,
            default_worktree=default_worktree,
            repo_url=remote_origin_url(runner, checkout_path),
            worktrees={},
        )

    adopted_worktrees: dict[str, ManagedWorktree] = dict(base_state.worktrees)
    workspace_root_resolved = workspace_root.resolve()
    checkout_path_resolved = checkout_path.resolve()
    for git_worktree in list_worktrees(runner, checkout_path):
        worktree_path = git_worktree.path.resolve()
        branch = git_worktree.branch
        if branch is None or worktree_path == checkout_path_resolved:
            continue
        if worktree_path.parent != workspace_root_resolved:
            continue
        if not worktree_path.is_dir():
            continue
        if branch in adopted_worktrees:
            continue
        relative_path = _safe_path_segment(worktree_path.name, "worktree path")
        slug = branch_slug(branch)
        if slug == "":
            raise BonsaiWorkspaceError(f"Invalid branch slug: {branch!r}")
        adopted_worktrees[branch] = ManagedWorktree(
            path=relative_path,
            slug=slug,
            slot=allocate_slot(adopted_worktrees),
        )
    state = replace(
        base_state,
        worktrees=adopted_worktrees,
    )
    files = list(
        generated_worktree_files(
            config,
            branch=default_branch,
            slot=0,
            worktree_path=checkout_path,
        )
    )
    for branch, worktree in adopted_worktrees.items():
        files.extend(
            generated_worktree_files(
                config,
                branch=branch,
                slot=worktree.slot,
                worktree_path=workspace_root / worktree.path,
            )
        )
    plan = CloneWorkspacePlan(
        workspace_root=workspace_root,
        default_worktree=checkout_path,
        state=state,
        files=tuple(files),
    )
    write_files(plan.files)
    save_state(state_path, state)
    return plan


def execute_add(
    runner: Runner,
    branch: str,
    workspace_root: Path,
    base_branch: str | None = None,
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
        creation_base_branch = base_branch or config.base_branch or state.default_branch
        fetch_origin(runner, default_worktree)
        if remote_branch_exists(runner, default_worktree, branch):
            add_existing_worktree(runner, default_worktree, branch, plan.worktree_path)
        else:
            add_new_worktree(
                runner,
                default_worktree,
                branch,
                plan.worktree_path,
                creation_base_branch,
            )
    apply_symlinks(plan.symlinks)
    write_files(plan.files)
    save_state(state_path, plan.updated_state)
    reload_workspace_caddy(runner)
    command_env = generated_worktree_env(plan.files)
    worktree_slug = plan.updated_state.worktrees[branch].slug
    run_configured_lifecycle_commands(
        runner,
        config=config,
        workspace_root=workspace_root,
        worktree_slug=worktree_slug,
        kinds=_PREPARE_COMMAND_KINDS,
        cwd=plan.worktree_path,
        env=command_env,
    )
    return plan


def execute_checkout(
    runner: Runner,
    name: str,
    workspace_root: Path,
    base_branch: str | None = None,
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

    fuzzy_target = _fuzzy_worktree_target(
        _configured_worktree_targets(state, workspace_root),
        name,
    )
    if fuzzy_target is not None:
        return CheckoutWorktreePlan(
            worktree_path=fuzzy_target.worktree_path,
            created=False,
        )

    add_plan = execute_add(runner, name, workspace_root, base_branch=base_branch)
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

    execute_down(
        workspace_root,
        resolved.branch,
        current_path=default_worktree,
        terminate_timeout=5.0,
    )
    execute_stop_processes(
        runner,
        workspace_root,
        current_path=default_worktree,
        name=resolved.branch,
    )
    compose_project = detect_compose_project(worktree_path)
    if compose_project is not None:
        teardown_compose_project(runner, compose_project)

    git_remove_worktree(runner, default_worktree, worktree_path, force=force)
    removed_snippets = _remove_generated_snippets(config, resolved.worktree.slug)
    removed_logs = _remove_worktree_logs(workspace_root, resolved.worktree.slug)
    updated_state = remove_worktree(state, resolved.branch)
    save_state(state_path, updated_state)
    reload_workspace_caddy(runner)
    return RemoveWorktreePlan(
        branch=resolved.branch,
        worktree_path=worktree_path,
        removed_snippets=removed_snippets,
        updated_state=updated_state,
        compose_project_name=compose_project.project_name if compose_project is not None else None,
        removed_logs=removed_logs,
    )


def _next_move_temp_path(workspace_root: Path, target_name: str) -> Path:
    base_name = f".bonsai-move-{target_name}"
    candidate = workspace_root / base_name
    suffix = 1
    while candidate.exists():
        candidate = workspace_root / f"{base_name}-{suffix}"
        suffix += 1
    return candidate


def _move_git_worktree_path(
    runner: Runner,
    default_worktree: Path,
    workspace_root: Path,
    plan: MoveWorktreePlan,
) -> None:
    if _paths_refer_to_same_existing_path(
        plan.old_worktree_path,
        plan.new_worktree_path,
    ):
        temp_path = _next_move_temp_path(workspace_root, plan.new_worktree_path.name)
        git_move_worktree(runner, default_worktree, plan.old_worktree_path, temp_path)
        git_move_worktree(runner, default_worktree, temp_path, plan.new_worktree_path)
        return

    git_move_worktree(
        runner,
        default_worktree,
        plan.old_worktree_path,
        plan.new_worktree_path,
    )


def execute_move(
    runner: Runner,
    name: str,
    new_folder: str,
    workspace_root: Path,
    *,
    force: bool = False,
) -> MoveWorktreePlan:
    state_path = workspace_root / ".bonsai" / "state.json"
    state = load_state(state_path)
    if name in _default_worktree_names(state):
        if not force:
            raise BonsaiWorkspaceError(
                "Renaming the default worktree re-points all secondary worktrees "
                "and rewrites generated files; pass --force to proceed."
            )
        return execute_rename_default(runner, workspace_root, new_folder)
    default_worktree = workspace_root / state.default_worktree
    plan = plan_move_worktree(state, workspace_root, name, new_folder)
    _move_git_worktree_path(runner, default_worktree, workspace_root, plan)
    save_state(state_path, plan.updated_state)
    execute_sync(runner, workspace_root, apply=True)
    return plan


def _relocate_default_worktree(
    runner: Runner,
    workspace_root: Path,
    plan: MoveWorktreePlan,
) -> None:
    if _paths_refer_to_same_existing_path(
        plan.old_worktree_path,
        plan.new_worktree_path,
    ):
        temp_path = _next_move_temp_path(workspace_root, plan.new_worktree_path.name)
        shutil.move(str(plan.old_worktree_path), str(temp_path))
        shutil.move(str(temp_path), str(plan.new_worktree_path))
    else:
        shutil.move(str(plan.old_worktree_path), str(plan.new_worktree_path))
    repair_worktrees(runner, plan.new_worktree_path)


def execute_rename_default(
    runner: Runner,
    workspace_root: Path,
    new_folder: str,
) -> MoveWorktreePlan:
    state_path = workspace_root / ".bonsai" / "state.json"
    state = load_state(state_path)
    plan = plan_rename_default(state, workspace_root, new_folder)
    _relocate_default_worktree(runner, workspace_root, plan)
    save_state(state_path, plan.updated_state)
    execute_sync(runner, workspace_root, apply=True)
    return plan


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
