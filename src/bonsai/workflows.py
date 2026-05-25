from __future__ import annotations

from pathlib import Path

from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import (
    AddFilesPlan,
    BonsaiConfig,
    BonsaiState,
    CloneWorkspacePlan,
    CommandSpec,
    FileWrite,
    ManagedWorktree,
)
from bonsai.ports import allocate_slot
from bonsai.process import Runner
from bonsai.rendering import render_caddy_snippets, render_env_local, render_root_caddyfile
from bonsai.slug import branch_slug
from bonsai.state import update_worktree


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
    slot = allocate_slot(state.worktrees)
    slug = branch_slug(branch)
    if slug == "":
        raise BonsaiWorkspaceError(f"Invalid branch slug: {branch!r}")
    worktree_path = workspace_root / slug
    snippets_dir = workspace_root / snippets_dir_name
    files: list[FileWrite] = [
        FileWrite(
            path=worktree_path / ".env.local",
            content=render_env_local(config, branch, slot, worktree_path),
        )
    ]
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
        updated_state=updated_state,
    )


def write_files(files: tuple[FileWrite, ...]) -> None:
    for file in files:
        file.path.parent.mkdir(parents=True, exist_ok=True)
        file.path.write_text(file.content, encoding="utf-8")


def run_command_specs(runner: Runner, commands: list[CommandSpec]) -> None:
    for command in commands:
        runner.run(list(command.argv), cwd=command.cwd)
