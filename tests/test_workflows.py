import re
from dataclasses import replace
from pathlib import Path

import pytest
from test_config import VALID_CONFIG, write_config

import bonsai.workflows as workflows
from bonsai.caddy import caddy_reload_plan, caddy_setup_plan
from bonsai.config import load_config
from bonsai.errors import BonsaiCommandError, BonsaiConfigError, BonsaiWorkspaceError
from bonsai.git import (
    clone_default_branch,
    discover_default_branch,
    move_worktree,
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
from bonsai.rendering import render_root_caddyfile
from bonsai.state import load_state, save_state
from bonsai.workflows import (
    check_workspace_health,
    command_summary,
    execute_add,
    execute_checkout,
    execute_cleanup,
    execute_clone,
    execute_doctor_apply,
    execute_init,
    execute_move,
    execute_port_repairs,
    execute_remove,
    execute_repair,
    execute_start,
    execute_sync,
    plan_add_files,
    plan_agent_context,
    plan_clone_workspace,
    plan_command_log,
    plan_current_worktree_status,
    plan_move_worktree,
    plan_open_url,
    plan_open_url_for_worktree,
    plan_repair,
    plan_sync,
    plan_workspace_summary,
    resolve_start_target,
    run_lifecycle_command,
    worktree_name_completions,
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


def test_move_worktree_uses_git_worktree_move() -> None:
    runner = RecordingRunner()

    move_worktree(
        runner,
        Path("/tmp/repo/main"),
        Path("/tmp/repo/mb-123-auth"),
        Path("/tmp/repo/MB-123-auth"),
    )

    assert runner.commands == [
        CommandSpec(
            argv=(
                "git",
                "-C",
                "/tmp/repo/main",
                "worktree",
                "move",
                "/tmp/repo/mb-123-auth",
                "/tmp/repo/MB-123-auth",
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


class SelectiveGitWorktreeRunner(RecordingRunner):
    def __init__(self, git_worktrees: set[Path]) -> None:
        super().__init__()
        self.git_worktrees = {path.resolve() for path in git_worktrees}

    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
        if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
            repo = Path(argv[2]).resolve()
            if repo in self.git_worktrees:
                return CommandResult(returncode=0, stdout="true\n")
            return CommandResult(returncode=128, stderr="not a git worktree\n")
        return CommandResult(returncode=0)


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


def test_plan_move_worktree_updates_state_path_preserving_slug_and_slot(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    old_worktree = workspace_root / "mb-123-auth"
    old_worktree.mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "MB-123-auth": ManagedWorktree(
                path="mb-123-auth",
                slug="mb-123-auth",
                slot=4,
            )
        },
    )

    plan = plan_move_worktree(
        state,
        workspace_root,
        "MB-123-auth",
        "MB-123-auth",
    )

    assert plan.branch == "MB-123-auth"
    assert plan.old_worktree_path == old_worktree
    assert plan.new_worktree_path == workspace_root / "MB-123-auth"
    moved = plan.updated_state.worktrees["MB-123-auth"]
    assert moved.path == "MB-123-auth"
    assert moved.slug == "mb-123-auth"
    assert moved.slot == 4


def test_plan_move_worktree_rejects_default_worktree(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Cannot move the default worktree"):
        plan_move_worktree(state, tmp_path / "authentic", "main", "Main")


def test_plan_move_worktree_rejects_unknown_worktree(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Unknown worktree: missing"):
        plan_move_worktree(state, tmp_path / "authentic", "missing", "target")


def test_plan_move_worktree_rejects_missing_default_path_collision(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    target = workspace_root / "main"
    assert not target.exists()
    with pytest.raises(
        BonsaiWorkspaceError,
        match=re.escape(f"Worktree target already exists: {target}"),
    ):
        plan_move_worktree(state, workspace_root, "feature", "main")


def test_plan_move_worktree_rejects_existing_distinct_target(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    (workspace_root / "taken").mkdir()
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Worktree target already exists"):
        plan_move_worktree(state, workspace_root, "feature", "taken")


def test_plan_move_worktree_rejects_managed_path_collision_with_missing_directory(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
            "other": ManagedWorktree(path="taken", slug="other", slot=2),
        },
    )

    target = workspace_root / "taken"
    assert not target.exists()
    with pytest.raises(
        BonsaiWorkspaceError,
        match=re.escape(f"Worktree target already exists: {target}"),
    ):
        plan_move_worktree(state, workspace_root, "feature", "taken")


def test_plan_move_worktree_rejects_other_branch_identifier_collision(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
            "other-branch": ManagedWorktree(
                path="other-path",
                slug="other-slug",
                slot=2,
            ),
        },
    )

    target = workspace_root / "other-branch"
    assert not target.exists()
    with pytest.raises(
        BonsaiWorkspaceError,
        match=re.escape(f"Worktree target already exists: {target}"),
    ):
        plan_move_worktree(state, workspace_root, "feature", "other-branch")


def test_plan_move_worktree_rejects_other_slug_identifier_collision(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
            "other-branch": ManagedWorktree(
                path="other-path",
                slug="other-slug",
                slot=2,
            ),
        },
    )

    target = workspace_root / "other-slug"
    assert not target.exists()
    with pytest.raises(
        BonsaiWorkspaceError,
        match=re.escape(f"Worktree target already exists: {target}"),
    ):
        plan_move_worktree(state, workspace_root, "feature", "other-slug")


def test_plan_move_worktree_allows_own_branch_identifier_as_target(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    old_worktree = workspace_root / "old-folder"
    old_worktree.mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "MB-123": ManagedWorktree(
                path="old-folder",
                slug="mb-123",
                slot=1,
            )
        },
    )

    plan = plan_move_worktree(state, workspace_root, "MB-123", "MB-123")

    assert plan.branch == "MB-123"
    assert plan.old_worktree_path == old_worktree
    assert plan.new_worktree_path == workspace_root / "MB-123"
    moved = plan.updated_state.worktrees["MB-123"]
    assert moved.path == "MB-123"
    assert moved.slug == "mb-123"
    assert moved.slot == 1


def test_plan_move_worktree_allows_case_only_samefile_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    old_worktree = workspace_root / "mb-123"
    new_worktree = workspace_root / "MB-123"
    old_worktree.mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="mb-123", slug="mb-123", slot=1)},
    )

    original_exists = Path.exists

    def fake_exists(path: Path) -> bool:
        if path == new_worktree:
            return True
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(workflows, "_paths_refer_to_same_existing_path", lambda _left, _right: True)

    plan = plan_move_worktree(state, workspace_root, "feature", "MB-123")

    assert plan.old_worktree_path == old_worktree
    assert plan.new_worktree_path == new_worktree
    assert plan.updated_state.worktrees["feature"].path == "MB-123"


def test_plan_move_worktree_rejects_samefile_alias_target(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    feature = workspace_root / "feature"
    alias = workspace_root / "alias"
    feature.mkdir(parents=True)
    alias.symlink_to(feature, target_is_directory=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    assert alias.samefile(feature)
    with pytest.raises(BonsaiWorkspaceError, match="Worktree target already exists"):
        plan_move_worktree(state, workspace_root, "feature", "alias")


def test_plan_move_worktree_rejects_dangling_symlink_target(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    feature = workspace_root / "feature"
    dangling = workspace_root / "dangling"
    feature.mkdir(parents=True)
    dangling.symlink_to("missing-target", target_is_directory=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    assert not dangling.exists()
    assert dangling.is_symlink()
    with pytest.raises(
        BonsaiWorkspaceError,
        match=re.escape(f"Worktree target already exists: {dangling}"),
    ):
        plan_move_worktree(state, workspace_root, "feature", "dangling")


def test_plan_move_worktree_rejects_case_only_samefile_symlink_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    feature = workspace_root / "feature"
    alias = workspace_root / "Feature"
    feature.mkdir(parents=True)
    try:
        alias.symlink_to(feature, target_is_directory=True)
    except FileExistsError:
        original_is_symlink = Path.is_symlink

        def fake_is_symlink(path: Path) -> bool:
            if path == alias:
                return True
            return original_is_symlink(path)

        monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
        monkeypatch.setattr(
            workflows,
            "_paths_refer_to_same_existing_path",
            lambda _left, _right: True,
        )
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    assert alias.is_symlink()
    with pytest.raises(BonsaiWorkspaceError, match="Worktree target already exists"):
        plan_move_worktree(state, workspace_root, "feature", "Feature")


def test_plan_move_worktree_rejects_unsafe_target_folder(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Invalid worktree folder"):
        plan_move_worktree(state, workspace_root, "feature", "../outside")


def test_plan_move_worktree_rejects_same_folder_name(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Worktree already uses folder"):
        plan_move_worktree(state, workspace_root, "feature", "feature")


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


def test_plan_agent_context_reports_current_worktree_services_and_env_status(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    expected_env = (
        "# Generated by bonsai. Do not edit by hand.\n"
        "SLOT=1\n"
        "FRONTEND_PORT=4201\n"
        "API_PORT=3334\n"
        "DB_PORT=5556\n"
        "\n"
        "COMPOSE_PROJECT_NAME=authentic-feature\n"
    )
    (feature_worktree / ".env.local").write_text(expected_env, encoding="utf-8")
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

    context = plan_agent_context(workspace_root, feature_worktree)

    assert context.workspace_name == "authentic"
    assert context.workspace_root == workspace_root
    assert context.config_path == default_worktree / ".bonsai.toml"
    assert context.branch == "feature"
    assert context.worktree_path == feature_worktree
    assert context.slot == 1
    assert context.env_file_path == feature_worktree / ".env.local"
    assert context.env_file_status == "current"
    assert context.generated_env["FRONTEND_PORT"] == "4201"
    assert context.generated_env["COMPOSE_PROJECT_NAME"] == "authentic-feature"
    assert context.commands["start"] == "bonsai start"
    assert context.services[0].name == "frontend"
    assert context.services[0].port_env == "FRONTEND_PORT"
    assert context.services[0].port == 4201
    assert context.services[0].url == "https://feature.authentic.localhost"
    assert context.services[2].name == "db"
    assert context.services[2].public is False
    assert context.services[2].url is None


def test_parse_env_content_ignores_comments_and_blank_lines() -> None:
    from bonsai.env import parse_env_content

    content = """
# Generated by bonsai. Do not edit by hand.
SLOT=1

COMPOSE_PROJECT_NAME=authentic-feature
VALUE_WITH_EQUALS=a=b
"""

    assert parse_env_content(content) == {
        "SLOT": "1",
        "COMPOSE_PROJECT_NAME": "authentic-feature",
        "VALUE_WITH_EQUALS": "a=b",
    }


def test_plan_agent_context_marks_missing_generated_env(tmp_path: Path) -> None:
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

    context = plan_agent_context(workspace_root, default_worktree)

    assert context.branch == "main"
    assert context.slot == 0
    assert context.env_file_status == "missing"
    assert context.generated_env["FRONTEND_PORT"] == "4200"


def test_plan_agent_context_marks_stale_generated_env(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env.local").write_text("FRONTEND_PORT=9999\n", encoding="utf-8")
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

    context = plan_agent_context(workspace_root, default_worktree)

    assert context.env_file_status == "stale"
    assert context.generated_env["FRONTEND_PORT"] == "4200"


def test_plan_workspace_summary_includes_default_managed_ports_urls_and_env_status(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    feature_worktree.joinpath(".env.local").write_text(
        "# Generated by bonsai. Do not edit by hand.\n"
        "SLOT=2\n"
        "FRONTEND_PORT=4202\n"
        "API_PORT=3335\n"
        "DB_PORT=5557\n"
        "\n"
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
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
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=2)},
        ),
    )

    summary = plan_workspace_summary(workspace_root)

    assert summary.workspace_name == "authentic"
    assert summary.workspace_root == workspace_root
    assert summary.default_branch == "main"
    assert summary.default_worktree == "main"
    assert summary.config_path == default_worktree / ".bonsai.toml"
    assert summary.commands["status"] == "bonsai status"
    assert [worktree.branch for worktree in summary.worktrees] == ["main", "feature"]

    default_summary = summary.worktrees[0]
    assert default_summary.relative_path == "main"
    assert default_summary.kind == "default"
    assert default_summary.slot == 0
    assert default_summary.env_file_status == "missing"
    assert [(service.port_env, service.port) for service in default_summary.services] == [
        ("FRONTEND_PORT", 4200),
        ("API_PORT", 3333),
        ("DB_PORT", 5555),
    ]
    assert default_summary.services[0].url == "https://main.authentic.localhost"
    assert default_summary.services[1].url == "https://api-main.authentic.localhost"
    assert default_summary.services[2].url is None

    feature_summary = summary.worktrees[1]
    assert feature_summary.worktree_path == feature_worktree
    assert feature_summary.relative_path == "feature"
    assert feature_summary.kind == "managed"
    assert feature_summary.slug == "feature"
    assert feature_summary.slot == 2
    assert feature_summary.env_file_path == feature_worktree / ".env.local"
    assert feature_summary.env_file_status == "current"
    assert [(service.port_env, service.port) for service in feature_summary.services] == [
        ("FRONTEND_PORT", 4202),
        ("API_PORT", 3335),
        ("DB_PORT", 5557),
    ]
    assert feature_summary.services[0].url == "https://feature.authentic.localhost"
    assert feature_summary.services[1].url == "https://api-feature.authentic.localhost"
    assert feature_summary.services[2].url is None


def test_plan_workspace_summary_marks_stale_generated_env(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    default_worktree.joinpath(".env.local").write_text("FRONTEND_PORT=9999\n", encoding="utf-8")
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

    summary = plan_workspace_summary(workspace_root)

    assert summary.worktrees[0].env_file_status == "stale"


def test_plan_workspace_summary_wraps_unreadable_generated_env(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    env_file_path = default_worktree / ".env.local"
    env_file_path.write_bytes(b"\xff")
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

    with pytest.raises(
        BonsaiWorkspaceError,
        match=rf"Unable to read generated env file at {re.escape(str(env_file_path))}",
    ):
        plan_workspace_summary(workspace_root)


def test_plan_current_worktree_status_resolves_current_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
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

    status = plan_current_worktree_status(workspace_root, feature_worktree / "src")

    assert status.workspace_name == "authentic"
    assert status.workspace_root == workspace_root
    assert status.default_branch == "main"
    assert status.default_worktree == "main"
    assert status.config_path == default_worktree / ".bonsai.toml"
    assert status.current.branch == "feature"
    assert status.current.worktree_path == feature_worktree
    assert status.current.relative_path == "feature"
    assert status.current.kind == "managed"
    assert status.current.slot == 1
    assert status.commands["list"] == "bonsai list"


def test_plan_current_worktree_status_reports_workspace_root_location(
    tmp_path: Path,
) -> None:
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

    status = plan_current_worktree_status(workspace_root, workspace_root)

    assert status.workspace_name == "authentic"
    assert status.workspace_root == workspace_root
    assert status.location_kind == "workspace_root"
    assert status.location_path == workspace_root
    assert status.current is None
    assert status.commands["list"] == "bonsai list"


def test_plan_workspace_summary_reports_invalid_service_url_template(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        'url = "https://${slug}.authentic.localhost"',
        'url = "https://${missing}.authentic.localhost"',
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
            worktrees={},
        ),
    )

    with pytest.raises(
        BonsaiConfigError,
        match="Service frontend URL uses unknown template key: missing",
    ):
        plan_workspace_summary(workspace_root)


def test_plan_sync_removes_stale_configured_service_snippets(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    stale = snippets_dir / "old-feature-frontend.caddy"
    stale.write_text(
        "# Generated by bonsai. Do not edit by hand.\n"
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

    plan = plan_sync(workspace_root)

    remove_paths = {action.path for action in plan.actions if action.kind == "remove"}
    assert custom not in remove_paths


def test_plan_sync_removes_marked_stale_unknown_service_snippet(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    stale = snippets_dir / "old-feature-removed-service.caddy"
    stale.write_text(
        "# Generated by bonsai. Do not edit by hand.\n"
        "https://old-feature.authentic.localhost {\n"
        "\ttls internal\n"
        "\treverse_proxy localhost:9000\n"
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
    assert stale in remove_paths


def test_plan_repair_removes_missing_worktree_and_repacks_survivors(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_a = workspace_root / "feature-a"
    feature_c = workspace_root / "feature-c"
    default_worktree.mkdir(parents=True)
    feature_a.mkdir()
    feature_c.mkdir()
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
                "old-branch": ManagedWorktree(path="old-branch", slug="old-branch", slot=2),
                "feature-c": ManagedWorktree(path="feature-c", slug="feature-c", slot=4),
            },
        ),
    )
    runner = SelectiveGitWorktreeRunner({feature_a, feature_c})

    plan = plan_repair(runner, workspace_root)

    assert plan.state_changed is True
    assert [
        (item.action, item.branch, item.reason, item.old_slot, item.new_slot)
        for item in plan.items
    ] == [
        ("remove", "old-branch", f"missing {workspace_root / 'old-branch'}", 2, None),
        ("repack", "feature-c", "slot 4 -> 2", 4, 2),
    ]
    assert set(plan.updated_state.worktrees) == {"feature-a", "feature-c"}
    assert plan.updated_state.worktrees["feature-a"].slot == 1
    assert plan.updated_state.worktrees["feature-c"].slot == 2


def test_plan_repair_warns_for_existing_path_that_is_not_git_worktree(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    suspicious = workspace_root / "suspicious"
    default_worktree.mkdir(parents=True)
    suspicious.mkdir()
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "suspicious": ManagedWorktree(
                    path="suspicious",
                    slug="suspicious",
                    slot=3,
                )
            },
        ),
    )
    runner = SelectiveGitWorktreeRunner(set())

    plan = plan_repair(runner, workspace_root)

    assert plan.state_changed is False
    assert [
        (item.action, item.branch, item.reason, item.old_slot, item.new_slot)
        for item in plan.items
    ] == [
        (
            "warn",
            "suspicious",
            f"not a git worktree {suspicious}",
            3,
            3,
        )
    ]
    assert plan.updated_state.worktrees["suspicious"].slot == 3


def test_execute_repair_dry_run_does_not_write_state(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_a = workspace_root / "feature-a"
    feature_c = workspace_root / "feature-c"
    default_worktree.mkdir(parents=True)
    feature_a.mkdir()
    feature_c.mkdir()
    state_path = workspace_root / ".bonsai" / "state.json"
    save_state(
        state_path,
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
                "old-branch": ManagedWorktree(path="old-branch", slug="old-branch", slot=2),
                "feature-c": ManagedWorktree(path="feature-c", slug="feature-c", slot=4),
            },
        ),
    )

    plan = execute_repair(
        SelectiveGitWorktreeRunner({feature_a, feature_c}),
        workspace_root,
        apply=False,
    )

    saved_state = load_state(state_path)
    assert plan.state_changed is True
    assert set(saved_state.worktrees) == {"feature-a", "old-branch", "feature-c"}
    assert saved_state.worktrees["feature-c"].slot == 4


def test_execute_repair_apply_writes_repaired_state(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_a = workspace_root / "feature-a"
    feature_c = workspace_root / "feature-c"
    default_worktree.mkdir(parents=True)
    feature_a.mkdir()
    feature_c.mkdir()
    state_path = workspace_root / ".bonsai" / "state.json"
    save_state(
        state_path,
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
                "old-branch": ManagedWorktree(path="old-branch", slug="old-branch", slot=2),
                "feature-c": ManagedWorktree(path="feature-c", slug="feature-c", slot=4),
            },
        ),
    )

    plan = execute_repair(
        SelectiveGitWorktreeRunner({feature_a, feature_c}),
        workspace_root,
        apply=True,
    )

    saved_state = load_state(state_path)
    assert plan.state_changed is True
    assert set(saved_state.worktrees) == {"feature-a", "feature-c"}
    assert saved_state.worktrees["feature-a"].slot == 1
    assert saved_state.worktrees["feature-c"].slot == 2


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
        "# Generated by bonsai. Do not edit by hand.\n"
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


def test_execute_sync_apply_removes_marked_stale_unknown_service_snippet(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    stale = snippets_dir / "old-feature-removed-service.caddy"
    stale.write_text(
        "# Generated by bonsai. Do not edit by hand.\n"
        "https://old-feature.authentic.localhost {\n"
        "\ttls internal\n"
        "\treverse_proxy localhost:9000\n"
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


def test_execute_sync_apply_reloads_caddy_when_removing_last_public_service_snippet(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    (workspace_root / "Caddyfile").write_text(
        render_root_caddyfile(snippets_dir),
        encoding="utf-8",
    )
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
    stale = snippets_dir / "old-feature-frontend.caddy"
    stale.write_text(
        "# Generated by bonsai. Do not edit by hand.\n"
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

    assert plan.reload_caddy is True
    assert not stale.exists()
    assert runner.commands[-1] == caddy_reload_plan(workspace_root / "Caddyfile")


def test_execute_sync_dry_run_keeps_stale_marked_snippet_and_skips_reload(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    (workspace_root / "Caddyfile").write_text(
        render_root_caddyfile(snippets_dir),
        encoding="utf-8",
    )
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
    stale = snippets_dir / "old-feature-frontend.caddy"
    stale.write_text(
        "# Generated by bonsai. Do not edit by hand.\n"
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

    plan = execute_sync(runner, workspace_root, apply=False)

    remove_paths = {action.path for action in plan.actions if action.kind == "remove"}
    assert stale in remove_paths
    assert plan.reload_caddy is True
    assert stale.exists()
    assert runner.commands == []


def test_execute_sync_apply_skips_caddy_reload_without_public_services(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    (workspace_root / "Caddyfile").write_text(
        render_root_caddyfile(snippets_dir),
        encoding="utf-8",
    )
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


def test_check_workspace_health_passes_for_complete_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class HealthyRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[0] == "git" and "rev-parse" in argv:
                return CommandResult(returncode=0, stdout="true\n")
            if argv[0] == "caddy":
                return CommandResult(returncode=0, stdout="v2.8.0\n")
            return CommandResult(returncode=0)

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
    execute_sync(RecordingRunner(), workspace_root, apply=True)
    monkeypatch.setattr("bonsai.workflows._check_port_listening", lambda _port: False)

    report = check_workspace_health(HealthyRunner(), workspace_root)

    assert report.failed is False
    assert all(check.status != "fail" for check in report.checks)


def test_check_workspace_health_fails_for_missing_generated_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class GitRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            if argv[0] == "git" and "rev-parse" in argv:
                return CommandResult(returncode=0, stdout="true\n")
            return CommandResult(returncode=0, stdout="ok\n")

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
    monkeypatch.setattr("bonsai.workflows._check_port_listening", lambda _port: False)

    report = check_workspace_health(GitRunner(), workspace_root)

    assert report.failed is True
    assert any(
        check.name == "env main" and check.hint == "Run: bonsai sync --apply"
        for check in report.checks
    )


def test_check_workspace_health_reports_port_conflicts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class GitRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            if argv[0] == "git" and "rev-parse" in argv:
                return CommandResult(returncode=0, stdout="true\n")
            return CommandResult(returncode=0, stdout="ok\n")

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
    execute_sync(RecordingRunner(), workspace_root, apply=True)
    monkeypatch.setattr("bonsai.workflows._check_port_listening", lambda port: port == 4200)

    report = check_workspace_health(GitRunner(), workspace_root)

    assert report.failed is True
    assert any(check.name == "port 4200" and check.status == "fail" for check in report.checks)


def test_plan_port_repairs_proposes_stable_conflict_free_slots(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
                "feature-b": ManagedWorktree(path="feature-b", slug="feature-b", slot=2),
                "feature-c": ManagedWorktree(path="feature-c", slug="feature-c", slot=3),
            },
        ),
    )
    busy_ports = {4201, 3336, 4204}
    monkeypatch.setattr(
        "bonsai.workflows._check_port_listening",
        lambda port: port in busy_ports,
    )

    plan = workflows.plan_port_repairs(workspace_root)

    assert [(item.branch, item.current_slot, item.proposed_slot) for item in plan.items] == [
        ("feature-a", 1, 5),
        ("feature-c", 3, 6),
    ]
    assert [
        (change.port_env, change.old_port, change.new_port)
        for change in plan.items[0].services
    ] == [
        ("FRONTEND_PORT", 4201, 4205),
        ("API_PORT", 3334, 3338),
        ("DB_PORT", 5556, 5560),
    ]
    assert [
        (change.port_env, change.old_port, change.new_port)
        for change in plan.items[1].services
    ] == [
        ("FRONTEND_PORT", 4203, 4206),
        ("API_PORT", 3336, 3339),
        ("DB_PORT", 5558, 5561),
    ]


def test_plan_port_repairs_ignores_default_slot_conflicts(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
            },
        ),
    )
    monkeypatch.setattr(
        "bonsai.workflows._check_port_listening",
        lambda port: port == 4200,
    )

    plan = workflows.plan_port_repairs(workspace_root)

    assert plan.items == ()


def test_execute_port_repairs_apply_updates_state_and_syncs_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_a = workspace_root / "feature-a"
    feature_b = workspace_root / "feature-b"
    default_worktree.mkdir(parents=True)
    feature_a.mkdir()
    feature_b.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
                "feature-b": ManagedWorktree(path="feature-b", slug="feature-b", slot=2),
            },
        ),
    )
    monkeypatch.setattr(
        "bonsai.workflows._check_port_listening",
        lambda port: port == 4201,
    )

    runner = RecordingRunner()
    plan = execute_port_repairs(runner, workspace_root, apply=True)

    assert [(item.branch, item.current_slot, item.proposed_slot) for item in plan.items] == [
        ("feature-a", 1, 3),
    ]
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.worktrees["feature-a"].slot == 3
    assert state.worktrees["feature-b"].slot == 2
    env_text = (feature_a / ".env.local").read_text(encoding="utf-8")
    assert "FRONTEND_PORT=4203" in env_text
    assert "API_PORT=3336" in env_text
    assert "DB_PORT=5558" in env_text
    assert ("caddy", "reload", "--config", str(workspace_root / "Caddyfile")) in [
        command.argv for command in runner.commands
    ]


def test_execute_port_repairs_preview_does_not_update_state_or_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_a = workspace_root / "feature-a"
    default_worktree.mkdir(parents=True)
    feature_a.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
            },
        ),
    )
    monkeypatch.setattr(
        "bonsai.workflows._check_port_listening",
        lambda port: port == 4201,
    )

    plan = execute_port_repairs(RecordingRunner(), workspace_root, apply=False)

    assert [(item.branch, item.current_slot, item.proposed_slot) for item in plan.items] == [
        ("feature-a", 1, 2),
    ]
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.worktrees["feature-a"].slot == 1
    assert not (feature_a / ".env.local").exists()


def test_execute_doctor_apply_repairs_syncs_and_sets_up_caddy(tmp_path: Path) -> None:
    class DoctorApplyRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
                return CommandResult(returncode=0, stdout="true\n")
            if argv == ["caddy", "version"]:
                return CommandResult(returncode=1, stderr="missing caddy\n")
            if argv == ["brew", "--version"]:
                return CommandResult(returncode=0, stdout="Homebrew 4.0\n")
            return CommandResult(returncode=0)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    missing_worktree = workspace_root / "missing"
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
            worktrees={
                "missing": ManagedWorktree(path="missing", slug="missing", slot=2),
            },
        ),
    )

    runner = DoctorApplyRunner()
    plan = execute_doctor_apply(runner, workspace_root)

    action_kinds = [action.kind for action in plan.actions]
    action_details = [action.detail for action in plan.actions]
    assert action_kinds[:3] == ["repair", "caddy", "caddy"]
    assert set(action_kinds[3:]) == {"sync"}
    assert "removed missing - missing " + str(missing_worktree) in action_details
    assert f"write {default_worktree / '.env.local'}" in action_details
    assert "reload Caddy" in action_details
    assert (default_worktree / ".env.local").exists()
    assert ("brew", "install", "caddy") in [command.argv for command in runner.commands]
    assert ("brew", "services", "start", "caddy") in [command.argv for command in runner.commands]
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees == {}


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


def test_lifecycle_command_failure_includes_log_path(tmp_path: Path) -> None:
    class FailingRunner(RecordingRunner):
        def run_stream_logged(
            self,
            argv: list[str],
            cwd: Path | None = None,
            env=None,
            log_path: Path | None = None,
            label: str | None = None,
        ) -> int:
            super().run_stream_logged(argv, cwd=cwd, env=env, log_path=log_path, label=label)
            return 9

    runner = FailingRunner()
    workspace_root = tmp_path / "authentic"
    worktree_path = workspace_root / "feature"

    with pytest.raises(BonsaiCommandError, match=r"Log: .*install\.log"):
        run_lifecycle_command(
            runner,
            workspace_root=workspace_root,
            worktree_slug="feature",
            kind="install",
            command="yarn install",
            cwd=worktree_path,
            env={"FRONTEND_PORT": "4201"},
            check=True,
        )


def test_lifecycle_command_uses_kind_as_stream_label(tmp_path: Path) -> None:
    class LabelRecordingRunner(RecordingRunner):
        def __init__(self) -> None:
            super().__init__()
            self.labels: list[str | None] = []

        def run_stream_logged(
            self,
            argv: list[str],
            cwd: Path | None = None,
            env=None,
            log_path: Path | None = None,
            label: str | None = None,
        ) -> int:
            self.labels.append(label)
            return super().run_stream_logged(
                argv,
                cwd=cwd,
                env=env,
                log_path=log_path,
                label=label,
            )

    runner = LabelRecordingRunner()

    run_lifecycle_command(
        runner,
        workspace_root=tmp_path / "authentic",
        worktree_slug="feature",
        kind="install",
        command="yarn install",
        cwd=tmp_path / "authentic" / "feature",
        env={},
    )

    run_lifecycle_command(
        runner,
        workspace_root=tmp_path / "authentic",
        worktree_slug="feature",
        kind="setup",
        command="yarn setup",
        cwd=tmp_path / "authentic" / "feature",
        env={},
    )

    assert runner.labels == ["install", "setup"]


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
    class MissingConfigCloneRunner(RecordingRunner):
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


def test_execute_clone_runs_install_and_setup_with_default_worktree_env(
    tmp_path: Path,
) -> None:
    class MissingConfigCloneRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:3] == ["git", "ls-remote", "--symref"]:
                return CommandResult(returncode=0, stdout="ref: refs/heads/main\tHEAD\n")
            if argv[:3] == ["git", "clone", "--branch"]:
                Path(argv[-1]).mkdir(parents=True)
                return CommandResult(returncode=0)
            return CommandResult(returncode=0)

    def initializer(
        config_path: Path,
        _workspace_name: str,
        _default_branch: str,
        _default_worktree: Path,
    ) -> None:
        config_path.write_text(VALID_CONFIG, encoding="utf-8")

    runner = MissingConfigCloneRunner()

    execute_clone(
        runner,
        "git@github.com:org/authentic.git",
        "authentic",
        tmp_path,
        config_initializer=initializer,
    )

    default_worktree = tmp_path / "authentic" / "main"
    assert (default_worktree / ".env.local").exists()
    install_command = runner.commands[-2]
    setup_command = runner.commands[-1]
    assert install_command.argv == ("yarn", "install")
    assert install_command.cwd == default_worktree
    assert install_command.log_path is not None
    assert install_command.log_path.parent == tmp_path / "authentic" / ".bonsai" / "logs" / "main"
    assert install_command.log_path.name.endswith("-install.log")
    assert setup_command.argv == ("yarn", "setup")
    assert setup_command.cwd == default_worktree
    assert setup_command.log_path is not None
    assert setup_command.log_path.parent == tmp_path / "authentic" / ".bonsai" / "logs" / "main"
    assert setup_command.log_path.name.endswith("-setup.log")
    setup_env = dict(setup_command.env)
    assert setup_env["COMPOSE_PROJECT_NAME"] == "authentic-main"
    assert setup_env["FRONTEND_PORT"] == "4200"
    assert setup_env["API_PORT"] == "3333"
    assert setup_env["DB_PORT"] == "5555"


def test_execute_clone_logs_default_branch_with_slash_under_slugged_directory(
    tmp_path: Path,
) -> None:
    class SlashDefaultBranchCloneRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:3] == ["git", "ls-remote", "--symref"]:
                return CommandResult(
                    returncode=0,
                    stdout="ref: refs/heads/release/2026\tHEAD\n",
                )
            if argv[:3] == ["git", "clone", "--branch"]:
                Path(argv[-1]).mkdir(parents=True)
                return CommandResult(returncode=0)
            return CommandResult(returncode=0)

    def initializer(
        config_path: Path,
        _workspace_name: str,
        _default_branch: str,
        _default_worktree: Path,
    ) -> None:
        config_path.write_text(VALID_CONFIG, encoding="utf-8")

    runner = SlashDefaultBranchCloneRunner()

    execute_clone(
        runner,
        "git@github.com:org/authentic.git",
        "authentic",
        tmp_path,
        config_initializer=initializer,
    )

    workspace_root = tmp_path / "authentic"
    expected_log_dir = workspace_root / ".bonsai" / "logs" / "release-2026"
    nested_log_dir = workspace_root / ".bonsai" / "logs" / "release" / "2026"
    install_command = runner.commands[-2]
    setup_command = runner.commands[-1]
    assert install_command.log_path is not None
    assert install_command.log_path.parent == expected_log_dir
    assert install_command.log_path.parent != nested_log_dir
    assert install_command.log_path.name.endswith("-install.log")
    assert setup_command.log_path is not None
    assert setup_command.log_path.parent == expected_log_dir
    assert setup_command.log_path.parent != nested_log_dir
    assert setup_command.log_path.name.endswith("-setup.log")


def test_execute_clone_uses_repo_config_when_root_config_is_missing(tmp_path: Path) -> None:
    class RepoConfigCloneRunner(RecordingRunner):
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


def test_execute_init_adopts_existing_checkout_config(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)

    class ExistingCheckoutRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            _ = (cwd, check, env)
            if argv[-2:] == ["--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="main\n")
            if argv[-3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(
                    returncode=0,
                    stdout="git@github.com:org/authentic.git\n",
                )
            if argv[-3:] == ["worktree", "list", "--porcelain"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        f"worktree {default_worktree}\n"
                        "HEAD 0000000000000000000000000000000000000000\n"
                        "branch refs/heads/main\n"
                    ),
                )
            raise AssertionError(f"unexpected command: {argv}")

    plan = execute_init(ExistingCheckoutRunner(), default_worktree)

    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.name == "authentic"
    assert state.default_branch == "main"
    assert state.default_worktree == "main"
    assert state.repo_url == "git@github.com:org/authentic.git"
    assert state.worktrees == {}
    assert plan.workspace_root == workspace_root
    assert plan.default_worktree == default_worktree
    assert (default_worktree / ".bonsai.toml").read_text(encoding="utf-8") == VALID_CONFIG
    assert (default_worktree / ".env.local").exists()
    assert (workspace_root / "Caddyfile").exists()


def test_execute_init_adopts_existing_sibling_worktrees(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "ma-123-auth"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)

    class ExistingCheckoutRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            _ = (cwd, check, env)
            if argv[-2:] == ["--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="main\n")
            if argv[-3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(
                    returncode=0,
                    stdout="git@github.com:org/authentic.git\n",
                )
            if argv[-3:] == ["worktree", "list", "--porcelain"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        f"worktree {default_worktree}\n"
                        "HEAD 0000000000000000000000000000000000000000\n"
                        "branch refs/heads/main\n"
                        "\n"
                        f"worktree {feature_worktree}\n"
                        "HEAD 1111111111111111111111111111111111111111\n"
                        "branch refs/heads/MA-123-auth\n"
                    ),
                )
            raise AssertionError(f"unexpected command: {argv}")

    execute_init(ExistingCheckoutRunner(), default_worktree)

    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.worktrees == {
        "MA-123-auth": ManagedWorktree(path="ma-123-auth", slug="ma-123-auth", slot=1)
    }
    assert (feature_worktree / ".env.local").exists()
    assert (workspace_root / "caddy.d" / "ma-123-auth-frontend.caddy").exists()


def test_execute_init_reconciles_existing_state_with_missing_worktrees(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    existing_worktree = workspace_root / "existing"
    missing_worktree = workspace_root / "ma-123-auth"
    default_worktree.mkdir(parents=True)
    existing_worktree.mkdir()
    missing_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "existing": ManagedWorktree(path="existing", slug="existing", slot=3)
            },
        ),
    )

    class ExistingCheckoutRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            _ = (cwd, check, env)
            if argv[-2:] == ["--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="main\n")
            if argv[-3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(
                    returncode=0,
                    stdout="git@github.com:org/authentic.git\n",
                )
            if argv[-3:] == ["worktree", "list", "--porcelain"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        f"worktree {default_worktree}\n"
                        "HEAD 0000000000000000000000000000000000000000\n"
                        "branch refs/heads/main\n"
                        "\n"
                        f"worktree {existing_worktree}\n"
                        "HEAD 1111111111111111111111111111111111111111\n"
                        "branch refs/heads/existing\n"
                        "\n"
                        f"worktree {missing_worktree}\n"
                        "HEAD 2222222222222222222222222222222222222222\n"
                        "branch refs/heads/MA-123-auth\n"
                    ),
                )
            raise AssertionError(f"unexpected command: {argv}")

    execute_init(ExistingCheckoutRunner(), default_worktree)

    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.worktrees == {
        "existing": ManagedWorktree(path="existing", slug="existing", slot=3),
        "MA-123-auth": ManagedWorktree(path="ma-123-auth", slug="ma-123-auth", slot=1),
    }
    assert (missing_worktree / ".env.local").exists()
    assert (workspace_root / "caddy.d" / "ma-123-auth-frontend.caddy").exists()


def test_execute_init_rejects_checkout_directory_that_does_not_match_branch(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    checkout = workspace_root / "app"
    checkout.mkdir(parents=True)
    write_config(checkout, VALID_CONFIG)

    class ExistingCheckoutRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            _ = (cwd, check, env)
            if argv[-2:] == ["--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="main\n")
            if argv[-3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(
                    returncode=0,
                    stdout="git@github.com:org/authentic.git\n",
                )
            raise AssertionError(f"unexpected command: {argv}")

    with pytest.raises(BonsaiWorkspaceError, match="checkout directory must match"):
        execute_init(ExistingCheckoutRunner(), checkout)

    assert not (workspace_root / ".bonsai" / "state.json").exists()


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
    class ExistingWorktreeRunner(RecordingRunner):
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


def test_execute_move_uses_git_move_updates_state_and_syncs_generated_files(
    tmp_path: Path,
) -> None:
    class MovingRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:5] == [
                "git",
                "-C",
                str(default_worktree),
                "worktree",
                "move",
            ]:
                Path(argv[5]).rename(Path(argv[6]))
                return CommandResult(returncode=0)
            return CommandResult(returncode=0)

    runner = MovingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    old_worktree = workspace_root / "mb-123-auth"
    new_worktree = workspace_root / "MB-123-auth-moved"
    default_worktree.mkdir(parents=True)
    old_worktree.mkdir()
    config_text = VALID_CONFIG.replace(
        'value = "authentic-${slug}"',
        'value = "${WORKTREE_PATH}"',
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
                "MB-123-auth": ManagedWorktree(
                    path="mb-123-auth",
                    slug="mb-123-auth",
                    slot=1,
                )
            },
        ),
    )

    plan = execute_move(runner, "MB-123-auth", "MB-123-auth-moved", workspace_root)

    assert plan.old_worktree_path == old_worktree
    assert plan.new_worktree_path == new_worktree
    assert not old_worktree.exists()
    assert new_worktree.exists()
    assert CommandSpec(
        argv=(
            "git",
            "-C",
            str(default_worktree),
            "worktree",
            "move",
            str(old_worktree),
            str(new_worktree),
        )
    ) in runner.commands
    state = load_state(workspace_root / ".bonsai" / "state.json")
    moved = state.worktrees["MB-123-auth"]
    assert moved.path == "MB-123-auth-moved"
    assert moved.slug == "mb-123-auth"
    assert moved.slot == 1
    assert f"COMPOSE_PROJECT_NAME={new_worktree}" in (
        new_worktree / ".env.local"
    ).read_text(encoding="utf-8")


def test_execute_move_uses_temporary_path_for_case_only_rename(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class MovingRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:5] == [
                "git",
                "-C",
                str(default_worktree),
                "worktree",
                "move",
            ]:
                Path(argv[5]).rename(Path(argv[6]))
                return CommandResult(returncode=0)
            return CommandResult(returncode=0)

    runner = MovingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    old_worktree = workspace_root / "mb-123"
    temp_worktree = workspace_root / ".bonsai-move-MB-123"
    new_worktree = workspace_root / "MB-123"
    default_worktree.mkdir(parents=True)
    old_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"MB-123": ManagedWorktree(path="mb-123", slug="mb-123", slot=1)},
        ),
    )
    monkeypatch.setattr(
        "bonsai.workflows._paths_refer_to_same_existing_path",
        lambda left, right: left == old_worktree and right == new_worktree,
    )

    execute_move(runner, "MB-123", "MB-123", workspace_root)

    assert CommandSpec(
        argv=(
            "git",
            "-C",
            str(default_worktree),
            "worktree",
            "move",
            str(old_worktree),
            str(temp_worktree),
        )
    ) in runner.commands
    assert CommandSpec(
        argv=(
            "git",
            "-C",
            str(default_worktree),
            "worktree",
            "move",
            str(temp_worktree),
            str(new_worktree),
        )
    ) in runner.commands
    assert new_worktree.exists()


def test_execute_add_keeps_existing_correct_shared_file_symlink_on_repair(
    tmp_path: Path,
) -> None:
    class ExistingWorktreeRunner(RecordingRunner):
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


def test_execute_add_can_override_base_branch_for_new_branch(tmp_path: Path) -> None:
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

    execute_add(runner, "feature", workspace_root, base_branch="develop")

    assert runner.commands[2].argv == (
        "git",
        "-C",
        str(default_worktree),
        "worktree",
        "add",
        "-b",
        "feature",
        str(workspace_root / "feature"),
        "origin/develop",
    )


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


def test_execute_add_runs_pre_and_post_commands_with_generated_worktree_env(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        '[commands]\ninstall = "yarn install"\nsetup = "yarn setup"\nstart = "yarn dev"',
        "\n".join(
            [
                "[commands]",
                'preinstall = "echo preinstall"',
                'install = "yarn install"',
                'postinstall = "echo postinstall"',
                'presetup = "echo presetup"',
                'setup = "yarn setup"',
                'postsetup = "echo postsetup"',
                'start = "yarn dev"',
            ]
        ),
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

    lifecycle_commands = runner.commands[-6:]
    assert [command.argv for command in lifecycle_commands] == [
        ("echo", "preinstall"),
        ("yarn", "install"),
        ("echo", "postinstall"),
        ("echo", "presetup"),
        ("yarn", "setup"),
        ("echo", "postsetup"),
    ]
    assert all(command.cwd == workspace_root / "feature" for command in lifecycle_commands)
    log_kinds = [
        command.log_path.name.removesuffix(".log").split("-", maxsplit=2)[-1]
        for command in lifecycle_commands
        if command.log_path is not None
    ]
    assert log_kinds == [
        "preinstall",
        "install",
        "postinstall",
        "presetup",
        "setup",
        "postsetup",
    ]
    for command in lifecycle_commands:
        command_env = dict(command.env)
        assert command_env["COMPOSE_PROJECT_NAME"] == "authentic-feature"
        assert command_env["FRONTEND_PORT"] == "4201"
        assert command_env["API_PORT"] == "3334"
        assert command_env["DB_PORT"] == "5556"


def test_execute_add_logs_install_and_setup_under_managed_worktree_slug(
    tmp_path: Path,
) -> None:
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

    execute_add(runner, "feature/auth", workspace_root)

    logs_dir = workspace_root / ".bonsai" / "logs" / "feature-auth"
    install_command = runner.commands[-2]
    setup_command = runner.commands[-1]
    assert install_command.argv == ("yarn", "install")
    assert install_command.log_path is not None
    assert install_command.log_path.parent == logs_dir
    assert install_command.log_path.name.endswith("-install.log")
    assert setup_command.argv == ("yarn", "setup")
    assert setup_command.log_path is not None
    assert setup_command.log_path.parent == logs_dir
    assert setup_command.log_path.name.endswith("-setup.log")


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


def test_execute_remove_tears_down_compose_before_git_remove(tmp_path: Path) -> None:
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
    (branch_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (branch_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
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

    plan = execute_remove(runner, "feature", workspace_root)

    assert plan.compose_project_name == "authentic-feature"
    assert runner.commands[:3] == [
        CommandSpec(argv=("git", "-C", str(branch_worktree), "status", "--porcelain")),
        CommandSpec(
            argv=("docker", "compose", "-p", "authentic-feature", "down"),
            cwd=branch_worktree,
        ),
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
    ]


def test_execute_remove_skips_compose_without_compose_file(tmp_path: Path) -> None:
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
    (branch_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
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

    plan = execute_remove(runner, "feature", workspace_root)

    assert plan.compose_project_name is None
    assert all(command.argv[:2] != ("docker", "compose") for command in runner.commands)


def test_execute_remove_blocks_when_compose_teardown_fails(tmp_path: Path) -> None:
    class FailingComposeRunner:
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
            if argv[:2] == ["docker", "compose"]:
                raise BonsaiCommandError("docker compose failed")
            return CommandResult(returncode=0)

    runner = FailingComposeRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (branch_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (branch_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
        encoding="utf-8",
    )
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

    expected = (
        "Failed to tear down Docker Compose project "
        f"authentic-feature at {branch_worktree}"
    )
    with pytest.raises(BonsaiWorkspaceError, match=expected):
        execute_remove(runner, "feature", workspace_root, force=True)

    assert snippet.exists()
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].slot == 1
    assert all("remove" not in command.argv for command in runner.commands)


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


def test_execute_cleanup_requires_authenticated_github_cli(tmp_path: Path) -> None:
    class UnauthenticatedGhRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0, stdout="gh version 2.0.0\n")
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=1, stderr="not logged in\n")
            return CommandResult(returncode=0)

    runner = UnauthenticatedGhRunner()
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
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

    with pytest.raises(BonsaiWorkspaceError, match="gh auth login"):
        execute_cleanup(runner, workspace_root)

    assert runner.commands == [
        CommandSpec(argv=("gh", "--version")),
        CommandSpec(argv=("gh", "auth", "status"), cwd=workspace_root / "main"),
    ]


def test_execute_cleanup_dry_run_marks_merged_prs_and_skips_others(tmp_path: Path) -> None:
    class CleanupDryRunRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0, stdout="gh version 2.0.0\n")
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:4] == ["gh", "pr", "list", "--head"]:
                branch = argv[4]
                payload = {
                    "feature": '[{"state":"MERGED","mergedAt":"2026-05-01T00:00:00Z","url":"https://github.com/org/repo/pull/1"}]',
                    "open": '[{"state":"OPEN","mergedAt":null,"url":"https://github.com/org/repo/pull/2"}]',
                    "missing": "[]",
                }[branch]
                return CommandResult(returncode=0, stdout=payload)
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = CleanupDryRunRunner()
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    for name in ("feature", "open", "missing"):
        (workspace_root / name).mkdir()
    feature_worktree = workspace_root / "feature"
    (feature_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (feature_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
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
            worktrees={
                "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
                "open": ManagedWorktree(path="open", slug="open", slot=2),
                "missing": ManagedWorktree(path="missing", slug="missing", slot=3),
            },
        ),
    )

    plan = execute_cleanup(runner, workspace_root)

    assert [(item.branch, item.action, item.reason, item.pr_url) for item in plan.items] == [
        ("feature", "remove", "pull request is merged", "https://github.com/org/repo/pull/1"),
        ("missing", "skip", "no pull request found", None),
        ("open", "skip", "pull request is open", "https://github.com/org/repo/pull/2"),
    ]
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees.keys() == {
        "feature",
        "open",
        "missing",
    }
    assert all(command.argv[:2] != ("docker", "compose") for command in runner.commands)


def test_execute_cleanup_skips_dirty_merged_prs_without_force(tmp_path: Path) -> None:
    class DirtyCleanupRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0)
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:4] == ["gh", "pr", "list", "--head"]:
                return CommandResult(
                    returncode=0,
                    stdout='[{"state":"MERGED","mergedAt":"2026-05-01T00:00:00Z","url":"https://github.com/org/repo/pull/1"}]',
                )
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout=" M README.md\n")
            return CommandResult(returncode=0)

    runner = DirtyCleanupRunner()
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

    plan = execute_cleanup(runner, workspace_root, apply=True)

    assert [(item.branch, item.action, item.reason) for item in plan.items] == [
        ("feature", "skip", "worktree has uncommitted changes")
    ]
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].slot == 1
    assert all("remove" not in command.argv for command in runner.commands)


def test_execute_cleanup_apply_removes_merged_clean_worktrees(tmp_path: Path) -> None:
    class ApplyCleanupRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0)
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:4] == ["gh", "pr", "list", "--head"]:
                branch = argv[4]
                if branch == "feature":
                    return CommandResult(
                        returncode=0,
                        stdout='[{"state":"MERGED","mergedAt":"2026-05-01T00:00:00Z","url":"https://github.com/org/repo/pull/1"}]',
                    )
                return CommandResult(
                    returncode=0,
                    stdout='[{"state":"OPEN","mergedAt":null,"url":"https://github.com/org/repo/pull/2"}]',
                )
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = ApplyCleanupRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    open_worktree = workspace_root / "open"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    open_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    snippets = workspace_root / "caddy.d"
    snippets.mkdir()
    (snippets / "feature-frontend.caddy").write_text("feature\n", encoding="utf-8")
    (snippets / "open-frontend.caddy").write_text("open\n", encoding="utf-8")
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
                "open": ManagedWorktree(path="open", slug="open", slot=2),
            },
        ),
    )

    plan = execute_cleanup(runner, workspace_root, apply=True)

    assert [(item.branch, item.action, item.reason) for item in plan.items] == [
        ("feature", "removed", "pull request is merged"),
        ("open", "skip", "pull request is open"),
    ]
    assert set(load_state(workspace_root / ".bonsai" / "state.json").worktrees) == {"open"}
    assert not (snippets / "feature-frontend.caddy").exists()
    assert (snippets / "open-frontend.caddy").exists()
    assert CommandSpec(
        argv=("git", "-C", str(default_worktree), "worktree", "remove", str(feature_worktree))
    ) in runner.commands


def test_execute_cleanup_apply_tears_down_compose_through_remove(tmp_path: Path) -> None:
    class ApplyCleanupRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0)
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:4] == ["gh", "pr", "list", "--head"]:
                return CommandResult(
                    returncode=0,
                    stdout='[{"state":"MERGED","mergedAt":"2026-05-01T00:00:00Z","url":"https://github.com/org/repo/pull/1"}]',
                )
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = ApplyCleanupRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    (feature_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (feature_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
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

    plan = execute_cleanup(runner, workspace_root, apply=True)

    assert [(item.branch, item.action, item.reason) for item in plan.items] == [
        ("feature", "removed", "pull request is merged")
    ]
    docker_index = next(
        index
        for index, command in enumerate(runner.commands)
        if command.argv[:2] == ("docker", "compose")
    )
    git_remove_index = next(
        index
        for index, command in enumerate(runner.commands)
        if command.argv[:5] == ("git", "-C", str(default_worktree), "worktree", "remove")
    )
    assert runner.commands[docker_index] == CommandSpec(
        argv=("docker", "compose", "-p", "authentic-feature", "down"),
        cwd=feature_worktree,
    )
    assert docker_index < git_remove_index


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


def test_worktree_name_completions_include_matching_aliases(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    (workspace_root / "feature-authentication").mkdir()
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature/authentication": ManagedWorktree(
                    path="feature-authentication",
                    slug="feature-authentication",
                    slot=1,
                )
            },
        ),
    )

    assert worktree_name_completions(workspace_root, "auth") == (
        "feature/authentication",
        "feature-authentication",
    )


def test_execute_checkout_resolves_unique_fuzzy_worktree_match(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    (workspace_root / "feature-authentication").mkdir()
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature/authentication": ManagedWorktree(
                    path="feature-authentication",
                    slug="feature-authentication",
                    slot=1,
                )
            },
        ),
    )

    plan = execute_checkout(runner, "auth", workspace_root)

    assert plan.worktree_path == workspace_root / "feature-authentication"
    assert plan.created is False
    assert runner.commands == []


def test_execute_checkout_rejects_ambiguous_fuzzy_worktree_match(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    (workspace_root / "feature-authentication").mkdir()
    (workspace_root / "fix-auth-redirect").mkdir()
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature/authentication": ManagedWorktree(
                    path="feature-authentication",
                    slug="feature-authentication",
                    slot=1,
                ),
                "fix/auth-redirect": ManagedWorktree(
                    path="fix-auth-redirect",
                    slug="fix-auth-redirect",
                    slot=2,
                ),
            },
        ),
    )

    with pytest.raises(BonsaiWorkspaceError, match="Ambiguous Bonsai worktree"):
        execute_checkout(RecordingRunner(), "auth", workspace_root)


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


def test_plan_open_url_for_worktree_renders_named_managed_worktree_url(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
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

    plan = plan_open_url_for_worktree(workspace_root, "feature")

    assert plan.branch == "feature"
    assert plan.worktree_path == feature_worktree
    assert plan.url == "https://feature.authentic.localhost"


def test_plan_open_url_still_resolves_current_worktree(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
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

    plan = plan_open_url(workspace_root, feature_worktree)

    assert plan.branch == "feature"
    assert plan.worktree_path == feature_worktree.resolve()
    assert plan.url == "https://feature.authentic.localhost"


def test_plan_open_url_for_worktree_rejects_unknown_name(
    tmp_path: Path,
) -> None:
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

    with pytest.raises(BonsaiWorkspaceError, match="Unknown Bonsai worktree"):
        plan_open_url_for_worktree(workspace_root, "feature")


def test_execute_start_passes_generated_env_to_streamed_command(tmp_path: Path) -> None:
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
    assert len(runner.commands) == 1
    command = runner.commands[0]
    assert command.argv == ("yarn", "dev")
    assert command.cwd == feature_worktree
    assert command.env == (
        ("COMPOSE_PROJECT_NAME", "authentic-feature"),
        ("FRONTEND_PORT", "4201"),
    )
    assert command.log_path is not None
    assert command.log_path.parent == workspace_root / ".bonsai" / "logs" / "feature"
    assert command.log_path.name.endswith("-start.log")


def test_execute_start_runs_pre_and_post_commands_around_start(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    config_text = VALID_CONFIG.replace(
        '[commands]\ninstall = "yarn install"\nsetup = "yarn setup"\nstart = "yarn dev"',
        "\n".join(
            [
                "[commands]",
                'install = "yarn install"',
                'setup = "yarn setup"',
                'prestart = "echo prestart"',
                'start = "yarn dev"',
                'poststart = "echo poststart"',
            ]
        ),
    )
    write_config(default_worktree, config_text)
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
    assert [command.argv for command in runner.commands] == [
        ("echo", "prestart"),
        ("yarn", "dev"),
        ("echo", "poststart"),
    ]
    assert all(command.cwd == feature_worktree for command in runner.commands)
    log_kinds = [
        command.log_path.name.removesuffix(".log").split("-", maxsplit=2)[-1]
        for command in runner.commands
        if command.log_path is not None
    ]
    assert log_kinds == [
        "prestart",
        "start",
        "poststart",
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


def test_plan_command_log_reads_latest_log_for_current_worktree(tmp_path: Path) -> None:
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
    log_dir = workspace_root / ".bonsai" / "logs" / "main"
    log_dir.mkdir(parents=True)
    (log_dir / "20260526-143012-install.log").write_text("install\n", encoding="utf-8")
    latest = log_dir / "20260526-143245-setup.log"
    latest.write_text("setup\n", encoding="utf-8")

    plan = plan_command_log(workspace_root, None, default_worktree, None)

    assert plan.branch == "main"
    assert plan.worktree_path == default_worktree
    assert plan.log_path == latest
    assert plan.content == "setup\n"


def test_plan_command_log_filters_by_command_for_named_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
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
    log_dir = workspace_root / ".bonsai" / "logs" / "feature"
    log_dir.mkdir(parents=True)
    setup = log_dir / "20260526-143245-setup.log"
    start = log_dir / "20260526-143300-start.log"
    setup.write_text("setup\n", encoding="utf-8")
    start.write_text("start\n", encoding="utf-8")

    plan = plan_command_log(workspace_root, "feature", default_worktree, "setup")

    assert plan.branch == "feature"
    assert plan.worktree_path == feature_worktree
    assert plan.log_path == setup
    assert plan.content == "setup\n"


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


def test_execute_checkout_can_override_base_branch_for_missing_branch(
    tmp_path: Path,
) -> None:
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

    plan = execute_checkout(runner, "feature", workspace_root, base_branch="develop")

    assert plan.worktree_path == workspace_root / "feature"
    assert plan.created is True
    assert runner.commands[2].argv[-1] == "origin/develop"
