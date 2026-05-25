from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from bonsai import cli
from bonsai.errors import BonsaiWorkspaceError

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert "bonsai 0.1.1" in result.stdout


def test_help_lists_core_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "clone" in result.stdout
    assert "add" in result.stdout
    assert "doctor" in result.stdout


def test_clone_executes_workflow(monkeypatch) -> None:
    calls = []

    class FakeRunner:
        pass

    def fake_execute_clone(runner, git_url: str, name: str, parent: Path):
        calls.append((runner, git_url, name, parent))
        return SimpleNamespace(
            workspace_root=parent / name,
            default_worktree=parent / name / "main",
        )

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "execute_clone", fake_execute_clone, raising=False)

    with runner.isolated_filesystem():
        parent = Path.cwd()
        result = runner.invoke(
            cli.app,
            ["clone", "https://github.com/quiller-ai/authentic", "bonsai-authentic"],
        )

    assert result.exit_code == 0
    assert len(calls) == 1
    runner_instance, git_url, name, parent_arg = calls[0]
    assert isinstance(runner_instance, FakeRunner)
    assert git_url == "https://github.com/quiller-ai/authentic"
    assert name == "bonsai-authentic"
    assert parent_arg == parent
    assert "Clone workflow ready" not in result.stdout


def test_clone_reports_workflow_errors(monkeypatch) -> None:
    def fake_execute_clone(_runner, _git_url: str, _name: str, _parent: Path):
        raise BonsaiWorkspaceError("Target workspace already exists")

    monkeypatch.setattr(cli, "execute_clone", fake_execute_clone, raising=False)

    result = runner.invoke(cli.app, ["clone", "https://github.com/org/repo", "repo"])

    assert result.exit_code == 1
    assert "Error: Target workspace already exists" in result.stdout


def test_add_executes_workflow(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "bonsai-authentic"
    calls = []

    class FakeRunner:
        pass

    def fake_find_workspace_root(path: Path) -> Path:
        calls.append(("find", path))
        return workspace_root

    def fake_execute_add(runner, branch: str, root: Path):
        calls.append(("add", runner, branch, root))
        return SimpleNamespace(worktree_path=root / "ma-123-test", slot=1)

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "find_workspace_root", fake_find_workspace_root)
    monkeypatch.setattr(cli, "execute_add", fake_execute_add, raising=False)

    with runner.isolated_filesystem():
        current = Path.cwd()
        result = runner.invoke(cli.app, ["add", "MA-123-test"])

    assert result.exit_code == 0
    assert calls[0] == ("find", current)
    assert calls[1][0] == "add"
    assert isinstance(calls[1][1], FakeRunner)
    assert calls[1][2:] == ("MA-123-test", workspace_root)
    assert "Add workflow ready" not in result.stdout


def test_list_command_exists() -> None:
    with runner.isolated_filesystem():
        Path(".bonsai").mkdir()
        result = runner.invoke(cli.app, ["list"])

    assert result.exit_code == 0


def test_sync_dry_run_command_exists() -> None:
    result = runner.invoke(cli.app, ["sync"])

    assert result.exit_code == 0
    assert "dry run" in result.stdout.lower()


def test_cleanup_dry_run_command_exists() -> None:
    result = runner.invoke(cli.app, ["cleanup"])

    assert result.exit_code == 0
    assert "dry run" in result.stdout.lower()


def test_doctor_command_exists() -> None:
    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "doctor" in result.stdout.lower()
