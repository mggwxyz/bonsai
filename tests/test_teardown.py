import json
import signal
from pathlib import Path

import pytest
from test_config import VALID_CONFIG, write_config

from bonsai.models import BonsaiState, CommandResult, CommandSpec, ManagedWorktree
from bonsai.state import save_state
from bonsai.workflows import execute_cleanup, execute_remove


def test_execute_remove_stops_worktree_listener_before_compose_and_cleans_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []

    class StopFirstRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            _ = check
            command = CommandSpec(argv=tuple(argv), cwd=cwd)
            self.commands.append(command)
            events.append(("run", command))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            if argv[0] == "lsof" and "-iTCP:4201" in argv:
                return CommandResult(returncode=0, stdout="p123\ncnode\numichael\n")
            if argv == ["lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p123\nn{branch_worktree}\n")
            if argv[0] == "lsof":
                return CommandResult(returncode=1)
            return CommandResult(returncode=0)

    def fake_kill(pid: int, sig: signal.Signals) -> None:
        events.append(("kill", (pid, sig)))

    runner = StopFirstRemoveRunner()
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
    log_dir = workspace_root / ".bonsai" / "logs" / "feature"
    log_dir.mkdir(parents=True)
    (log_dir / "20260526-143300-start.log").write_text("started\n", encoding="utf-8")
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
    monkeypatch.setattr("bonsai.workflows.os.kill", fake_kill)
    monkeypatch.setattr("bonsai.workflows._check_port_listening", lambda _port: False)

    plan = execute_remove(runner, "feature", workspace_root)

    docker_index = events.index(
        (
            "run",
            CommandSpec(
                argv=("docker", "compose", "-p", "authentic-feature", "down"),
                cwd=branch_worktree,
            ),
        )
    )
    git_remove_index = events.index(
        (
            "run",
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
        )
    )
    kill_index = events.index(("kill", (123, signal.SIGTERM)))
    assert kill_index < docker_index < git_remove_index
    assert plan.removed_logs == log_dir
    assert not log_dir.exists()


def test_execute_remove_runs_tracked_down_before_listener_stop_and_compose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []

    class StopFirstRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            _ = check
            command = CommandSpec(argv=tuple(argv), cwd=cwd)
            self.commands.append(command)
            events.append(("run", command))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            if argv[0] == "lsof" and "-iTCP:4201" in argv:
                return CommandResult(returncode=0, stdout="p123\ncnode\numichael\n")
            if argv == ["lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p123\nn{branch_worktree}\n")
            if argv[0] == "lsof":
                return CommandResult(returncode=1)
            return CommandResult(returncode=0)

    def fake_terminate(pid: int, timeout: float) -> None:
        events.append(("down", (pid, timeout)))

    def fake_kill(pid: int, sig: signal.Signals) -> None:
        events.append(("kill", (pid, sig)))

    runner = StopFirstRemoveRunner()
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
    pid_path = workspace_root / ".bonsai" / "pids" / "feature.json"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(
        json.dumps(
            {
                "branch": "feature",
                "worktree_path": str(branch_worktree),
                "pid": 321,
                "command": ["yarn", "dev"],
                "log_path": str(workspace_root / ".bonsai" / "logs" / "feature" / "start.log"),
            }
        ),
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
    monkeypatch.setattr("bonsai.workflows._process_is_alive", lambda pid: pid == 321)
    monkeypatch.setattr("bonsai.workflows._terminate_process_id", fake_terminate)
    monkeypatch.setattr("bonsai.workflows.os.kill", fake_kill)
    monkeypatch.setattr("bonsai.workflows._check_port_listening", lambda _port: False)

    execute_remove(runner, "feature", workspace_root)

    down_index = events.index(("down", (321, 5.0)))
    listener_stop_index = events.index(("kill", (123, signal.SIGTERM)))
    docker_index = events.index(
        (
            "run",
            CommandSpec(
                argv=("docker", "compose", "-p", "authentic-feature", "down"),
                cwd=branch_worktree,
            ),
        )
    )
    assert down_index < listener_stop_index < docker_index
    assert not pid_path.exists()


def test_execute_cleanup_apply_stops_worktree_listener_and_cleans_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []

    class ApplyCleanupRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            _ = check
            command = CommandSpec(argv=tuple(argv), cwd=cwd)
            self.commands.append(command)
            events.append(("run", command))
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
            if argv[0] == "lsof" and "-iTCP:4201" in argv:
                return CommandResult(returncode=0, stdout="p123\ncnode\numichael\n")
            if argv == ["lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"]:
                return CommandResult(returncode=0, stdout=f"p123\nn{feature_worktree}\n")
            if argv[0] == "lsof":
                return CommandResult(returncode=1)
            return CommandResult(returncode=0)

    def fake_kill(pid: int, sig: signal.Signals) -> None:
        events.append(("kill", (pid, sig)))

    runner = ApplyCleanupRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    log_dir = workspace_root / ".bonsai" / "logs" / "feature"
    log_dir.mkdir(parents=True)
    (log_dir / "20260526-143300-start.log").write_text("started\n", encoding="utf-8")
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
    monkeypatch.setattr("bonsai.workflows.os.kill", fake_kill)
    monkeypatch.setattr("bonsai.workflows._check_port_listening", lambda _port: False)

    plan = execute_cleanup(runner, workspace_root, apply=True)

    assert [(item.branch, item.action, item.reason) for item in plan.items] == [
        ("feature", "removed", "pull request is merged")
    ]
    assert ("kill", (123, signal.SIGTERM)) in events
    assert not log_dir.exists()
