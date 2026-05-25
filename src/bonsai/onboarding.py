from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        'root_caddyfile = "Caddyfile"',
        'snippets_dir = "caddy.d"',
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


def _read_package_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


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
