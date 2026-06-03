import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from rich.console import Console
from test_config import VALID_CONFIG, write_config
from typer.main import get_command
from typer.testing import CliRunner

from bonsai import cli
from bonsai.config import load_config
from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import (
    BonsaiState,
    CaddySetupResult,
    DoctorCheck,
    DoctorReport,
    ManagedWorktree,
    OpenUrlPlan,
    PortOwner,
)
from bonsai.state import save_state

runner = CliRunner()


def write_checkout_workspace(root: Path) -> None:
    (root / "main").mkdir(parents=True)
    (root / "ma-123-test").mkdir()
    save_state(
        root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@example.com:org/repo.git",
            worktrees={
                "MA-123-test": ManagedWorktree(
                    path="ma-123-test",
                    slug="ma-123-test",
                    slot=1,
                )
            },
        ),
    )


def test_version_flag_prints_version() -> None:
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert "bonsai 0.2.18" in result.stdout


def test_help_lists_core_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "clone" in result.stdout
    assert "start-here" in result.stdout
    assert "add" in result.stdout
    assert "remove" in result.stdout
    assert "move" in result.stdout
    assert "checkout" in result.stdout
    assert "open" in result.stdout
    assert "shell-init" in result.stdout
    assert "install-shell" in result.stdout
    assert "init" in result.stdout
    assert "doctor" in result.stdout
    assert "agent-guide" not in result.stdout
    assert "context" in result.stdout
    assert "logs" in result.stdout
    assert "status" in result.stdout
    assert "repair" in result.stdout
    assert "repair-ports" in result.stdout
    assert "ports" in result.stdout
    assert "urls" in result.stdout
    assert "ps" in result.stdout
    assert "stop" in result.stdout
    assert "restart" in result.stdout
    assert "up" in result.stdout
    assert "down" in result.stdout

    repair_help = runner.invoke(cli.app, ["repair", "--help"])
    assert repair_help.exit_code == 0


def test_agent_guide_command_is_not_exposed() -> None:
    result = runner.invoke(cli.app, ["agent-guide"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_clone_executes_workflow(monkeypatch) -> None:
    calls = []

    class FakeRunner:
        pass

    def fake_execute_clone(
        runner,
        git_url: str,
        name: str,
        parent: Path,
        config_initializer=None,
    ):
        calls.append((runner, git_url, name, parent, config_initializer))
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
    runner_instance, git_url, name, parent_arg, config_initializer = calls[0]
    assert isinstance(runner_instance, FakeRunner)
    assert git_url == "https://github.com/quiller-ai/authentic"
    assert name == "bonsai-authentic"
    assert parent_arg == parent
    assert callable(config_initializer)
    assert "Clone workflow ready" not in result.stdout


def test_clone_no_interactive_disables_guided_config(monkeypatch) -> None:
    calls = []

    def fake_execute_clone(
        _runner,
        _git_url: str,
        _name: str,
        _parent: Path,
        config_initializer=None,
    ):
        calls.append(config_initializer)
        return SimpleNamespace(
            workspace_root=Path("/workspace/repo"),
            default_worktree=Path("/workspace/repo/main"),
        )

    monkeypatch.setattr(cli, "execute_clone", fake_execute_clone, raising=False)

    result = runner.invoke(
        cli.app,
        ["clone", "https://github.com/org/repo", "repo", "--no-interactive"],
    )

    assert result.exit_code == 0
    assert calls == [None]


def test_clone_reports_workflow_errors(monkeypatch) -> None:
    def fake_execute_clone(
        _runner,
        _git_url: str,
        _name: str,
        _parent: Path,
        config_initializer=None,
    ):
        raise BonsaiWorkspaceError("Target workspace already exists")

    monkeypatch.setattr(cli, "execute_clone", fake_execute_clone, raising=False)

    result = runner.invoke(cli.app, ["clone", "https://github.com/org/repo", "repo"])

    assert result.exit_code == 1
    assert "Error: Target workspace already exists" in result.stdout


def test_init_writes_guided_config(monkeypatch) -> None:
    calls = []

    class FakeRunner:
        pass

    def fake_current_branch(runner, repo: Path) -> str:
        calls.append(("branch", runner, repo))
        return "main"

    def fake_write_guided_config(
        config_path: Path,
        repo_path: Path,
        fallback_name: str,
        base_branch: str,
        force: bool = False,
    ) -> Path:
        calls.append(("write", config_path, repo_path, fallback_name, base_branch, force))
        config_path.write_text('name = "repo"\n', encoding="utf-8")
        return config_path

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "current_branch", fake_current_branch, raising=False)
    monkeypatch.setattr(cli, "write_guided_config", fake_write_guided_config, raising=False)

    with runner.isolated_filesystem():
        repo = Path.cwd()
        result = runner.invoke(cli.app, ["init"])

    assert result.exit_code == 0
    assert calls[0][0] == "branch"
    assert isinstance(calls[0][1], FakeRunner)
    assert calls[0][2] == repo
    assert calls[1] == ("write", repo / ".bonsai.toml", repo, repo.name, "main", False)
    assert "Created" in result.stdout


def test_init_writes_guided_config_to_workspace_root_when_managed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = []
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@example.com:org/repo.git",
            worktrees={},
        ),
    )

    monkeypatch.chdir(default_worktree)
    monkeypatch.setattr(cli, "current_branch", lambda _runner, _repo: "main", raising=False)

    def fake_write_guided_config(
        config_path: Path,
        repo_path: Path,
        fallback_name: str,
        base_branch: str,
        force: bool = False,
    ) -> Path:
        calls.append(("write", config_path, repo_path, fallback_name, base_branch, force))
        config_path.write_text('name = "repo"\n', encoding="utf-8")
        return config_path

    monkeypatch.setattr(cli, "write_guided_config", fake_write_guided_config, raising=False)

    result = runner.invoke(cli.app, ["init"])

    assert result.exit_code == 0
    assert calls == [
        ("write", workspace_root / ".bonsai.toml", default_worktree, "authentic", "main", False)
    ]


def test_init_from_workspace_root_uses_default_worktree_for_project_detection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    branch_calls = []
    write_calls = []
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@example.com:org/repo.git",
            worktrees={},
        ),
    )

    monkeypatch.chdir(workspace_root)

    def fake_current_branch(_runner, repo: Path) -> str:
        branch_calls.append(repo)
        return "main"

    def fake_write_guided_config(
        config_path: Path,
        repo_path: Path,
        fallback_name: str,
        base_branch: str,
        force: bool = False,
    ) -> Path:
        write_calls.append((config_path, repo_path, fallback_name, base_branch, force))
        config_path.write_text('name = "repo"\n', encoding="utf-8")
        return config_path

    monkeypatch.setattr(cli, "current_branch", fake_current_branch, raising=False)
    monkeypatch.setattr(cli, "write_guided_config", fake_write_guided_config, raising=False)

    result = runner.invoke(cli.app, ["init"])

    assert result.exit_code == 0
    assert branch_calls == [default_worktree]
    assert write_calls == [
        (workspace_root / ".bonsai.toml", default_worktree, "authentic", "main", False)
    ]


def test_init_force_allows_existing_config(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(cli, "current_branch", lambda _runner, _repo: "main", raising=False)

    def fake_write_guided_config(
        config_path: Path,
        repo_path: Path,
        fallback_name: str,
        base_branch: str,
        force: bool = False,
    ) -> Path:
        _ = (repo_path, fallback_name, base_branch)
        calls.append(force)
        return config_path

    monkeypatch.setattr(cli, "write_guided_config", fake_write_guided_config, raising=False)

    result = runner.invoke(cli.app, ["init", "--force"])

    assert result.exit_code == 0
    assert calls == [True]


def test_init_adopts_existing_config_without_overwriting(monkeypatch, tmp_path: Path) -> None:
    calls = []
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    monkeypatch.chdir(default_worktree)

    class FakeRunner:
        pass

    def fake_execute_init(runner, checkout_path: Path):
        calls.append((runner, checkout_path))
        return SimpleNamespace(
            workspace_root=workspace_root,
            default_worktree=default_worktree,
        )

    def fail_write_guided_config(*_args, **_kwargs):
        raise AssertionError("existing config should be adopted, not overwritten")

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "execute_init", fake_execute_init, raising=False)
    monkeypatch.setattr(cli, "write_guided_config", fail_write_guided_config, raising=False)

    result = runner.invoke(cli.app, ["init"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert isinstance(calls[0][0], FakeRunner)
    assert calls[0][1] == default_worktree
    assert "Initialized workspace" in result.stdout
    assert str(workspace_root) in result.stdout


def test_init_reconciles_managed_workspace_with_repo_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = []
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@example.com:org/repo.git",
            worktrees={},
        ),
    )
    monkeypatch.chdir(default_worktree)

    class FakeRunner:
        pass

    def fake_execute_init(runner, checkout_path: Path):
        calls.append((runner, checkout_path))
        return SimpleNamespace(
            workspace_root=workspace_root,
            default_worktree=default_worktree,
        )

    def fail_write_guided_config(*_args, **_kwargs):
        raise AssertionError("existing repo config should be reconciled, not prompted")

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "execute_init", fake_execute_init, raising=False)
    monkeypatch.setattr(cli, "write_guided_config", fail_write_guided_config, raising=False)

    result = runner.invoke(cli.app, ["init"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert isinstance(calls[0][0], FakeRunner)
    assert calls[0][1] == default_worktree
    assert "Initialized workspace" in result.stdout


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


def test_resolve_editor_command_prefers_visual(monkeypatch) -> None:
    monkeypatch.setenv("VISUAL", "code --reuse-window")
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/code")

    assert cli._resolve_editor_command() == ["code", "--reuse-window"]


def test_resolve_editor_command_uses_editor_when_visual_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/code")

    assert cli._resolve_editor_command() == ["vim"]


def test_resolve_editor_command_falls_back_to_code(monkeypatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(
        cli.shutil,
        "which",
        lambda name, path=None: "/usr/local/bin/code" if name == "code" else None,
    )

    assert cli._resolve_editor_command() == ["/usr/local/bin/code"]


def test_resolve_editor_command_uses_environ_path_for_code_fallback(monkeypatch) -> None:
    calls = []

    def fake_which(name: str, path: str | None = None) -> str | None:
        calls.append((name, path))
        if name == "code" and path == "/custom/bin":
            return "/custom/bin/code"
        return None

    monkeypatch.setattr(cli.shutil, "which", fake_which)

    assert cli._resolve_editor_command({"PATH": "/custom/bin"}) == ["/custom/bin/code"]
    assert calls == [("code", "/custom/bin")]


def test_resolve_editor_command_rejects_empty_executable(monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _name, path=None: None)

    with pytest.raises(BonsaiWorkspaceError, match="Invalid editor command"):
        cli._resolve_editor_command({"VISUAL": "''", "PATH": ""})


def test_resolve_editor_command_rejects_malformed_command(monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _name, path=None: None)

    with pytest.raises(BonsaiWorkspaceError, match="Invalid editor command"):
        cli._resolve_editor_command({"VISUAL": "'code", "PATH": ""})


def test_resolve_editor_command_fails_when_no_editor_is_available(monkeypatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda _name, path=None: None)

    with pytest.raises(BonsaiWorkspaceError, match="No editor configured"):
        cli._resolve_editor_command()


def test_open_editor_appends_worktree_path(monkeypatch, tmp_path: Path) -> None:
    calls = []
    worktree_path = tmp_path / "feature"

    monkeypatch.setenv("VISUAL", "code --reuse-window")
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)

    def fake_run(argv, check: bool = False):
        calls.append((tuple(argv), check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    cli._open_editor(worktree_path)

    assert calls == [
        (
            ("code", "--reuse-window", str(worktree_path)),
            False,
        )
    ]


def test_open_editor_reports_nonzero_exit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VISUAL", "code")
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda _argv, check=False: SimpleNamespace(returncode=2),
    )

    with pytest.raises(BonsaiWorkspaceError, match="Editor exited with code 2"):
        cli._open_editor(tmp_path / "feature")


def test_open_editor_reports_os_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VISUAL", "code")
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)

    def fail_run(_argv, check: bool = False):
        _ = check
        raise OSError("launch failed")

    monkeypatch.setattr(cli.subprocess, "run", fail_run)

    with pytest.raises(BonsaiWorkspaceError, match="Failed to open editor"):
        cli._open_editor(tmp_path / "feature")


def test_add_editor_flag_opens_prepared_worktree(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "bonsai-authentic"
    calls = []

    class FakeRunner:
        pass

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: workspace_root)
    monkeypatch.setattr(
        cli,
        "execute_add",
        lambda _runner, _branch, root: SimpleNamespace(
            worktree_path=root / "feature",
            slot=1,
        ),
        raising=False,
    )
    monkeypatch.setattr(cli, "_open_editor", lambda path: calls.append(("editor", path)))

    result = runner.invoke(cli.app, ["add", "feature", "--editor"])

    assert result.exit_code == 0
    assert calls == [("editor", workspace_root / "feature")]


def test_add_base_branch_flag_overrides_creation_base(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "bonsai-authentic"
    calls = []

    class FakeRunner:
        pass

    def fake_execute_add(
        runner,
        branch: str,
        root: Path,
        base_branch: str | None = None,
    ):
        calls.append((isinstance(runner, FakeRunner), branch, root, base_branch))
        return SimpleNamespace(worktree_path=root / "feature", slot=1)

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: workspace_root)
    monkeypatch.setattr(cli, "execute_add", fake_execute_add, raising=False)

    result = runner.invoke(cli.app, ["add", "feature", "--base-branch", "develop"])

    assert result.exit_code == 0
    assert calls == [(True, "feature", workspace_root, "develop")]


def test_add_open_flag_opens_named_worktree_url(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "bonsai-authentic"
    calls = []

    class FakeRunner:
        pass

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: workspace_root)
    monkeypatch.setattr(
        cli,
        "execute_add",
        lambda _runner, _branch, root: SimpleNamespace(
            worktree_path=root / "feature",
            slot=1,
        ),
        raising=False,
    )

    def fake_open_primary_url(root: Path, name: str) -> None:
        calls.append(("open", root, name))

    monkeypatch.setattr(cli, "_open_primary_url", fake_open_primary_url)

    result = runner.invoke(cli.app, ["add", "feature", "--open"])

    assert result.exit_code == 0
    assert calls == [("open", workspace_root, "feature")]


def test_add_post_actions_run_editor_browser_then_start(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "bonsai-authentic"
    current_path = tmp_path / "current"
    calls = []
    current_path.mkdir()

    class FakeRunner:
        pass

    def fake_execute_add(runner, branch: str, root: Path):
        calls.append(("add", isinstance(runner, FakeRunner), branch, root))
        return SimpleNamespace(worktree_path=root / "feature", slot=1)

    def fake_execute_start(runner, root: Path, branch: str | None, current: Path) -> int:
        calls.append(("start", isinstance(runner, FakeRunner), root, branch, current))
        return 7

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: workspace_root)
    monkeypatch.setattr(cli, "execute_add", fake_execute_add, raising=False)
    monkeypatch.setattr(cli, "execute_start", fake_execute_start, raising=False)
    monkeypatch.setattr(cli, "_open_editor", lambda path: calls.append(("editor", path)))
    monkeypatch.setattr(
        cli,
        "_open_primary_url",
        lambda root, name: calls.append(("open", root, name)),
    )
    monkeypatch.chdir(current_path)

    result = runner.invoke(cli.app, ["add", "feature", "--editor", "--open", "--start"])

    assert result.exit_code == 7
    assert calls == [
        ("add", True, "feature", workspace_root),
        ("editor", workspace_root / "feature"),
        ("open", workspace_root, "feature"),
        ("start", True, workspace_root, "feature", current_path),
    ]


def test_add_start_flag_exits_with_start_exit_code(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "bonsai-authentic"

    class FakeRunner:
        pass

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: workspace_root)
    monkeypatch.setattr(
        cli,
        "execute_add",
        lambda _runner, _branch, root: SimpleNamespace(
            worktree_path=root / "feature",
            slot=1,
        ),
        raising=False,
    )
    monkeypatch.setattr(cli, "execute_start", lambda _runner, _root, _branch, _current: 9)

    result = runner.invoke(cli.app, ["add", "feature", "--start"])

    assert result.exit_code == 9
    assert "Starting feature" in result.stdout


def test_open_primary_url_uses_named_url_plan(monkeypatch, tmp_path: Path) -> None:
    calls = []
    workspace_root = tmp_path / "bonsai-authentic"

    plan = SimpleNamespace(
        url="https://feature.authentic.localhost",
        port=4201,
        via="caddy",
        branch="feature",
    )

    def fake_plan_open_url_for_worktree(root: Path, name: str):
        calls.append(("plan", root, name))
        return plan

    monkeypatch.setattr(
        cli,
        "plan_open_url_for_worktree",
        fake_plan_open_url_for_worktree,
        raising=False,
    )
    monkeypatch.setattr(cli, "resolve_open_target", lambda value: value, raising=False)
    monkeypatch.setattr(cli, "url_liveness_ok", lambda _value: True, raising=False)
    monkeypatch.setattr(
        cli.webbrowser,
        "open",
        lambda url: calls.append(("browser", url)) or True,
    )

    cli._open_primary_url(workspace_root, "feature")

    assert calls == [
        ("plan", workspace_root, "feature"),
        ("browser", "https://feature.authentic.localhost"),
    ]


def test_remove_executes_workflow(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "bonsai-authentic"
    calls = []

    class FakeRunner:
        pass

    def fake_find_workspace_root(path: Path) -> Path:
        calls.append(("find", path))
        return workspace_root

    def fake_execute_remove(runner, name: str, root: Path, force: bool = False):
        calls.append(("remove", runner, name, root, force))
        return SimpleNamespace(worktree_path=root / "feature", branch="feature")

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "find_workspace_root", fake_find_workspace_root)
    monkeypatch.setattr(cli, "execute_remove", fake_execute_remove, raising=False)

    with runner.isolated_filesystem():
        current = Path.cwd()
        result = runner.invoke(cli.app, ["remove", "feature"])

    assert result.exit_code == 0
    assert calls[0] == ("find", current)
    assert calls[1][0] == "remove"
    assert isinstance(calls[1][1], FakeRunner)
    assert calls[1][2:] == ("feature", workspace_root, False)
    assert "Removed worktree" in result.stdout


def test_remove_reports_compose_teardown(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_remove(_runner, name: str, root: Path, force: bool = False):
        return SimpleNamespace(
            worktree_path=root / "feature",
            branch=name,
            compose_project_name="authentic-feature",
        )

    monkeypatch.setattr(cli, "execute_remove", fake_execute_remove, raising=False)

    result = runner.invoke(cli.app, ["remove", "feature"])

    assert result.exit_code == 0
    assert "compose down authentic-feature" in result.stdout
    assert "Removed worktree" in result.stdout


def test_remove_force_passes_force(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_remove(_runner, name: str, root: Path, force: bool = False):
        calls.append((name, root, force))
        return SimpleNamespace(worktree_path=root / "feature", branch="feature")

    monkeypatch.setattr(cli, "execute_remove", fake_execute_remove, raising=False)

    result = runner.invoke(cli.app, ["remove", "feature", "--force"])

    assert result.exit_code == 0
    assert calls == [("feature", tmp_path, True)]


def test_move_executes_workflow(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "bonsai-authentic"
    calls = []

    class FakeRunner:
        pass

    def fake_find_workspace_root(path: Path) -> Path:
        calls.append(("find", path))
        return workspace_root

    def fake_execute_move(runner, name: str, new_folder: str, root: Path):
        calls.append(("move", runner, name, new_folder, root))
        return SimpleNamespace(
            old_worktree_path=root / "mb-123-auth",
            new_worktree_path=root / "MB-123-auth",
        )

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)
    monkeypatch.setattr(cli, "find_workspace_root", fake_find_workspace_root)
    monkeypatch.setattr(cli, "execute_move", fake_execute_move, raising=False)

    with runner.isolated_filesystem():
        current = Path.cwd()
        result = runner.invoke(cli.app, ["move", "MB-123-auth", "MB-123-auth"])

    assert result.exit_code == 0
    assert calls[0] == ("find", current)
    assert calls[1][0] == "move"
    assert isinstance(calls[1][1], FakeRunner)
    assert calls[1][2:] == ("MB-123-auth", "MB-123-auth", workspace_root)
    assert "Moved worktree:" in result.stdout
    assert "mb-123-auth" in result.stdout
    assert "MB-123-auth" in result.stdout


def test_checkout_path_resolves_worktree_by_branch(monkeypatch, tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["checkout", "--path", "MA-123-test"])

    assert result.exit_code == 0
    assert Path(result.stdout.strip()).samefile(tmp_path / "ma-123-test")


def test_checkout_path_resolves_worktree_by_path(monkeypatch, tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["checkout", "--path", "ma-123-test"])

    assert result.exit_code == 0
    assert Path(result.stdout.strip()).samefile(tmp_path / "ma-123-test")


def test_checkout_path_reports_checkout_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_checkout(_runner, name: str, _root: Path):
        raise BonsaiWorkspaceError(f"Unable to prepare worktree: {name}")

    monkeypatch.setattr(cli, "execute_checkout", fake_execute_checkout, raising=False)

    result = runner.invoke(cli.app, ["checkout", "--path", "missing"])

    assert result.exit_code == 1
    assert "Error: Unable to prepare worktree: missing" in result.stdout


def test_checkout_path_adds_missing_branch(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_checkout(_runner, name: str, root: Path):
        calls.append((name, root))
        return SimpleNamespace(worktree_path=root / "feature", created=True)

    monkeypatch.setattr(cli, "execute_checkout", fake_execute_checkout, raising=False)

    result = runner.invoke(cli.app, ["checkout", "--path", "feature"])

    assert result.exit_code == 0
    assert result.stdout.strip() == str(tmp_path / "feature")
    assert calls == [("feature", tmp_path)]


def test_checkout_base_branch_flag_overrides_creation_base(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_checkout(
        _runner,
        name: str,
        root: Path,
        base_branch: str | None = None,
    ):
        calls.append((name, root, base_branch))
        return SimpleNamespace(worktree_path=root / "feature", created=True)

    monkeypatch.setattr(cli, "execute_checkout", fake_execute_checkout, raising=False)

    result = runner.invoke(
        cli.app,
        ["checkout", "--path", "--base-branch", "develop", "feature"],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == str(tmp_path / "feature")
    assert calls == [("feature", tmp_path, "develop")]


def test_checkout_path_keeps_status_output_off_stdout(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)
    monkeypatch.setenv("TERM", "xterm")

    class StatusRunner(cli.SubprocessRunner):
        def __init__(self) -> None:
            super().__init__(
                console=Console(stderr=True, force_terminal=True, color_system=None, width=120)
            )

    def fake_execute_checkout(runner_arg, name: str, root: Path):
        calls.append((name, root))
        runner_arg.run([sys.executable, "-c", "print('internal stdout')"])
        return SimpleNamespace(worktree_path=root / "feature", created=True)

    monkeypatch.setattr(cli, "SubprocessRunner", StatusRunner, raising=False)
    monkeypatch.setattr(cli, "execute_checkout", fake_execute_checkout, raising=False)

    result = runner.invoke(cli.app, ["checkout", "--path", "feature"])

    assert result.exit_code == 0
    assert result.stdout == f"{tmp_path / 'feature'}\n"
    assert "internal stdout" not in result.stdout
    assert "Running" in result.stderr
    assert calls == [("feature", tmp_path)]


def test_open_opens_primary_url_for_current_worktree(monkeypatch, tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    config_path = tmp_path / "main" / ".bonsai.toml"
    config_path.write_text(
        """
name = "authentic"
base_branch = "main"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
""",
        encoding="utf-8",
    )
    opened_urls: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened_urls.append(url) or True)
    monkeypatch.setattr(cli, "resolve_open_target", lambda plan: plan, raising=False)
    monkeypatch.setattr(cli, "url_liveness_ok", lambda _plan: True, raising=False)
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["open"])

    assert result.exit_code == 0
    assert opened_urls == ["https://ma-123-test.authentic.localhost"]
    assert "Opened https://ma-123-test.authentic.localhost" in result.stdout


def test_open_opens_primary_url_for_named_worktree(monkeypatch, tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    config_path = tmp_path / "main" / ".bonsai.toml"
    config_path.write_text(
        """
name = "authentic"
base_branch = "main"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
""",
        encoding="utf-8",
    )
    opened_urls: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened_urls.append(url) or True)
    monkeypatch.setattr(cli, "resolve_open_target", lambda plan: plan, raising=False)
    monkeypatch.setattr(cli, "url_liveness_ok", lambda _plan: True, raising=False)
    monkeypatch.chdir(tmp_path / "main")

    result = runner.invoke(cli.app, ["open", "ma-123-test"])

    assert result.exit_code == 0
    assert opened_urls == ["https://ma-123-test.authentic.localhost"]
    assert "Opened https://ma-123-test.authentic.localhost" in result.stdout


def test_open_service_flag_opens_named_service_url(monkeypatch, tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    config_path = tmp_path / "main" / ".bonsai.toml"
    config_path.write_text(
        """
name = "authentic"
base_branch = "main"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"

[[services]]
name = "api"
port_env = "API_PORT"
base_port = 3333
url = "https://api-${slug}.authentic.localhost"
""",
        encoding="utf-8",
    )
    opened_urls: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened_urls.append(url) or True)
    monkeypatch.setattr(cli, "resolve_open_target", lambda plan: plan, raising=False)
    monkeypatch.setattr(cli, "url_liveness_ok", lambda _plan: True, raising=False)
    monkeypatch.chdir(tmp_path / "main")

    result = runner.invoke(cli.app, ["open", "ma-123-test", "--service", "api"])

    assert result.exit_code == 0
    assert opened_urls == ["https://api-ma-123-test.authentic.localhost"]
    assert "Opened https://api-ma-123-test.authentic.localhost" in result.stdout


def _write_primary_open_workspace(tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    config_path = tmp_path / "main" / ".bonsai.toml"
    config_path.write_text(
        """
name = "authentic"
base_branch = "main"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
""",
        encoding="utf-8",
    )


def test_open_demotes_to_port_url_when_caddy_down(monkeypatch, tmp_path: Path) -> None:
    _write_primary_open_workspace(tmp_path)
    opened_urls: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened_urls.append(url) or True)

    def fake_resolve(plan):
        return SimpleNamespace(
            url=f"http://localhost:{plan.port}",
            port=plan.port,
            via="port",
            branch=plan.branch,
        )

    monkeypatch.setattr(cli, "resolve_open_target", fake_resolve, raising=False)
    monkeypatch.setattr(cli, "url_liveness_ok", lambda _plan: True, raising=False)
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["open"])

    assert result.exit_code == 0
    assert opened_urls == ["http://localhost:4201"]
    assert "Opened http://localhost:4201" in result.stdout
    assert "authentic.localhost" not in result.stdout


def test_open_does_not_report_success_when_url_is_dead(monkeypatch, tmp_path: Path) -> None:
    _write_primary_open_workspace(tmp_path)

    def fail_browser(_url):
        raise AssertionError("browser must not open when the URL is dead")

    monkeypatch.setattr(cli.webbrowser, "open", fail_browser)
    monkeypatch.setattr(cli, "resolve_open_target", lambda plan: plan, raising=False)
    monkeypatch.setattr(cli, "url_liveness_ok", lambda _plan: False, raising=False)
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["open"])

    assert result.exit_code == 1
    assert "Opened" not in result.stdout
    assert "localhost:4201" in result.stdout
    assert "bonsai up MA-123-test" in result.stdout
    assert "bonsai open MA-123-test" in result.stdout


def test_open_no_interactive_prints_labeled_url_without_probing(
    monkeypatch, tmp_path: Path
) -> None:
    _write_primary_open_workspace(tmp_path)

    def fail_browser(_url):
        raise AssertionError("--no-interactive must not open a browser")

    def fail_liveness(_plan):
        raise AssertionError("--no-interactive must not gate on a liveness probe")

    monkeypatch.setattr(cli.webbrowser, "open", fail_browser)
    monkeypatch.setattr(cli, "resolve_open_target", lambda plan: plan, raising=False)
    monkeypatch.setattr(cli, "url_liveness_ok", fail_liveness, raising=False)
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["open", "--no-interactive"])

    assert result.exit_code == 0
    assert "Opened" not in result.stdout
    assert "https://ma-123-test.authentic.localhost (Caddy route)" in result.stdout


def test_complete_worktree_names_returns_matching_aliases(
    monkeypatch,
    tmp_path: Path,
) -> None:
    write_checkout_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert cli._complete_worktree_names("test") == ["MA-123-test", "ma-123-test"]


def test_complete_managed_worktree_names_omits_default_worktree(
    monkeypatch,
    tmp_path: Path,
) -> None:
    write_checkout_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert cli._complete_managed_worktree_names("ma") == ["MA-123-test", "ma-123-test"]


def test_checkout_argument_shell_completion_returns_fuzzy_matches(
    monkeypatch,
    tmp_path: Path,
) -> None:
    write_checkout_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    root_command = get_command(cli.app)
    checkout_command = root_command.commands["checkout"]
    name_argument = checkout_command.params[0]
    context = click.Context(
        checkout_command,
        info_name="checkout",
        parent=click.Context(root_command, info_name="bonsai"),
    )

    completions = name_argument.shell_complete(context, "test")

    assert [completion.value for completion in completions] == [
        "MA-123-test",
        "ma-123-test",
    ]


def test_context_json_prints_current_worktree_context(monkeypatch, tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    config_path = tmp_path / "main" / ".bonsai.toml"
    config_path.write_text(
        """
name = "authentic"
base_branch = "main"

[commands]
start = "yarn dev"

[[env]]
name = "COMPOSE_PROJECT_NAME"
value = "authentic-${slug}"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
""",
        encoding="utf-8",
    )
    env_path = tmp_path / "ma-123-test" / ".env.local"
    env_path.write_text(
        "# Generated by bonsai. Do not edit by hand.\n"
        "SLOT=1\n"
        "FRONTEND_PORT=4201\n"
        "\n"
        "COMPOSE_PROJECT_NAME=authentic-ma-123-test\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["context", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "bonsai.context.v1"
    assert payload["workspace"]["name"] == "authentic"
    assert payload["current"]["branch"] == "MA-123-test"
    assert payload["current"]["slot"] == 1
    assert payload["env_file"]["status"] == "current"
    assert payload["commands"]["start"] == "bonsai start"
    assert payload["commands"]["open"] == "bonsai open"
    assert payload["services"] == [
        {
            "name": "frontend",
            "port_env": "FRONTEND_PORT",
            "port": 4201,
            "public": True,
            "primary": True,
            "url": "https://ma-123-test.authentic.localhost",
        }
    ]


def test_context_text_prints_current_worktree_commands(monkeypatch, tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    (tmp_path / "main" / ".bonsai.toml").write_text(
        """
name = "authentic"
base_branch = "main"

[commands]
start = "yarn dev"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["context"])

    assert result.exit_code == 0
    assert "Bonsai context" in result.stdout
    assert "Workspace: authentic" in result.stdout
    assert "Branch: MA-123-test" in result.stdout
    assert "Env file:" in result.stdout
    assert "missing" in result.stdout
    assert "FRONTEND_PORT=4201" in result.stdout
    assert "https://ma-123-test.authentic.localhost" in result.stdout
    assert "bonsai sync --apply" in result.stdout


def test_context_rejects_unknown_format(monkeypatch, tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    (tmp_path / "main" / ".bonsai.toml").write_text(
        """
name = "authentic"
base_branch = "main"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["context", "--format", "xml"])

    assert result.exit_code == 1
    assert "Unsupported format: xml" in result.stdout


def test_shell_init_zsh_prints_checkout_wrapper() -> None:
    result = runner.invoke(cli.app, ["shell-init", "zsh"])

    assert result.exit_code == 0
    assert "bonsai() {" in result.stdout
    assert 'if [[ "$1" == "checkout" ]]; then' in result.stdout
    assert "shift" in result.stdout
    assert 'bonsai_bin="${commands[bonsai]}"' in result.stdout
    assert 'checkout_path="$("$bonsai_bin" checkout --path "$@")"' in result.stdout
    assert "bonsai_exit=$?" in result.stdout
    assert 'printf "%s\\n" "$checkout_path" >&2' in result.stdout
    assert "return $bonsai_exit" in result.stdout
    assert 'cd "$checkout_path"' in result.stdout
    assert '"$bonsai_bin" "$@"' in result.stdout
    assert "_bonsai_completion() {" in result.stdout
    assert '_TYPER_COMPLETE_ARGS="${words[1,$CURRENT]}"' in result.stdout
    assert "_BONSAI_COMPLETE=complete_zsh" in result.stdout
    assert "compdef _bonsai_completion bonsai" in result.stdout


def test_shell_init_zsh_checkout_cd_uses_external_bonsai_inside_wrapper(tmp_path: Path) -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        return

    target = tmp_path / "worktree"
    target.mkdir()
    path_capture = tmp_path / "hook-path.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_bonsai = bin_dir / "bonsai"
    fake_bonsai.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "checkout" ] && [ "$2" = "--path" ] && [ "$3" = "feature" ]; then\n'
        '  printf "%s\\n" "$BONSAI_TEST_TARGET"\n'
        "  exit 0\n"
        "fi\n"
        'printf "unexpected args: %s\\n" "$*" >&2\n'
        "exit 2\n",
        encoding="utf-8",
    )
    fake_bonsai.chmod(0o755)

    script = (
        cli.ZSH_SHELL_INIT
        + "\n"
        + "chpwd_capture_path() {\n"
        + '  print -r -- "$PATH" > "$BONSAI_TEST_PATH_CAPTURE"\n'
        + "}\n"
        + "typeset -ag chpwd_functions\n"
        + "chpwd_functions+=(chpwd_capture_path)\n"
        + "bonsai checkout feature\n"
        + "pwd\n"
    )
    result = subprocess.run(
        [zsh, "-fc", script],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}",
            "BONSAI_TEST_TARGET": str(target),
            "BONSAI_TEST_PATH_CAPTURE": str(path_capture),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(target)
    assert path_capture.read_text(encoding="utf-8").strip() == str(bin_dir)


def test_install_shell_zsh_appends_integration_block(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text("# existing config\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["install-shell", "zsh"])

    assert result.exit_code == 0
    assert "Installed zsh integration" in result.stdout
    text = zshrc.read_text(encoding="utf-8")
    assert "# existing config" in text
    assert "# >>> bonsai shell integration >>>" in text
    assert 'eval "$(bonsai shell-init zsh)"' in text
    assert "# <<< bonsai shell integration <<<" in text


def test_install_shell_zsh_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)

    first = runner.invoke(cli.app, ["install-shell", "zsh"])
    second = runner.invoke(cli.app, ["install-shell", "zsh"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "zsh integration already installed" in second.stdout
    text = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert text.count("# >>> bonsai shell integration >>>") == 1
    assert text.count('eval "$(bonsai shell-init zsh)"') == 1


def test_ensure_shell_integration_installs_on_empty_zshrc(tmp_path: Path) -> None:
    result = cli.ensure_shell_integration(tmp_path, "zsh", offer=lambda: True)

    assert result == "installed"
    text = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert text.count("# >>> bonsai shell integration >>>") == 1
    assert text.count('eval "$(bonsai shell-init zsh)"') == 1


def test_ensure_shell_integration_already_present_skips_backup(tmp_path: Path) -> None:
    cli.ensure_shell_integration(tmp_path, "zsh", offer=lambda: True)
    backups_before = len(list(tmp_path.glob(".zshrc.bonsai*.bak")))

    result = cli.ensure_shell_integration(tmp_path, "zsh", offer=lambda: True)

    assert result == "already"
    text = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert text.count("# >>> bonsai shell integration >>>") == 1
    backups_after = len(list(tmp_path.glob(".zshrc.bonsai*.bak")))
    assert backups_after == backups_before


def test_ensure_shell_integration_backs_up_pre_append_contents(tmp_path: Path) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text("# existing config\n", encoding="utf-8")

    cli.ensure_shell_integration(tmp_path, "zsh", offer=lambda: True)

    backups = list(tmp_path.glob(".zshrc.bonsai*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "# existing config\n"


def test_ensure_shell_integration_declined_makes_no_changes(tmp_path: Path) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text("# existing config\n", encoding="utf-8")

    result = cli.ensure_shell_integration(tmp_path, "zsh", offer=lambda: False)

    assert result == "manual"
    assert zshrc.read_text(encoding="utf-8") == "# existing config\n"
    assert list(tmp_path.glob(".zshrc.bonsai*.bak")) == []


@pytest.mark.parametrize("shell", ["fish", "bash"])
def test_ensure_shell_integration_non_zsh_returns_manual_without_raising(
    tmp_path: Path, shell: str
) -> None:
    result = cli.ensure_shell_integration(tmp_path, shell, offer=lambda: True)

    assert result == "manual"
    assert not (tmp_path / ".zshrc").exists()
    assert list(tmp_path.glob(".zshrc.bonsai*.bak")) == []


def test_install_shell_non_zsh_still_raises(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)

    result = runner.invoke(cli.app, ["install-shell", "fish"])

    assert result.exit_code == 1
    assert "Unsupported shell: fish" in result.stdout


def test_ensure_shell_integration_preserves_trailing_newline(tmp_path: Path) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text("# existing config", encoding="utf-8")

    cli.ensure_shell_integration(tmp_path, "zsh", offer=lambda: True)

    text = zshrc.read_text(encoding="utf-8")
    assert text.startswith("# existing config\n\n")
    assert text.endswith(cli.ZSH_INTEGRATION_BLOCK)


def test_list_command_shows_default_and_managed_worktrees(tmp_path: Path, monkeypatch) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path / "main")

    result = runner.invoke(cli.app, ["list"])

    assert result.exit_code == 0
    assert "Worktrees for authentic" in result.stdout
    assert "main" in result.stdout
    assert "MA-123-test" in result.stdout
    assert "ma-123-test" in result.stdout
    assert "default" in result.stdout
    assert "managed" in result.stdout
    assert "missing" in result.stdout
    assert "FRONTEND_PORT=4200" in result.stdout
    assert "FRONTEND_PORT=4201" in result.stdout
    assert "https://main.authentic.localhost" in result.stdout
    assert "https://ma-123-test.authentic.localhost" in result.stdout


def test_list_command_json_prints_workspace_summary(tmp_path: Path, monkeypatch) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path / "main")

    result = runner.invoke(cli.app, ["list", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "bonsai.list.v1"
    assert payload["workspace"]["name"] == "authentic"
    assert [worktree["branch"] for worktree in payload["worktrees"]] == [
        "main",
        "MA-123-test",
    ]
    assert payload["worktrees"][0]["services"][0]["port"] == 4200
    assert payload["worktrees"][1]["services"][0]["url"] == (
        "https://ma-123-test.authentic.localhost"
    )
    assert payload["commands"]["status"] == "bonsai status"


def test_list_command_rejects_unknown_format(tmp_path: Path, monkeypatch) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path / "main")

    result = runner.invoke(cli.app, ["list", "--format", "xml"])

    assert result.exit_code == 1
    assert "Unsupported format: xml" in result.stdout


def test_urls_command_renders_url_diagnostics(monkeypatch, tmp_path: Path) -> None:
    calls = []

    monkeypatch.setattr(
        cli,
        "find_workspace_root",
        lambda path: calls.append(("find", path)) or tmp_path,
    )

    def fake_plan(runner, workspace_root: Path, **kwargs):
        calls.append(("plan", type(runner).__name__, workspace_root, kwargs))
        return SimpleNamespace(urls=())

    monkeypatch.setattr(cli, "plan_workspace_urls", fake_plan, raising=False)
    monkeypatch.setattr(
        cli,
        "render_workspace_urls",
        lambda plan, output_format: f"{output_format}:{len(plan.urls)}\n",
        raising=False,
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        cli.app,
        [
            "urls",
            "feature",
            "--service",
            "api",
            "--diagnose",
            "https://api-feature.authentic.localhost",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "json:0\n"
    assert calls[0] == ("find", tmp_path)
    assert calls[1][0:3] == ("plan", "SubprocessRunner", tmp_path)
    assert calls[1][3] == {
        "name": "feature",
        "service_name": "api",
        "diagnose_url": "https://api-feature.authentic.localhost",
    }


def test_status_command_prints_current_worktree_status(tmp_path: Path, monkeypatch) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "Bonsai status" in result.stdout
    assert "Workspace: authentic" in result.stdout
    assert "Branch: MA-123-test" in result.stdout
    assert "Kind: managed" in result.stdout
    assert "Env file:" in result.stdout
    assert "missing" in result.stdout
    assert "FRONTEND_PORT=4201" in result.stdout
    assert "https://ma-123-test.authentic.localhost" in result.stdout
    assert "List worktrees: bonsai list" in result.stdout


def test_status_command_colorizes_terminal_text_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path / "ma-123-test")
    monkeypatch.setattr(
        cli,
        "console",
        Console(force_terminal=True, color_system="standard", width=200),
    )

    result = runner.invoke(cli.app, ["status"], color=True)

    assert result.exit_code == 0
    assert "\x1b[" in result.stdout
    assert "Bonsai status" in result.stdout
    assert "Workspace:" in result.stdout
    assert "authentic" in result.stdout


def test_status_command_reports_workspace_root_location(tmp_path: Path, monkeypatch) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "Bonsai status" in result.stdout
    assert "Workspace: authentic" in result.stdout
    assert "Location: workspace root (parent folder)" in result.stdout
    assert f"Path: {tmp_path}" in result.stdout
    assert "List worktrees: bonsai list" in result.stdout


def test_status_command_json_prints_current_worktree_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["status", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "bonsai.status.v1"
    assert payload["workspace"]["name"] == "authentic"
    assert payload["current"]["branch"] == "MA-123-test"
    assert payload["current"]["slot"] == 1
    assert payload["current"]["kind"] == "managed"
    assert payload["current"]["services"][0]["port_env"] == "FRONTEND_PORT"
    assert payload["current"]["services"][0]["port"] == 4201
    assert payload["commands"]["list"] == "bonsai list"


def test_status_command_json_stays_uncolored_for_terminal_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path / "ma-123-test")
    monkeypatch.setattr(
        cli,
        "console",
        Console(force_terminal=True, color_system="standard", width=200),
    )

    result = runner.invoke(cli.app, ["status", "--format", "json"], color=True)

    assert result.exit_code == 0
    assert "\x1b[" not in result.stdout
    assert json.loads(result.stdout)["schema"] == "bonsai.status.v1"


def test_status_command_json_reports_workspace_root_location(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["status", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "bonsai.status.v1"
    assert payload["workspace"]["name"] == "authentic"
    assert payload["location"] == {
        "kind": "workspace_root",
        "path": str(tmp_path),
    }
    assert payload["current"] is None
    assert payload["commands"]["list"] == "bonsai list"


def test_status_command_rejects_unknown_format(tmp_path: Path, monkeypatch) -> None:
    write_checkout_workspace(tmp_path)
    write_config(tmp_path / "main", VALID_CONFIG)
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["status", "--format", "xml"])

    assert result.exit_code == 1
    assert "Unsupported format: xml" in result.stdout


def test_start_executes_workflow(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_start(_runner, root: Path, branch: str | None, current_path: Path) -> int:
        calls.append((root, branch, current_path))
        return 7

    monkeypatch.setattr(cli, "execute_start", fake_execute_start, raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["start", "feature"])

    assert result.exit_code == 7
    assert calls == [(tmp_path, "feature", tmp_path)]
    assert "Starting feature" in result.stdout


def test_logs_command_prints_latest_log_for_current_worktree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_checkout_workspace(tmp_path)
    log_dir = tmp_path / ".bonsai" / "logs" / "main"
    log_dir.mkdir(parents=True)
    (log_dir / "20260526-143012-install.log").write_text("install\n", encoding="utf-8")
    latest = log_dir / "20260526-143245-setup.log"
    latest.write_text("setup\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "main")

    result = runner.invoke(cli.app, ["logs"])

    assert result.exit_code == 0
    assert result.stdout == "setup\n"


def test_logs_command_filters_by_command_and_branch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_checkout_workspace(tmp_path)
    log_dir = tmp_path / ".bonsai" / "logs" / "ma-123-test"
    log_dir.mkdir(parents=True)
    (log_dir / "20260526-143012-install.log").write_text("install\n", encoding="utf-8")
    (log_dir / "20260526-143245-setup.log").write_text("setup\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "main")

    result = runner.invoke(cli.app, ["logs", "MA-123-test", "--command", "install"])

    assert result.exit_code == 0
    assert result.stdout == "install\n"


def test_logs_command_reports_missing_logs(tmp_path: Path, monkeypatch) -> None:
    write_checkout_workspace(tmp_path)
    monkeypatch.chdir(tmp_path / "main")

    result = runner.invoke(cli.app, ["logs", "--command", "start"])

    assert result.exit_code == 1
    assert "No logs found for main with command start" in result.stdout


def test_logs_command_follows_resolved_log(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "start.log"
    log_path.write_text("start\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)
    monkeypatch.setattr(
        cli,
        "plan_command_log",
        lambda root, branch, current, command: SimpleNamespace(
            branch="main",
            worktree_path=tmp_path / "main",
            log_path=log_path,
            content="start\n",
        ),
        raising=False,
    )

    class FakeRunner:
        def run_stream(self, argv, cwd=None, env=None):
            calls.append((argv, cwd, env))
            return 17

    monkeypatch.setattr(cli, "SubprocessRunner", FakeRunner, raising=False)

    result = runner.invoke(cli.app, ["logs", "--follow"])

    assert result.exit_code == 17
    assert calls == [(["tail", "-n", "+1", "-f", str(log_path)], None, None)]


def test_up_command_starts_detached_app(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_up(
        _runner,
        root: Path,
        name: str | None,
        current_path: Path,
        readiness_timeout: float,
    ):
        calls.append((root, name, current_path, readiness_timeout))
        return SimpleNamespace(
            branch="feature-a",
            worktree_path=tmp_path / "feature-a",
            pid=123,
            log_path=tmp_path / ".bonsai" / "logs" / "feature-a" / "start.log",
            ready_ports=(4201,),
            stale_pid=None,
        )

    monkeypatch.setattr(cli, "execute_up", fake_execute_up, raising=False)

    result = runner.invoke(cli.app, ["up", "feature-a", "--wait-timeout", "0.25"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, "feature-a", Path.cwd(), 0.25)]
    assert "started feature-a pid=123" in result.stdout
    assert "ready ports: 4201" in result.stdout


def test_down_command_stops_tracked_app(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_down(
        root: Path,
        name: str | None,
        current_path: Path,
        terminate_timeout: float,
    ):
        calls.append((root, name, current_path, terminate_timeout))
        return SimpleNamespace(
            branch="feature-a",
            worktree_path=tmp_path / "feature-a",
            pid=123,
            action="stopped",
            log_path=tmp_path / ".bonsai" / "logs" / "feature-a" / "start.log",
        )

    monkeypatch.setattr(cli, "execute_down", fake_execute_down, raising=False)

    result = runner.invoke(cli.app, ["down", "feature-a", "--timeout", "0"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, "feature-a", Path.cwd(), 0.0)]
    assert "stopped feature-a pid=123" in result.stdout


def test_sync_dry_run_reports_planned_actions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_sync(_runner, root: Path, apply: bool = False):
        assert root == tmp_path
        assert apply is False
        return SimpleNamespace(
            actions=[
                SimpleNamespace(kind="write", path=tmp_path / "main" / ".env.local"),
                SimpleNamespace(kind="remove", path=tmp_path / "caddy.d" / "old.caddy"),
            ],
            reload_caddy=True,
        )

    monkeypatch.setattr(cli, "execute_sync", fake_execute_sync, raising=False)

    result = runner.invoke(cli.app, ["sync"])

    assert result.exit_code == 0
    assert "sync dry run" in result.stdout.lower()
    assert "write" in result.stdout
    assert "remove" in result.stdout
    assert "reload Caddy" in result.stdout


def test_sync_apply_passes_apply_true(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_sync(_runner, root: Path, apply: bool = False):
        calls.append((root, apply))
        return SimpleNamespace(actions=[], reload_caddy=False)

    monkeypatch.setattr(cli, "execute_sync", fake_execute_sync, raising=False)

    result = runner.invoke(cli.app, ["sync", "--apply"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, True)]
    assert "No sync changes" in result.stdout


def test_repair_dry_run_reports_planned_actions(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_repair(_runner, root: Path, apply: bool = False):
        calls.append((root, apply))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    branch="old-branch",
                    action="remove",
                    reason=f"missing {tmp_path / 'old-branch'}",
                    worktree_path=tmp_path / "old-branch",
                    old_slot=2,
                    new_slot=None,
                ),
                SimpleNamespace(
                    branch="feature-c",
                    action="repack",
                    reason="slot 4 -> 2",
                    worktree_path=tmp_path / "feature-c",
                    old_slot=4,
                    new_slot=2,
                ),
            ],
            state_changed=True,
        )

    monkeypatch.setattr(cli, "execute_repair", fake_execute_repair, raising=False)

    result = runner.invoke(cli.app, ["repair"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, False)]
    assert "repair dry run" in result.stdout.lower()
    assert "remove old-branch" in result.stdout
    assert "repack feature-c" in result.stdout
    assert "Run: bonsai sync --apply" in result.stdout


def test_repair_apply_passes_apply_true_and_uses_past_tense_actions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_repair(_runner, root: Path, apply: bool = False):
        calls.append((root, apply))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    branch="old-branch",
                    action="remove",
                    reason=f"missing {tmp_path / 'old-branch'}",
                    worktree_path=tmp_path / "old-branch",
                    old_slot=2,
                    new_slot=None,
                ),
                SimpleNamespace(
                    branch="feature-c",
                    action="repack",
                    reason="slot 4 -> 2",
                    worktree_path=tmp_path / "feature-c",
                    old_slot=4,
                    new_slot=2,
                ),
            ],
            state_changed=True,
        )

    monkeypatch.setattr(cli, "execute_repair", fake_execute_repair, raising=False)

    result = runner.invoke(cli.app, ["repair", "--apply"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, True)]
    assert "repair apply" in result.stdout.lower()
    assert "removed old-branch" in result.stdout
    assert "repacked feature-c" in result.stdout
    assert "Run: bonsai sync --apply" in result.stdout


def test_repair_noop_reports_no_state_repairs_needed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_repair(_runner, root: Path, apply: bool = False):
        assert root == tmp_path
        assert apply is False
        return SimpleNamespace(items=[], state_changed=False)

    monkeypatch.setattr(cli, "execute_repair", fake_execute_repair, raising=False)

    result = runner.invoke(cli.app, ["repair"])

    assert result.exit_code == 0
    assert "No state repairs needed" in result.stdout
    assert "bonsai sync --apply" not in result.stdout


def test_repair_noop_with_state_change_reports_sync_instruction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_repair(_runner, root: Path, apply: bool = False):
        assert root == tmp_path
        assert apply is False
        return SimpleNamespace(items=[], state_changed=True)

    monkeypatch.setattr(cli, "execute_repair", fake_execute_repair, raising=False)

    result = runner.invoke(cli.app, ["repair"])

    assert result.exit_code == 0
    assert "No state repairs needed" in result.stdout
    assert "Run: bonsai sync --apply" in result.stdout


def test_repair_ports_previews_reassignment_plan(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)
    calls = []

    def fake_plan_port_repairs(root: Path, runner=None):
        assert root == tmp_path
        calls.append(runner)
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    branch="feature-a",
                    slug="feature-a",
                    current_slot=1,
                    proposed_slot=5,
                    services=[
                        SimpleNamespace(
                            name="frontend",
                            port_env="FRONTEND_PORT",
                            old_port=4201,
                            new_port=4205,
                        ),
                        SimpleNamespace(
                            name="api",
                            port_env="API_PORT",
                            old_port=3334,
                            new_port=3338,
                        ),
                    ],
                )
            ]
        )

    monkeypatch.setattr(cli, "plan_port_repairs", fake_plan_port_repairs, raising=False)

    result = runner.invoke(cli.app, ["repair-ports"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "repair-ports dry run" in result.stdout.lower()
    assert "feature-a slot 1 -> 5" in result.stdout
    assert "FRONTEND_PORT 4201 -> 4205" in result.stdout
    assert "API_PORT 3334 -> 3338" in result.stdout
    assert "No files changed" in result.stdout


def test_repair_ports_apply_updates_state_and_generated_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_port_repairs(_runner, root: Path, apply: bool = False):
        calls.append((root, apply))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    branch="feature-a",
                    slug="feature-a",
                    current_slot=1,
                    proposed_slot=5,
                    services=[
                        SimpleNamespace(
                            name="frontend",
                            port_env="FRONTEND_PORT",
                            old_port=4201,
                            new_port=4205,
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr(cli, "execute_port_repairs", fake_execute_port_repairs, raising=False)

    result = runner.invoke(cli.app, ["repair-ports", "--apply"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, True)]
    assert "repair-ports apply" in result.stdout.lower()
    assert "feature-a slot 1 -> 5" in result.stdout
    assert "FRONTEND_PORT 4201 -> 4205" in result.stdout
    assert "Updated state and regenerated files" in result.stdout
    assert "No files changed" not in result.stdout


def test_repair_ports_json_prints_machine_readable_plan(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_plan_port_repairs(root: Path, runner=None):
        assert root == tmp_path
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    branch="feature-a",
                    slug="feature-a",
                    current_slot=1,
                    proposed_slot=5,
                    services=[
                        SimpleNamespace(
                            name="frontend",
                            port_env="FRONTEND_PORT",
                            old_port=4201,
                            new_port=4205,
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr(cli, "plan_port_repairs", fake_plan_port_repairs, raising=False)

    result = runner.invoke(cli.app, ["repair-ports", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "schema": "bonsai.port-repair.v1",
        "workspace": {"root": str(tmp_path)},
        "repairs": [
            {
                "branch": "feature-a",
                "slug": "feature-a",
                "current_slot": 1,
                "proposed_slot": 5,
                "services": [
                    {
                        "name": "frontend",
                        "port_env": "FRONTEND_PORT",
                        "old_port": 4201,
                        "new_port": 4205,
                    }
                ],
            }
        ],
    }


def test_ports_command_prints_json_owner_report(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_plan_workspace_ports(_runner, root: Path):
        assert root == tmp_path
        return SimpleNamespace(
            workspace_root=tmp_path,
            ports=[
                SimpleNamespace(
                    branch="feature-a",
                    worktree_path=tmp_path / "feature-a",
                    service_name="frontend",
                    port_env="FRONTEND_PORT",
                    port=4201,
                    status="owned",
                    owners=[
                        PortOwner(
                            pid=123,
                            command="node",
                            user="michael",
                            cwd=tmp_path / "feature-a",
                            worktree_branch="feature-a",
                            worktree_path=tmp_path / "feature-a",
                        )
                    ],
                )
            ],
        )

    monkeypatch.setattr(cli, "plan_workspace_ports", fake_plan_workspace_ports, raising=False)

    result = runner.invoke(cli.app, ["ports", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "bonsai.ports.v1"
    assert payload["ports"][0]["status"] == "owned"
    assert payload["ports"][0]["owners"][0]["pid"] == 123
    assert payload["ports"][0]["owners"][0]["worktree_branch"] == "feature-a"


def test_ps_command_filters_to_busy_ports(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_plan_workspace_ports(_runner, root: Path):
        assert root == tmp_path
        return SimpleNamespace(
            workspace_root=tmp_path,
            ports=[
                SimpleNamespace(
                    branch="main",
                    worktree_path=tmp_path / "main",
                    service_name="frontend",
                    port_env="FRONTEND_PORT",
                    port=4200,
                    status="free",
                    owners=[],
                ),
                SimpleNamespace(
                    branch="feature-a",
                    worktree_path=tmp_path / "feature-a",
                    service_name="frontend",
                    port_env="FRONTEND_PORT",
                    port=4201,
                    status="conflict",
                    owners=[
                        PortOwner(
                            pid=456,
                            command="ruby",
                            user="michael",
                            cwd=tmp_path / "other",
                        )
                    ],
                ),
            ],
        )

    monkeypatch.setattr(cli, "plan_workspace_ports", fake_plan_workspace_ports, raising=False)

    result = runner.invoke(cli.app, ["ps"])

    assert result.exit_code == 0
    assert "feature-a" in result.stdout
    assert "ruby[456]" in result.stdout
    assert "main" not in result.stdout


def test_stop_command_terminates_matching_processes(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_stop_processes(
        _runner,
        root: Path,
        current_path: Path,
        name: str | None = None,
        all_worktrees: bool = False,
        force: bool = False,
    ):
        calls.append((root, current_path, name, all_worktrees, force))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    action="stopped",
                    branch="feature-a",
                    service_name="frontend",
                    port_env="FRONTEND_PORT",
                    port=4201,
                    owner=PortOwner(pid=123, command="node", user="michael"),
                    reason="terminated",
                )
            ]
        )

    monkeypatch.setattr(
        cli,
        "execute_stop_processes",
        fake_execute_stop_processes,
        raising=False,
    )

    result = runner.invoke(cli.app, ["stop", "feature-a"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, Path.cwd(), "feature-a", False, False)]
    assert "stopped feature-a frontend FRONTEND_PORT=4201 node[123]" in result.stdout


def test_stop_command_passes_all_and_force(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_stop_processes(
        _runner,
        root: Path,
        current_path: Path,
        name: str | None = None,
        all_worktrees: bool = False,
        force: bool = False,
    ):
        calls.append((root, current_path, name, all_worktrees, force))
        return SimpleNamespace(items=[])

    monkeypatch.setattr(
        cli,
        "execute_stop_processes",
        fake_execute_stop_processes,
        raising=False,
    )

    result = runner.invoke(cli.app, ["stop", "--all", "--force"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, Path.cwd(), None, True, True)]
    assert "No listener processes matched" in result.stdout


def test_restart_stops_then_starts_foreground_process(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_stop_processes(
        _runner,
        root: Path,
        current_path: Path,
        name: str | None = None,
        all_worktrees: bool = False,
        force: bool = False,
    ):
        calls.append(("stop", root, current_path, name, all_worktrees, force))
        return SimpleNamespace(items=[])

    def fake_execute_start(_runner, root: Path, name: str | None, current_path: Path) -> int:
        calls.append(("start", root, name, current_path))
        return 7

    monkeypatch.setattr(
        cli,
        "execute_stop_processes",
        fake_execute_stop_processes,
        raising=False,
    )
    monkeypatch.setattr(cli, "execute_start", fake_execute_start, raising=False)

    result = runner.invoke(cli.app, ["restart", "feature-a", "--force"])

    assert result.exit_code == 7
    assert calls == [
        ("stop", tmp_path, Path.cwd(), "feature-a", False, True),
        ("start", tmp_path, "feature-a", Path.cwd()),
    ]
    assert "Restarting feature-a" in result.stdout


def test_restart_detach_stops_then_starts_supervised_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_stop_processes(
        _runner,
        root: Path,
        current_path: Path,
        name: str | None = None,
        all_worktrees: bool = False,
        force: bool = False,
    ):
        calls.append(("stop", root, current_path, name, all_worktrees, force))
        return SimpleNamespace(items=[])

    def fake_execute_up(
        _runner,
        root: Path,
        name: str | None,
        current_path: Path,
        readiness_timeout: float,
    ):
        calls.append(("up", root, name, current_path, readiness_timeout))
        return SimpleNamespace(
            branch="feature-a",
            worktree_path=tmp_path / "feature-a",
            pid=456,
            log_path=tmp_path / ".bonsai" / "logs" / "feature-a" / "start.log",
            ready_ports=(4201,),
            stale_pid=None,
        )

    def fake_execute_down(
        root: Path,
        name: str | None,
        current_path: Path,
        terminate_timeout: float,
    ):
        calls.append(("down", root, name, current_path, terminate_timeout))
        return SimpleNamespace(
            branch="feature-a",
            worktree_path=tmp_path / "feature-a",
            pid=123,
            action="stopped",
            log_path=tmp_path / ".bonsai" / "logs" / "feature-a" / "start.log",
        )

    monkeypatch.setattr(
        cli,
        "execute_stop_processes",
        fake_execute_stop_processes,
        raising=False,
    )
    monkeypatch.setattr(cli, "execute_down", fake_execute_down, raising=False)
    monkeypatch.setattr(cli, "execute_up", fake_execute_up, raising=False)

    result = runner.invoke(
        cli.app,
        ["restart", "feature-a", "--force", "--detach", "--wait-timeout", "0.5"],
    )

    assert result.exit_code == 0
    assert calls == [
        ("down", tmp_path, "feature-a", Path.cwd(), 5.0),
        ("stop", tmp_path, Path.cwd(), "feature-a", False, True),
        ("up", tmp_path, "feature-a", Path.cwd(), 0.5),
    ]
    assert "stopped feature-a pid=123" in result.stdout
    assert "started feature-a pid=456" in result.stdout


def test_repair_ports_rejects_unknown_format(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    result = runner.invoke(cli.app, ["repair-ports", "--format", "xml"])

    assert result.exit_code == 1
    assert "Unsupported format: xml" in result.stdout


def test_cleanup_dry_run_reports_pr_aware_plan(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_cleanup(_runner, root: Path, apply: bool = False, force: bool = False):
        calls.append((root, apply, force))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    branch="feature",
                    action="remove",
                    reason="pull request is merged",
                    pr_url="https://github.com/org/repo/pull/1",
                    worktree_path=tmp_path / "feature",
                ),
                SimpleNamespace(
                    branch="open",
                    action="skip",
                    reason="pull request is open",
                    pr_url="https://github.com/org/repo/pull/2",
                    worktree_path=tmp_path / "open",
                ),
            ],
        )

    monkeypatch.setattr(cli, "execute_cleanup", fake_execute_cleanup, raising=False)

    result = runner.invoke(cli.app, ["cleanup"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, False, False)]
    assert "dry run" in result.stdout.lower()
    assert "remove feature" in result.stdout
    assert "skip open" in result.stdout
    assert "https://github.com/org/repo/pull/1" in result.stdout


def test_cleanup_apply_passes_apply_and_force(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_cleanup(_runner, root: Path, apply: bool = False, force: bool = False):
        calls.append((root, apply, force))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    branch="feature",
                    action="removed",
                    reason="pull request is merged",
                    pr_url=None,
                    worktree_path=tmp_path / "feature",
                )
            ],
        )

    monkeypatch.setattr(cli, "execute_cleanup", fake_execute_cleanup, raising=False)

    result = runner.invoke(cli.app, ["cleanup", "--apply", "--force"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, True, True)]
    assert "cleanup apply" in result.stdout.lower()
    assert "removed feature" in result.stdout


def test_doctor_reports_failed_checks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_check_workspace_health(_runner, root: Path):
        assert root == tmp_path
        return SimpleNamespace(
            failed=True,
            checks=[
                SimpleNamespace(
                    name="env main",
                    status="fail",
                    detail="Missing .env.local",
                    hint="Run: bonsai sync --apply",
                )
            ],
        )

    monkeypatch.setattr(cli, "check_workspace_health", fake_check_workspace_health, raising=False)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "env main" in result.stdout
    assert "Missing .env.local" in result.stdout
    assert "bonsai sync --apply" in result.stdout


def test_doctor_json_prints_machine_readable_report(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_check_workspace_health(_runner, root: Path):
        assert root == tmp_path
        return SimpleNamespace(
            failed=True,
            checks=[
                SimpleNamespace(
                    id="env-main",
                    name="env main",
                    status="fail",
                    detail="Missing .env.local",
                    hint="Run: bonsai sync --apply",
                    repair="sync",
                )
            ],
        )

    monkeypatch.setattr(cli, "check_workspace_health", fake_check_workspace_health, raising=False)

    result = runner.invoke(cli.app, ["doctor", "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["schema"] == "bonsai.doctor.v1"
    assert payload["workspace"]["root"] == str(tmp_path)
    assert payload["failed"] is True
    assert payload["checks"] == [
        {
            "id": "env-main",
            "name": "env main",
            "status": "fail",
            "detail": "Missing .env.local",
            "hint": "Run: bonsai sync --apply",
            "repair": "sync",
        }
    ]
    assert payload["applied"] == []


def test_doctor_rejects_unknown_format(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    result = runner.invoke(cli.app, ["doctor", "--format", "xml"])

    assert result.exit_code == 1
    assert "Unsupported format: xml" in result.stdout


def test_doctor_apply_runs_apply_workflow_before_final_health(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_doctor_apply(_runner, root: Path):
        calls.append(("apply", root))
        return SimpleNamespace(
            actions=[
                SimpleNamespace(kind="repair", detail="removed old-branch"),
                SimpleNamespace(kind="sync", detail=f"write {tmp_path / 'main' / '.env.local'}"),
                SimpleNamespace(kind="caddy", detail="brew services start caddy"),
            ]
        )

    def fake_check_workspace_health(_runner, root: Path):
        calls.append(("health", root))
        return SimpleNamespace(
            failed=False,
            checks=[SimpleNamespace(name="config", status="ok", detail="loaded", hint=None)],
        )

    monkeypatch.setattr(cli, "execute_doctor_apply", fake_execute_doctor_apply, raising=False)
    monkeypatch.setattr(cli, "check_workspace_health", fake_check_workspace_health, raising=False)

    result = runner.invoke(cli.app, ["doctor", "--apply"])

    assert result.exit_code == 0
    assert calls == [("apply", tmp_path), ("health", tmp_path)]
    assert "doctor apply" in result.stdout.lower()
    assert "repair removed old-branch" in result.stdout
    assert "sync write" in result.stdout
    assert "caddy brew services start caddy" in result.stdout
    assert "config" in result.stdout
    assert "loaded" in result.stdout


def test_doctor_exits_zero_when_checks_pass(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)
    monkeypatch.setattr(
        cli,
        "check_workspace_health",
        lambda _runner, _root: SimpleNamespace(
            failed=False,
            checks=[SimpleNamespace(name="config", status="ok", detail="loaded", hint=None)],
        ),
        raising=False,
    )

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "config" in result.stdout
    assert "loaded" in result.stdout


_NON_NPM_CONFIG = """
name = "authentic"
base_branch = "main"

[caddy]
auto_install = true
auto_start = true
root_caddyfile = "Caddyfile"
snippets_dir = "caddy.d"

[commands]
install = "poetry install"
start = "poetry run dev"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
"""


def _install_start_here_fakes(monkeypatch, tmp_path: Path, *, live: bool) -> dict:
    """Wire fakes so `start-here` exercises its sequence with no real IO."""
    parent = tmp_path / "parent"
    parent.mkdir()
    workspace_root = parent / "bonsai-authentic"
    default_worktree = workspace_root / "main"
    config_path = default_worktree / ".bonsai.toml"
    calls: list[str] = []
    record: dict = {"config_path": config_path, "workspace_root": workspace_root}

    def fake_preflight_report(_runner, _repo_path=None, _home=None):
        calls.append("preflight")
        return DoctorReport(
            checks=(
                DoctorCheck("git", "ok", "git is available", id="git"),
                DoctorCheck("caddy", "ok", "caddy is available", id="caddy"),
            )
        )

    def fake_execute_clone(_runner, _git_url, _name, _parent, config_initializer=None):
        calls.append("clone")
        record["config_initializer"] = config_initializer
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_NON_NPM_CONFIG, encoding="utf-8")
        state = BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@example.com:org/repo.git",
            worktrees={},
        )
        return SimpleNamespace(
            workspace_root=workspace_root,
            default_worktree=default_worktree,
            state=state,
        )

    def fake_ensure_shell_integration(_home, _shell, *, offer):
        calls.append("shell")
        record["offer"] = offer
        return "installed"

    def fake_execute_add(_runner, branch, root):
        calls.append("add")
        record["add_branch"] = branch
        record["add_root"] = root
        return SimpleNamespace(worktree_path=root / "main", slot=0)

    def fake_setup_caddy(_runner, _workspace_root):
        calls.append("caddy")
        return CaddySetupResult()

    def fake_plan_open_url_for_worktree(_root, _name, service_name=None):
        return OpenUrlPlan(
            branch="main",
            worktree_path=default_worktree,
            url="https://main.authentic.localhost",
            service_name="frontend",
            port=4200,
        )

    monkeypatch.setattr(cli, "SubprocessRunner", lambda: SimpleNamespace(), raising=False)
    monkeypatch.setattr(cli, "preflight_report", fake_preflight_report, raising=False)
    monkeypatch.setattr(cli, "execute_clone", fake_execute_clone, raising=False)
    monkeypatch.setattr(
        cli, "ensure_shell_integration", fake_ensure_shell_integration, raising=False
    )
    monkeypatch.setattr(cli, "execute_add", fake_execute_add, raising=False)
    monkeypatch.setattr(cli, "setup_caddy", fake_setup_caddy, raising=False)
    monkeypatch.setattr(
        cli, "plan_open_url_for_worktree", fake_plan_open_url_for_worktree, raising=False
    )
    monkeypatch.setattr(cli, "resolve_open_target", lambda plan: plan, raising=False)
    monkeypatch.setattr(cli, "url_liveness_ok", lambda _plan: live, raising=False)
    monkeypatch.chdir(parent)
    record["calls"] = calls
    return record


def test_start_here_reports_live_payoff(monkeypatch, tmp_path: Path) -> None:
    record = _install_start_here_fakes(monkeypatch, tmp_path, live=True)

    result = runner.invoke(
        cli.app,
        ["start-here", "https://github.com/org/authentic", "bonsai-authentic"],
        input="y\n",
    )

    assert result.exit_code == 0
    calls = record["calls"]
    assert calls == ["preflight", "clone", "shell", "add", "caddy"]
    assert calls.index("preflight") < calls.index("clone")
    assert "Bonsai doctor" in result.stdout
    assert result.stdout.index("Bonsai doctor") < result.stdout.index("Created workspace")
    assert callable(record["config_initializer"])
    loaded = load_config(record["config_path"])
    assert loaded.name == "authentic"
    assert callable(record["offer"])
    assert "✅ done — your app is at https://main.authentic.localhost" in result.stdout


def test_start_here_omits_payoff_when_app_is_dead(monkeypatch, tmp_path: Path) -> None:
    record = _install_start_here_fakes(monkeypatch, tmp_path, live=False)

    result = runner.invoke(
        cli.app,
        ["start-here", "https://github.com/org/authentic", "bonsai-authentic"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "✅ done" not in result.stdout
    assert "bonsai up main" in result.stdout
    assert "bonsai open main" in result.stdout
    loaded = load_config(record["config_path"])
    assert loaded.name == "authentic"


def test_start_here_scripted_prints_url_without_liveness_gate(
    monkeypatch, tmp_path: Path
) -> None:
    record = _install_start_here_fakes(monkeypatch, tmp_path, live=False)

    def fail_liveness(_plan):
        raise AssertionError("--no-interactive must not gate on a liveness probe")

    monkeypatch.setattr(cli, "url_liveness_ok", fail_liveness, raising=False)

    result = runner.invoke(
        cli.app,
        [
            "start-here",
            "https://github.com/org/authentic",
            "bonsai-authentic",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 0
    assert record["config_initializer"] is None
    assert "✅ done" not in result.stdout
    assert "https://main.authentic.localhost (Caddy route)" in result.stdout


def test_start_here_stops_when_git_missing(monkeypatch, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    clone_calls: list[str] = []

    def fake_preflight_report(_runner, _repo_path=None, _home=None):
        return DoctorReport(
            checks=(
                DoctorCheck(
                    "git", "fail", "git command not found", id="git", hint="Install Git"
                ),
            )
        )

    monkeypatch.setattr(cli, "SubprocessRunner", lambda: SimpleNamespace(), raising=False)
    monkeypatch.setattr(cli, "preflight_report", fake_preflight_report, raising=False)
    monkeypatch.setattr(
        cli,
        "execute_clone",
        lambda *a, **k: clone_calls.append("clone"),
        raising=False,
    )
    monkeypatch.chdir(parent)

    result = runner.invoke(
        cli.app,
        ["start-here", "https://github.com/org/authentic", "bonsai-authentic"],
    )

    assert result.exit_code == 1
    assert clone_calls == []
    assert "brew install git" in result.stdout
