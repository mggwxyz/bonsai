import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console
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
    assert "bonsai 0.1.14" in result.stdout


def test_help_lists_core_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "clone" in result.stdout
    assert "add" in result.stdout
    assert "remove" in result.stdout
    assert "checkout" in result.stdout
    assert "open" in result.stdout
    assert "shell-init" in result.stdout
    assert "install-shell" in result.stdout
    assert "init" in result.stdout
    assert "doctor" in result.stdout
    assert "agent-guide" in result.stdout
    assert "context" in result.stdout


def test_agent_guide_prints_package_level_agent_rules() -> None:
    result = runner.invoke(cli.app, ["agent-guide"])

    assert result.exit_code == 0
    assert "Bonsai agent guide" in result.stdout
    assert "Do not guess ports or localhost URLs" in result.stdout
    assert "bonsai context --format json" in result.stdout
    assert "bonsai start" in result.stdout
    assert "bonsai sync --apply" in result.stdout


def test_agent_guide_json_prints_machine_readable_rules() -> None:
    result = runner.invoke(cli.app, ["agent-guide", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "bonsai.agent-guide.v1"
    assert "Do not guess ports or localhost URLs." in payload["rules"]
    assert payload["commands"]["context"] == "bonsai context --format json"
    assert payload["commands"]["start"] == "bonsai start"
    assert payload["commands"]["sync"] == "bonsai sync --apply"


def test_agent_guide_rejects_unknown_format() -> None:
    result = runner.invoke(cli.app, ["agent-guide", "--format", "xml"])

    assert result.exit_code == 1
    assert "Unsupported format: xml" in result.stdout


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
    monkeypatch.chdir(tmp_path / "ma-123-test")

    result = runner.invoke(cli.app, ["open"])

    assert result.exit_code == 0
    assert opened_urls == ["https://ma-123-test.authentic.localhost"]
    assert "Opened https://ma-123-test.authentic.localhost" in result.stdout


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


def test_list_command_shows_default_and_managed_worktrees(tmp_path: Path, monkeypatch) -> None:
    write_checkout_workspace(tmp_path)
    monkeypatch.chdir(tmp_path / "main")

    result = runner.invoke(cli.app, ["list"])

    assert result.exit_code == 0
    assert "Worktrees for authentic" in result.stdout
    assert "main" in result.stdout
    assert "MA-123-test" in result.stdout
    assert "ma-123-test" in result.stdout
    assert "default" in result.stdout
    assert "managed" in result.stdout


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
