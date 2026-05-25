from typer.testing import CliRunner

from bonsai.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "bonsai 0.1.0" in result.stdout


def test_help_lists_core_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "clone" in result.stdout
    assert "add" in result.stdout
    assert "doctor" in result.stdout


def test_list_command_exists() -> None:
    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0


def test_sync_dry_run_command_exists() -> None:
    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    assert "dry run" in result.stdout.lower()


def test_cleanup_dry_run_command_exists() -> None:
    result = runner.invoke(app, ["cleanup"])

    assert result.exit_code == 0
    assert "dry run" in result.stdout.lower()
