from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import ManagedWorktree


@dataclass(frozen=True)
class WorktreeChoice:
    branch: str
    worktree: ManagedWorktree
    path: Path
    kind: str


@dataclass(frozen=True)
class PickerStreams:
    stdin: TextIO = sys.stdin
    stderr: TextIO = sys.stderr


def _normalized(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _matches(query: str, choice: WorktreeChoice) -> bool:
    if query == "":
        return True
    folded_query = query.casefold()
    normalized_query = _normalized(query)
    for alias in (choice.branch, choice.worktree.path, choice.worktree.slug):
        if folded_query in alias.casefold():
            return True
        if normalized_query and normalized_query in _normalized(alias):
            return True
    return False


def _filtered_choices(
    choices: tuple[WorktreeChoice, ...],
    *,
    include_default: bool,
    query: str | None,
) -> tuple[WorktreeChoice, ...]:
    filtered = tuple(choice for choice in choices if include_default or choice.kind != "default")
    if query:
        filtered = tuple(choice for choice in filtered if _matches(query, choice))
    return filtered


def _choice_row(choice: WorktreeChoice) -> str:
    return "\t".join(
        (
            choice.branch,
            choice.worktree.path,
            choice.kind,
            str(choice.worktree.slot),
        )
    )


def _pick_with_fzf(
    choices: tuple[WorktreeChoice, ...],
    *,
    query: str | None,
    environ: Mapping[str, str],
) -> str | None:
    fzf = shutil.which("fzf", path=environ.get("PATH"))
    if fzf is None:
        return None
    argv = [fzf, "--height=40%", "--reverse", "--select-1"]
    if query:
        argv.extend(["--query", query])
    result = subprocess.run(
        argv,
        input="\n".join(_choice_row(choice) for choice in choices) + "\n",
        text=True,
        capture_output=True,
        check=False,
        env=dict(environ),
    )
    if result.returncode != 0:
        raise BonsaiWorkspaceError("Worktree selection cancelled")
    selected = result.stdout.strip().split("\t", maxsplit=1)[0]
    return selected or None


def _write_fallback_choices(
    choices: tuple[WorktreeChoice, ...],
    stderr: TextIO,
) -> None:
    stderr.write("Select a Bonsai worktree:\n")
    for index, choice in enumerate(choices, start=1):
        stderr.write(
            f"  {index}. {choice.branch}  ./{choice.worktree.path}  {choice.kind}\n"
        )
    stderr.write("Worktree number or unique substring: ")
    stderr.flush()


def _pick_with_prompt(
    choices: tuple[WorktreeChoice, ...],
    *,
    streams: PickerStreams,
) -> str:
    _write_fallback_choices(choices, streams.stderr)
    answer = streams.stdin.readline().strip()
    if not answer:
        raise BonsaiWorkspaceError("Worktree selection cancelled")
    if answer.isdigit():
        index = int(answer)
        if 1 <= index <= len(choices):
            return choices[index - 1].branch
        raise BonsaiWorkspaceError(f"Invalid worktree selection: {answer}")

    matches = tuple(choice for choice in choices if _matches(answer, choice))
    if len(matches) == 1:
        return matches[0].branch
    if not matches:
        raise BonsaiWorkspaceError(f"No worktree matches: {answer}")
    labels = ", ".join(choice.branch for choice in matches)
    raise BonsaiWorkspaceError(f"Ambiguous worktree selection {answer!r}: {labels}")


def pick_worktree_branch(
    choices: tuple[WorktreeChoice, ...],
    *,
    include_default: bool = True,
    query: str | None = None,
    streams: PickerStreams | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    streams = streams or PickerStreams()
    environ = environ or {}
    selectable = _filtered_choices(choices, include_default=include_default, query=query)
    if not selectable:
        raise BonsaiWorkspaceError("No Bonsai worktrees are available to select")
    if not streams.stdin.isatty() or not streams.stderr.isatty():
        raise BonsaiWorkspaceError("Worktree selection requires an interactive terminal")

    selected = _pick_with_fzf(selectable, query=query, environ=environ)
    if selected is not None:
        return selected
    return _pick_with_prompt(selectable, streams=streams)
