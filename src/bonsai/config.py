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
        workspace=_workspace(raw.get("workspace", {})),
        caddy=_caddy(raw.get("caddy", {})),
        commands=_commands(raw.get("commands", {})),
        shared_files=tuple(_shared_file(item) for item in raw.get("shared_files", [])),
        env=tuple(_env(item) for item in raw.get("env", [])),
        services=tuple(_service(item) for item in raw.get("services", [])),
        path=path,
    )
    _validate(config)
    return config


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BonsaiConfigError(f"Config key {key} must be a non-empty string")
    return value


def _optional_str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise BonsaiConfigError(f"Config key {key} must be a non-empty string")
    return value


def _workspace(raw: dict[str, Any]) -> WorkspaceConfig:
    return WorkspaceConfig(default_parent=str(raw.get("default_parent", "~/Projects")))


def _caddy(raw: dict[str, Any]) -> CaddyConfig:
    return CaddyConfig(
        auto_install=bool(raw.get("auto_install", True)),
        auto_start=bool(raw.get("auto_start", True)),
        root_caddyfile=str(raw.get("root_caddyfile", "Caddyfile")),
        snippets_dir=str(raw.get("snippets_dir", "caddy.d")),
    )


def _commands(raw: dict[str, Any]) -> CommandsConfig:
    return CommandsConfig(
        install=_none_or_str(raw.get("install")),
        start=_none_or_str(raw.get("start")),
        migrate=_none_or_str(raw.get("migrate")),
    )


def _shared_file(raw: dict[str, Any]) -> SharedFileConfig:
    return SharedFileConfig(
        source=_require_str(raw, "source"),
        target=_require_str(raw, "target"),
        mode=str(raw.get("mode", "symlink")),
    )


def _env(raw: dict[str, Any]) -> EnvConfig:
    return EnvConfig(name=_require_str(raw, "name"), value=_require_str(raw, "value"))


def _service(raw: dict[str, Any]) -> ServiceConfig:
    return ServiceConfig(
        name=_require_str(raw, "name"),
        port_env=_require_str(raw, "port_env"),
        base_port=_require_int(raw, "base_port"),
        public=bool(raw.get("public", True)),
        primary=bool(raw.get("primary", False)),
        url=_none_or_str(raw.get("url")),
    )


def _require_int(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BonsaiConfigError(f"Config key {key} must be an integer")
    return value


def _none_or_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise BonsaiConfigError("Optional string values must be non-empty strings")
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
