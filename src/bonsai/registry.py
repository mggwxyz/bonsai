from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bonsai.models import BonsaiState


@dataclass(frozen=True)
class WorkspaceRegistryEntry:
    name: str
    root: Path
    last_seen: str


def registry_path() -> Path:
    return Path.home() / ".bonsai" / "workspaces.json"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_raw(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _entry_from_raw(item: dict[str, Any]) -> WorkspaceRegistryEntry | None:
    name = item.get("name")
    root = item.get("root")
    last_seen = item.get("last_seen")
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(root, str) or not root:
        return None
    if not isinstance(last_seen, str) or not last_seen:
        last_seen = ""
    return WorkspaceRegistryEntry(name=name, root=Path(root), last_seen=last_seen)


def _write_entries(entries: tuple[WorkspaceRegistryEntry, ...]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"name": entry.name, "root": str(entry.root), "last_seen": entry.last_seen}
        for entry in entries
    ]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_workspace_registry() -> tuple[WorkspaceRegistryEntry, ...]:
    entries = tuple(
        entry
        for item in _read_raw(registry_path())
        if (entry := _entry_from_raw(item)) is not None
    )
    live = tuple(entry for entry in entries if (entry.root / ".bonsai" / "state.json").exists())
    if live != entries:
        _write_entries(live)
    return live


def register_workspace(workspace_root: Path, state: BonsaiState) -> None:
    workspace_root = workspace_root.resolve()
    existing = {entry.root.resolve(): entry for entry in read_workspace_registry()}
    existing[workspace_root] = WorkspaceRegistryEntry(
        name=state.name,
        root=workspace_root,
        last_seen=_now(),
    )
    _write_entries(tuple(sorted(existing.values(), key=lambda entry: str(entry.root))))
