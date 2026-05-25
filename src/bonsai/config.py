from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from bonsai.errors import BonsaiConfigError
from bonsai.models import (
    BonsaiConfig,
    CaddyConfig,
    CommandsConfig,
    EnvConfig,
    ServiceConfig,
    SharedFileConfig,
    WorkspaceConfig,
)


def load_config(path: Path) -> BonsaiConfig:
    if not path.exists():
        raise BonsaiConfigError(f"Missing .bonsai.toml at {path}")

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise BonsaiConfigError(f"Invalid TOML in {path}: {exc}") from exc

    config = BonsaiConfig(
        name=_require_str(raw, "name"),
        base_branch=_optional_str(raw, "base_branch"),
        workspace=_workspace(_optional_table(raw, "workspace")),
        caddy=_caddy(_optional_table(raw, "caddy")),
        commands=_commands(_optional_table(raw, "commands")),
        shared_files=tuple(_shared_file(item) for item in _array_of_tables(raw, "shared_files")),
        env=tuple(_env(item) for item in _array_of_tables(raw, "env")),
        services=tuple(_service(item) for item in _array_of_tables(raw, "services")),
        path=path,
    )
    _validate(config)
    return config


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BonsaiConfigError(f"Config key {key} must be a non-empty string")
    return value


def _optional_str(raw: dict[str, Any], key: str, default: str | None = None) -> str | None:
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise BonsaiConfigError(f"Config key {key} must be a string")
    if not value.strip():
        raise BonsaiConfigError(f"Config key {key} must be a non-empty string")
    return value


def _table(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise BonsaiConfigError(f"Config key {key} must be a table")
    return value


def _optional_table(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if value is None:
        return {}
    return _table(raw, key)


def _array_of_tables(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise BonsaiConfigError(f"Config key {key} must contain tables")
    return value


def _optional_bool(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise BonsaiConfigError(f"Config key {key} must be a boolean")
    return value


def _workspace(raw: dict[str, Any]) -> WorkspaceConfig:
    return WorkspaceConfig(default_parent=_optional_str(raw, "default_parent", "~/Projects"))


def _caddy(raw: dict[str, Any]) -> CaddyConfig:
    return CaddyConfig(
        auto_install=_optional_bool(raw, "auto_install", True),
        auto_start=_optional_bool(raw, "auto_start", True),
        root_caddyfile=_optional_str(raw, "root_caddyfile", "Caddyfile"),
        snippets_dir=_optional_str(raw, "snippets_dir", "caddy.d"),
    )


def _commands(raw: dict[str, Any]) -> CommandsConfig:
    return CommandsConfig(
        install=_optional_str(raw, "install"),
        start=_optional_str(raw, "start"),
        migrate=_optional_str(raw, "migrate"),
    )


def _shared_file(raw: dict[str, Any]) -> SharedFileConfig:
    return SharedFileConfig(
        source=_require_str(raw, "source"),
        target=_require_str(raw, "target"),
        mode=_optional_str(raw, "mode", "symlink"),
    )


def _env(raw: dict[str, Any]) -> EnvConfig:
    return EnvConfig(name=_require_str(raw, "name"), value=_require_str(raw, "value"))


def _service(raw: dict[str, Any]) -> ServiceConfig:
    return ServiceConfig(
        name=_require_str(raw, "name"),
        port_env=_require_str(raw, "port_env"),
        base_port=_require_int(raw, "base_port"),
        public=_optional_bool(raw, "public", True),
        primary=_optional_bool(raw, "primary", False),
        url=_optional_str(raw, "url"),
    )


def _require_int(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BonsaiConfigError(f"Config key {key} must be an integer")
    return value


def _validate(config: BonsaiConfig) -> None:
    names: set[str] = set()
    for service in config.services:
        if service.name in names:
            raise BonsaiConfigError(f"Duplicate service name: {service.name}")
        names.add(service.name)
        if service.public and not service.url:
            raise BonsaiConfigError(f"Public service {service.name} requires a url")

    primary_count = sum(1 for service in config.public_services() if service.primary)
    if primary_count > 1:
        raise BonsaiConfigError("Multiple primary public services")
    if config.public_services() and primary_count == 0:
        raise BonsaiConfigError("Exactly one primary public service is required")

    for shared_file in config.shared_files:
        if shared_file.mode != "symlink":
            raise BonsaiConfigError(f"Unsupported shared file mode: {shared_file.mode}")
