import io
import sys
from pathlib import Path

from rich.console import Console
from rich.text import Text

from bonsai.models import CommandSpec
from bonsai.process import RecordingRunner, SubprocessRunner, format_command


class StatusContext:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    def __enter__(self) -> None:
        self.entered = True

    def __exit__(self, *args: object) -> None:
        self.exited = True


class TerminalStatusConsole:
    is_terminal = True
    is_dumb_terminal = True

    def __init__(self) -> None:
        self.status_context = StatusContext()
        self.status_calls: list[tuple[object, str]] = []

    def status(self, status: object, *, spinner: str) -> StatusContext:
        self.status_calls.append((status, spinner))
        return self.status_context

    def print(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("terminal consoles should render subprocess status via status()")


def test_format_command_shell_quotes_arguments() -> None:
    command = format_command(["python", "-c", "print(1)"], cwd=Path("/tmp/space dir"))

    assert command == "cd '/tmp/space dir' && python -c 'print(1)'"


def test_format_command_without_cwd_only_renders_command() -> None:
    command = format_command(["git", "status", "--short"])

    assert command == "git status --short"


def test_subprocess_runner_returns_stdout_and_writes_status_to_stderr(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TERM", "xterm")
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


def test_subprocess_runner_status_treats_command_as_literal_text(monkeypatch) -> None:
    monkeypatch.setenv("TERM", "xterm")
    stderr = io.StringIO()
    console = Console(
        file=stderr,
        force_terminal=True,
        color_system=None,
        width=120,
    )
    runner = SubprocessRunner(console=console)

    result = runner.run([sys.executable, "-c", "print('[/]')"])

    assert result.returncode == 0
    assert result.stdout == "[/]\n"
    status_output = stderr.getvalue()
    assert "Running" in status_output
    assert "[/]" in status_output


def test_subprocess_runner_uses_status_for_terminal_console() -> None:
    console = TerminalStatusConsole()
    runner = SubprocessRunner(console=console)

    with runner.status(["git", "status"], cwd=Path("/tmp/repo")):
        assert console.status_context.entered

    assert console.status_context.exited
    assert len(console.status_calls) == 1
    status, spinner = console.status_calls[0]
    assert isinstance(status, Text)
    assert status.plain == "Running cd /tmp/repo && git status"
    assert spinner == "dots"


def test_subprocess_runner_skips_status_for_non_terminal_console() -> None:
    stderr = io.StringIO()
    console = Console(file=stderr, force_terminal=False, color_system=None)
    runner = SubprocessRunner(console=console)

    result = runner.run([sys.executable, "-c", "print('ok')"])

    assert result.stdout == "ok\n"
    assert stderr.getvalue() == ""


def test_subprocess_runner_streams_stdout_and_stderr_to_log_and_stream(
    tmp_path: Path,
) -> None:
    stream = io.StringIO()
    stderr = io.StringIO()
    console = Console(file=stderr, force_terminal=False, color_system=None)
    runner = SubprocessRunner(console=console, stream=stream)
    log_path = tmp_path / "command.log"

    exit_code = runner.run_stream_logged(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ],
        log_path=log_path,
        label="install",
    )

    assert exit_code == 0
    assert "out\n" in stream.getvalue()
    assert "err\n" in stream.getvalue()
    assert "out\n" in log_path.read_text(encoding="utf-8")
    assert "err\n" in log_path.read_text(encoding="utf-8")
    assert "Running install:" in stderr.getvalue()


def test_subprocess_runner_logged_stream_merges_env(tmp_path: Path) -> None:
    stream = io.StringIO()
    runner = SubprocessRunner(
        console=Console(file=io.StringIO(), force_terminal=False, color_system=None),
        stream=stream,
    )

    exit_code = runner.run_stream_logged(
        [sys.executable, "-u", "-c", "import os; print(os.environ['BONSAI_TEST_VALUE'])"],
        env={"BONSAI_TEST_VALUE": "ok"},
        log_path=tmp_path / "env.log",
        label="setup",
    )

    assert exit_code == 0
    assert stream.getvalue() == "ok\n"


def test_recording_runner_records_logged_stream_command(tmp_path: Path) -> None:
    runner = RecordingRunner()
    log_path = tmp_path / "install.log"

    exit_code = runner.run_stream_logged(
        ["yarn", "install"],
        cwd=Path("/tmp/repo"),
        env={"FRONTEND_PORT": "4201"},
        log_path=log_path,
        label="install",
    )

    assert exit_code == 0
    assert runner.commands == [
        CommandSpec(
            argv=("yarn", "install"),
            cwd=Path("/tmp/repo"),
            env=(("FRONTEND_PORT", "4201"),),
            log_path=log_path,
        )
    ]
