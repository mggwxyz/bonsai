from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bonsai.env import parse_env_content
from bonsai.errors import BonsaiCommandError, BonsaiWorkspaceError
from bonsai.process import Runner

COMPOSE_FILENAMES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)


@dataclass(frozen=True)
class ComposeProject:
    worktree_path: Path
    project_name: str
    compose_file: Path


def detect_compose_project(worktree_path: Path) -> ComposeProject | None:
    compose_file = next(
        (
            worktree_path / filename
            for filename in COMPOSE_FILENAMES
            if (worktree_path / filename).exists()
        ),
        None,
    )
    if compose_file is None:
        return None

    return ComposeProject(
        worktree_path=worktree_path,
        project_name=resolve_compose_project_name(worktree_path),
        compose_file=compose_file,
    )


def resolve_compose_project_name(worktree_path: Path) -> str:
    env_path = worktree_path / ".env.local"
    if env_path.exists():
        env = parse_env_content(env_path.read_text(encoding="utf-8"))
        project_name = env.get("COMPOSE_PROJECT_NAME", "").strip()
        if project_name:
            return project_name
    return worktree_path.name


def teardown_compose_project(runner: Runner, project: ComposeProject) -> None:
    try:
        runner.run(
            ["docker", "compose", "-p", project.project_name, "down"],
            cwd=project.worktree_path,
        )
    except (BonsaiCommandError, FileNotFoundError) as exc:
        raise BonsaiWorkspaceError(
            "Failed to tear down Docker Compose project "
            f"{project.project_name} at {project.worktree_path}"
        ) from exc
