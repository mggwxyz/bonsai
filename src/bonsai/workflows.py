from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path

from bonsai.config import load_config
from bonsai.errors import BonsaiConfigError, BonsaiWorkspaceError
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
from bonsai.models import (
    AddFilesPlan,
    BonsaiConfig,
    BonsaiState,
    CheckoutWorktreePlan,
    CloneWorkspacePlan,
    CommandSpec,
    FileSymlink,
    FileWrite,
    ManagedWorktree,
    OpenUrlPlan,
    RemoveWorktreePlan,
    ResolvedWorktree,
)
from bonsai.ports import allocate_slot
from bonsai.process import Runner
from bonsai.rendering import (
    render_caddy_snippets,
    render_env_local,
    render_root_caddyfile,
    template_values,
)
from bonsai.slug import branch_slug
from bonsai.state import load_state, remove_worktree, save_state, update_worktree
from bonsai.templates import render_template

ConfigInitializer = Callable[[Path, str, str, Path], None]


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
    snippets_dir_name = _safe_path_segment(config.caddy.snippets_dir, "caddy snippets_dir")
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
    snippets_dir = workspace_root / snippets_dir_name
    default_worktree_path = workspace_root / state.default_worktree
    files: list[FileWrite] = [
        FileWrite(
            path=worktree_path / ".env.local",
            content=render_env_local(config, branch, slot, worktree_path),
        )
    ]
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
    for service_name, content in render_caddy_snippets(config, branch, slot, worktree_path).items():
        service_name = _safe_path_segment(service_name, "service name")
        files.append(FileWrite(path=snippets_dir / f"{slug}-{service_name}.caddy", content=content))

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


def plan_open_url(workspace_root: Path, current_path: Path) -> OpenUrlPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    default_worktree = workspace_root / state.default_worktree
    config = load_config(default_worktree / ".bonsai.toml")
    branch, worktree, worktree_path = _resolve_current_worktree(state, workspace_root, current_path)

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
    rendered = " ".join(shlex.quote(arg) for arg in command.argv)
    if command.cwd is None:
        return rendered
    return f"cd {shlex.quote(str(command.cwd))} && {rendered}"


def run_command_specs(runner: Runner, commands: list[CommandSpec]) -> None:
    for command in commands:
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
    config_path = default_worktree / ".bonsai.toml"
    if not config_path.exists() and config_initializer is not None:
        config_initializer(config_path, safe_name, default_branch, default_worktree)
    config = load_config(config_path)
    plan = plan_clone_workspace(git_url, safe_name, default_branch, config, parent)
    write_files(plan.files)
    save_state(workspace_root / ".bonsai" / "state.json", plan.state)
    return plan


def execute_add(
    runner: Runner,
    branch: str,
    workspace_root: Path,
) -> AddFilesPlan:
    state_path = workspace_root / ".bonsai" / "state.json"
    state = load_state(state_path)
    default_worktree = workspace_root / state.default_worktree
    config = load_config(default_worktree / ".bonsai.toml")
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
    if config.commands.install:
        runner.run(shlex.split(config.commands.install), cwd=plan.worktree_path)
    if config.commands.setup:
        runner.run(shlex.split(config.commands.setup), cwd=plan.worktree_path)
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
    config = load_config(default_worktree / ".bonsai.toml")
    if not force and worktree_has_changes(runner, worktree_path):
        raise BonsaiWorkspaceError(
            f"Worktree has uncommitted changes: {worktree_path}. Use --force to remove it."
        )

    git_remove_worktree(runner, default_worktree, worktree_path, force=force)
    removed_snippets = _remove_generated_snippets(workspace_root, config, resolved.worktree.slug)
    updated_state = remove_worktree(state, resolved.branch)
    save_state(state_path, updated_state)
    return RemoveWorktreePlan(
        branch=resolved.branch,
        worktree_path=worktree_path,
        removed_snippets=removed_snippets,
        updated_state=updated_state,
    )
