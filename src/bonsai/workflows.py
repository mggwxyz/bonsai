from __future__ import annotations

from pathlib import Path

from bonsai.models import (
    AddFilesPlan,
    BonsaiConfig,
    BonsaiState,
    CloneWorkspacePlan,
    FileWrite,
    ManagedWorktree,
)
from bonsai.ports import allocate_slot
from bonsai.rendering import render_caddy_snippets, render_env_local, render_root_caddyfile
from bonsai.slug import branch_slug
from bonsai.state import update_worktree


def plan_clone_workspace(
    git_url: str,
    name: str,
    default_branch: str,
    config: BonsaiConfig,
    parent: Path,
) -> CloneWorkspacePlan:
    workspace_root = parent / name
    default_worktree = workspace_root / default_branch
    snippets_dir = workspace_root / config.caddy.snippets_dir
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
            path=workspace_root / config.caddy.root_caddyfile,
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
    slot = allocate_slot(state.worktrees)
    slug = branch_slug(branch)
    worktree_path = workspace_root / branch
    snippets_dir = workspace_root / config.caddy.snippets_dir
    files: list[FileWrite] = [
        FileWrite(
            path=worktree_path / ".env.local",
            content=render_env_local(config, branch, slot, worktree_path),
        )
    ]
    for service_name, content in render_caddy_snippets(config, branch, slot, worktree_path).items():
        files.append(FileWrite(path=snippets_dir / f"{slug}-{service_name}.caddy", content=content))

    updated_state = update_worktree(
        state,
        branch,
        ManagedWorktree(path=branch, slug=slug, slot=slot),
    )
    return AddFilesPlan(
        branch=branch,
        worktree_path=worktree_path,
        slot=slot,
        files=tuple(files),
        updated_state=updated_state,
    )
