from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.text import Text

from bonsai.errors import BonsaiCommandError
from bonsai.models import CommandResult, CommandSpec


def format_command(argv: Sequence[str], cwd: Path | None = None) -> str:
    rendered = " ".join(shlex.quote(arg) for arg in argv)
    if cwd is None:
        return rendered
    return f"cd {shlex.quote(str(cwd))} && {rendered}"


class Runner(Protocol):
    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        ...


class SubprocessRunner:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(stderr=True)

    def status(self, argv: Sequence[str], cwd: Path | None):
        if not self.console.is_terminal:
            return nullcontext()
        return self.console.status(
            Text(f"Running {format_command(argv, cwd=cwd)}"),
            spinner="dots",
        )

    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        process_env = None
        if env is not None:
            process_env = os.environ.copy()
            process_env.update(env)
        with self.status(argv, cwd):
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=process_env,
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
            command = format_command(argv, cwd=cwd)
            raise BonsaiCommandError(
                f"Command failed ({result.returncode}): {command}\n{result.stderr}"
            )
        return result


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[CommandSpec] = []

    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        recorded_env = tuple(sorted(env.items())) if env is not None else ()
        self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
        return CommandResult(returncode=0)
