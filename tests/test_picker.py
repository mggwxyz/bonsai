from pathlib import Path

import pytest

from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import ManagedWorktree
from bonsai.picker import PickerStreams, WorktreeChoice, pick_worktree_branch


class _TTY:
    def __init__(self, input_text: str = "") -> None:
        self._lines = iter(input_text.splitlines(True))
        self.output = ""

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        return next(self._lines, "")

    def write(self, value: str) -> int:
        self.output += value
        return len(value)

    def flush(self) -> None:
        pass


class _NonTTY(_TTY):
    def isatty(self) -> bool:
        return False


def _choices() -> tuple[WorktreeChoice, ...]:
    return (
        WorktreeChoice(
            branch="main",
            worktree=ManagedWorktree(path="main", slug="main", slot=0),
            path=Path("/repo/main"),
            kind="default",
        ),
        WorktreeChoice(
            branch="MA-123-auth",
            worktree=ManagedWorktree(path="ma-123-auth", slug="ma-123-auth", slot=1),
            path=Path("/repo/ma-123-auth"),
            kind="managed",
        ),
        WorktreeChoice(
            branch="MA-124-api",
            worktree=ManagedWorktree(path="ma-124-api", slug="ma-124-api", slot=2),
            path=Path("/repo/ma-124-api"),
            kind="managed",
        ),
    )


def test_pick_worktree_branch_falls_back_to_numbered_prompt() -> None:
    stdin = _TTY("2\n")
    stderr = _TTY()

    selected = pick_worktree_branch(
        _choices(),
        include_default=False,
        streams=PickerStreams(stdin=stdin, stderr=stderr),
        environ={"PATH": ""},
    )

    assert selected == "MA-124-api"
    assert "MA-123-auth" in stderr.output
    assert "MA-124-api" in stderr.output


def test_pick_worktree_branch_accepts_unique_substring() -> None:
    stdin = _TTY("api\n")
    stderr = _TTY()

    selected = pick_worktree_branch(
        _choices(),
        include_default=True,
        streams=PickerStreams(stdin=stdin, stderr=stderr),
        environ={"PATH": ""},
    )

    assert selected == "MA-124-api"


def test_pick_worktree_branch_rejects_non_tty() -> None:
    with pytest.raises(BonsaiWorkspaceError, match="requires an interactive terminal"):
        pick_worktree_branch(
            _choices(),
            include_default=False,
            streams=PickerStreams(stdin=_NonTTY(), stderr=_TTY()),
            environ={"PATH": ""},
        )
