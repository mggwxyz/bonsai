from pathlib import Path

from bonsai.process import format_command


def test_format_command_shell_quotes_arguments() -> None:
    command = format_command(["python", "-c", "print(1)"], cwd=Path("/tmp/space dir"))

    assert command == "cd '/tmp/space dir' && python -c 'print(1)'"


def test_format_command_without_cwd_only_renders_command() -> None:
    command = format_command(["git", "status", "--short"])

    assert command == "git status --short"
