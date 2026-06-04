from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bonsai.compose import detect_compose_project
from bonsai.config import load_config
from bonsai.errors import BonsaiConfigError
from bonsai.slug import branch_slug


@dataclass(frozen=True)
class ProjectDefaults:
    app_name: str
    base_branch: str
    install_command: str | None
    setup_command: str | None
    start_command: str | None
    has_env_file: bool
    service_name: str
    port_env: str
    base_port: int
    url: str


@dataclass(frozen=True)
class StarterConfig:
    name: str
    base_branch: str
    install_command: str | None
    setup_command: str | None
    start_command: str | None
    symlink_env: bool
    service_name: str
    port_env: str
    base_port: int
    url: str


def detect_project_defaults(
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
) -> ProjectDefaults:
    if not (repo_path / "package.json").exists():
        detected = _detect_non_npm_stack(repo_path, fallback_name, base_branch)
        if detected is not None:
            return detected

    package = _read_package_json(repo_path / "package.json")
    app_name = _package_app_name(package) or fallback_name
    package_manager = _package_manager(repo_path)
    scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}
    start_script = _start_script(scripts)
    app_slug = branch_slug(app_name) or "app"

    return ProjectDefaults(
        app_name=app_slug,
        base_branch=base_branch,
        install_command=f"{package_manager} install" if package else None,
        setup_command=None,
        start_command=_script_command(package_manager, start_script) if start_script else None,
        has_env_file=(repo_path / ".env").exists(),
        service_name="frontend",
        port_env="PORT",
        base_port=3000,
        url=f"https://${{slug}}.{app_slug}.localhost",
    )


def _detect_non_npm_stack(
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
) -> ProjectDefaults | None:
    detectors = (
        _detect_python_stack,
        _detect_go_stack,
        _detect_rails_stack,
        _detect_compose_stack,
        _detect_makefile_stack,
    )
    for detector in detectors:
        detected = detector(repo_path, fallback_name, base_branch)
        if detected is not None:
            return detected
    return None


def _stack_defaults(
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
    *,
    install_command: str | None,
    start_command: str | None,
    service_name: str,
    port_env: str,
    base_port: int,
) -> ProjectDefaults:
    app_slug = branch_slug(fallback_name) or "app"
    return ProjectDefaults(
        app_name=app_slug,
        base_branch=base_branch,
        install_command=install_command,
        setup_command=None,
        start_command=start_command,
        has_env_file=(repo_path / ".env").exists(),
        service_name=service_name,
        port_env=port_env,
        base_port=base_port,
        url=f"https://${{slug}}.{app_slug}.localhost",
    )


def _detect_python_stack(
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
) -> ProjectDefaults | None:
    pyproject = repo_path / "pyproject.toml"
    requirements = repo_path / "requirements.txt"
    if not pyproject.exists() and not requirements.exists():
        return None

    if (repo_path / "uv.lock").exists():
        install_command = "uv sync"
    elif requirements.exists():
        install_command = "pip install -r requirements.txt"
    else:
        install_command = None

    return _stack_defaults(
        repo_path,
        fallback_name,
        base_branch,
        install_command=install_command,
        start_command=_pyproject_start_command(pyproject),
        service_name="api",
        port_env="API_PORT",
        base_port=8000,
    )


def _detect_go_stack(
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
) -> ProjectDefaults | None:
    if not (repo_path / "go.mod").exists():
        return None
    return _stack_defaults(
        repo_path,
        fallback_name,
        base_branch,
        install_command="go mod download",
        start_command="go run .",
        service_name="app",
        port_env="PORT",
        base_port=8080,
    )


def _detect_rails_stack(
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
) -> ProjectDefaults | None:
    if not (repo_path / "Gemfile").exists():
        return None
    return _stack_defaults(
        repo_path,
        fallback_name,
        base_branch,
        install_command="bundle install",
        start_command="bin/rails server",
        service_name="web",
        port_env="PORT",
        base_port=3000,
    )


def _detect_compose_stack(
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
) -> ProjectDefaults | None:
    if detect_compose_project(repo_path) is None:
        return None
    return _stack_defaults(
        repo_path,
        fallback_name,
        base_branch,
        install_command=None,
        start_command="docker compose up",
        service_name="app",
        port_env="PORT",
        base_port=8080,
    )


def _detect_makefile_stack(
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
) -> ProjectDefaults | None:
    makefile = repo_path / "Makefile"
    if not makefile.exists():
        return None
    targets = _makefile_targets(makefile)
    install_command = "make install" if "install" in targets else None
    start_command = next(
        (f"make {target}" for target in ("dev", "run") if target in targets),
        "make" if "make" in targets else None,
    )
    return _stack_defaults(
        repo_path,
        fallback_name,
        base_branch,
        install_command=install_command,
        start_command=start_command,
        service_name="app",
        port_env="PORT",
        base_port=8080,
    )


def render_starter_config(config: StarterConfig) -> str:
    lines = [
        f"name = {_toml_string(config.name)}",
        f"base_branch = {_toml_string(config.base_branch)}",
        "",
        "[workspace]",
        'default_parent = "~/Projects"',
        "",
        "[caddy]",
        "auto_install = true",
        "auto_start = true",
    ]

    command_lines = []
    if config.install_command:
        command_lines.append(f"install = {_toml_string(config.install_command)}")
    if config.setup_command:
        command_lines.append(f"setup = {_toml_string(config.setup_command)}")
    if config.start_command:
        command_lines.append(f"start = {_toml_string(config.start_command)}")
    if command_lines:
        lines.extend(["", "[commands]", *command_lines])

    if config.symlink_env:
        lines.extend(
            [
                "",
                "[[shared_files]]",
                'source = ".env"',
                'target = ".env"',
                'mode = "symlink"',
            ]
        )

    lines.extend(
        [
            "",
            "[[services]]",
            f"name = {_toml_string(config.service_name)}",
            f"port_env = {_toml_string(config.port_env)}",
            f"base_port = {config.base_port}",
            "primary = true",
            f"url = {_toml_string(config.url)}",
            "",
        ]
    )
    return "\n".join(lines)


def write_starter_config(path: Path, config: StarterConfig) -> Path:
    path.write_text(render_starter_config(config), encoding="utf-8")
    return path


def prompt_starter_config(
    defaults: ProjectDefaults,
    *,
    ask: Callable[..., str],
    confirm: Callable[[str], bool],
    ask_optional: Callable[[str, str | None], str | None],
) -> StarterConfig:
    app_name = ask("App name", default=defaults.app_name).strip()
    base_branch = ask("Base branch", default=defaults.base_branch).strip()
    install_command = ask_optional("Install command", defaults.install_command)
    setup_command = ask_optional("Setup command", defaults.setup_command)
    start_command = ask_optional("Start command", defaults.start_command)
    symlink_env = confirm(
        "Symlink .env into each worktree",
        default=defaults.has_env_file,
    )
    service_name = ask("Primary service name", default=defaults.service_name).strip()
    port_env = ask("Port environment variable", default=defaults.port_env).strip()
    base_port = ask("Base port", default=defaults.base_port, type=int)
    url = ask("Local URL template", default=defaults.url).strip()
    return StarterConfig(
        name=app_name,
        base_branch=base_branch,
        install_command=install_command,
        setup_command=setup_command,
        start_command=start_command,
        symlink_env=symlink_env,
        service_name=service_name,
        port_env=port_env,
        base_port=base_port,
        url=url,
    )


def write_guided_config(
    config_path: Path,
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
    force: bool = False,
    *,
    ask: Callable[..., str],
    confirm: Callable[..., bool],
    ask_optional: Callable[[str, str | None], str | None],
) -> Path:
    if config_path.exists() and not force:
        raise BonsaiConfigError(f".bonsai.toml already exists at {config_path}")
    defaults = detect_project_defaults(repo_path, fallback_name, base_branch)
    config = prompt_starter_config(
        defaults,
        ask=ask,
        confirm=confirm,
        ask_optional=ask_optional,
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    path = write_starter_config(config_path, config)
    load_config(path)
    return path


def _read_package_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _pyproject_start_command(pyproject: Path) -> str | None:
    if not pyproject.exists():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    project = data.get("project")
    scripts = project.get("scripts") if isinstance(project, dict) else None
    if not isinstance(scripts, dict):
        return None
    for name in scripts:
        if isinstance(name, str) and name.strip():
            return name
    return None


def _makefile_targets(makefile: Path) -> frozenset[str]:
    try:
        content = makefile.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    targets = re.findall(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", content, flags=re.MULTILINE)
    return frozenset(targets)


def _package_app_name(package: dict[str, Any]) -> str | None:
    value = package.get("name")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.rsplit("/", maxsplit=1)[-1]


def _package_manager(repo_path: Path) -> str:
    if (repo_path / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo_path / "yarn.lock").exists():
        return "yarn"
    if (repo_path / "bun.lockb").exists() or (repo_path / "bun.lock").exists():
        return "bun"
    return "npm"


def _start_script(scripts: dict[Any, Any]) -> str | None:
    if isinstance(scripts.get("dev"), str):
        return "dev"
    if isinstance(scripts.get("start"), str):
        return "start"
    return None


def _script_command(package_manager: str, script: str) -> str:
    if package_manager == "npm":
        return "npm start" if script == "start" else f"npm run {script}"
    if package_manager == "bun":
        return f"bun run {script}"
    return f"{package_manager} {script}"


def _toml_string(value: str) -> str:
    return json.dumps(value)
