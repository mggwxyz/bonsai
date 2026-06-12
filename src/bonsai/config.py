from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bonsai.errors import BonsaiConfigError
from bonsai.models import (
    BonsaiConfig,
    BrowserExtensionConfig,
    CaddyConfig,
    CommandsConfig,
    EnvConfig,
    RunConfig,
    ServiceConfig,
    SharedFileConfig,
)
from bonsai.rendering import RESERVED_SERVICE_PORT_ENV_NAMES, RESERVED_USER_ENV_NAMES

LOCAL_CONFIG_NAME = ".bonsai.local.toml"


def load_config(path: Path, local_paths: Sequence[Path] | None = None) -> BonsaiConfig:
    if not path.exists():
        raise BonsaiConfigError(f"Missing .bonsai.toml at {path}")

    raw = _load_toml(path)
    overlay_candidates = (
        (path.with_name(LOCAL_CONFIG_NAME),)
        if local_paths is None
        else tuple(local_paths)
    )
    overlays = tuple(candidate for candidate in overlay_candidates if candidate.is_file())
    for overlay_path in overlays:
        raw = _merge_tables(raw, _load_toml(overlay_path))

    try:
        config = _parse_config(raw, path, overlays)
        _validate(config)
    except BonsaiConfigError as exc:
        if overlays:
            overlay_list = ", ".join(str(overlay) for overlay in overlays)
            raise BonsaiConfigError(
                f"{exc} (after applying local override {overlay_list})"
            ) from exc
        raise
    return config


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise BonsaiConfigError(f"Invalid TOML in {path}: {exc}") from exc


def _merge_tables(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_tables(existing, value)
            continue
        merged[key] = value
    return merged


def _parse_config(raw: dict[str, Any], path: Path, local_paths: tuple[Path, ...]) -> BonsaiConfig:
    return BonsaiConfig(
        name=_require_str(raw, "name"),
        base_branch=_optional_str(raw, "base_branch"),
        caddy=_caddy(_optional_table(raw, "caddy")),
        commands=_commands(_optional_table(raw, "commands")),
        run=_run(_optional_table(raw, "run")),
        browser_extension=_browser_extension(_optional_table(raw, "browser_extension")),
        shared_files=tuple(_shared_file(item) for item in _array_of_tables(raw, "shared_files")),
        env=tuple(_env(item) for item in _array_of_tables(raw, "env")),
        services=tuple(_service(item) for item in _array_of_tables(raw, "services")),
        path=path,
        local_paths=local_paths,
    )


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


def _caddy(raw: dict[str, Any]) -> CaddyConfig:
    return CaddyConfig(
        auto_install=_optional_bool(raw, "auto_install", True),
        auto_start=_optional_bool(raw, "auto_start", True),
    )


def _commands(raw: dict[str, Any]) -> CommandsConfig:
    return CommandsConfig(
        preinstall=_optional_str(raw, "preinstall"),
        install=_optional_str(raw, "install"),
        postinstall=_optional_str(raw, "postinstall"),
        presetup=_optional_str(raw, "presetup"),
        setup=_optional_str(raw, "setup"),
        postsetup=_optional_str(raw, "postsetup"),
        postadd=_optional_str(raw, "postadd"),
        preremove=_optional_str(raw, "preremove"),
        prestart=_optional_str(raw, "prestart"),
        start=_optional_str(raw, "start"),
        poststart=_optional_str(raw, "poststart"),
    )


def _run(raw: dict[str, Any]) -> RunConfig:
    value = raw.get("mode")
    if value is None:
        mode = "concurrent"
    elif not isinstance(value, str):
        raise BonsaiConfigError("Config key run.mode must be a string")
    elif not value.strip():
        raise BonsaiConfigError("Config key run.mode must be a non-empty string")
    else:
        mode = value
    if mode == "concurrent" or mode == "single":
        return RunConfig(mode=mode)
    raise BonsaiConfigError("Config key run.mode must be one of: concurrent, single")


def _browser_extension(raw: dict[str, Any]) -> BrowserExtensionConfig:
    value = raw.get("extension_id")
    if value is None:
        return BrowserExtensionConfig()
    if not isinstance(value, str) or not value.strip():
        raise BonsaiConfigError(
            "Config key browser_extension.extension_id must be a 32-character "
            "Chrome extension ID using lowercase a-p"
        )
    if len(value) != 32 or any(char < "a" or char > "p" for char in value):
        raise BonsaiConfigError(
            "Config key browser_extension.extension_id must be a 32-character "
            "Chrome extension ID using lowercase a-p"
        )
    return BrowserExtensionConfig(extension_id=value)


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
        start=_optional_str(raw, "start"),
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
        if service.port_env in RESERVED_SERVICE_PORT_ENV_NAMES:
            raise BonsaiConfigError(
                f"Service {service.name} port_env uses reserved environment name: "
                f"{service.port_env}"
            )
        if service.public and not service.url:
            raise BonsaiConfigError(f"Public service {service.name} requires a url")

    primary_count = sum(1 for service in config.public_services() if service.primary)
    if primary_count > 1:
        raise BonsaiConfigError("Multiple primary public services")
    if config.public_services() and primary_count == 0:
        raise BonsaiConfigError("Exactly one primary public service is required")

    for shared_file in config.shared_files:
        if shared_file.mode not in {"symlink", "copy"}:
            raise BonsaiConfigError(f"Unsupported shared file mode: {shared_file.mode}")

    for env in config.env:
        if env.name in RESERVED_USER_ENV_NAMES:
            raise BonsaiConfigError(f"Config env uses reserved environment name: {env.name}")
