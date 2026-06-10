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
    execute_start,
    execute_stop_processes,
    execute_up,
    plan_command_log,
    plan_stop_processes,
    resolve_start_target,
    run_lifecycle_command,
)
from bonsai.workflows import probes as wf_probes
from bonsai.workflows import processes as wf_processes


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
    assert command.env == (
        ("COMPOSE_PROJECT_NAME", "authentic-feature"),
        ("FRONTEND_PORT", "4201"),
    )
    assert command.log_path == plan.log_path
    record = json.loads(pid_path.read_text(encoding="utf-8"))
    assert record["branch"] == "feature"
    assert record["pid"] == 1000
    assert record["worktree_path"] == str(feature_worktree)
    assert record["command"] == ["yarn", "dev"]
    assert record["log_path"] == str(plan.log_path)


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
