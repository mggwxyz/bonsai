from __future__ import annotations

from pathlib import Path

from bonsai.errors import BonsaiWorkspaceError


def find_workspace_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for path in (current, *current.parents):
        if (path / ".bonsai" / "state.json").exists() or (path / ".bonsai").is_dir():
            return path

    raise BonsaiWorkspaceError(f"No Bonsai workspace found from {start}")
