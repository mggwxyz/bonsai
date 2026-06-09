from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bonsai.env import parse_env_content
from bonsai.errors import BonsaiCommandError, BonsaiWorkspaceError
from bonsai.models import CommandResult
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


@dataclass(frozen=True)
class StaleComposeContainer:
    container_id: str
    name: str
    project_name: str
    status: str
    network_ids: tuple[str, ...]
    running: bool


@dataclass(frozen=True)
class ComposePublishedPort:
    project_name: str
    host_port: int


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


def _run_docker(runner: Runner, argv: list[str], failure: str) -> CommandResult:
    try:
        result = runner.run(argv, check=False)
    except FileNotFoundError as exc:
        raise BonsaiWorkspaceError("Docker command not found") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise BonsaiWorkspaceError(f"{failure}{suffix}")
    return result


def _docker_lines(output: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def _project_container_ids(runner: Runner, project_name: str) -> tuple[str, ...]:
    result = _run_docker(
        runner,
        [
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
        ],
        f"Failed to list Docker Compose containers for {project_name}",
    )
    return _docker_lines(result.stdout)


def _parse_inspect_containers(output: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(output or "[]")
    except json.JSONDecodeError as exc:
        raise BonsaiWorkspaceError("Docker inspect returned invalid JSON") from exc
    if not isinstance(parsed, list):
        raise BonsaiWorkspaceError("Docker inspect returned an unexpected payload")
    return [item for item in parsed if isinstance(item, dict)]


def _missing_network_ids(
    container: dict[str, Any],
    live_network_ids: set[str],
) -> tuple[str, ...]:
    network_settings = container.get("NetworkSettings")
    if not isinstance(network_settings, dict):
        return ()
    networks = network_settings.get("Networks")
    if not isinstance(networks, dict):
        return ()

    missing_ids: set[str] = set()
    for network in networks.values():
        if not isinstance(network, dict):
            continue
        network_id = str(network.get("NetworkID") or "").strip()
        if network_id and network_id not in live_network_ids:
            missing_ids.add(network_id)
    return tuple(sorted(missing_ids))


def _compose_project_name(container: dict[str, Any]) -> str:
    config = container.get("Config")
    if not isinstance(config, dict):
        return ""
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        return ""
    return str(labels.get("com.docker.compose.project") or "")


def _published_host_ports(container: dict[str, Any]) -> tuple[int, ...]:
    network_settings = container.get("NetworkSettings")
    if not isinstance(network_settings, dict):
        return ()
    ports = network_settings.get("Ports")
    if not isinstance(ports, dict):
        return ()

    host_ports: set[int] = set()
    for bindings in ports.values():
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            host_port = str(binding.get("HostPort") or "").strip()
            if host_port.isdigit():
                host_ports.add(int(host_port))
    return tuple(sorted(host_ports))


def find_compose_published_ports(
    runner: Runner,
    project_names: tuple[str, ...],
) -> tuple[ComposePublishedPort, ...]:
    project_names = tuple(sorted({name for name in project_names if name}))
    if not project_names:
        return ()

    container_ids: list[str] = []
    seen_container_ids: set[str] = set()
    for project_name in project_names:
        for container_id in _project_container_ids(runner, project_name):
            if container_id in seen_container_ids:
                continue
            seen_container_ids.add(container_id)
            container_ids.append(container_id)
    if not container_ids:
        return ()

    inspect_result = _run_docker(
        runner,
        ["docker", "inspect", *container_ids],
        "Failed to inspect Docker Compose containers",
    )
    published: list[ComposePublishedPort] = []
    for container in _parse_inspect_containers(inspect_result.stdout):
        project_name = _compose_project_name(container)
        if not project_name:
            continue
        published.extend(
            ComposePublishedPort(project_name=project_name, host_port=host_port)
            for host_port in _published_host_ports(container)
        )
    return tuple(published)


def _container_state(container: dict[str, Any]) -> tuple[str, bool]:
    state = container.get("State")
    if not isinstance(state, dict):
        return "unknown", False
    return str(state.get("Status") or "unknown"), bool(state.get("Running"))


def find_stale_compose_containers(
    runner: Runner,
    project_names: tuple[str, ...],
) -> tuple[StaleComposeContainer, ...]:
    project_names = tuple(sorted({name for name in project_names if name}))
    if not project_names:
        return ()

    network_result = _run_docker(
        runner,
        ["docker", "network", "ls", "--no-trunc", "--quiet"],
        "Failed to list Docker networks",
    )
    live_network_ids = set(_docker_lines(network_result.stdout))

    container_ids: list[str] = []
    seen_container_ids: set[str] = set()
    for project_name in project_names:
        for container_id in _project_container_ids(runner, project_name):
            if container_id in seen_container_ids:
                continue
            seen_container_ids.add(container_id)
            container_ids.append(container_id)
    if not container_ids:
        return ()

    inspect_result = _run_docker(
        runner,
        ["docker", "inspect", *container_ids],
        "Failed to inspect Docker Compose containers",
    )
    stale: list[StaleComposeContainer] = []
    for container in _parse_inspect_containers(inspect_result.stdout):
        missing_network_ids = _missing_network_ids(container, live_network_ids)
        if not missing_network_ids:
            continue
        status, running = _container_state(container)
        container_id = str(container.get("Id") or "")
        name = str(container.get("Name") or container_id).lstrip("/")
        stale.append(
            StaleComposeContainer(
                container_id=container_id,
                name=name,
                project_name=_compose_project_name(container),
                status=status,
                network_ids=missing_network_ids,
                running=running,
            )
        )
    return tuple(stale)


def remove_stopped_stale_compose_containers(
    runner: Runner,
    containers: tuple[StaleComposeContainer, ...],
) -> tuple[StaleComposeContainer, ...]:
    stopped = tuple(container for container in containers if not container.running)
    if not stopped:
        return ()

    _run_docker(
        runner,
        ["docker", "rm", *(container.container_id for container in stopped)],
        "Failed to remove stale Docker Compose containers",
    )
    return stopped
