import shutil
import subprocess
from pathlib import Path

import pytest
from test_config import VALID_CONFIG, write_config

import bonsai.workflows as workflows
from bonsai.caddy import BOOT_BLOCK_BEGIN, BOOT_BLOCK_END, caddy_reload_plan, caddy_setup_plan
from bonsai.config import load_config
from bonsai.errors import BonsaiCommandError
from bonsai.git import (
    repair_worktrees,
)
from bonsai.models import (
    BonsaiState,
    CommandResult,
    CommandSpec,
    ManagedWorktree,
    PortOwner,
)
from bonsai.process import RecordingRunner
from bonsai.rendering import render_root_caddyfile
from bonsai.state import load_state, save_state
from bonsai.workflows import (
    app_snippets_dir,
    check_workspace_health,
    command_summary,
    execute_add,
    execute_doctor_apply,
    execute_move,
    execute_port_repairs,
    execute_rename_default,
    execute_repair,
    execute_sync,
    global_caddy_paths,
    plan_repair,
    plan_sync,
    reload_workspace_caddy,
)


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


class RealGitHermeticCaddyRunner(RecordingRunner):
    def __init__(self, brew_prefix: Path) -> None:
        super().__init__()
        self.brew_prefix = brew_prefix

    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
        if argv[:2] == ["caddy", "version"]:
            return CommandResult(returncode=0, stdout="v2.11.4\n")
        if argv[:2] == ["brew", "--prefix"]:
            return CommandResult(returncode=0, stdout=f"{self.brew_prefix}\n")
        if argv[:2] == ["caddy", "reload"]:
            return CommandResult(returncode=0)
        if argv and argv[0] == "git":
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            result = CommandResult(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            if check and result.returncode != 0:
                raise BonsaiCommandError(result.stderr)
            return result
        return CommandResult(returncode=0)


def _caddy_setup_config(tmp_path: Path) -> object:
    config_path = write_config(tmp_path, VALID_CONFIG)
    return load_config(config_path)


class _CaddySetupRunner(RecordingRunner):
    def __init__(self, fail_argv: tuple[str, ...] | None = None) -> None:
        super().__init__()
        self.fail_argv = fail_argv

    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
        if argv == ["caddy", "version"]:
            return CommandResult(returncode=1, stderr="missing caddy\n")
        if argv == ["brew", "--version"]:
            return CommandResult(returncode=0, stdout="Homebrew 4.0\n")
        if self.fail_argv is not None and tuple(argv) == self.fail_argv:
            return CommandResult(returncode=1, stderr="brew failed\n")
        return CommandResult(returncode=0)


def test_repair_worktrees_runs_git_worktree_repair() -> None:
    runner = RecordingRunner()

    repair_worktrees(runner, Path("/tmp/repo/trunk"))

    assert runner.commands == [
        CommandSpec(
            argv=(
                "git",
                "-C",
                "/tmp/repo/trunk",
                "worktree",
                "repair",
            )
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

    env_actions = {
        (action.kind, action.path) for action in plan.actions if action.path.name == ".env.local"
    }
    assert ("write", default_worktree / ".env.local") in env_actions
    assert ("write", feature_worktree / ".env.local") in env_actions

    snippets = app_snippets_dir("authentic")
    caddy_actions = {(action.kind, action.path) for action in plan.actions}
    assert ("write", snippets / "main-frontend.caddy") in caddy_actions
    assert ("write", snippets / "feature-frontend.caddy") in caddy_actions
    assert plan.reload_caddy is True


def test_plan_sync_removes_stale_configured_service_snippets(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = app_snippets_dir("authentic")
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir(parents=True)
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
    snippets_dir = app_snippets_dir("authentic")
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir(parents=True)
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
    snippets_dir = app_snippets_dir("authentic")
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir(parents=True)
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

    root_caddyfile, _ = global_caddy_paths()
    assert (default_worktree / ".env.local").exists()
    assert root_caddyfile.exists()
    assert (app_snippets_dir("authentic") / "main-frontend.caddy").exists()
    assert plan.reload_caddy is True
    assert runner.commands[-1] == caddy_reload_plan(root_caddyfile)


def test_execute_sync_apply_removes_stale_generated_snippet(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = app_snippets_dir("authentic")
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir(parents=True)
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
    snippets_dir = app_snippets_dir("authentic")
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir(parents=True)
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
    root_caddyfile, _ = global_caddy_paths()
    snippets_dir = app_snippets_dir("authentic")
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir(parents=True)
    root_caddyfile.parent.mkdir(parents=True, exist_ok=True)
    root_caddyfile.write_text(
        render_root_caddyfile([snippets_dir]),
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
    assert runner.commands[-1] == caddy_reload_plan(root_caddyfile)


def test_execute_sync_dry_run_keeps_stale_marked_snippet_and_skips_reload(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    root_caddyfile, _ = global_caddy_paths()
    snippets_dir = app_snippets_dir("authentic")
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir(parents=True)
    root_caddyfile.parent.mkdir(parents=True, exist_ok=True)
    root_caddyfile.write_text(
        render_root_caddyfile([snippets_dir]),
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
    root_caddyfile, _ = global_caddy_paths()
    default_worktree.mkdir(parents=True)
    root_caddyfile.parent.mkdir(parents=True, exist_ok=True)
    root_caddyfile.write_text(
        render_root_caddyfile([]),
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
    monkeypatch.setattr("bonsai.workflows.probes._check_port_listening", lambda _port: False)

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
    monkeypatch.setattr("bonsai.workflows.probes._check_port_listening", lambda _port: False)

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
    monkeypatch.setattr("bonsai.workflows.probes._check_port_listening", lambda port: port == 4200)

    report = check_workspace_health(GitRunner(), workspace_root)

    assert report.failed is True
    assert any(check.name == "port 4200" and check.status == "fail" for check in report.checks)


def test_check_workspace_health_reports_stale_compose_networks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class DockerRunner(RecordingRunner):
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
            if argv == ["docker", "network", "ls", "--no-trunc", "--quiet"]:
                return CommandResult(returncode=0, stdout="live-network\n")
            if argv[:5] == ["docker", "ps", "--all", "--quiet", "--no-trunc"]:
                return CommandResult(returncode=0, stdout="stale-id\n")
            if argv[:2] == ["docker", "inspect"]:
                return CommandResult(
                    returncode=0,
                    stdout="""
[
  {
    "Id": "stale-id",
    "Name": "/authentic-seed-migrate-1",
    "Config": {
      "Labels": {
        "com.docker.compose.project": "authentic"
      }
    },
    "State": {
      "Running": false,
      "Status": "exited"
    },
    "NetworkSettings": {
      "Networks": {
        "authentic_default": {
          "NetworkID": "missing-network"
        }
      }
    }
  }
]
""",
                )
            return CommandResult(returncode=1)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    (default_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
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
    monkeypatch.setattr("bonsai.workflows.probes._check_port_listening", lambda _port: False)

    report = check_workspace_health(DockerRunner(), workspace_root)

    assert report.failed is True
    assert any(
        check.name == "docker compose networks"
        and check.status == "fail"
        and "authentic-seed-migrate-1" in check.detail
        and check.hint == "Run: bonsai doctor --apply"
        and check.repair == "docker-compose-networks"
        for check in report.checks
    )


def test_doctor_accepts_same_worktree_listener_and_fails_external_listener(
    tmp_path: Path,
    monkeypatch,
) -> None:
    external_path = tmp_path / "other-app"

    class LsofRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            if argv[0] == "git" and "rev-parse" in argv:
                return CommandResult(returncode=0, stdout="true\n")
            if argv[0] == "caddy":
                return CommandResult(returncode=0, stdout="v2.8.0\n")
            if argv[0] == "lsof" and "-iTCP:4201" in argv:
                return CommandResult(returncode=0, stdout="p123\ncnode\numichael\n")
            if argv[0] == "lsof" and "-iTCP:3334" in argv:
                return CommandResult(returncode=0, stdout="p456\ncruby\numichael\n")
            if argv == ["lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p123\nn{feature_worktree}\n")
            if argv == ["lsof", "-a", "-p", "456", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p456\nn{external_path}\n")
            return CommandResult(returncode=1)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature-a"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    external_path.mkdir()
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
    execute_sync(RecordingRunner(), workspace_root, apply=True)
    monkeypatch.setattr("bonsai.workflows.probes._check_port_listening", lambda _port: False)

    report = check_workspace_health(LsofRunner(), workspace_root)

    assert any(
        check.name == "port 4201"
        and check.status == "ok"
        and "owned by node[123]" in check.detail
        for check in report.checks
    )
    assert any(
        check.name == "port 3334"
        and check.status == "fail"
        and "ruby[456]" in check.detail
        for check in report.checks
    )


def test_plan_port_repairs_ignores_same_worktree_listener_with_owner_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    external_path = tmp_path / "other-app"

    class LsofRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            if argv[0] == "lsof" and "-iTCP:4201" in argv:
                return CommandResult(returncode=0, stdout="p123\ncnode\numichael\n")
            if argv[0] == "lsof" and "-iTCP:3334" in argv:
                return CommandResult(returncode=0, stdout="p456\ncruby\numichael\n")
            if argv == ["lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p123\nn{feature_worktree}\n")
            if argv == ["lsof", "-a", "-p", "456", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p456\nn{external_path}\n")
            return CommandResult(returncode=1)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature-a"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    external_path.mkdir()
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
    monkeypatch.setattr("bonsai.workflows.probes._check_port_listening", lambda _port: False)

    plan = workflows.plan_port_repairs(workspace_root, runner=LsofRunner())

    assert [(item.branch, item.current_slot, item.proposed_slot) for item in plan.items] == [
        ("feature-a", 1, 2),
    ]
    assert plan.items[0].services[0].owners == ()
    assert plan.items[0].services[1].owners == (
        PortOwner(
            pid=456,
            command="ruby",
            user="michael",
            cwd=external_path,
            worktree_branch=None,
            worktree_path=None,
        ),
    )


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
        "bonsai.workflows.probes._check_port_listening",
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
        "bonsai.workflows.probes._check_port_listening",
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
        "bonsai.workflows.probes._check_port_listening",
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
    assert ("caddy", "reload", "--config", str(global_caddy_paths()[0])) in [
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
        "bonsai.workflows.probes._check_port_listening",
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


def test_run_caddy_setup_brew_install_failure_is_non_fatal(tmp_path: Path) -> None:
    config = _caddy_setup_config(tmp_path)
    runner = _CaddySetupRunner(fail_argv=("brew", "install", "caddy"))

    result = workflows._run_caddy_setup(runner, config)

    assert result.actions == ()
    assert [check.status for check in result.checks] == ["fail"]
    assert result.checks[0].hint and "brew install caddy" in result.checks[0].hint
    assert ("brew", "install", "caddy") in [command.argv for command in runner.commands]
    assert ("brew", "services", "start", "caddy") not in [
        command.argv for command in runner.commands
    ]


def test_run_caddy_setup_brew_start_failure_is_non_fatal(tmp_path: Path) -> None:
    config = _caddy_setup_config(tmp_path)
    runner = _CaddySetupRunner(fail_argv=("brew", "services", "start", "caddy"))

    result = workflows._run_caddy_setup(runner, config)

    assert [action.kind for action in result.actions] == ["caddy"]
    assert result.actions[0].detail == "brew install caddy"
    assert [check.status for check in result.checks] == ["fail"]
    assert result.checks[0].hint and "brew install caddy" in result.checks[0].hint


def test_run_caddy_setup_success_returns_actions_unchanged(tmp_path: Path) -> None:
    config = _caddy_setup_config(tmp_path)
    runner = _CaddySetupRunner()

    result = workflows._run_caddy_setup(runner, config)

    assert [action.kind for action in result.actions] == ["caddy", "caddy"]
    assert [action.detail for action in result.actions] == [
        "brew install caddy",
        "brew services start caddy",
    ]
    assert result.checks == ()


def test_execute_doctor_apply_removes_stopped_stale_compose_containers(
    tmp_path: Path,
) -> None:
    class DockerApplyRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[:2] == ["git", "-C"] and "rev-parse" in argv:
                return CommandResult(returncode=0, stdout="true\n")
            if argv == ["caddy", "version"]:
                return CommandResult(returncode=0, stdout="v2.8.0\n")
            if argv == ["docker", "network", "ls", "--no-trunc", "--quiet"]:
                return CommandResult(returncode=0, stdout="live-network\n")
            if argv[:5] == ["docker", "ps", "--all", "--quiet", "--no-trunc"]:
                return CommandResult(returncode=0, stdout="stale-id\n")
            if argv[:2] == ["docker", "inspect"]:
                return CommandResult(
                    returncode=0,
                    stdout="""
[
  {
    "Id": "stale-id",
    "Name": "/authentic-seed-migrate-1",
    "Config": {
      "Labels": {
        "com.docker.compose.project": "authentic"
      }
    },
    "State": {
      "Running": false,
      "Status": "exited"
    },
    "NetworkSettings": {
      "Networks": {
        "authentic_default": {
          "NetworkID": "missing-network"
        }
      }
    }
  }
]
""",
                )
            return CommandResult(returncode=0)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    (default_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
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

    runner = DockerApplyRunner()
    plan = execute_doctor_apply(runner, workspace_root)

    assert CommandSpec(argv=("docker", "rm", "stale-id")) in runner.commands
    assert any(
        action.kind == "docker"
        and action.detail == "removed authentic-seed-migrate-1 stale Docker network reference"
        for action in plan.actions
    )


def test_caddy_reload_command_is_displayable() -> None:
    command = caddy_reload_plan(Path("/tmp/authentic/Caddyfile"))

    assert command_summary(command) == "caddy reload --config /tmp/authentic/Caddyfile"


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

    assert (app_snippets_dir("authentic") / "feature-frontend.caddy").exists()
    assert CommandSpec(
        argv=("caddy", "reload", "--config", str(global_caddy_paths()[0]))
    ) in runner.commands


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
    assert (app_snippets_dir("authentic") / "feature-frontend.caddy").exists()
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


def test_execute_rename_default_relocates_repairs_and_syncs(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
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
            worktrees={},
        ),
    )
    runner = RecordingRunner()

    plan = execute_rename_default(runner, workspace_root, "trunk")

    new_default = workspace_root / "trunk"
    assert plan.old_worktree_path == default_worktree
    assert plan.new_worktree_path == new_default
    assert not default_worktree.exists()
    assert new_default.is_dir()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.default_worktree == "trunk"
    assert state.default_branch == "main"
    assert CommandSpec(
        argv=("git", "-C", str(new_default), "worktree", "repair")
    ) in runner.commands
    assert f"COMPOSE_PROJECT_NAME={new_default}" in (
        new_default / ".env.local"
    ).read_text(encoding="utf-8")


def test_execute_rename_default_repairs_real_secondary_worktree(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    brew_prefix = tmp_path / "brew"
    default_worktree.mkdir(parents=True)

    def git(repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    subprocess.run(
        ["git", "init", "-b", "main", str(default_worktree)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(default_worktree, "config", "user.email", "test@example.com")
    git(default_worktree, "config", "user.name", "Test")
    (default_worktree / "README.md").write_text("hi\n", encoding="utf-8")
    git(default_worktree, "add", "-A")
    git(default_worktree, "commit", "-m", "init")
    secondary = workspace_root / "feature"
    git(default_worktree, "worktree", "add", "-b", "feature", str(secondary))
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
                "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
            },
        ),
    )

    runner = RealGitHermeticCaddyRunner(brew_prefix)

    execute_rename_default(runner, workspace_root, "trunk")

    assert not default_worktree.exists()
    assert (workspace_root / "trunk").is_dir()
    status = subprocess.run(
        ["git", "-C", str(secondary), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    assert status.returncode == 0, status.stderr
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.default_worktree == "trunk"
    assert "feature" in state.worktrees
    boot_caddyfile = brew_prefix / "etc" / "Caddyfile"
    assert boot_caddyfile.exists()
    assert (
        f"import {Path.home() / '.bonsai' / 'caddy.d' / 'authentic'}/*.caddy"
        in boot_caddyfile.read_text(encoding="utf-8")
    )
    assert CommandSpec(argv=("brew", "--prefix")) in runner.commands
    assert CommandSpec(
        argv=("caddy", "reload", "--config", str(Path.home() / ".bonsai" / "Caddyfile"))
    ) in runner.commands


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


def test_two_workspaces_coexist_in_global_snippets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def make_workspace(app: str) -> Path:
        workspace_root = tmp_path / app
        default_worktree = workspace_root / "main"
        default_worktree.mkdir(parents=True)
        write_config(
            default_worktree,
            VALID_CONFIG.replace('name = "authentic"', f'name = "{app}"'),
        )
        save_state(
            workspace_root / ".bonsai" / "state.json",
            BonsaiState(
                version=1,
                name=app,
                default_branch="main",
                default_worktree="main",
                repo_url=f"git@github.com:org/{app}.git",
                worktrees={},
            ),
        )
        return workspace_root

    alpha = make_workspace("alpha")
    beta = make_workspace("beta")

    execute_sync(RecordingRunner(), alpha, apply=True)
    execute_sync(RecordingRunner(), beta, apply=True)

    assert (app_snippets_dir("beta") / "main-frontend.caddy").exists()
    # Syncing beta must not delete alpha's snippets (per-app cleanup scoping).
    assert (app_snippets_dir("alpha") / "main-frontend.caddy").exists()


def test_caddy_validates_global_root_with_two_app_subdirs(tmp_path: Path) -> None:
    if shutil.which("caddy") is None:
        pytest.skip("caddy not installed")
    snippets_root = tmp_path / "caddy.d"
    for app in ("alpha", "beta"):
        app_dir = snippets_root / app
        app_dir.mkdir(parents=True)
        (app_dir / "main-frontend.caddy").write_text(
            f"https://main.{app}.localhost {{\n"
            "\ttls internal\n\treverse_proxy localhost:4201\n}\n",
            encoding="utf-8",
        )
    root = tmp_path / "Caddyfile"
    root.write_text(
        render_root_caddyfile([snippets_root / "alpha", snippets_root / "beta"]),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["caddy", "validate", "--config", str(root), "--adapter", "caddyfile"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_reload_skips_boot_config_when_caddy_absent_but_still_reloads(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    snippets_root = global_caddy_paths()[1]
    app_dir = snippets_root / "authentic"
    app_dir.mkdir(parents=True)
    (app_dir / "main-frontend.caddy").write_text(
        "# Generated by bonsai. Do not edit by hand.\n", encoding="utf-8"
    )

    class NoCaddyRunner(RecordingRunner):
        def run(self, argv, cwd=None, check=True, env=None):
            if argv[:2] == ["caddy", "version"]:
                return CommandResult(returncode=1)
            return super().run(argv, cwd=cwd, check=check, env=env)

    runner = NoCaddyRunner()
    reload_workspace_caddy(runner)

    root_caddyfile = global_caddy_paths()[0]
    assert root_caddyfile.exists()
    # caddy unavailable → boot-config path never probed
    assert all(tuple(cmd.argv[:2]) != ("brew", "--prefix") for cmd in runner.commands)
    # the reload itself still happened
    assert runner.commands[-1] == caddy_reload_plan(root_caddyfile)


def test_boot_config_block_persists_and_updates_across_reload(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    brew_prefix = tmp_path / "brew"
    (brew_prefix / "etc").mkdir(parents=True)

    class BrewCaddyRunner(RecordingRunner):
        def run(self, argv, cwd=None, check=True, env=None):
            if argv[:2] == ["caddy", "version"]:
                return CommandResult(returncode=0)
            if argv[:2] == ["brew", "--prefix"]:
                return CommandResult(returncode=0, stdout=str(brew_prefix) + "\n")
            return super().run(argv, cwd=cwd, check=check, env=env)

    snippets_root = global_caddy_paths()[1]
    for app in ("alpha", "beta"):
        app_dir = snippets_root / app
        app_dir.mkdir(parents=True)
        (app_dir / "main-frontend.caddy").write_text(
            "# Generated by bonsai. Do not edit by hand.\n", encoding="utf-8"
        )

    runner = BrewCaddyRunner()
    reload_workspace_caddy(runner)

    boot = brew_prefix / "etc" / "Caddyfile"
    first = boot.read_text(encoding="utf-8")
    assert BOOT_BLOCK_BEGIN in first and BOOT_BLOCK_END in first
    assert f"import {snippets_root / 'alpha'}/*.caddy" in first
    assert f"import {snippets_root / 'beta'}/*.caddy" in first

    # Simulate removing alpha's last snippet (what `bonsai remove` does), then reload.
    (snippets_root / "alpha" / "main-frontend.caddy").unlink()
    reload_workspace_caddy(runner)

    second = boot.read_text(encoding="utf-8")
    assert BOOT_BLOCK_BEGIN in second and BOOT_BLOCK_END in second  # block survived
    assert f"import {snippets_root / 'beta'}/*.caddy" in second  # remaining app kept
    assert f"import {snippets_root / 'alpha'}/*.caddy" not in second  # emptied app dropped
