from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from bonsai.errors import BonsaiCommandError
from bonsai.models import CommandResult, CommandSpec


def format_command(argv: list[str], cwd: Path | None = None) -> str:
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
            command = " ".join(argv)
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
