from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from bonsai.errors import BonsaiWorkspaceError

LogKind = Literal["install", "setup", "start"]
LOG_KINDS: tuple[str, ...] = ("install", "setup", "start")


def validate_log_kind(kind: str | None) -> LogKind | None:
    if kind is None:
        return None
    if kind not in LOG_KINDS:
        raise BonsaiWorkspaceError(f"Unsupported log command: {kind}")
    return kind  # type: ignore[return-value]


def command_log_dir(workspace_root: Path, worktree_slug: str) -> Path:
    return workspace_root / ".bonsai" / "logs" / worktree_slug


def next_command_log_path(
    workspace_root: Path,
    worktree_slug: str,
    kind: LogKind,
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    log_dir = command_log_dir(workspace_root, worktree_slug)
    candidate = log_dir / f"{timestamp}-{kind}.log"
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        candidate = log_dir / f"{timestamp}-{kind}-{suffix}.log"
        if not candidate.exists():
            return candidate
        suffix += 1


def latest_command_log(
    workspace_root: Path,
    worktree_slug: str,
    kind: str | None = None,
) -> Path:
    kind = validate_log_kind(kind)
    log_dir = command_log_dir(workspace_root, worktree_slug)
    if kind is None:
        matches = sorted(
            (path for path in log_dir.glob("*.log") if path.is_file()),
            key=_command_log_sort_key,
        )
    else:
        matches = sorted(
            (
                path
                for path in log_dir.glob(f"*-{kind}*.log")
                if path.is_file() and _matches_kind(path, kind)
            ),
            key=_command_log_sort_key,
        )
    if not matches:
        filter_text = f" with command {kind}" if kind is not None else ""
        raise BonsaiWorkspaceError(f"No logs found for {worktree_slug}{filter_text}")
    return matches[-1]


def _command_log_sort_key(path: Path) -> tuple[str, str, int, str]:
    timestamp, kind_and_suffix = path.stem[:15], path.stem[16:]
    kind, suffix = kind_and_suffix, 1
    if "-" in kind_and_suffix:
        possible_kind, possible_suffix = kind_and_suffix.rsplit("-", maxsplit=1)
        if possible_suffix.isdigit():
            kind, suffix = possible_kind, int(possible_suffix)
    return timestamp, kind, suffix, path.name


def _matches_kind(path: Path, kind: str) -> bool:
    stem = path.stem
    return stem.endswith(f"-{kind}") or f"-{kind}-" in stem
