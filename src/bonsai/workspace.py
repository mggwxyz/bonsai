from __future__ import annotations

from pathlib import Path

from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import BonsaiState, WorkspacePaths


def find_workspace_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for path in (current, *current.parents):
        if (path / ".bonsai" / "state.json").exists() or (path / ".bonsai").is_dir():
            return path

    raise BonsaiWorkspaceError(f"No Bonsai workspace found from {start}")


def workspace_paths(root: Path, state: BonsaiState) -> WorkspacePaths:
    return WorkspacePaths(
        root=root,
        default_worktree=root / state.default_worktree,
        state_file=root / ".bonsai" / "state.json",
        caddyfile=root / "Caddyfile",
        snippets_dir=root / "caddy.d",
    )
