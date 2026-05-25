from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from bonsai.errors import BonsaiCommandError
from bonsai.models import CommandResult, CommandSpec


class Runner(Protocol):
    def run(self, argv: list[str], cwd: Path | None = None, check: bool = True) -> CommandResult:
        ...


class SubprocessRunner:
    def run(self, argv: list[str], cwd: Path | None = None, check: bool = True) -> CommandResult:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        result = CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.returncode != 0:
            command = " ".join(argv)
            raise BonsaiCommandError(
                f"Command failed ({result.returncode}): {command}\n{result.stderr}"
            )
        return result


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[CommandSpec] = []

    def run(self, argv: list[str], cwd: Path | None = None, check: bool = True) -> CommandResult:
        self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
        return CommandResult(returncode=0)
