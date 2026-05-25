from __future__ import annotations

import shlex
from pathlib import Path

from bonsai.config import load_config
from bonsai.errors import BonsaiWorkspaceError
from bonsai.git import (
    add_existing_worktree,
    add_new_worktree,
    clone_default_branch,
    current_branch,
    discover_default_branch,
    fetch_origin,
    is_git_worktree,
    remote_branch_exists,
)
from bonsai.models import (
    AddFilesPlan,
    BonsaiConfig,
    BonsaiState,
    CloneWorkspacePlan,
    CommandSpec,
    FileSymlink,
    FileWrite,
    ManagedWorktree,
)
from bonsai.ports import allocate_slot
from bonsai.process import Runner
from bonsai.rendering import render_caddy_snippets, render_env_local, render_root_caddyfile
from bonsai.slug import branch_slug
from bonsai.state import load_state, save_state, update_worktree


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
) -> CloneWorkspacePlan:
    safe_name = _safe_path_segment(name, "workspace name")
    workspace_root = parent / safe_name
    if workspace_root.exists():
        raise BonsaiWorkspaceError(f"Target workspace already exists: {workspace_root}")

    default_branch = discover_default_branch(runner, git_url)
    default_worktree = workspace_root / default_branch
    clone_default_branch(runner, git_url, default_branch, default_worktree)
    config = load_config(default_worktree / ".bonsai.toml")
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
    return plan
