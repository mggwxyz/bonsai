from __future__ import annotations

from pathlib import Path

from bonsai.models import CommandSpec
from bonsai.process import Runner
from bonsai.rendering import GENERATED_FILE_HEADER

BOOT_BLOCK_BEGIN = "# >>> bonsai managed (do not edit) >>>"
BOOT_BLOCK_END = "# <<< bonsai managed (do not edit) <<<"


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


def merge_boot_config(existing_text: str, snippets_glob: str) -> str:
    """Insert or refresh the marker-delimited managed import block.

    Empty input is owned outright (header + global block + managed block).
    Foreign content keeps everything outside the markers byte-for-byte.
    """
    managed = "\n".join([BOOT_BLOCK_BEGIN, f"import {snippets_glob}", BOOT_BLOCK_END])
    if existing_text.strip() == "":
        return "\n".join([GENERATED_FILE_HEADER, "{", "\tlocal_certs", "}", "", managed, ""])
    begin = existing_text.find(BOOT_BLOCK_BEGIN)
    if begin != -1:
        end = existing_text.find(BOOT_BLOCK_END, begin)
        if end != -1:
            return existing_text[:begin] + managed + existing_text[end + len(BOOT_BLOCK_END) :]
    prefix = existing_text if existing_text.endswith("\n") else existing_text + "\n"
    return f"{prefix}\n{managed}\n"


def caddy_boot_config_path(runner: Runner) -> Path | None:
    """Resolve Homebrew's boot-time Caddyfile, or None when `brew --prefix` fails.

    Deliberately does not scan the filesystem for a prefix: that would let tests
    (which stub the runner so `brew --prefix` returns empty) write into a real
    Homebrew tree. On any machine where Homebrew works, `brew --prefix` works.
    """
    result = runner.run(["brew", "--prefix"], check=False)
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()) / "etc" / "Caddyfile"
    return None
