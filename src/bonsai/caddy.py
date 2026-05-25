from __future__ import annotations

from pathlib import Path

from bonsai.models import CommandSpec


def caddy_setup_plan(
    auto_install: bool,
    auto_start: bool,
    caddy_exists: bool,
    brew_exists: bool,
) -> list[CommandSpec]:
    commands: list[CommandSpec] = []
    if auto_install and not caddy_exists and brew_exists:
        commands.append(CommandSpec(argv=("brew", "install", "caddy")))
        caddy_exists = True
    if auto_start and caddy_exists:
        commands.append(CommandSpec(argv=("brew", "services", "start", "caddy")))
    return commands


def caddy_reload_plan(caddyfile: Path) -> CommandSpec:
    return CommandSpec(argv=("caddy", "reload", "--config", str(caddyfile)))
