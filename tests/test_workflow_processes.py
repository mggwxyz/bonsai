import hashlib
import json
import signal
from pathlib import Path

import pytest
from test_config import VALID_CONFIG, write_config

from bonsai.errors import BonsaiCommandError, BonsaiConfigError, BonsaiWorkspaceError
from bonsai.git import (
    clone_default_branch,
    discover_default_branch,
)
from bonsai.models import (
    BonsaiState,
    CommandResult,
    CommandSpec,
    ManagedWorktree,
)
from bonsai.process import RecordingRunner
from bonsai.state import save_state
from bonsai.workflows import (
    execute_each_command,
    execute_start,
    execute_stop_processes,
    execute_tmux,
    execute_up,
    execute_worktree_command,
    plan_app_processes,
    plan_command_log,
    plan_stop_processes,
    resolve_start_target,
    run_lifecycle_command,
)
from bonsai.workflows import probes as wf_probes
from bonsai.workflows import processes as wf_processes


def _write_exec_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env.local").write_text("FRONTEND_PORT=4200\n", encoding="utf-8")
    (feature_worktree / ".env.local").write_text("FRONTEND_PORT=4201\n", encoding="utf-8")
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
    return workspace_root, default_worktree, feature_worktree


def _expected_command_env(
    workspace_root: Path,
    branch: str,
    slug: str,
    slot: int,
    worktree_path: Path,
    extra: dict[str, str] | None = None,
) -> tuple[tuple[str, str], ...]:
    values = dict(extra or {})
    values.update(
        {
            "BONSAI_WORKSPACE_NAME": "authentic",
            "BONSAI_BRANCH": branch,
            "BONSAI_SLUG": slug,
            "BONSAI_SLOT": str(slot),
            "BONSAI_WORKTREE_PATH": str(worktree_path),
            "BONSAI_ROOT_PATH": str(workspace_root),
            "BONSAI_DEFAULT_BRANCH": "main",
            "BONSAI_PRIMARY_URL": f"https://{slug}.authentic.localhost",
        }
    )
    return tuple(sorted(values.items()))


def test_execute_worktree_command_runs_in_selected_worktree_with_env(tmp_path: Path) -> None:
    workspace_root, default_worktree, feature_worktree = _write_exec_workspace(tmp_path)
    runner = RecordingRunner()

    result = execute_worktree_command(
        runner,
        workspace_root,
        name="feature",
        current_path=default_worktree,
        argv=["python", "-V"],
    )

    assert result.branch == "feature"
    assert result.exit_code == 0
    assert runner.commands == [
        CommandSpec(
            argv=("python", "-V"),
            cwd=feature_worktree,
            env=_expected_command_env(
                workspace_root,
                "feature",
                "feature",
                1,
                feature_worktree,
                {"FRONTEND_PORT": "4201"},
            ),
        )
    ]


def test_execute_worktree_command_defaults_to_current_worktree(tmp_path: Path) -> None:
    workspace_root, _default_worktree, feature_worktree = _write_exec_workspace(tmp_path)
    runner = RecordingRunner()

    result = execute_worktree_command(
        runner,
        workspace_root,
        name=None,
        current_path=feature_worktree,
        argv=["pwd"],
    )

    assert result.branch == "feature"
    assert runner.commands[0].cwd == feature_worktree


def test_execute_each_command_runs_sequentially_and_reports_failures(tmp_path: Path) -> None:
    class ExitCodeRunner(RecordingRunner):
        def run_stream(self, argv, cwd=None, env=None) -> int:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            return 2 if cwd == feature_worktree else 0

    workspace_root, default_worktree, feature_worktree = _write_exec_workspace(tmp_path)
    runner = ExitCodeRunner()

    result = execute_each_command(
        runner,
        workspace_root,
        current_path=feature_worktree,
        argv=["git", "status"],
        skip_default=False,
    )

    assert [(item.branch, item.exit_code) for item in result.items] == [
        ("main", 0),
        ("feature", 2),
    ]
    assert result.exit_code == 2
    assert [command.cwd for command in runner.commands] == [default_worktree, feature_worktree]


def test_execute_each_command_can_skip_default(tmp_path: Path) -> None:
    workspace_root, _default_worktree, feature_worktree = _write_exec_workspace(tmp_path)
    runner = RecordingRunner()

    result = execute_each_command(
        runner,
        workspace_root,
        current_path=feature_worktree,
        argv=["true"],
        skip_default=True,
    )

    assert [(item.branch, item.exit_code) for item in result.items] == [("feature", 0)]
    assert [command.cwd for command in runner.commands] == [feature_worktree]


def test_plan_app_processes_reads_live_records_and_prunes_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root, _default_worktree, feature_worktree = _write_exec_workspace(tmp_path)
    from bonsai.state import load_state

    load_state(workspace_root / ".bonsai" / "state.json")
    pid_dir = workspace_root / ".bonsai" / "pids"
    pid_dir.mkdir()
    (pid_dir / "feature.json").write_text(
        json.dumps(
            {
                "branch": "feature",
                "worktree_path": str(feature_worktree),
                "pid": 123,
                "command": ["npm", "run", "dev"],
                "log_path": str(workspace_root / ".bonsai" / "logs" / "feature.log"),
                "started_at": "2026-06-11T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (pid_dir / "dead.json").write_text(
        json.dumps(
            {
                "branch": "dead",
                "worktree_path": str(workspace_root / "dead"),
                "pid": 999,
                "command": ["false"],
                "log_path": str(workspace_root / "dead.log"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wf_processes, "_process_is_alive", lambda pid: pid == 123)

    plan = plan_app_processes()

    assert [(item.workspace_name, item.branch, item.pid, item.command) for item in plan.items] == [
        ("authentic", "feature", 123, ("npm", "run", "dev"))
    ]
    assert not (pid_dir / "dead.json").exists()


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


def test_plan_stop_processes_targets_only_selected_worktree_owners(
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

    plan = plan_stop_processes(
        LsofRunner(),
        workspace_root,
        current_path=default_worktree,
        name="feature-a",
    )

    assert [(item.action, item.owner.pid, item.port_env) for item in plan.items] == [
        ("stop", 123, "FRONTEND_PORT"),
        ("skip", 456, "API_PORT"),
    ]
    assert "outside selected worktree" in plan.items[1].reason


def test_execute_stop_processes_terminates_selected_worktree_owners(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
            if argv == ["lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p123\nn{feature_worktree}\n")
            return CommandResult(returncode=1)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature-a"
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
            worktrees={
                "feature-a": ManagedWorktree(path="feature-a", slug="feature-a", slot=1),
            },
        ),
    )
    monkeypatch.setattr("bonsai.workflows.probes._check_port_listening", lambda _port: False)
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        "bonsai.workflows.processes.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )

    plan = execute_stop_processes(
        LsofRunner(),
        workspace_root,
        current_path=default_worktree,
        name="feature-a",
    )

    assert [(item.action, item.owner.pid) for item in plan.items] == [("stopped", 123)]
    assert killed == [(123, signal.SIGTERM)]


def test_execute_stop_processes_force_terminates_external_owner(
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
            if argv[0] == "lsof" and "-iTCP:3334" in argv:
                return CommandResult(returncode=0, stdout="p456\ncruby\numichael\n")
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
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        "bonsai.workflows.processes.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )

    plan = execute_stop_processes(
        LsofRunner(),
        workspace_root,
        current_path=default_worktree,
        name="feature-a",
        force=True,
    )

    assert [(item.action, item.owner.pid) for item in plan.items] == [("stopped", 456)]
    assert killed == [(456, signal.SIGTERM)]


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
    assert command.env == _expected_command_env(
        workspace_root,
        "feature",
        "feature",
        1,
        feature_worktree,
        {
            "COMPOSE_PROJECT_NAME": "authentic-feature",
            "FRONTEND_PORT": "4201",
        },
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


def test_execute_up_replaces_stale_record_and_tracks_detached_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
    pid_path = workspace_root / ".bonsai" / "pids" / "feature.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(
        json.dumps(
            {
                "branch": "feature",
                "worktree_path": str(feature_worktree),
                "pid": 999,
                "command": ["yarn", "dev"],
                "log_path": str(workspace_root / ".bonsai" / "logs" / "feature" / "old.log"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wf_processes, "_process_is_alive", lambda _pid: False)
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda port: port == 4201)

    plan = execute_up(
        runner,
        workspace_root,
        "feature",
        feature_worktree,
        readiness_timeout=0.01,
    )

    assert plan.branch == "feature"
    assert plan.pid == 1000
    assert plan.stale_pid == 999
    assert plan.ready_ports == (4201,)
    assert plan.log_path.parent == workspace_root / ".bonsai" / "logs" / "feature"
    assert len(runner.commands) == 1
    command = runner.commands[0]
    assert command.argv == ("yarn", "dev")
    assert command.cwd == feature_worktree
    assert command.env == _expected_command_env(
        workspace_root,
        "feature",
        "feature",
        1,
        feature_worktree,
        {
            "COMPOSE_PROJECT_NAME": "authentic-feature",
            "FRONTEND_PORT": "4201",
        },
    )
    assert command.log_path == plan.log_path
    record = json.loads(pid_path.read_text(encoding="utf-8"))
    assert record["branch"] == "feature"
    assert record["pid"] == 1000
    assert record["worktree_path"] == str(feature_worktree)
    assert record["command"] == ["yarn", "dev"]
    assert record["log_path"] == str(plan.log_path)


def test_execute_tmux_creates_deterministic_session_with_start_env(tmp_path: Path) -> None:
    workspace_root, default_worktree, feature_worktree = _write_exec_workspace(tmp_path)

    class TmuxRunner(RecordingRunner):
        def run(self, argv, cwd=None, check=True, env=None):
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:2] == ["tmux", "has-session"]:
                return CommandResult(returncode=1)
            return CommandResult(returncode=0)

    runner = TmuxRunner()

    plan = execute_tmux(runner, workspace_root, "feature", default_worktree)

    root_hash = hashlib.sha1(str(workspace_root.resolve()).encode("utf-8")).hexdigest()[:8]
    assert plan.branch == "feature"
    assert plan.worktree_path == feature_worktree
    assert plan.session_name == f"bonsai-authentic-feature-{root_hash}"
    assert plan.attach_command == f"tmux attach -t {plan.session_name}"
    assert plan.created is True
    assert runner.commands[0] == CommandSpec(
        argv=("tmux", "has-session", "-t", plan.session_name)
    )

    new_session = runner.commands[1]
    assert new_session.cwd is None
    assert new_session.argv[:9] == (
        "tmux",
        "new-session",
        "-d",
        "-s",
        plan.session_name,
        "-n",
        "services",
        "-c",
        str(feature_worktree),
    )
    assert "-e" in new_session.argv
    assert "FRONTEND_PORT=4201" in new_session.argv
    assert "BONSAI_BRANCH=feature" in new_session.argv
    assert new_session.argv[-2:] == ("--", "yarn dev")
    assert [(pane.name, pane.command) for pane in plan.panes] == [("start", "yarn dev")]


def test_execute_tmux_creates_service_panes_when_services_define_start_commands(
    tmp_path: Path,
) -> None:
    workspace_root, default_worktree, feature_worktree = _write_exec_workspace(tmp_path)
    config_text = (
        VALID_CONFIG.replace(
            'name = "frontend"\nport_env = "FRONTEND_PORT"',
            'name = "frontend"\nstart = "yarn web"\nport_env = "FRONTEND_PORT"',
        )
        .replace(
            'name = "api"\nport_env = "API_PORT"',
            'name = "api"\nstart = "yarn api"\nport_env = "API_PORT"',
        )
        .replace('start = "yarn dev"\n', "")
    )
    write_config(default_worktree, config_text)
    (feature_worktree / ".env.local").write_text(
        "FRONTEND_PORT=4201\nAPI_PORT=3334\n",
        encoding="utf-8",
    )

    class TmuxRunner(RecordingRunner):
        def run(self, argv, cwd=None, check=True, env=None):
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:2] == ["tmux", "has-session"]:
                return CommandResult(returncode=1)
            return CommandResult(returncode=0)

    runner = TmuxRunner()

    plan = execute_tmux(runner, workspace_root, "feature", default_worktree)

    assert [(pane.name, pane.command) for pane in plan.panes] == [
        ("frontend", "yarn web"),
        ("api", "yarn api"),
    ]
    assert [command.argv[:2] for command in runner.commands] == [
        ("tmux", "has-session"),
        ("tmux", "new-session"),
        ("tmux", "split-window"),
        ("tmux", "select-layout"),
    ]

    new_session = runner.commands[1]
    assert new_session.argv[:9] == (
        "tmux",
        "new-session",
        "-d",
        "-s",
        plan.session_name,
        "-n",
        "services",
        "-c",
        str(feature_worktree),
    )
    assert "-e" in new_session.argv
    assert "FRONTEND_PORT=4201" in new_session.argv
    assert "BONSAI_BRANCH=feature" in new_session.argv
    assert new_session.argv[-2:] == ("--", "yarn web")

    split_window = runner.commands[2]
    assert split_window.argv[:7] == (
        "tmux",
        "split-window",
        "-d",
        "-t",
        f"{plan.session_name}:services",
        "-c",
        str(feature_worktree),
    )
    assert "API_PORT=3334" in split_window.argv
    assert split_window.argv[-2:] == ("--", "yarn api")
    assert runner.commands[3].argv == (
        "tmux",
        "select-layout",
        "-t",
        f"{plan.session_name}:services",
        "tiled",
    )


def test_execute_tmux_reports_existing_session_without_starting(tmp_path: Path) -> None:
    workspace_root, default_worktree, _feature_worktree = _write_exec_workspace(tmp_path)

    class ExistingTmuxRunner(RecordingRunner):
        def run(self, argv, cwd=None, check=True, env=None):
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            return CommandResult(returncode=0)

    runner = ExistingTmuxRunner()

    plan = execute_tmux(runner, workspace_root, "feature", default_worktree)

    assert plan.created is False
    assert len(runner.commands) == 1
    assert runner.commands[0].argv == ("tmux", "has-session", "-t", plan.session_name)


def test_execute_up_refuses_when_tracked_process_is_alive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
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
    pid_path = workspace_root / ".bonsai" / "pids" / "main.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(
        json.dumps(
            {
                "branch": "main",
                "worktree_path": str(default_worktree),
                "pid": 321,
                "command": ["yarn", "dev"],
                "log_path": str(workspace_root / ".bonsai" / "logs" / "main" / "start.log"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wf_processes, "_process_is_alive", lambda pid: pid == 321)

    with pytest.raises(BonsaiWorkspaceError, match=r"already running"):
        execute_up(runner, workspace_root, None, default_worktree)

    assert runner.commands == []


def test_execute_up_in_single_mode_refuses_when_another_worktree_is_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    config_text = VALID_CONFIG.replace("[commands]", '[run]\nmode = "single"\n\n[commands]')
    write_config(default_worktree, config_text)
    (feature_worktree / ".env.local").write_text("FRONTEND_PORT=4201\n", encoding="utf-8")
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
    pid_path = workspace_root / ".bonsai" / "pids" / "main.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(
        json.dumps(
            {
                "branch": "main",
                "worktree_path": str(default_worktree),
                "pid": 321,
                "command": ["yarn", "dev"],
                "log_path": str(workspace_root / ".bonsai" / "logs" / "main" / "start.log"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wf_processes, "_process_is_alive", lambda pid: pid == 321)

    with pytest.raises(BonsaiWorkspaceError) as exc_info:
        execute_up(runner, workspace_root, "feature", feature_worktree)

    message = str(exc_info.value)
    assert "main is already running with pid 321" in message
    assert "bonsai stop main" in message
    assert "bonsai stop --all" in message
    assert runner.commands == []


def test_execute_up_in_concurrent_mode_allows_another_worktree_to_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    (feature_worktree / ".env.local").write_text("FRONTEND_PORT=4201\n", encoding="utf-8")
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
    pid_path = workspace_root / ".bonsai" / "pids" / "main.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(
        json.dumps(
            {
                "branch": "main",
                "worktree_path": str(default_worktree),
                "pid": 321,
                "command": ["yarn", "dev"],
                "log_path": str(workspace_root / ".bonsai" / "logs" / "main" / "start.log"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wf_processes, "_process_is_alive", lambda pid: pid == 321)
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda port: port == 4201)

    plan = execute_up(
        runner,
        workspace_root,
        "feature",
        feature_worktree,
        readiness_timeout=0.01,
    )

    assert plan.branch == "feature"
    assert plan.pid == 1000
    assert [command.argv for command in runner.commands] == [("yarn", "dev")]


def test_execute_up_removes_record_and_stops_process_when_readiness_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
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
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(wf_processes, "_process_is_alive", lambda pid: pid == 1000)
    monkeypatch.setattr(wf_probes, "_check_port_listening", lambda _port: False)
    monkeypatch.setattr(
        "bonsai.workflows.processes.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )

    with pytest.raises(BonsaiWorkspaceError, match=r"did not become ready"):
        execute_up(
            runner,
            workspace_root,
            None,
            default_worktree,
            readiness_timeout=0.0,
        )

    assert killed == [(1000, signal.SIGTERM)]
    assert not (workspace_root / ".bonsai" / "pids" / "main.json").exists()


def test_stop_terminates_tracked_process_and_removes_record(
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
            worktrees={},
        ),
    )
    pid_path = workspace_root / ".bonsai" / "pids" / "main.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(
        json.dumps(
            {
                "branch": "main",
                "worktree_path": str(default_worktree),
                "pid": 123,
                "command": ["yarn", "dev"],
                "log_path": str(workspace_root / ".bonsai" / "logs" / "main" / "start.log"),
            }
        ),
        encoding="utf-8",
    )
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(wf_processes, "_process_is_alive", lambda pid: pid == 123)
    monkeypatch.setattr(
        "bonsai.workflows.processes.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )

    plan = execute_stop_processes(
        RecordingRunner(),
        workspace_root,
        current_path=default_worktree,
        terminate_timeout=0.0,
    )

    assert plan.apps[0].branch == "main"
    assert plan.apps[0].pid == 123
    assert plan.apps[0].action == "stopped"
    assert killed == [(123, signal.SIGTERM)]
    assert not pid_path.exists()


def test_stop_removes_stale_tracked_record(tmp_path: Path, monkeypatch) -> None:
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
    pid_path = workspace_root / ".bonsai" / "pids" / "main.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(
        json.dumps(
            {
                "branch": "main",
                "worktree_path": str(default_worktree),
                "pid": 123,
                "command": ["yarn", "dev"],
                "log_path": str(workspace_root / ".bonsai" / "logs" / "main" / "start.log"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wf_processes, "_process_is_alive", lambda _pid: False)

    plan = execute_stop_processes(
        RecordingRunner(),
        workspace_root,
        current_path=default_worktree,
    )

    assert plan.apps[0].action == "stale"
    assert plan.apps[0].pid == 123
    assert not pid_path.exists()


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
