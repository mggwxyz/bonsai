from pathlib import Path

import pytest
from test_config import VALID_CONFIG, write_config

from bonsai.caddy import caddy_reload_plan, caddy_setup_plan
from bonsai.config import load_config
from bonsai.errors import BonsaiCommandError, BonsaiWorkspaceError
from bonsai.git import (
    clone_default_branch,
    discover_default_branch,
    parse_default_branch,
    remote_branch_exists,
)
from bonsai.models import BonsaiState, CommandResult, CommandSpec, FileWrite, ManagedWorktree
from bonsai.ports import allocate_slot
from bonsai.process import RecordingRunner
from bonsai.state import load_state, save_state
from bonsai.workflows import (
    command_summary,
    execute_add,
    execute_clone,
    plan_add_files,
    plan_clone_workspace,
    write_files,
)


def test_allocate_slot_uses_lowest_available_positive_integer() -> None:
    worktrees = {
        "a": ManagedWorktree(path="a", slug="a", slot=1),
        "c": ManagedWorktree(path="c", slug="c", slot=3),
    }

    assert allocate_slot(worktrees) == 2


def test_allocate_slot_returns_one_for_empty_state() -> None:
    assert allocate_slot({}) == 1


def test_parse_default_branch_from_ls_remote_symref() -> None:
    output = "ref: refs/heads/staging\tHEAD\nabc123\tHEAD\n"

    assert parse_default_branch(output) == "staging"


def test_discover_default_branch_terminates_options_before_git_url() -> None:
    class DefaultBranchRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            return CommandResult(returncode=0, stdout="ref: refs/heads/main\tHEAD\n")

    runner = DefaultBranchRunner()

    discover_default_branch(runner, "-bad-url")

    assert runner.commands == [
        CommandSpec(argv=("git", "ls-remote", "--symref", "--", "-bad-url", "HEAD"))
    ]


def test_clone_default_branch_terminates_options_before_git_url() -> None:
    runner = RecordingRunner()

    clone_default_branch(runner, "-bad-url", "main", Path("/tmp/repo"))

    assert runner.commands == [
        CommandSpec(argv=("git", "clone", "--branch", "main", "--", "-bad-url", "/tmp/repo"))
    ]


def test_remote_branch_exists_uses_checked_runner_behavior() -> None:
    class FailingRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            if check:
                raise BonsaiCommandError("network failure")
            return CommandResult(returncode=1)

    with pytest.raises(BonsaiCommandError, match="network failure"):
        remote_branch_exists(FailingRunner(), Path("/tmp/repo"), "feature")


def test_recording_runner_captures_commands_without_running_them() -> None:
    runner = RecordingRunner()

    result = runner.run(["git", "status"], cwd=Path("/tmp/repo"))

    assert result.returncode == 0
    assert runner.commands == [CommandSpec(argv=("git", "status"), cwd=Path("/tmp/repo"))]


def test_caddy_setup_plan_installs_and_starts_when_missing() -> None:
    plan = caddy_setup_plan(
        auto_install=True,
        auto_start=True,
        caddy_exists=False,
        brew_exists=True,
    )

    assert [command.argv for command in plan] == [
        ("brew", "install", "caddy"),
        ("brew", "services", "start", "caddy"),
    ]


def test_caddy_reload_plan_targets_workspace_caddyfile() -> None:
    plan = caddy_reload_plan(Path("/tmp/authentic/Caddyfile"))

    assert plan.argv == ("caddy", "reload", "--config", "/tmp/authentic/Caddyfile")


def test_plan_clone_workspace_uses_discovered_default_branch(tmp_path: Path) -> None:
    (tmp_path / "main").mkdir()
    config_path = write_config(tmp_path / "main", VALID_CONFIG)
    config = load_config(config_path)

    plan = plan_clone_workspace(
        git_url="git@github.com:org/authentic.git",
        name="authentic",
        default_branch="main",
        config=config,
        parent=tmp_path,
    )

    assert plan.workspace_root == tmp_path / "authentic"
    assert plan.default_worktree == tmp_path / "authentic" / "main"
    assert plan.state.default_branch == "main"
    assert plan.state.default_worktree == "main"


def test_plan_add_files_renders_env_caddy_and_state(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    plan = plan_add_files(
        config=config,
        state=state,
        workspace_root=tmp_path / "authentic",
        branch="MB-2036-multi-worktree-port-slots",
    )

    assert plan.worktree_path == tmp_path / "authentic" / "mb-2036-multi-worktree-port-slots"
    assert plan.slot == 1
    worktree = plan.updated_state.worktrees["MB-2036-multi-worktree-port-slots"]
    assert worktree.path == "mb-2036-multi-worktree-port-slots"
    assert worktree.slot == 1
    assert ".env.local" in {path.name for path in plan.files}
    assert "mb-2036-multi-worktree-port-slots-frontend.caddy" in {
        path.name for path in plan.files
    }
    assert "mb-2036-multi-worktree-port-slots-api.caddy" in {path.name for path in plan.files}


def test_plan_clone_workspace_rejects_unsafe_workspace_name(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))

    with pytest.raises(BonsaiWorkspaceError, match="Invalid workspace name"):
        plan_clone_workspace(
            git_url="git@github.com:org/authentic.git",
            name="../escape",
            default_branch="main",
            config=config,
            parent=tmp_path,
        )


def test_plan_clone_workspace_rejects_unsafe_snippets_dir(tmp_path: Path) -> None:
    config_text = VALID_CONFIG.replace(
        'snippets_dir = "caddy.d"',
        'snippets_dir = "../outside"',
    )
    config = load_config(
        write_config(tmp_path, config_text)
    )

    with pytest.raises(BonsaiWorkspaceError, match="Invalid caddy snippets_dir"):
        plan_clone_workspace(
            git_url="git@github.com:org/authentic.git",
            name="authentic",
            default_branch="main",
            config=config,
            parent=tmp_path,
        )


def test_plan_clone_workspace_rejects_unsafe_root_caddyfile(tmp_path: Path) -> None:
    config_text = VALID_CONFIG.replace(
        'root_caddyfile = "Caddyfile"',
        'root_caddyfile = "/tmp/Caddyfile"',
    )
    config = load_config(
        write_config(tmp_path, config_text)
    )

    with pytest.raises(BonsaiWorkspaceError, match="Invalid caddy root_caddyfile"):
        plan_clone_workspace(
            git_url="git@github.com:org/authentic.git",
            name="authentic",
            default_branch="main",
            config=config,
            parent=tmp_path,
        )


def test_plan_add_files_rejects_unsafe_service_name(tmp_path: Path) -> None:
    config_text = VALID_CONFIG.replace(
        '[[services]]\nname = "frontend"',
        '[[services]]\nname = "../frontend"',
    )
    config = load_config(
        write_config(tmp_path, config_text)
    )
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Invalid service name"):
        plan_add_files(
            config=config,
            state=state,
            workspace_root=tmp_path / "authentic",
            branch="MB-2036-multi-worktree-port-slots",
        )


def test_plan_add_files_uses_safe_slug_for_absolute_branch_path(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )
    workspace_root = tmp_path / "authentic"

    plan = plan_add_files(
        config=config,
        state=state,
        workspace_root=workspace_root,
        branch="/tmp/outside",
    )

    assert plan.branch == "/tmp/outside"
    assert plan.worktree_path == workspace_root / "tmp-outside"
    assert plan.worktree_path.is_relative_to(workspace_root)
    assert plan.updated_state.worktrees["/tmp/outside"].path == "tmp-outside"


def test_plan_add_files_uses_safe_slug_for_parent_relative_branch_path(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )
    workspace_root = tmp_path / "authentic"

    plan = plan_add_files(
        config=config,
        state=state,
        workspace_root=workspace_root,
        branch="../outside",
    )

    assert plan.branch == "../outside"
    assert plan.worktree_path == workspace_root / "outside"
    assert plan.worktree_path.is_relative_to(workspace_root)
    assert plan.updated_state.worktrees["../outside"].path == "outside"


def test_plan_add_files_rejects_branch_with_empty_slug(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Invalid branch slug"):
        plan_add_files(
            config=config,
            state=state,
            workspace_root=tmp_path / "authentic",
            branch="???",
        )


def test_write_files_creates_parent_directories(tmp_path: Path) -> None:
    write_files((FileWrite(path=tmp_path / "a" / "b.txt", content="hello\n"),))

    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "hello\n"


def test_command_summary_formats_command_and_cwd() -> None:
    summary = command_summary(
        CommandSpec(argv=("yarn", "install"), cwd=Path("/tmp/authentic/main"))
    )

    assert summary == "cd /tmp/authentic/main && yarn install"


def test_command_summary_shell_quotes_command_and_cwd() -> None:
    summary = command_summary(
        CommandSpec(argv=("python", "-c", "print(1)"), cwd=Path("/tmp/space dir"))
    )

    assert summary == "cd '/tmp/space dir' && python -c 'print(1)'"


def test_caddy_reload_command_is_displayable() -> None:
    command = caddy_reload_plan(Path("/tmp/authentic/Caddyfile"))

    assert command_summary(command) == "caddy reload --config /tmp/authentic/Caddyfile"


def test_execute_clone_rejects_unsafe_name_before_git_commands(tmp_path: Path) -> None:
    class CloneRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            return CommandResult(returncode=0, stdout="ref: refs/heads/main\tHEAD\n")

    runner = CloneRunner()

    with pytest.raises(BonsaiWorkspaceError, match="Invalid workspace name"):
        execute_clone(runner, "git@github.com:org/authentic.git", "../escape", tmp_path)

    assert runner.commands == []


def test_execute_add_uses_slug_path_when_adding_git_worktree(tmp_path: Path) -> None:
    runner = RecordingRunner()
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
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    plan = execute_add(runner, "../outside", workspace_root)

    assert plan.worktree_path == workspace_root / "outside"
    assert runner.commands[2].argv == (
        "git",
        "-C",
        str(default_worktree),
        "worktree",
        "add",
        "-b",
        "../outside",
        str(workspace_root / "outside"),
        "origin/main",
    )


def test_execute_add_repairs_existing_worktree_path_without_git_add(tmp_path: Path) -> None:
    class ExistingWorktreeRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["rev-parse", "--is-inside-work-tree"]:
                return CommandResult(returncode=0, stdout="true\n")
            if argv[-3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="feature\n")
            return CommandResult(returncode=0)

    runner = ExistingWorktreeRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    plan = execute_add(runner, "feature", workspace_root)

    assert plan.worktree_path == branch_worktree
    assert (branch_worktree / ".env.local").exists()
    assert (workspace_root / "caddy.d" / "feature-frontend.caddy").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.worktrees["feature"].path == "feature"
    assert all("worktree" not in command.argv for command in runner.commands)
    assert runner.commands[-1] == CommandSpec(argv=("yarn", "install"), cwd=branch_worktree)


def test_execute_add_rejects_unrelated_existing_directory(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(BonsaiWorkspaceError, match="not a git worktree"):
        execute_add(runner, "feature", workspace_root)

    assert not (branch_worktree / ".env.local").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert "feature" not in state.worktrees
    assert all(command.argv != ("yarn", "install") for command in runner.commands)


def test_execute_add_rejects_existing_worktree_for_different_branch(tmp_path: Path) -> None:
    class DifferentBranchRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["rev-parse", "--is-inside-work-tree"]:
                return CommandResult(returncode=0, stdout="true\n")
            if argv[-3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="other-branch\n")
            return CommandResult(returncode=0)

    runner = DifferentBranchRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(BonsaiWorkspaceError, match="has branch other-branch"):
        execute_add(runner, "feature", workspace_root)

    assert not (branch_worktree / ".env.local").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert "feature" not in state.worktrees
    assert all(command.argv != ("yarn", "install") for command in runner.commands)


def test_execute_add_parses_quoted_install_command(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        'install = "yarn install"',
        'install = "python -c \\"print(1)\\""',
    )
    write_config(default_worktree, config_text)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    execute_add(runner, "feature", workspace_root)

    assert runner.commands[-1].argv == ("python", "-c", "print(1)")
