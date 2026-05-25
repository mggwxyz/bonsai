import io
import sys
from pathlib import Path

from rich.console import Console

from bonsai.process import SubprocessRunner, format_command


def test_format_command_shell_quotes_arguments() -> None:
    command = format_command(["python", "-c", "print(1)"], cwd=Path("/tmp/space dir"))

    assert command == "cd '/tmp/space dir' && python -c 'print(1)'"


def test_format_command_without_cwd_only_renders_command() -> None:
    command = format_command(["git", "status", "--short"])

    assert command == "git status --short"


def test_subprocess_runner_returns_stdout_and_writes_status_to_stderr() -> None:
    stderr = io.StringIO()
    console = Console(
        file=stderr,
        force_terminal=True,
        color_system=None,
        width=120,
    )
    runner = SubprocessRunner(console=console)

    result = runner.run([sys.executable, "-c", "print('ok')"])

    assert result.returncode == 0
    assert result.stdout == "ok\n"
    assert result.stderr == ""
    status_output = stderr.getvalue()
    assert "Running" in status_output
    assert sys.executable in status_output
    assert "-c" in status_output
    assert "print" in status_output


def test_subprocess_runner_skips_status_for_non_terminal_console() -> None:
    stderr = io.StringIO()
    console = Console(file=stderr, force_terminal=False, color_system=None)
    runner = SubprocessRunner(console=console)

    result = runner.run([sys.executable, "-c", "print('ok')"])

    assert result.stdout == "ok\n"
    assert stderr.getvalue() == ""
