from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from bonsai import cli
from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import BonsaiState, ManagedWorktree
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
    assert "bonsai 0.1.3" in result.stdout


def test_help_lists_core_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "clone" in result.stdout
    assert "add" in result.stdout
    assert "checkout" in result.stdout
    assert "shell-init" in result.stdout
    assert "install-shell" in result.stdout
    assert "init" in result.stdout
    assert "doctor" in result.stdout


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


def test_checkout_path_reports_unknown_worktree(monkeypatch, tmp_path: Path) -> None:
    write_checkout_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["checkout", "--path", "missing"])

    assert result.exit_code == 1
    assert "Error: Unknown worktree: missing" in result.stdout


def test_shell_init_zsh_prints_checkout_wrapper() -> None:
    result = runner.invoke(cli.app, ["shell-init", "zsh"])

    assert result.exit_code == 0
    assert "bonsai() {" in result.stdout
    assert 'if [[ "$1" == "checkout" ]]; then' in result.stdout
    assert "shift" in result.stdout
    assert 'path="$(command bonsai checkout --path "$@")"' in result.stdout
    assert "status=$?" in result.stdout
    assert 'printf "%s\\n" "$path" >&2' in result.stdout
    assert "return $status" in result.stdout
    assert 'cd "$path"' in result.stdout
    assert 'command bonsai "$@"' in result.stdout


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
