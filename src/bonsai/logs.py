from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple, cast

from bonsai.errors import BonsaiWorkspaceError

LogKind = Literal[
    "preinstall",
    "install",
    "postinstall",
    "presetup",
    "setup",
    "postsetup",
    "prestart",
    "start",
    "poststart",
]
LOG_KINDS: tuple[str, ...] = (
    "preinstall",
    "install",
    "postinstall",
    "presetup",
    "setup",
    "postsetup",
    "prestart",
    "start",
    "poststart",
)
_LOG_FILENAME_PATTERN = re.compile(
    r"^(?P<timestamp>\d{8}-\d{6})-(?P<kind>[a-z]+)(?:-(?P<suffix>\d+))?\.log$"
)


class _ParsedCommandLogName(NamedTuple):
    timestamp: str
    kind: LogKind
    suffix: int
    name: str


def validate_log_kind(kind: str | None) -> LogKind | None:
    if kind is None:
        return None
    return _require_log_kind(kind)


def command_log_dir(workspace_root: Path, worktree_slug: str) -> Path:
    return workspace_root / ".bonsai" / "logs" / worktree_slug


def next_command_log_path(
    workspace_root: Path,
    worktree_slug: str,
    kind: LogKind,
    now: datetime | None = None,
) -> Path:
    kind = _require_log_kind(kind)
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
    matches = _matching_command_logs(log_dir, kind)
    if not matches:
        filter_text = f" with command {kind}" if kind is not None else ""
        raise BonsaiWorkspaceError(f"No logs found for {worktree_slug}{filter_text}")
    return matches[-1]


def _require_log_kind(kind: str | None) -> LogKind:
    if kind not in LOG_KINDS:
        raise BonsaiWorkspaceError(f"Unsupported log command: {kind}")
    return cast(LogKind, kind)


def _matching_command_logs(log_dir: Path, kind: LogKind | None) -> list[Path]:
    matches: list[tuple[_ParsedCommandLogName, Path]] = []
    for path in log_dir.glob("*.log"):
        if not path.is_file():
            continue
        parsed = _parse_command_log_name(path.name)
        if parsed is None:
            continue
        if kind is not None and parsed.kind != kind:
            continue
        matches.append((parsed, path))
    return [path for _, path in sorted(matches)]


def _parse_command_log_name(name: str) -> _ParsedCommandLogName | None:
    match = _LOG_FILENAME_PATTERN.fullmatch(name)
    if match is None:
        return None

    kind = _coerce_log_kind(match.group("kind"))
    if kind is None:
        return None

    suffix = int(match.group("suffix") or "1")
    return _ParsedCommandLogName(match.group("timestamp"), kind, suffix, name)


def _matches_kind(path: Path, kind: str) -> bool:
    parsed = _parse_command_log_name(path.name)
    return parsed is not None and parsed.kind == kind


def _coerce_log_kind(kind: str) -> LogKind | None:
    if kind not in LOG_KINDS:
        return None
    return cast(LogKind, kind)
