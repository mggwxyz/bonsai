from __future__ import annotations

import codecs
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Protocol, TextIO

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

    def run_stream(
        self,
        argv: list[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> int:
        ...

    def run_stream_logged(
        self,
        argv: list[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        log_path: Path | None = None,
        label: str | None = None,
    ) -> int:
        ...


class SubprocessRunner:
    def __init__(self, console: Console | None = None, stream: TextIO | None = None) -> None:
        self.console = console or Console(stderr=True)
        self.stream = stream or sys.stdout

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

    def run_stream(
        self,
        argv: list[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> int:
        process_env = None
        if env is not None:
            process_env = os.environ.copy()
            process_env.update(env)
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=process_env,
            check=False,
        )
        return completed.returncode

    def run_stream_logged(
        self,
        argv: list[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        log_path: Path | None = None,
        label: str | None = None,
    ) -> int:
        process_env = None
        if env is not None:
            process_env = os.environ.copy()
            process_env.update(env)
        if log_path is None:
            return self.run_stream(argv, cwd=cwd, env=env)

        log_path.parent.mkdir(parents=True, exist_ok=True)
        label_text = f"{label}: " if label else ""
        self.console.print(Text(f"Running {label_text}{format_command(argv, cwd=cwd)}"))
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            decoder = codecs.getincrementaldecoder("utf-8")()
            try:
                if process.stdout is not None:
                    while chunk := process.stdout.read(8192):
                        text = decoder.decode(chunk)
                        if text:
                            self._write_logged_chunk(text, log_file)
                    tail = decoder.decode(b"", final=True)
                    if tail:
                        self._write_logged_chunk(tail, log_file)
                return process.wait()
            except BaseException:
                self._terminate_process(process)
                raise

    def _write_logged_chunk(self, chunk: str, log_file: TextIO) -> None:
        self.stream.write(chunk)
        self.stream.flush()
        log_file.write(chunk)
        log_file.flush()

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            process.wait()
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


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

    def run_stream(
        self,
        argv: list[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> int:
        recorded_env = tuple(sorted(env.items())) if env is not None else ()
        self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
        return 0

    def run_stream_logged(
        self,
        argv: list[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        log_path: Path | None = None,
        label: str | None = None,
    ) -> int:
        _ = label
        recorded_env = tuple(sorted(env.items())) if env is not None else ()
        self.commands.append(
            CommandSpec(
                argv=tuple(argv),
                cwd=cwd,
                env=recorded_env,
                log_path=log_path,
            )
        )
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("", encoding="utf-8")
        return 0
