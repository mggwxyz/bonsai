from pathlib import Path

import pytest

from bonsai.compose import (
    COMPOSE_FILENAMES,
    ComposeProject,
    StaleComposeContainer,
    detect_compose_project,
    find_stale_compose_containers,
    remove_stopped_stale_compose_containers,
    teardown_compose_project,
)
from bonsai.errors import BonsaiCommandError, BonsaiWorkspaceError
from bonsai.models import CommandResult, CommandSpec


def test_detect_compose_project_uses_env_project_name(tmp_path: Path) -> None:
    worktree = tmp_path / "feature-folder"
    worktree.mkdir()
    (worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
        encoding="utf-8",
    )

    project = detect_compose_project(worktree)

    assert project == ComposeProject(
        worktree_path=worktree,
        project_name="authentic-feature",
        compose_file=worktree / "compose.yaml",
    )


def test_detect_compose_project_falls_back_to_folder_name(tmp_path: Path) -> None:
    worktree = tmp_path / "feature-folder"
    worktree.mkdir()
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (worktree / ".env.local").write_text("SLOT=1\n", encoding="utf-8")

    project = detect_compose_project(worktree)

    assert project == ComposeProject(
        worktree_path=worktree,
        project_name="feature-folder",
        compose_file=worktree / "docker-compose.yml",
    )


def test_detect_compose_project_returns_none_without_root_compose_file(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "feature-folder"
    worktree.mkdir()
    (worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
        encoding="utf-8",
    )

    assert detect_compose_project(worktree) is None


def test_detect_compose_project_checks_supported_filenames() -> None:
    assert COMPOSE_FILENAMES == (
        "compose.yaml",
        "compose.yml",
        "docker-compose.yaml",
        "docker-compose.yml",
    )


def test_teardown_compose_project_runs_down_with_project_name(tmp_path: Path) -> None:
    class RecordingComposeRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            return CommandResult(returncode=0)

    runner = RecordingComposeRunner()
    worktree = tmp_path / "feature-folder"
    worktree.mkdir()
    project = ComposeProject(
        worktree_path=worktree,
        project_name="authentic-feature",
        compose_file=worktree / "compose.yaml",
    )

    teardown_compose_project(runner, project)

    assert runner.commands == [
        CommandSpec(
            argv=("docker", "compose", "-p", "authentic-feature", "down"),
            cwd=worktree,
        )
    ]


def test_teardown_compose_project_wraps_command_failure(tmp_path: Path) -> None:
    class FailingComposeRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            raise BonsaiCommandError("docker compose failed")

    worktree = tmp_path / "feature-folder"
    worktree.mkdir()
    project = ComposeProject(
        worktree_path=worktree,
        project_name="authentic-feature",
        compose_file=worktree / "compose.yaml",
    )

    expected = f"Failed to tear down Docker Compose project authentic-feature at {worktree}"
    with pytest.raises(BonsaiWorkspaceError, match=expected):
        teardown_compose_project(FailingComposeRunner(), project)


def test_find_stale_compose_containers_reports_missing_network_ids() -> None:
    class DockerRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["docker", "network", "ls", "--no-trunc", "--quiet"]:
                return CommandResult(returncode=0, stdout="live-network\n")
            if argv[:5] == [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--no-trunc",
            ]:
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
            return CommandResult(returncode=1, stderr="unexpected command")

    stale = find_stale_compose_containers(DockerRunner(), ("authentic",))

    assert stale == (
        StaleComposeContainer(
            container_id="stale-id",
            name="authentic-seed-migrate-1",
            project_name="authentic",
            status="exited",
            network_ids=("missing-network",),
            running=False,
        ),
    )


def test_remove_stopped_stale_compose_containers_skips_running_containers() -> None:
    class DockerRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            return CommandResult(returncode=0)

    runner = DockerRunner()
    stopped = StaleComposeContainer(
        container_id="stopped-id",
        name="authentic-seed-migrate-1",
        project_name="authentic",
        status="exited",
        network_ids=("missing-network",),
        running=False,
    )
    running = StaleComposeContainer(
        container_id="running-id",
        name="authentic-api-1",
        project_name="authentic",
        status="running",
        network_ids=("missing-network",),
        running=True,
    )

    removed = remove_stopped_stale_compose_containers(runner, (stopped, running))

    assert removed == (stopped,)
    assert runner.commands == [
        CommandSpec(argv=("docker", "rm", "stopped-id")),
    ]
