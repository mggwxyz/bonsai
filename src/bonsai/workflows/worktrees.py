from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from bonsai.compose import (
    detect_compose_project,
    teardown_compose_project,
)
from bonsai.config import load_config
from bonsai.env import parse_env_content
from bonsai.errors import BonsaiWorkspaceError
from bonsai.git import (
    add_existing_worktree,
    add_new_worktree,
    clone_default_branch,
    current_branch,
    discover_default_branch,
    fetch_origin,
    fetch_ref,
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
from bonsai.logs import command_log_dir
from bonsai.models import (
    AddFilesPlan,
    BonsaiConfig,
    BonsaiState,
    CheckoutWorktreePlan,
    CleanupItem,
    CleanupPlan,
    CloneWorkspacePlan,
    FileCopy,
    FileSymlink,
    ManagedWorktree,
    MoveWorktreePlan,
    PullRequestWorktreePlan,
    RemoveWorktreePlan,
)
from bonsai.ports import allocate_slot
from bonsai.process import Runner
from bonsai.rendering import standard_bonsai_env
from bonsai.slug import branch_slug
from bonsai.state import load_state, remove_worktree, save_state, update_worktree
from bonsai.workflows.caddy_ops import (
    reload_workspace_caddy,
)
from bonsai.workflows.maintenance import (
    execute_sync,
)
from bonsai.workflows.processes import (
    execute_stop_processes,
)
from bonsai.workflows.shared import (
    _POST_ADD_COMMAND_KINDS,
    _PREPARE_COMMAND_KINDS,
    ConfigInitializer,
    _configured_worktree_targets,
    _default_worktree_names,
    _fuzzy_worktree_target,
    _safe_path_segment,
    app_snippets_dir,
    apply_file_copies,
    apply_symlinks,
    generated_worktree_env,
    generated_worktree_files,
    load_workspace_config,
    repo_config_path,
    resolve_managed_worktree,
    resolve_workspace_config_path,
    run_configured_lifecycle_commands,
    run_lifecycle_command,
    workspace_config_path,
    workspace_local_config_paths,
    worktreeinclude_file_copies,
    write_files,
)


@dataclass(frozen=True)
class _PullRequestInfo:
    state: str
    merged_at: str | None
    url: str | None


@dataclass(frozen=True)
class _PullRequestView:
    head_ref_name: str
    is_cross_repository: bool
    state: str
    title: str
    url: str | None


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
        workspace_root=workspace_root,
        default_branch=default_branch,
    )
    return CloneWorkspacePlan(
        workspace_root=workspace_root,
        default_worktree=default_worktree,
        state=state,
        files=files,
    )


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
    files = list(
        generated_worktree_files(
            config,
            branch,
            slot,
            worktree_path,
            workspace_root=workspace_root,
            default_branch=state.default_branch,
        )
    )
    symlinks: list[FileSymlink] = []
    copies: list[FileCopy] = []
    for shared_file in config.shared_files:
        source = _safe_path_segment(shared_file.source, "shared file source")
        target = _safe_path_segment(shared_file.target, "shared file target")
        if shared_file.mode == "copy":
            copies.append(
                FileCopy(
                    source=default_worktree_path / source,
                    target=worktree_path / target,
                )
            )
        else:
            symlinks.append(
                FileSymlink(
                    source=default_worktree_path / source,
                    target=worktree_path / target,
                )
            )

    copies.extend(worktreeinclude_file_copies(config, default_worktree_path, worktree_path))

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
        copies=tuple(copies),
        updated_state=updated_state,
    )


def _paths_refer_to_same_existing_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


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


def _require_github_cli(runner: Runner, repo: Path, purpose: str = "cleanup") -> None:
    try:
        version = runner.run(["gh", "--version"], check=False)
    except FileNotFoundError as exc:
        raise _github_cli_error(f"GitHub CLI is required for {purpose}") from exc
    if version.returncode != 0:
        raise _github_cli_error(f"GitHub CLI is required for {purpose}")

    try:
        auth = runner.run(["gh", "auth", "status"], cwd=repo, check=False)
    except FileNotFoundError as exc:
        raise _github_cli_error(f"GitHub CLI is required for {purpose}") from exc
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


_FORK_PR_BRANCH_PATTERN = re.compile(r"^bonsai/pr-(?P<number>\d+)$")


def _github_pr_for_number(
    runner: Runner,
    repo: Path,
    pr_number: int,
) -> tuple[_PullRequestInfo, ...]:
    result = runner.run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "state,mergedAt,url",
        ],
        cwd=repo,
    )
    try:
        raw_item = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise BonsaiWorkspaceError(f"Unable to parse GitHub PR data: {pr_number}") from exc
    if not isinstance(raw_item, dict):
        raise BonsaiWorkspaceError(f"Unable to parse GitHub PR data: {pr_number}")
    state = raw_item.get("state")
    merged_at = raw_item.get("mergedAt")
    url = raw_item.get("url")
    return (
        _PullRequestInfo(
            state=str(state or "").lower(),
            merged_at=str(merged_at) if merged_at else None,
            url=str(url) if url else None,
        ),
    )


def _github_prs_for_cleanup_branch(
    runner: Runner,
    repo: Path,
    branch: str,
) -> tuple[_PullRequestInfo, ...]:
    match = _FORK_PR_BRANCH_PATTERN.fullmatch(branch)
    if match is not None:
        return _github_pr_for_number(runner, repo, int(match.group("number")))
    return _github_prs_for_branch(runner, repo, branch)


def _github_pr_view(runner: Runner, repo: Path, pr_number: int) -> _PullRequestView:
    _require_github_cli(runner, repo, purpose="PR worktrees")
    result = runner.run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "headRefName,isCrossRepository,state,title,url",
        ],
        cwd=repo,
    )
    try:
        raw = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise BonsaiWorkspaceError(f"Unable to parse GitHub PR data: {pr_number}") from exc
    if not isinstance(raw, dict):
        raise BonsaiWorkspaceError(f"Unable to parse GitHub PR data: {pr_number}")
    head_ref_name = raw.get("headRefName")
    if not isinstance(head_ref_name, str) or not head_ref_name:
        raise BonsaiWorkspaceError(f"GitHub PR {pr_number} has no head branch")
    is_cross_repository = bool(raw.get("isCrossRepository"))
    state = str(raw.get("state") or "").lower()
    title = str(raw.get("title") or "")
    url = raw.get("url")
    return _PullRequestView(
        head_ref_name=head_ref_name,
        is_cross_repository=is_cross_repository,
        state=state,
        title=title,
        url=str(url) if url else None,
    )


def _pr_cleanup_decision(
    runner: Runner,
    default_worktree: Path,
    branch: str,
    worktree: ManagedWorktree,
    workspace_root: Path,
) -> CleanupItem:
    worktree_path = workspace_root / worktree.path
    pull_requests = _github_prs_for_cleanup_branch(runner, default_worktree, branch)
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
    config_path = resolve_workspace_config_path(workspace_root, default_branch)
    config = load_config(
        config_path,
        local_paths=workspace_local_config_paths(workspace_root, default_branch, config_path),
    )
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
        default_worktree = _safe_path_segment(checkout_path.name, "default worktree")
        config = load_config(
            config_path,
            local_paths=workspace_local_config_paths(workspace_root, default_worktree, config_path),
        )
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
            workspace_root=workspace_root,
            default_branch=state.default_branch,
        )
    )
    for branch, worktree in adopted_worktrees.items():
        files.extend(
            generated_worktree_files(
                config,
                branch=branch,
                slot=worktree.slot,
                worktree_path=workspace_root / worktree.path,
                workspace_root=workspace_root,
                default_branch=state.default_branch,
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
    _finalize_add(
        runner,
        config=config,
        state_path=state_path,
        workspace_root=workspace_root,
        branch=branch,
        plan=plan,
    )
    return plan


def _finalize_add(
    runner: Runner,
    *,
    config: BonsaiConfig,
    state_path: Path,
    workspace_root: Path,
    branch: str,
    plan: AddFilesPlan,
) -> None:
    apply_symlinks(plan.symlinks)
    apply_file_copies(plan.copies)
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
    run_configured_lifecycle_commands(
        runner,
        config=config,
        workspace_root=workspace_root,
        worktree_slug=worktree_slug,
        kinds=_POST_ADD_COMMAND_KINDS,
        cwd=plan.worktree_path,
        env=command_env,
    )


def execute_add_pull_request(
    runner: Runner,
    pr_number: int,
    workspace_root: Path,
    *,
    force: bool = False,
) -> PullRequestWorktreePlan:
    state_path = workspace_root / ".bonsai" / "state.json"
    state = load_state(state_path)
    default_worktree = workspace_root / state.default_worktree
    pr = _github_pr_view(runner, default_worktree, pr_number)
    if pr.state not in {"open"} and not force:
        raise BonsaiWorkspaceError(f"Pull request {pr_number} is {pr.state}; requires --force")

    if not pr.is_cross_repository:
        add_plan = execute_add(runner, pr.head_ref_name, workspace_root)
        return PullRequestWorktreePlan(
            pr_number=pr_number,
            branch=pr.head_ref_name,
            title=pr.title,
            url=pr.url,
            state=pr.state,
            read_only=False,
            add_plan=add_plan,
        )

    branch = f"bonsai/pr-{pr_number}"
    config = load_workspace_config(workspace_root, state)
    plan = plan_add_files(config, state, workspace_root, branch)
    if plan.worktree_path.exists() and not plan.worktree_path.is_dir():
        raise BonsaiWorkspaceError(f"Branch worktree path is not a directory: {plan.worktree_path}")
    fetch_ref(runner, default_worktree, f"pull/{pr_number}/head:{branch}")
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
        add_existing_worktree(runner, default_worktree, branch, plan.worktree_path)
    _finalize_add(
        runner,
        config=config,
        state_path=state_path,
        workspace_root=workspace_root,
        branch=branch,
        plan=plan,
    )
    return PullRequestWorktreePlan(
        pr_number=pr_number,
        branch=branch,
        title=pr.title,
        url=pr.url,
        state=pr.state,
        read_only=True,
        add_plan=plan,
    )


def _worktree_env(
    config: BonsaiConfig,
    state: BonsaiState,
    workspace_root: Path,
    branch: str,
    worktree: ManagedWorktree,
    worktree_path: Path,
) -> dict[str, str]:
    env_path = worktree_path / ".env.local"
    if not env_path.exists():
        env: dict[str, str] = {}
    else:
        env = parse_env_content(env_path.read_text(encoding="utf-8"))
    env.update(
        standard_bonsai_env(
            config,
            branch,
            worktree.slot,
            worktree_path,
            workspace_root=workspace_root,
            default_branch=state.default_branch,
        )
    )
    return env


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

    if config.commands.preremove:
        run_lifecycle_command(
            runner,
            workspace_root=workspace_root,
            worktree_slug=resolved.worktree.slug,
            kind="preremove",
            command=config.commands.preremove,
            cwd=worktree_path,
            env=_worktree_env(
                config,
                state,
                workspace_root,
                resolved.branch,
                resolved.worktree,
                worktree_path,
            ),
            check=not force,
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
