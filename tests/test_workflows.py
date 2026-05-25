from dataclasses import replace
from pathlib import Path

import pytest
from test_config import VALID_CONFIG, write_config

from bonsai.caddy import caddy_reload_plan, caddy_setup_plan
from bonsai.config import load_config
from bonsai.errors import BonsaiCommandError, BonsaiConfigError, BonsaiWorkspaceError
from bonsai.git import (
    clone_default_branch,
    discover_default_branch,
    parse_default_branch,
    remote_branch_exists,
    remove_worktree,
    worktree_has_changes,
)
from bonsai.models import (
    BonsaiState,
    CommandResult,
    CommandSpec,
    FileWrite,
    ManagedWorktree,
    SharedFileConfig,
)
from bonsai.ports import allocate_slot
from bonsai.process import RecordingRunner
from bonsai.state import load_state, save_state
from bonsai.workflows import (
    command_summary,
    execute_add,
    execute_checkout,
    execute_clone,
    execute_remove,
    execute_start,
    execute_sync,
    plan_add_files,
    plan_clone_workspace,
    plan_open_url,
    plan_sync,
    resolve_start_target,
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


def test_worktree_has_changes_uses_porcelain_status() -> None:
    class DirtyRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            return CommandResult(returncode=0, stdout=" M src/app.py\n")

    runner = DirtyRunner()

    assert worktree_has_changes(runner, Path("/tmp/repo")) is True
    assert runner.commands == [
        CommandSpec(
            argv=("git", "-C", "/tmp/repo", "status", "--porcelain"),
            cwd=None,
        )
    ]


def test_remove_worktree_passes_force_when_requested() -> None:
    runner = RecordingRunner()

    remove_worktree(runner, Path("/tmp/repo/main"), Path("/tmp/repo/feature"), force=True)

    assert runner.commands == [
        CommandSpec(
            argv=(
                "git",
                "-C",
                "/tmp/repo/main",
                "worktree",
                "remove",
                "--force",
                "/tmp/repo/feature",
            )
        )
    ]


def test_recording_runner_captures_commands_without_running_them() -> None:
    runner = RecordingRunner()

    result = runner.run(["git", "status"], cwd=Path("/tmp/repo"))

    assert result.returncode == 0
    assert runner.commands == [CommandSpec(argv=("git", "status"), cwd=Path("/tmp/repo"))]


def test_recording_runner_captures_stream_commands() -> None:
    runner = RecordingRunner()

    result = runner.run_stream(
        ["yarn", "dev"],
        cwd=Path("/tmp/worktree"),
        env={"FRONTEND_PORT": "4201"},
    )

    assert result == 0
    assert runner.commands == [
        CommandSpec(
            argv=("yarn", "dev"),
            cwd=Path("/tmp/worktree"),
            env=(("FRONTEND_PORT", "4201"),),
        )
    ]


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
    assert plan.symlinks[0].source == tmp_path / "authentic" / "main" / ".env"
    assert plan.symlinks[0].target == (
        tmp_path / "authentic" / "mb-2036-multi-worktree-port-slots" / ".env"
    )


def test_plan_sync_reports_missing_and_stale_generated_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    (feature_worktree / ".env.local").write_text("STALE=1\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    plan = plan_sync(workspace_root)

    actions = {(action.kind, action.path.relative_to(workspace_root)) for action in plan.actions}
    assert ("write", Path("main/.env.local")) in actions
    assert ("write", Path("feature/.env.local")) in actions
    assert ("write", Path("Caddyfile")) in actions
    assert ("write", Path("caddy.d/main-frontend.caddy")) in actions
    assert ("write", Path("caddy.d/feature-frontend.caddy")) in actions
    assert plan.reload_caddy is True


def test_plan_sync_removes_stale_configured_service_snippets(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    stale = snippets_dir / "old-feature-frontend.caddy"
    stale.write_text(
        "https://old-feature.authentic.localhost {\n"
        "\ttls internal\n"
        "\treverse_proxy localhost:4202\n"
        "}\n",
        encoding="utf-8",
    )
    keep = snippets_dir / "handwritten.caddy"
    keep.write_text("http://example.localhost {\n}\n", encoding="utf-8")
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

    plan = plan_sync(workspace_root)

    remove_paths = {action.path for action in plan.actions if action.kind == "remove"}
    assert stale in remove_paths
    assert keep not in remove_paths


def test_plan_sync_preserves_handwritten_service_suffix_collision(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    custom = snippets_dir / "custom-frontend.caddy"
    custom.write_text(
        "https://custom.authentic.localhost {\n"
        '\trespond "custom"\n'
        "}\n",
        encoding="utf-8",
    )
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

    plan = plan_sync(workspace_root)

    remove_paths = {action.path for action in plan.actions if action.kind == "remove"}
    assert custom not in remove_paths


def test_execute_sync_dry_run_does_not_write_files(tmp_path: Path) -> None:
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

    plan = execute_sync(RecordingRunner(), workspace_root, apply=False)

    assert any(action.path == default_worktree / ".env.local" for action in plan.actions)
    assert not (default_worktree / ".env.local").exists()


def test_execute_sync_apply_writes_files_and_reloads_caddy(tmp_path: Path) -> None:
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

    plan = execute_sync(runner, workspace_root, apply=True)

    assert (default_worktree / ".env.local").exists()
    assert (workspace_root / "Caddyfile").exists()
    assert (workspace_root / "caddy.d" / "main-frontend.caddy").exists()
    assert plan.reload_caddy is True
    assert runner.commands[-1] == caddy_reload_plan(workspace_root / "Caddyfile")


def test_execute_sync_apply_removes_stale_generated_snippet(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    stale = snippets_dir / "old-feature-frontend.caddy"
    stale.write_text(
        "https://old-feature.authentic.localhost {\n"
        "\ttls internal\n"
        "\treverse_proxy localhost:4202\n"
        "}\n",
        encoding="utf-8",
    )
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

    plan = execute_sync(runner, workspace_root, apply=True)

    remove_paths = {action.path for action in plan.actions if action.kind == "remove"}
    assert stale in remove_paths
    assert not stale.exists()


def test_execute_sync_apply_skips_caddy_reload_without_public_services(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(
        default_worktree,
        """
name = "authentic"
base_branch = "main"

[commands]
start = "yarn dev"

[[services]]
name = "db"
port_env = "DB_PORT"
base_port = 5555
public = false
""",
    )
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

    plan = execute_sync(runner, workspace_root, apply=True)

    assert plan.reload_caddy is False
    assert runner.commands == []


def test_plan_open_url_renders_primary_url_for_current_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "mb-2036-multi-worktree-port-slots"
    nested_dir = branch_worktree / "src"
    nested_dir.mkdir(parents=True)
    default_worktree.mkdir()
    config_text = VALID_CONFIG.replace(
        'url = "https://${slug}.authentic.localhost"',
        'url = "https://${slug}-${FRONTEND_PORT}.authentic.localhost"',
        1,
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
            worktrees={
                "MB-2036-multi-worktree-port-slots": ManagedWorktree(
                    path="mb-2036-multi-worktree-port-slots",
                    slug="mb-2036-multi-worktree-port-slots",
                    slot=2,
                )
            },
        ),
    )

    plan = plan_open_url(workspace_root, nested_dir)

    assert plan.branch == "MB-2036-multi-worktree-port-slots"
    assert plan.worktree_path == branch_worktree
    assert plan.url == "https://mb-2036-multi-worktree-port-slots-4202.authentic.localhost"


def test_plan_open_url_rejects_directory_outside_worktree(tmp_path: Path) -> None:
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

    with pytest.raises(BonsaiWorkspaceError, match="Current directory is not inside"):
        plan_open_url(workspace_root, workspace_root)


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


@pytest.mark.parametrize(
    ("source", "target", "message"),
    [
        ("../.env", ".env", "Invalid shared file source"),
        ("/tmp/.env", ".env", "Invalid shared file source"),
        ("", ".env", "Invalid shared file source"),
        ("config/.env", ".env", "Invalid shared file source"),
        (".env", "../.env", "Invalid shared file target"),
        (".env", "/tmp/.env", "Invalid shared file target"),
        (".env", "", "Invalid shared file target"),
        (".env", "config/.env", "Invalid shared file target"),
    ],
)
def test_plan_add_files_rejects_unsafe_shared_file_path(
    tmp_path: Path,
    source: str,
    target: str,
    message: str,
) -> None:
    config = replace(
        load_config(write_config(tmp_path, VALID_CONFIG)),
        shared_files=(SharedFileConfig(source=source, target=target),),
    )
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match=message):
        plan_add_files(
            config=config,
            state=state,
            workspace_root=tmp_path / "authentic",
            branch="feature",
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


def test_execute_clone_initializes_missing_config_after_clone(tmp_path: Path) -> None:
    class MissingConfigCloneRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[:3] == ["git", "ls-remote", "--symref"]:
                return CommandResult(returncode=0, stdout="ref: refs/heads/main\tHEAD\n")
            if argv[:3] == ["git", "clone", "--branch"]:
                Path(argv[-1]).mkdir(parents=True)
                return CommandResult(returncode=0)
            return CommandResult(returncode=0)

    runner = MissingConfigCloneRunner()
    initializer_calls = []

    def initializer(
        config_path: Path,
        workspace_name: str,
        default_branch: str,
        default_worktree: Path,
    ) -> None:
        initializer_calls.append(
            (config_path, workspace_name, default_branch, default_worktree)
        )
        config_path.write_text(VALID_CONFIG, encoding="utf-8")

    plan = execute_clone(
        runner,
        "git@github.com:org/authentic.git",
        "authentic",
        tmp_path,
        config_initializer=initializer,
    )

    assert initializer_calls == [
        (
            tmp_path / "authentic" / ".bonsai.toml",
            "authentic",
            "main",
            tmp_path / "authentic" / "main",
        )
    ]
    assert plan.workspace_root == tmp_path / "authentic"
    assert (tmp_path / "authentic" / ".bonsai" / "state.json").exists()
    assert (tmp_path / "authentic" / "Caddyfile").exists()


def test_execute_clone_uses_repo_config_when_root_config_is_missing(tmp_path: Path) -> None:
    class RepoConfigCloneRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            if argv[:3] == ["git", "ls-remote", "--symref"]:
                return CommandResult(returncode=0, stdout="ref: refs/heads/main\tHEAD\n")
            if argv[:3] == ["git", "clone", "--branch"]:
                target = Path(argv[-1])
                target.mkdir(parents=True)
                write_config(target, VALID_CONFIG)
            return CommandResult(returncode=0)

    plan = execute_clone(
        RepoConfigCloneRunner(),
        "git@github.com:org/authentic.git",
        "authentic",
        tmp_path,
    )

    assert plan.workspace_root == tmp_path / "authentic"
    assert (tmp_path / "authentic" / "Caddyfile").exists()


def test_execute_add_uses_slug_path_when_adding_git_worktree(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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
    assert (workspace_root / "outside" / ".env").is_symlink()
    assert (workspace_root / "outside" / ".env").resolve() == default_worktree / ".env"


def test_execute_add_reloads_workspace_caddyfile_after_writing_snippets(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    assert (workspace_root / "caddy.d" / "feature-frontend.caddy").exists()
    assert CommandSpec(
        argv=("caddy", "reload", "--config", str(workspace_root / "Caddyfile"))
    ) in runner.commands


def test_execute_add_prefers_workspace_root_config_over_repo_config(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(
        default_worktree,
        VALID_CONFIG.replace('setup = "yarn setup"', 'setup = "yarn repo-setup"'),
    )
    root_config = VALID_CONFIG.replace(
        'setup = "yarn setup"',
        'setup = "python -c \\"print(2)\\""',
    ).replace("base_port = 5555", "base_port = 6000")
    write_config(workspace_root, root_config)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    assert runner.commands[-1].argv == ("python", "-c", "print(2)")
    assert dict(runner.commands[-1].env)["DB_PORT"] == "6001"


def test_execute_add_repairs_existing_worktree_path_without_git_add(tmp_path: Path) -> None:
    class ExistingWorktreeRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
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
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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
    assert (branch_worktree / ".env").is_symlink()
    assert (branch_worktree / ".env").resolve() == default_worktree / ".env"
    assert (workspace_root / "caddy.d" / "feature-frontend.caddy").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.worktrees["feature"].path == "feature"
    assert all("worktree" not in command.argv for command in runner.commands)
    assert runner.commands[-2].argv == ("yarn", "install")
    assert runner.commands[-2].cwd == branch_worktree
    assert runner.commands[-1].argv == ("yarn", "setup")
    assert runner.commands[-1].cwd == branch_worktree


def test_execute_add_keeps_existing_correct_shared_file_symlink_on_repair(
    tmp_path: Path,
) -> None:
    class ExistingWorktreeRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
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
    source = default_worktree / ".env"
    source.write_text("SECRET=value\n", encoding="utf-8")
    target = branch_worktree / ".env"
    target.symlink_to(source)
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

    assert target.is_symlink()
    assert target.resolve() == source
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.worktrees["feature"].path == "feature"


def test_execute_add_rejects_conflicting_shared_file_target_without_saving_state(
    tmp_path: Path,
) -> None:
    class ExistingWorktreeRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
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
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (branch_worktree / ".env").write_text("local secret\n", encoding="utf-8")
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

    with pytest.raises(BonsaiWorkspaceError, match="Shared file target already exists"):
        execute_add(runner, "feature", workspace_root)

    assert not (branch_worktree / ".env.local").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert "feature" not in state.worktrees


def test_execute_add_rejects_missing_shared_file_source_without_saving_state(
    tmp_path: Path,
) -> None:
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

    with pytest.raises(BonsaiWorkspaceError, match="Shared file source does not exist"):
        execute_add(runner, "feature", workspace_root)

    assert not (workspace_root / "feature" / ".env.local").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert "feature" not in state.worktrees


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
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    assert runner.commands[-2].argv == ("python", "-c", "print(1)")
    assert runner.commands[-1].argv == ("yarn", "setup")


def test_execute_add_runs_setup_after_install(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        'setup = "yarn setup"',
        'setup = "python -c \\"print(2)\\""',
    )
    write_config(default_worktree, config_text)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    assert runner.commands[-2].argv == ("yarn", "install")
    assert runner.commands[-2].cwd == workspace_root / "feature"
    assert runner.commands[-1].argv == ("python", "-c", "print(2)")
    assert runner.commands[-1].cwd == workspace_root / "feature"


def test_execute_add_runs_setup_with_generated_worktree_env(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    setup_env = dict(runner.commands[-1].env)
    assert setup_env["COMPOSE_PROJECT_NAME"] == "authentic-feature"
    assert setup_env["FRONTEND_PORT"] == "4201"
    assert setup_env["API_PORT"] == "3334"
    assert setup_env["DB_PORT"] == "5556"


def test_execute_remove_removes_clean_worktree_snippets_and_state(tmp_path: Path) -> None:
    class CleanRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = CleanRemoveRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    snippets = workspace_root / "caddy.d"
    snippets.mkdir()
    (snippets / "feature-frontend.caddy").write_text("feature\n", encoding="utf-8")
    (snippets / "other-frontend.caddy").write_text("other\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
                "other": ManagedWorktree(path="other", slug="other", slot=2),
            },
        ),
    )

    plan = execute_remove(runner, "feature", workspace_root)

    assert plan.branch == "feature"
    assert plan.worktree_path == branch_worktree
    assert plan.removed_snippets == (snippets / "feature-frontend.caddy",)
    assert not (snippets / "feature-frontend.caddy").exists()
    assert (snippets / "other-frontend.caddy").exists()
    assert set(load_state(workspace_root / ".bonsai" / "state.json").worktrees) == {"other"}
    assert runner.commands == [
        CommandSpec(argv=("git", "-C", str(branch_worktree), "status", "--porcelain")),
        CommandSpec(
            argv=(
                "git",
                "-C",
                str(default_worktree),
                "worktree",
                "remove",
                str(branch_worktree),
            )
        ),
        CommandSpec(argv=("caddy", "reload", "--config", str(workspace_root / "Caddyfile"))),
    ]


def test_execute_remove_refuses_dirty_worktree_without_force(tmp_path: Path) -> None:
    class DirtyRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            return CommandResult(returncode=0, stdout=" M README.md\n")

    runner = DirtyRemoveRunner()
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
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    with pytest.raises(BonsaiWorkspaceError, match="has uncommitted changes"):
        execute_remove(runner, "feature", workspace_root)

    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].slot == 1
    assert all("remove" not in command.argv for command in runner.commands)


def test_execute_remove_forces_dirty_worktree_when_requested(tmp_path: Path) -> None:
    class DirtyForceRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout=" M README.md\n")
            return CommandResult(returncode=0)

    runner = DirtyForceRunner()
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
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    execute_remove(runner, "feature", workspace_root, force=True)

    assert runner.commands[-2].argv == (
        "git",
        "-C",
        str(default_worktree),
        "worktree",
        "remove",
        "--force",
        str(branch_worktree),
    )
    assert runner.commands[-1].argv == (
        "caddy",
        "reload",
        "--config",
        str(workspace_root / "Caddyfile"),
    )
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees == {}


def test_execute_remove_rejects_unknown_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
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

    with pytest.raises(BonsaiWorkspaceError, match="Unknown worktree: missing"):
        execute_remove(RecordingRunner(), "missing", workspace_root)


def test_execute_remove_rejects_default_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
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

    with pytest.raises(BonsaiWorkspaceError, match="Cannot remove the default worktree"):
        execute_remove(RecordingRunner(), "main", workspace_root)


def test_execute_remove_preserves_state_when_git_remove_fails(tmp_path: Path) -> None:
    class FailingRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            raise BonsaiCommandError("git worktree remove failed")

    runner = FailingRemoveRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    snippets = workspace_root / "caddy.d"
    snippets.mkdir()
    snippet = snippets / "feature-frontend.caddy"
    snippet.write_text("feature\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    with pytest.raises(BonsaiCommandError, match="git worktree remove failed"):
        execute_remove(runner, "feature", workspace_root)

    assert snippet.exists()
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].slot == 1


def test_execute_checkout_resolves_existing_managed_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    (workspace_root / "feature").mkdir()
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    plan = execute_checkout(RecordingRunner(), "feature", workspace_root)

    assert plan.worktree_path == workspace_root / "feature"
    assert plan.created is False


def test_resolve_start_target_includes_default_worktree(tmp_path: Path) -> None:
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
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    target = resolve_start_target(workspace_root, "main", default_worktree)

    assert target.branch == "main"
    assert target.worktree.path == "main"
    assert target.worktree.slot == 0
    assert target.worktree_path == default_worktree


def test_execute_start_runs_configured_command_with_generated_env(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    (feature_worktree / ".env.local").write_text(
        "FRONTEND_PORT=4201\nCOMPOSE_PROJECT_NAME=authentic-feature\n",
        encoding="utf-8",
    )
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    exit_code = execute_start(runner, workspace_root, "feature", feature_worktree)

    assert exit_code == 0
    assert runner.commands == [
        CommandSpec(
            argv=("yarn", "dev"),
            cwd=feature_worktree,
            env=(
                ("COMPOSE_PROJECT_NAME", "authentic-feature"),
                ("FRONTEND_PORT", "4201"),
            ),
        )
    ]


def test_execute_start_fails_when_start_command_is_missing(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG.replace('start = "yarn dev"\n', ""))
    (default_worktree / ".env.local").write_text("FRONTEND_PORT=4200\n", encoding="utf-8")
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

    with pytest.raises(BonsaiConfigError, match=r"commands.start"):
        execute_start(runner, workspace_root, None, default_worktree)


def test_execute_start_requires_generated_env_file(tmp_path: Path) -> None:
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

    with pytest.raises(BonsaiWorkspaceError, match=r"bonsai sync --apply"):
        execute_start(RecordingRunner(), workspace_root, None, default_worktree)


def test_execute_checkout_adds_missing_branch_with_existing_add_workflow(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    plan = execute_checkout(runner, "feature", workspace_root)

    assert plan.worktree_path == workspace_root / "feature"
    assert plan.created is True
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].path == (
        "feature"
    )
    assert runner.commands[2].argv == (
        "git",
        "-C",
        str(default_worktree),
        "worktree",
        "add",
        "-b",
        "feature",
        str(workspace_root / "feature"),
        "origin/main",
    )
