from datetime import datetime
from pathlib import Path

import pytest

from bonsai.errors import BonsaiWorkspaceError
from bonsai.logs import latest_command_log, next_command_log_path, validate_log_kind


def test_next_command_log_path_uses_worktree_slug_kind_and_timestamp(tmp_path: Path) -> None:
    path = next_command_log_path(
        tmp_path,
        "feature-auth",
        "install",
        now=datetime(2026, 5, 26, 14, 30, 12),
    )

    assert path == tmp_path / ".bonsai" / "logs" / "feature-auth" / "20260526-143012-install.log"


def test_next_command_log_path_adds_suffix_on_collision(tmp_path: Path) -> None:
    first = next_command_log_path(
        tmp_path,
        "feature-auth",
        "setup",
        now=datetime(2026, 5, 26, 14, 30, 12),
    )
    first.parent.mkdir(parents=True)
    first.write_text("first\n", encoding="utf-8")

    second = next_command_log_path(
        tmp_path,
        "feature-auth",
        "setup",
        now=datetime(2026, 5, 26, 14, 30, 12),
    )

    assert second == (
        tmp_path
        / ".bonsai"
        / "logs"
        / "feature-auth"
        / "20260526-143012-setup-2.log"
    )


def test_latest_command_log_returns_newest_log_for_worktree(tmp_path: Path) -> None:
    log_dir = tmp_path / ".bonsai" / "logs" / "feature-auth"
    log_dir.mkdir(parents=True)
    older = log_dir / "20260526-143012-install.log"
    newer = log_dir / "20260526-143245-setup.log"
    older.write_text("older\n", encoding="utf-8")
    newer.write_text("newer\n", encoding="utf-8")

    assert latest_command_log(tmp_path, "feature-auth") == newer


def test_latest_command_log_filters_by_kind(tmp_path: Path) -> None:
    log_dir = tmp_path / ".bonsai" / "logs" / "feature-auth"
    log_dir.mkdir(parents=True)
    setup = log_dir / "20260526-143245-setup.log"
    install = log_dir / "20260526-143012-install.log"
    setup.write_text("setup\n", encoding="utf-8")
    install.write_text("install\n", encoding="utf-8")

    assert latest_command_log(tmp_path, "feature-auth", "install") == install


def test_latest_command_log_fails_when_no_matching_log_exists(tmp_path: Path) -> None:
    with pytest.raises(BonsaiWorkspaceError, match="No logs found for feature-auth"):
        latest_command_log(tmp_path, "feature-auth", "start")


def test_validate_log_kind_rejects_unknown_kind() -> None:
    with pytest.raises(BonsaiWorkspaceError, match="Unsupported log command: build"):
        validate_log_kind("build")
