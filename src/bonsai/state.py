from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

from bonsai.models import BonsaiState, ManagedWorktree
from bonsai.registry import register_workspace


def load_state(path: Path) -> BonsaiState:
    raw = json.loads(path.read_text(encoding="utf-8"))
    state = BonsaiState(
        version=int(raw["version"]),
        name=str(raw["name"]),
        default_branch=str(raw["default_branch"]),
        default_worktree=str(raw["default_worktree"]),
        repo_url=str(raw["repo_url"]),
        worktrees={
            branch: ManagedWorktree(
                path=str(data["path"]),
                slug=str(data["slug"]),
                slot=int(data["slot"]),
            )
            for branch, data in raw.get("worktrees", {}).items()
        },
    )
    if path.name == "state.json" and path.parent.name == ".bonsai":
        register_workspace(path.parent.parent, state)
    return state


def save_state(path: Path, state: BonsaiState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(state), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def update_worktree(
    state: BonsaiState,
    branch: str,
    worktree: ManagedWorktree,
) -> BonsaiState:
    worktrees = dict(state.worktrees)
    worktrees[branch] = worktree
    return replace(state, worktrees=worktrees)


def remove_worktree(state: BonsaiState, branch: str) -> BonsaiState:
    worktrees = dict(state.worktrees)
    worktrees.pop(branch, None)
    return replace(state, worktrees=worktrees)
