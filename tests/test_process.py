import io
import os
import sys
import threading
from contextlib import suppress
from pathlib import Path

from rich.console import Console
from rich.text import Text

from bonsai.models import CommandSpec
from bonsai.process import RecordingRunner, Runner, SubprocessRunner, format_command


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


class SignalingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.wrote = threading.Event()

    def write(self, value: str) -> int:
        written = super().write(value)
        if value:
            self.wrote.set()
        return written


class FailingStream(io.StringIO):
    def write(self, value: str) -> int:
        raise RuntimeError(f"stream failed while writing {value!r}")


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def terminate_process(pid: int) -> None:
    with suppress(ProcessLookupError):
        os.kill(pid, 15)


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
    console = Console(file=stderr, force_terminal=False, color_system=None, width=300)
    runner = SubprocessRunner(console=console, stream=stream)
    log_path = tmp_path / "command.log"

    exit_code = runner.run_stream_logged(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ],
        cwd=tmp_path,
        log_path=log_path,
        label="install",
    )

    assert exit_code == 0
    assert "out\n" in stream.getvalue()
    assert "err\n" in stream.getvalue()
    assert "out\n" in log_path.read_text(encoding="utf-8")
    assert "err\n" in log_path.read_text(encoding="utf-8")
    assert "Running install:" in stderr.getvalue()
    assert format_command([sys.executable], cwd=tmp_path).split(" && ")[0] in stderr.getvalue()


def test_subprocess_runner_logged_stream_treats_command_as_literal_text(
    tmp_path: Path,
) -> None:
    stream = io.StringIO()
    stderr = io.StringIO()
    console = Console(
        file=stderr,
        force_terminal=True,
        color_system=None,
        width=120,
    )
    runner = SubprocessRunner(console=console, stream=stream)
    log_path = tmp_path / "literal.log"

    exit_code = runner.run_stream_logged(
        [sys.executable, "-u", "-c", "print('[/]')"],
        log_path=log_path,
        label="literal",
    )

    assert exit_code == 0
    assert stream.getvalue() == "[/]\n"
    assert log_path.read_text(encoding="utf-8") == "[/]\n"
    assert "[/]" in stderr.getvalue()


def test_runner_protocol_includes_logged_stream() -> None:
    annotations = Runner.run_stream_logged.__annotations__

    assert annotations["argv"] == "list[str]"
    assert annotations["cwd"] == "Path | None"
    assert annotations["env"] == "Mapping[str, str] | None"
    assert annotations["log_path"] == "Path | None"
    assert annotations["label"] == "str | None"
    assert annotations["return"] == "int"


def test_subprocess_runner_no_log_delegates_to_run_stream(monkeypatch) -> None:
    runner = SubprocessRunner(
        console=Console(file=io.StringIO(), force_terminal=False, color_system=None)
    )
    calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []

    def fake_run_stream(
        argv: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        calls.append((argv, cwd, env))
        return 17

    monkeypatch.setattr(runner, "run_stream", fake_run_stream)

    exit_code = runner.run_stream_logged(
        ["tool", "arg"],
        cwd=Path("/tmp/repo"),
        env={"BONSAI_TEST": "1"},
        log_path=None,
        label="ignored",
    )

    assert exit_code == 17
    assert calls == [(["tool", "arg"], Path("/tmp/repo"), {"BONSAI_TEST": "1"})]


def test_subprocess_runner_logged_stream_creates_nested_log_directories(
    tmp_path: Path,
) -> None:
    stream = io.StringIO()
    runner = SubprocessRunner(
        console=Console(file=io.StringIO(), force_terminal=False, color_system=None),
        stream=stream,
    )
    log_path = tmp_path / "nested" / "logs" / "command.log"

    exit_code = runner.run_stream_logged(
        [sys.executable, "-u", "-c", "print('ok')"],
        log_path=log_path,
    )

    assert exit_code == 0
    assert log_path.read_text(encoding="utf-8") == "ok\n"


def test_subprocess_runner_logged_stream_and_log_match_exactly(tmp_path: Path) -> None:
    stream = io.StringIO()
    runner = SubprocessRunner(
        console=Console(file=io.StringIO(), force_terminal=False, color_system=None),
        stream=stream,
    )
    log_path = tmp_path / "command.log"

    exit_code = runner.run_stream_logged(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; sys.stdout.write('out\\n'); sys.stderr.write('err\\n')",
        ],
        log_path=log_path,
    )

    assert exit_code == 0
    assert stream.getvalue() == log_path.read_text(encoding="utf-8") == "out\nerr\n"


def test_subprocess_runner_logged_stream_replaces_invalid_utf8(tmp_path: Path) -> None:
    stream = io.StringIO()
    runner = SubprocessRunner(
        console=Console(file=io.StringIO(), force_terminal=False, color_system=None),
        stream=stream,
    )
    log_path = tmp_path / "invalid.log"

    exit_code = runner.run_stream_logged(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; sys.stdout.buffer.write(b'\\xff'); sys.stdout.buffer.flush()",
        ],
        log_path=log_path,
    )

    assert exit_code == 0
    assert stream.getvalue() == log_path.read_text(encoding="utf-8") == "\ufffd"


def test_subprocess_runner_logged_stream_writes_output_before_newline(
    tmp_path: Path,
) -> None:
    stream = SignalingStream()
    runner = SubprocessRunner(
        console=Console(file=io.StringIO(), force_terminal=False, color_system=None),
        stream=stream,
    )
    log_path = tmp_path / "command.log"
    result: list[int] = []
    script = (
        "import sys, time; "
        "sys.stdout.write('partial'); "
        "sys.stdout.flush(); "
        "time.sleep(2)"
    )

    def run_command() -> None:
        result.append(
            runner.run_stream_logged(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    script,
                ],
                log_path=log_path,
            )
        )

    thread = threading.Thread(target=run_command)
    thread.start()
    wrote_before_exit = stream.wrote.wait(timeout=1)
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert wrote_before_exit
    assert result == [0]
    assert stream.getvalue() == log_path.read_text(encoding="utf-8") == "partial"


def test_subprocess_runner_logged_stream_terminates_process_when_writes_fail(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "child.pid"
    runner = SubprocessRunner(
        console=Console(file=io.StringIO(), force_terminal=False, color_system=None),
        stream=FailingStream(),
    )
    script = (
        "import os, pathlib, sys, time; "
        f"pathlib.Path({str(pid_path)!r}).write_text("
        "str(os.getpid()), encoding='utf-8'"
        "); "
        "sys.stdout.write('started\\n'); "
        "sys.stdout.flush(); "
        "time.sleep(10)"
    )

    try:
        try:
            runner.run_stream_logged(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    script,
                ],
                log_path=tmp_path / "command.log",
            )
        except RuntimeError as error:
            assert "stream failed" in str(error)
        else:
            raise AssertionError("stream failure should be re-raised")

        pid = int(pid_path.read_text(encoding="utf-8"))
        assert not process_is_alive(pid)
    finally:
        if pid_path.exists():
            terminate_process(int(pid_path.read_text(encoding="utf-8")))


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
