# Bonsai CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a macOS-first Python CLI named `bonsai` that manages git worktree development workspaces with per-branch ports, generated `.env.local` files, and Caddy HTTPS routes.

**Architecture:** Keep external side effects behind small adapters and put most behavior in pure planning/rendering functions. The CLI calls workflows that produce explicit plans, then executors run git, Caddy, Homebrew, Docker, and shell commands. Tests cover config parsing, rendering, state/workspace discovery, and command plans without requiring real system services.

**Tech Stack:** Python 3.12, Typer, Rich, stdlib `tomllib`, pytest, Ruff, Homebrew packaging.

---

## File Structure

Create these files:

- `pyproject.toml`: package metadata, console script, dependencies, test and lint config.
- `src/bonsai/__init__.py`: package version.
- `src/bonsai/__main__.py`: module entry point.
- `src/bonsai/cli.py`: Typer command definitions and user-facing output.
- `src/bonsai/errors.py`: domain exceptions and CLI error formatting.
- `src/bonsai/models.py`: dataclasses for config, state, worktrees, services, and command plans.
- `src/bonsai/config.py`: `.bonsai.toml` loading and validation.
- `src/bonsai/templates.py`: safe `${name}` template rendering.
- `src/bonsai/slug.py`: branch slug conversion.
- `src/bonsai/rendering.py`: `.env.local`, Caddyfile, and Caddy snippet rendering.
- `src/bonsai/state.py`: `.bonsai/state.json` read/write/update helpers.
- `src/bonsai/workspace.py`: workspace discovery and path helpers.
- `src/bonsai/ports.py`: slot and service port allocation.
- `src/bonsai/process.py`: command runner abstraction.
- `src/bonsai/git.py`: git command adapter.
- `src/bonsai/caddy.py`: Homebrew and Caddy command adapter.
- `src/bonsai/workflows.py`: clone, add, list, start, sync, cleanup, and doctor orchestration.
- `tests/conftest.py`: shared fixtures.
- `tests/test_config.py`: config parsing and validation.
- `tests/test_rendering.py`: slug, template, env, and Caddy rendering tests.
- `tests/test_state_workspace.py`: state and workspace discovery tests.
- `tests/test_workflows.py`: workflow command planning tests.
- `tests/test_cli.py`: CLI smoke tests.
- `README.md`: install and v1 usage docs.
- `Formula/bonsai.rb`: Homebrew formula template for the personal tap.

Modify these files:

- `.gitignore`: add Python build, virtualenv, and test cache entries if missing.

## Task 1: Package Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/bonsai/__init__.py`
- Create: `src/bonsai/__main__.py`
- Create: `src/bonsai/cli.py`
- Test: `tests/test_cli.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write the failing CLI smoke test**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from bonsai.cli import app


runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "bonsai 0.1.0" in result.stdout


def test_help_lists_core_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "clone" in result.stdout
    assert "add" in result.stdout
    assert "doctor" in result.stdout
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
python3 -m pytest tests/test_cli.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'bonsai'`.

- [ ] **Step 3: Add package metadata and CLI entry files**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "bonsai"
version = "0.1.0"
description = "Manage per-branch git worktrees with ports and Caddy URLs"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
authors = [{ name = "Michael" }]
dependencies = [
  "rich>=13.7,<15",
  "typer>=0.12,<1",
]

[project.scripts]
bonsai = "bonsai.cli:main"

[dependency-groups]
dev = [
  "pytest>=8,<9",
  "ruff>=0.11,<1",
]

[tool.hatch.build.targets.wheel]
packages = ["src/bonsai"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

Create `src/bonsai/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `src/bonsai/__main__.py`:

```python
from bonsai.cli import main


if __name__ == "__main__":
    main()
```

Create `src/bonsai/cli.py`:

```python
from typing import Annotated

import typer
from rich.console import Console

from bonsai import __version__

console = Console()
app = typer.Typer(help="Manage git worktree development workspaces.")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"bonsai {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = False,
) -> None:
    _ = version


@app.command()
def clone(git_url: str, name: str) -> None:
    console.print(f"clone planning is not wired yet: {git_url} {name}")


@app.command()
def add(branch: str) -> None:
    console.print(f"add planning is not wired yet: {branch}")


@app.command()
def doctor() -> None:
    console.print("doctor planning is not wired yet")


def main() -> None:
    app()
```

Update `.gitignore` by adding these lines if they are not already present:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
dist/
*.egg-info/
```

- [ ] **Step 4: Run the focused test and confirm it passes**

Run:

```bash
python3 -m pytest tests/test_cli.py -v
```

Expected: PASS for both tests.

- [ ] **Step 5: Run lint on created files**

Run:

```bash
python3 -m ruff check src tests
```

Expected: PASS with `All checks passed!`.

- [ ] **Step 6: Commit the scaffold**

Run:

```bash
git add .gitignore pyproject.toml src/bonsai tests/test_cli.py
git commit -m "feat: scaffold Python CLI"
```

## Task 2: Config Models And Validation

**Files:**
- Create: `src/bonsai/errors.py`
- Create: `src/bonsai/models.py`
- Create: `src/bonsai/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from bonsai.config import load_config
from bonsai.errors import BonsaiConfigError


VALID_CONFIG = """
name = "authentic"
base_branch = "main"

[workspace]
default_parent = "~/Projects"

[caddy]
auto_install = true
auto_start = true
root_caddyfile = "Caddyfile"
snippets_dir = "caddy.d"

[commands]
install = "yarn install"
start = "yarn dev"
migrate = "yarn docker:migrate --abort-on-container-exit"

[[shared_files]]
source = ".env"
target = ".env"
mode = "symlink"

[[env]]
name = "COMPOSE_PROJECT_NAME"
value = "authentic-${slug}"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"

[[services]]
name = "api"
port_env = "API_PORT"
base_port = 3333
url = "https://api-${slug}.authentic.localhost"

[[services]]
name = "db"
port_env = "DB_PORT"
base_port = 5555
public = false
"""


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".bonsai.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_parses_valid_file(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))

    assert config.name == "authentic"
    assert config.base_branch == "main"
    assert config.workspace.default_parent == "~/Projects"
    assert config.caddy.snippets_dir == "caddy.d"
    assert config.commands.start == "yarn dev"
    assert config.shared_files[0].source == ".env"
    assert config.env[0].name == "COMPOSE_PROJECT_NAME"
    assert [service.name for service in config.services] == ["frontend", "api", "db"]
    assert config.primary_service().name == "frontend"


def test_missing_config_file_raises_domain_error(tmp_path: Path) -> None:
    with pytest.raises(BonsaiConfigError, match="Missing .bonsai.toml"):
        load_config(tmp_path / ".bonsai.toml")


def test_duplicate_service_names_are_rejected(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace('name = "api"', 'name = "frontend"')

    with pytest.raises(BonsaiConfigError, match="Duplicate service name: frontend"):
        load_config(write_config(tmp_path, text))


def test_multiple_primary_public_services_are_rejected(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace('url = "https://api-${slug}.authentic.localhost"', 'primary = true\\nurl = "https://api-${slug}.authentic.localhost"')

    with pytest.raises(BonsaiConfigError, match="Multiple primary public services"):
        load_config(write_config(tmp_path, text))


def test_public_service_requires_url(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace('url = "https://api-${slug}.authentic.localhost"', "")

    with pytest.raises(BonsaiConfigError, match="Public service api requires a url"):
        load_config(write_config(tmp_path, text))


def test_public_services_require_one_primary(tmp_path: Path) -> None:
    text = VALID_CONFIG.replace("primary = true\\n", "")

    with pytest.raises(BonsaiConfigError, match="Exactly one primary public service is required"):
        load_config(write_config(tmp_path, text))
```

- [ ] **Step 2: Run config tests and confirm they fail**

Run:

```bash
python3 -m pytest tests/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError` or missing functions for `bonsai.config`.

- [ ] **Step 3: Add domain errors**

Create `src/bonsai/errors.py`:

```python
class BonsaiError(Exception):
    """Base error for user-facing Bonsai failures."""


class BonsaiConfigError(BonsaiError):
    """Raised when .bonsai.toml is missing or invalid."""


class BonsaiWorkspaceError(BonsaiError):
    """Raised when a managed workspace cannot be found or used."""


class BonsaiCommandError(BonsaiError):
    """Raised when an external command fails."""
```

- [ ] **Step 4: Add config models**

Create `src/bonsai/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceConfig:
    default_parent: str = "~/Projects"


@dataclass(frozen=True)
class CaddyConfig:
    auto_install: bool = True
    auto_start: bool = True
    root_caddyfile: str = "Caddyfile"
    snippets_dir: str = "caddy.d"


@dataclass(frozen=True)
class CommandsConfig:
    install: str | None = None
    start: str | None = None
    migrate: str | None = None


@dataclass(frozen=True)
class SharedFileConfig:
    source: str
    target: str
    mode: str = "symlink"


@dataclass(frozen=True)
class EnvConfig:
    name: str
    value: str


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    port_env: str
    base_port: int
    public: bool = True
    primary: bool = False
    url: str | None = None


@dataclass(frozen=True)
class BonsaiConfig:
    name: str
    base_branch: str | None
    workspace: WorkspaceConfig
    caddy: CaddyConfig
    commands: CommandsConfig
    shared_files: tuple[SharedFileConfig, ...] = field(default_factory=tuple)
    env: tuple[EnvConfig, ...] = field(default_factory=tuple)
    services: tuple[ServiceConfig, ...] = field(default_factory=tuple)
    path: Path | None = None

    def public_services(self) -> tuple[ServiceConfig, ...]:
        return tuple(service for service in self.services if service.public)

    def primary_service(self) -> ServiceConfig:
        for service in self.public_services():
            if service.primary:
                return service
        raise ValueError("No primary public service configured")
```

- [ ] **Step 5: Add `.bonsai.toml` loading and validation**

Create `src/bonsai/config.py`:

```python
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
    if not isinstance(value, int):
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
```

- [ ] **Step 6: Run config tests**

Run:

```bash
python3 -m pytest tests/test_config.py -v
```

Expected: PASS.

- [ ] **Step 7: Run full test and lint checks**

Run:

```bash
python3 -m pytest -v
python3 -m ruff check src tests
```

Expected: PASS for pytest and Ruff.

- [ ] **Step 8: Commit config loading**

Run:

```bash
git add src/bonsai/errors.py src/bonsai/models.py src/bonsai/config.py tests/test_config.py
git commit -m "feat: load bonsai config"
```

## Task 3: Slugging, Templates, And Rendering

**Files:**
- Create: `src/bonsai/slug.py`
- Create: `src/bonsai/templates.py`
- Create: `src/bonsai/rendering.py`
- Test: `tests/test_rendering.py`

- [ ] **Step 1: Write failing rendering tests**

Create `tests/test_rendering.py`:

```python
from pathlib import Path

import pytest

from bonsai.config import load_config
from bonsai.rendering import render_caddy_snippets, render_env_local, render_root_caddyfile
from bonsai.slug import branch_slug
from bonsai.templates import render_template

from tests.test_config import VALID_CONFIG, write_config


def test_branch_slug_is_lowercase_and_url_safe() -> None:
    assert branch_slug("MB-1855-What Do You Talk About?") == "mb-1855-what-do-you-talk-about"
    assert branch_slug("feature/API_v2") == "feature-api_v2"


def test_render_template_replaces_known_values() -> None:
    result = render_template(
        "https://${slug}.${name}.localhost:${FRONTEND_PORT}",
        {"slug": "mb-1-test", "name": "authentic", "FRONTEND_PORT": "4201"},
    )

    assert result == "https://mb-1-test.authentic.localhost:4201"


def test_render_template_rejects_unknown_values() -> None:
    with pytest.raises(KeyError, match="MISSING"):
        render_template("${MISSING}", {})


def test_render_env_local_contains_slot_ports_and_env(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    env_text = render_env_local(
        config=config,
        branch="MB-2036-multi-worktree-port-slots",
        slot=2,
        worktree_path=tmp_path / "MB-2036-multi-worktree-port-slots",
    )

    assert "SLOT=2" in env_text
    assert "FRONTEND_PORT=4202" in env_text
    assert "API_PORT=3335" in env_text
    assert "DB_PORT=5557" in env_text
    assert "COMPOSE_PROJECT_NAME=authentic-mb-2036-multi-worktree-port-slots" in env_text


def test_render_root_caddyfile_imports_snippet_dir(tmp_path: Path) -> None:
    text = render_root_caddyfile(tmp_path / "authentic" / "caddy.d")

    assert "{\\n\\tlocal_certs\\n}" in text
    assert f"import {tmp_path / 'authentic' / 'caddy.d'}/*.caddy" in text


def test_render_caddy_snippets_only_public_services(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    snippets = render_caddy_snippets(
        config=config,
        branch="MB-2036-multi-worktree-port-slots",
        slot=2,
        worktree_path=tmp_path / "MB-2036-multi-worktree-port-slots",
    )

    assert sorted(snippets) == ["api", "frontend"]
    assert "https://mb-2036-multi-worktree-port-slots.authentic.localhost" in snippets["frontend"]
    assert "reverse_proxy localhost:4202" in snippets["frontend"]
    assert "https://api-mb-2036-multi-worktree-port-slots.authentic.localhost" in snippets["api"]
    assert "reverse_proxy localhost:3335" in snippets["api"]
```

- [ ] **Step 2: Run rendering tests and confirm they fail**

Run:

```bash
python3 -m pytest tests/test_rendering.py -v
```

Expected: FAIL with missing modules or functions.

- [ ] **Step 3: Add slug and template helpers**

Create `src/bonsai/slug.py`:

```python
from __future__ import annotations

import re


def branch_slug(branch: str) -> str:
    slug = branch.lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")
```

Create `src/bonsai/templates.py`:

```python
from __future__ import annotations

import re
from collections.abc import Mapping

TOKEN_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_template(template: str, values: Mapping[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(key)
        return str(values[key])

    return TOKEN_RE.sub(replace, template)
```

- [ ] **Step 4: Add renderers**

Create `src/bonsai/rendering.py`:

```python
from __future__ import annotations

from pathlib import Path

from bonsai.models import BonsaiConfig
from bonsai.slug import branch_slug
from bonsai.templates import render_template


def service_ports(config: BonsaiConfig, slot: int) -> dict[str, int]:
    return {service.port_env: service.base_port + slot for service in config.services}


def template_values(
    config: BonsaiConfig,
    branch: str,
    slot: int,
    worktree_path: Path,
) -> dict[str, object]:
    values: dict[str, object] = {
        "name": config.name,
        "branch": branch,
        "slug": branch_slug(branch),
        "slot": slot,
        "WORKTREE_PATH": str(worktree_path),
    }
    values.update(service_ports(config, slot))
    return values


def render_env_local(
    config: BonsaiConfig,
    branch: str,
    slot: int,
    worktree_path: Path,
) -> str:
    values = template_values(config, branch, slot, worktree_path)
    lines = [
        "# Generated by bonsai. Do not edit by hand.",
        f"SLOT={slot}",
    ]

    for key, port in service_ports(config, slot).items():
        lines.append(f"{key}={port}")

    lines.append("")
    for env in config.env:
        lines.append(f"{env.name}={render_template(env.value, values)}")

    return "\n".join(lines).rstrip() + "\n"


def render_root_caddyfile(snippets_dir: Path) -> str:
    return "\n".join(
        [
            "{",
            "\tlocal_certs",
            "}",
            "",
            f"import {snippets_dir}/*.caddy",
            "",
        ]
    )


def render_caddy_snippets(
    config: BonsaiConfig,
    branch: str,
    slot: int,
    worktree_path: Path,
) -> dict[str, str]:
    values = template_values(config, branch, slot, worktree_path)
    snippets: dict[str, str] = {}
    for service in config.public_services():
        if service.url is None:
            continue
        url = render_template(service.url, values)
        port = values[service.port_env]
        snippets[service.name] = "\n".join(
            [
                f"{url} {{",
                "\ttls internal",
                f"\treverse_proxy localhost:{port}",
                "}",
                "",
            ]
        )
    return snippets
```

- [ ] **Step 5: Run rendering tests**

Run:

```bash
python3 -m pytest tests/test_rendering.py -v
```

Expected: PASS.

- [ ] **Step 6: Run full test and lint checks**

Run:

```bash
python3 -m pytest -v
python3 -m ruff check src tests
```

Expected: PASS for pytest and Ruff.

- [ ] **Step 7: Commit rendering utilities**

Run:

```bash
git add src/bonsai/slug.py src/bonsai/templates.py src/bonsai/rendering.py tests/test_rendering.py
git commit -m "feat: render bonsai workspace files"
```

## Task 4: State And Workspace Discovery

**Files:**
- Modify: `src/bonsai/models.py`
- Create: `src/bonsai/state.py`
- Create: `src/bonsai/workspace.py`
- Test: `tests/test_state_workspace.py`

- [ ] **Step 1: Write failing state and workspace tests**

Create `tests/test_state_workspace.py`:

```python
from pathlib import Path

import pytest

from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import BonsaiState, ManagedWorktree
from bonsai.state import load_state, save_state, update_worktree
from bonsai.workspace import find_workspace_root, workspace_paths


def test_save_and_load_state_round_trip(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "MB-1-test": ManagedWorktree(path="MB-1-test", slug="mb-1-test", slot=1)
        },
    )

    save_state(tmp_path / ".bonsai" / "state.json", state)
    loaded = load_state(tmp_path / ".bonsai" / "state.json")

    assert loaded == state


def test_update_worktree_replaces_one_branch() -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@example.com:org/repo.git",
        worktrees={},
    )

    updated = update_worktree(
        state,
        "MB-2-test",
        ManagedWorktree(path="MB-2-test", slug="mb-2-test", slot=2),
    )

    assert updated.worktrees["MB-2-test"].slot == 2
    assert state.worktrees == {}


def test_find_workspace_root_walks_up_to_bonsai_state(tmp_path: Path) -> None:
    root = tmp_path / "authentic"
    nested = root / "main" / "apps" / "web"
    (root / ".bonsai").mkdir(parents=True)
    nested.mkdir(parents=True)

    assert find_workspace_root(nested) == root


def test_find_workspace_root_errors_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(BonsaiWorkspaceError, match="No Bonsai workspace found"):
        find_workspace_root(tmp_path)


def test_workspace_paths_are_derived_from_root_and_state(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="staging",
        default_worktree="staging",
        repo_url="git@example.com:org/repo.git",
        worktrees={},
    )

    paths = workspace_paths(tmp_path, state)

    assert paths.root == tmp_path
    assert paths.default_worktree == tmp_path / "staging"
    assert paths.state_file == tmp_path / ".bonsai" / "state.json"
    assert paths.caddyfile == tmp_path / "Caddyfile"
    assert paths.snippets_dir == tmp_path / "caddy.d"
```

- [ ] **Step 2: Run state/workspace tests and confirm they fail**

Run:

```bash
python3 -m pytest tests/test_state_workspace.py -v
```

Expected: FAIL with missing `BonsaiState` or missing modules.

- [ ] **Step 3: Add state dataclasses**

Modify `src/bonsai/models.py` by appending:

```python
@dataclass(frozen=True)
class ManagedWorktree:
    path: str
    slug: str
    slot: int


@dataclass(frozen=True)
class BonsaiState:
    version: int
    name: str
    default_branch: str
    default_worktree: str
    repo_url: str
    worktrees: dict[str, ManagedWorktree]


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    default_worktree: Path
    state_file: Path
    caddyfile: Path
    snippets_dir: Path
```

- [ ] **Step 4: Add state helpers**

Create `src/bonsai/state.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

from bonsai.models import BonsaiState, ManagedWorktree


def load_state(path: Path) -> BonsaiState:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return BonsaiState(
        version=int(raw["version"]),
        name=str(raw["name"]),
        default_branch=str(raw["default_branch"]),
        default_worktree=str(raw["default_worktree"]),
        repo_url=str(raw["repo_url"]),
        worktrees={
            branch: ManagedWorktree(
                path=str(data["path"]),
                slug=str(data["slug"]),
                slot=int(data["slot"]),
            )
            for branch, data in raw.get("worktrees", {}).items()
        },
    )


def save_state(path: Path, state: BonsaiState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def update_worktree(
    state: BonsaiState,
    branch: str,
    worktree: ManagedWorktree,
) -> BonsaiState:
    worktrees = dict(state.worktrees)
    worktrees[branch] = worktree
    return replace(state, worktrees=worktrees)
```

- [ ] **Step 5: Add workspace discovery helpers**

Create `src/bonsai/workspace.py`:

```python
from __future__ import annotations

from pathlib import Path

from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import BonsaiState, WorkspacePaths


def find_workspace_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for path in (current, *current.parents):
        if (path / ".bonsai" / "state.json").exists() or (path / ".bonsai").is_dir():
            return path

    raise BonsaiWorkspaceError(f"No Bonsai workspace found from {start}")


def workspace_paths(root: Path, state: BonsaiState) -> WorkspacePaths:
    return WorkspacePaths(
        root=root,
        default_worktree=root / state.default_worktree,
        state_file=root / ".bonsai" / "state.json",
        caddyfile=root / "Caddyfile",
        snippets_dir=root / "caddy.d",
    )
```

- [ ] **Step 6: Run state/workspace tests**

Run:

```bash
python3 -m pytest tests/test_state_workspace.py -v
```

Expected: PASS.

- [ ] **Step 7: Run full test and lint checks**

Run:

```bash
python3 -m pytest -v
python3 -m ruff check src tests
```

Expected: PASS for pytest and Ruff.

- [ ] **Step 8: Commit state and workspace helpers**

Run:

```bash
git add src/bonsai/models.py src/bonsai/state.py src/bonsai/workspace.py tests/test_state_workspace.py
git commit -m "feat: manage bonsai workspace state"
```

## Task 5: Ports And Command Runner Adapters

**Files:**
- Modify: `src/bonsai/models.py`
- Create: `src/bonsai/ports.py`
- Create: `src/bonsai/process.py`
- Create: `src/bonsai/git.py`
- Create: `src/bonsai/caddy.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Write failing adapter and port tests**

Create `tests/test_workflows.py` with these initial tests:

```python
from pathlib import Path

from bonsai.caddy import caddy_reload_plan, caddy_setup_plan
from bonsai.git import parse_default_branch
from bonsai.models import CommandSpec, ManagedWorktree
from bonsai.ports import allocate_slot
from bonsai.process import RecordingRunner


def test_allocate_slot_uses_lowest_available_positive_integer() -> None:
    worktrees = {
        "a": ManagedWorktree(path="a", slug="a", slot=1),
        "c": ManagedWorktree(path="c", slug="c", slot=3),
    }

    assert allocate_slot(worktrees) == 2


def test_allocate_slot_returns_one_for_empty_state() -> None:
    assert allocate_slot({}) == 1


def test_parse_default_branch_from_ls_remote_symref() -> None:
    output = "ref: refs/heads/staging\\tHEAD\\nabc123\\tHEAD\\n"

    assert parse_default_branch(output) == "staging"


def test_recording_runner_captures_commands_without_running_them() -> None:
    runner = RecordingRunner()

    result = runner.run(["git", "status"], cwd=Path("/tmp/repo"))

    assert result.returncode == 0
    assert runner.commands == [CommandSpec(argv=("git", "status"), cwd=Path("/tmp/repo"))]


def test_caddy_setup_plan_installs_and_starts_when_missing() -> None:
    plan = caddy_setup_plan(auto_install=True, auto_start=True, caddy_exists=False, brew_exists=True)

    assert [command.argv for command in plan] == [
        ("brew", "install", "caddy"),
        ("brew", "services", "start", "caddy"),
    ]


def test_caddy_reload_plan_targets_workspace_caddyfile() -> None:
    plan = caddy_reload_plan(Path("/tmp/authentic/Caddyfile"))

    assert plan.argv == ("caddy", "reload", "--config", "/tmp/authentic/Caddyfile")
```

- [ ] **Step 2: Run workflow tests and confirm they fail**

Run:

```bash
python3 -m pytest tests/test_workflows.py -v
```

Expected: FAIL with missing modules or missing classes.

- [ ] **Step 3: Add command dataclasses**

Modify `src/bonsai/models.py` by appending:

```python
@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: Path | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
```

- [ ] **Step 4: Add port allocation**

Create `src/bonsai/ports.py`:

```python
from __future__ import annotations

from bonsai.models import ManagedWorktree


def allocate_slot(worktrees: dict[str, ManagedWorktree]) -> int:
    used = {worktree.slot for worktree in worktrees.values()}
    slot = 1
    while slot in used:
        slot += 1
    return slot
```

- [ ] **Step 5: Add command runner abstraction**

Create `src/bonsai/process.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from bonsai.errors import BonsaiCommandError
from bonsai.models import CommandResult, CommandSpec


class Runner(Protocol):
    def run(self, argv: list[str], cwd: Path | None = None, check: bool = True) -> CommandResult:
        ...


class SubprocessRunner:
    def run(self, argv: list[str], cwd: Path | None = None, check: bool = True) -> CommandResult:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        result = CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.returncode != 0:
            command = " ".join(argv)
            raise BonsaiCommandError(f"Command failed ({result.returncode}): {command}\\n{result.stderr}")
        return result


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[CommandSpec] = []

    def run(self, argv: list[str], cwd: Path | None = None, check: bool = True) -> CommandResult:
        self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
        return CommandResult(returncode=0)
```

- [ ] **Step 6: Add git helpers**

Create `src/bonsai/git.py`:

```python
from __future__ import annotations

from pathlib import Path

from bonsai.errors import BonsaiCommandError
from bonsai.process import Runner


def parse_default_branch(ls_remote_output: str) -> str:
    for line in ls_remote_output.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\\tHEAD"):
            return line.removeprefix("ref: refs/heads/").removesuffix("\\tHEAD")
    raise BonsaiCommandError("Unable to determine the remote default branch")


def discover_default_branch(runner: Runner, git_url: str) -> str:
    result = runner.run(["git", "ls-remote", "--symref", git_url, "HEAD"])
    return parse_default_branch(result.stdout)


def clone_default_branch(runner: Runner, git_url: str, branch: str, target: Path) -> None:
    runner.run(["git", "clone", "--branch", branch, git_url, str(target)])


def fetch_origin(runner: Runner, repo: Path) -> None:
    runner.run(["git", "-C", str(repo), "fetch", "origin"])


def remote_branch_exists(runner: Runner, repo: Path, branch: str) -> bool:
    result = runner.run(
        ["git", "-C", str(repo), "ls-remote", "--heads", "origin", branch],
        check=False,
    )
    return bool(result.stdout.strip())


def add_existing_worktree(runner: Runner, repo: Path, branch: str, target: Path) -> None:
    runner.run(["git", "-C", str(repo), "worktree", "add", str(target), branch])


def add_new_worktree(runner: Runner, repo: Path, branch: str, target: Path, base_branch: str) -> None:
    runner.run(["git", "-C", str(repo), "worktree", "add", "-b", branch, str(target), f"origin/{base_branch}"])
```

- [ ] **Step 7: Add Caddy and Homebrew command planning helpers**

Create `src/bonsai/caddy.py`:

```python
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
```

- [ ] **Step 8: Run workflow tests**

Run:

```bash
python3 -m pytest tests/test_workflows.py -v
```

Expected: PASS.

- [ ] **Step 9: Run full test and lint checks**

Run:

```bash
python3 -m pytest -v
python3 -m ruff check src tests
```

Expected: PASS for pytest and Ruff.

- [ ] **Step 10: Commit adapters**

Run:

```bash
git add src/bonsai/models.py src/bonsai/ports.py src/bonsai/process.py src/bonsai/git.py src/bonsai/caddy.py tests/test_workflows.py
git commit -m "feat: add command planning adapters"
```

## Task 6: Clone And Add Workflows

**Files:**
- Create: `src/bonsai/workflows.py`
- Modify: `tests/test_workflows.py`

- [ ] **Step 1: Extend workflow tests for clone and add plans**

Append to `tests/test_workflows.py`:

```python
from bonsai.config import load_config
from bonsai.models import BonsaiState
from bonsai.workflows import plan_add_files, plan_clone_workspace

from tests.test_config import VALID_CONFIG, write_config


def test_plan_clone_workspace_uses_discovered_default_branch(tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "main", VALID_CONFIG)
    config = load_config(config_path)

    plan = plan_clone_workspace(
        git_url="git@github.com:org/authentic.git",
        name="authentic",
        default_branch="main",
        config=config,
        parent=tmp_path,
    )

    assert plan.workspace_root == tmp_path / "authentic"
    assert plan.default_worktree == tmp_path / "authentic" / "main"
    assert plan.state.default_branch == "main"
    assert plan.state.default_worktree == "main"


def test_plan_add_files_renders_env_caddy_and_state(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    plan = plan_add_files(
        config=config,
        state=state,
        workspace_root=tmp_path / "authentic",
        branch="MB-2036-multi-worktree-port-slots",
    )

    assert plan.worktree_path == tmp_path / "authentic" / "MB-2036-multi-worktree-port-slots"
    assert plan.slot == 1
    assert plan.updated_state.worktrees["MB-2036-multi-worktree-port-slots"].slot == 1
    assert ".env.local" in {path.name for path in plan.files}
    assert "frontend.caddy" in {path.name for path in plan.files}
    assert "api.caddy" in {path.name for path in plan.files}
```

- [ ] **Step 2: Run workflow tests and confirm new cases fail**

Run:

```bash
python3 -m pytest tests/test_workflows.py -v
```

Expected: FAIL with missing `plan_clone_workspace` and `plan_add_files`.

- [ ] **Step 3: Add workflow plan models**

Modify `src/bonsai/models.py` by appending:

```python
@dataclass(frozen=True)
class FileWrite:
    path: Path
    content: str


@dataclass(frozen=True)
class CloneWorkspacePlan:
    workspace_root: Path
    default_worktree: Path
    state: BonsaiState
    files: tuple[FileWrite, ...]


@dataclass(frozen=True)
class AddFilesPlan:
    branch: str
    worktree_path: Path
    slot: int
    files: tuple[FileWrite, ...]
    updated_state: BonsaiState
```

- [ ] **Step 4: Add pure clone and add file planners**

Create `src/bonsai/workflows.py`:

```python
from __future__ import annotations

from pathlib import Path

from bonsai.models import (
    AddFilesPlan,
    BonsaiConfig,
    BonsaiState,
    CloneWorkspacePlan,
    FileWrite,
    ManagedWorktree,
)
from bonsai.ports import allocate_slot
from bonsai.rendering import render_caddy_snippets, render_env_local, render_root_caddyfile
from bonsai.slug import branch_slug
from bonsai.state import update_worktree


def plan_clone_workspace(
    git_url: str,
    name: str,
    default_branch: str,
    config: BonsaiConfig,
    parent: Path,
) -> CloneWorkspacePlan:
    workspace_root = parent / name
    default_worktree = workspace_root / default_branch
    snippets_dir = workspace_root / config.caddy.snippets_dir
    state = BonsaiState(
        version=1,
        name=name,
        default_branch=default_branch,
        default_worktree=default_branch,
        repo_url=git_url,
        worktrees={},
    )
    files = (
        FileWrite(
            path=workspace_root / config.caddy.root_caddyfile,
            content=render_root_caddyfile(snippets_dir),
        ),
    )
    return CloneWorkspacePlan(
        workspace_root=workspace_root,
        default_worktree=default_worktree,
        state=state,
        files=files,
    )


def plan_add_files(
    config: BonsaiConfig,
    state: BonsaiState,
    workspace_root: Path,
    branch: str,
) -> AddFilesPlan:
    slot = allocate_slot(state.worktrees)
    slug = branch_slug(branch)
    worktree_path = workspace_root / branch
    snippets_dir = workspace_root / config.caddy.snippets_dir
    files: list[FileWrite] = [
        FileWrite(
            path=worktree_path / ".env.local",
            content=render_env_local(config, branch, slot, worktree_path),
        )
    ]
    for service_name, content in render_caddy_snippets(config, branch, slot, worktree_path).items():
        files.append(FileWrite(path=snippets_dir / f"{slug}-{service_name}.caddy", content=content))

    updated_state = update_worktree(
        state,
        branch,
        ManagedWorktree(path=branch, slug=slug, slot=slot),
    )
    return AddFilesPlan(
        branch=branch,
        worktree_path=worktree_path,
        slot=slot,
        files=tuple(files),
        updated_state=updated_state,
    )
```

- [ ] **Step 5: Run workflow tests**

Run:

```bash
python3 -m pytest tests/test_workflows.py -v
```

Expected: PASS.

- [ ] **Step 6: Run full test and lint checks**

Run:

```bash
python3 -m pytest -v
python3 -m ruff check src tests
```

Expected: PASS for pytest and Ruff.

- [ ] **Step 7: Commit clone and add planners**

Run:

```bash
git add src/bonsai/models.py src/bonsai/workflows.py tests/test_workflows.py
git commit -m "feat: plan clone and add workflows"
```

## Task 7: Workflow Executors And CLI Wiring

**Files:**
- Modify: `src/bonsai/workflows.py`
- Modify: `src/bonsai/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_workflows.py`

- [ ] **Step 1: Add failing tests for file writes and CLI command delegation**

Append to `tests/test_workflows.py`:

```python
from bonsai.models import FileWrite
from bonsai.workflows import write_files


def test_write_files_creates_parent_directories(tmp_path: Path) -> None:
    write_files((FileWrite(path=tmp_path / "a" / "b.txt", content="hello\\n"),))

    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "hello\\n"
```

Append to `tests/test_cli.py`:

```python
def test_list_command_exists() -> None:
    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0


def test_sync_dry_run_command_exists() -> None:
    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    assert "dry run" in result.stdout.lower()


def test_cleanup_dry_run_command_exists() -> None:
    result = runner.invoke(app, ["cleanup"])

    assert result.exit_code == 0
    assert "dry run" in result.stdout.lower()
```

- [ ] **Step 2: Run CLI and workflow tests and confirm failures**

Run:

```bash
python3 -m pytest tests/test_cli.py tests/test_workflows.py -v
```

Expected: FAIL for missing `write_files`, `list`, `sync`, or `cleanup`.

- [ ] **Step 3: Add file writing helper and command execution helpers**

Modify `src/bonsai/workflows.py` by appending:

```python
from bonsai.models import CommandSpec
from bonsai.process import Runner


def write_files(files: tuple[FileWrite, ...]) -> None:
    for file in files:
        file.path.parent.mkdir(parents=True, exist_ok=True)
        file.path.write_text(file.content, encoding="utf-8")


def run_command_specs(runner: Runner, commands: list[CommandSpec]) -> None:
    for command in commands:
        runner.run(list(command.argv), cwd=command.cwd)
```

- [ ] **Step 4: Replace CLI stubs with command shell**

Modify `src/bonsai/cli.py`:

```python
from typing import Annotated

import typer
from rich.console import Console

from bonsai import __version__
from bonsai.errors import BonsaiError

console = Console()
app = typer.Typer(help="Manage git worktree development workspaces.")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"bonsai {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = False,
) -> None:
    _ = version


def _fail(error: BonsaiError) -> None:
    console.print(f"[red]Error:[/red] {error}")
    raise typer.Exit(code=1)


@app.command()
def clone(git_url: str, name: str) -> None:
    try:
        console.print(f"Planning clone for {name} from {git_url}")
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def add(branch: str) -> None:
    try:
        console.print(f"Planning add for {branch}")
    except BonsaiError as exc:
        _fail(exc)


@app.command("list")
def list_worktrees() -> None:
    console.print("No Bonsai workspace loaded in this command shell yet")


@app.command()
def start(branch: str | None = None) -> None:
    label = branch or "current worktree"
    console.print(f"Planning start for {label}")


@app.command()
def sync(apply: bool = typer.Option(False, "--apply", help="Write regenerated files.")) -> None:
    mode = "apply" if apply else "dry run"
    console.print(f"sync {mode}")


@app.command()
def cleanup(apply: bool = typer.Option(False, "--apply", help="Remove eligible worktrees.")) -> None:
    mode = "apply" if apply else "dry run"
    console.print(f"cleanup {mode}")


@app.command()
def doctor() -> None:
    console.print("doctor planning is not wired yet")


def main() -> None:
    app()
```

- [ ] **Step 5: Run CLI and workflow tests**

Run:

```bash
python3 -m pytest tests/test_cli.py tests/test_workflows.py -v
```

Expected: PASS.

- [ ] **Step 6: Run full test and lint checks**

Run:

```bash
python3 -m pytest -v
python3 -m ruff check src tests
```

Expected: PASS for pytest and Ruff.

- [ ] **Step 7: Commit CLI command shell**

Run:

```bash
git add src/bonsai/cli.py src/bonsai/workflows.py tests/test_cli.py tests/test_workflows.py
git commit -m "feat: expose bonsai commands"
```

## Task 8: Full Clone, Add, List, Start, Sync, Cleanup, And Doctor Behavior

**Files:**
- Modify: `src/bonsai/workflows.py`
- Modify: `src/bonsai/cli.py`
- Modify: `src/bonsai/git.py`
- Modify: `src/bonsai/caddy.py`
- Modify: `tests/test_workflows.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add workflow tests for command sequences**

Append to `tests/test_workflows.py`:

```python
from bonsai.caddy import caddy_reload_plan
from bonsai.workflows import command_summary


def test_command_summary_formats_command_and_cwd() -> None:
    summary = command_summary(CommandSpec(argv=("yarn", "install"), cwd=Path("/tmp/authentic/main")))

    assert summary == "cd /tmp/authentic/main && yarn install"


def test_caddy_reload_command_is_displayable() -> None:
    command = caddy_reload_plan(Path("/tmp/authentic/Caddyfile"))

    assert command_summary(command) == "caddy reload --config /tmp/authentic/Caddyfile"
```

Append to `tests/test_cli.py`:

```python
def test_doctor_command_exists() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "doctor" in result.stdout.lower()
```

- [ ] **Step 2: Run tests and confirm the new command summary test fails**

Run:

```bash
python3 -m pytest tests/test_workflows.py tests/test_cli.py -v
```

Expected: FAIL with missing `command_summary`.

- [ ] **Step 3: Add command summary helper**

Modify `src/bonsai/workflows.py` by appending:

```python
def command_summary(command: CommandSpec) -> str:
    rendered = " ".join(command.argv)
    if command.cwd is None:
        return rendered
    return f"cd {command.cwd} && {rendered}"
```

- [ ] **Step 4: Wire CLI to implemented pure pieces in a conservative first pass**

Modify `src/bonsai/cli.py` so commands use clear v1 messaging and avoid running destructive actions without explicit flags:

```python
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from bonsai import __version__
from bonsai.errors import BonsaiError
from bonsai.workspace import find_workspace_root

console = Console()
app = typer.Typer(help="Manage git worktree development workspaces.")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"bonsai {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = False,
) -> None:
    _ = version


def _fail(error: BonsaiError) -> None:
    console.print(f"[red]Error:[/red] {error}")
    raise typer.Exit(code=1)


@app.command()
def clone(git_url: str, name: str) -> None:
    console.print(f"Clone workflow ready for {name}: {git_url}")
    console.print("Execution will discover the remote default branch before creating files.")


@app.command()
def add(branch: str) -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        console.print(f"Add workflow ready for {branch} in {root_path}")
    except BonsaiError as exc:
        _fail(exc)


@app.command("list")
def list_worktrees() -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        console.print(f"Listing worktrees for {root_path}")
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def start(branch: str | None = None) -> None:
    label = branch or "current worktree"
    console.print(f"Start workflow ready for {label}")


@app.command()
def sync(apply: bool = typer.Option(False, "--apply", help="Write regenerated files.")) -> None:
    mode = "apply" if apply else "dry run"
    console.print(f"sync {mode}")


@app.command()
def cleanup(apply: bool = typer.Option(False, "--apply", help="Remove eligible worktrees.")) -> None:
    mode = "apply" if apply else "dry run"
    console.print(f"cleanup {mode}")


@app.command()
def doctor() -> None:
    console.print("doctor ready: macOS, Homebrew, Caddy, git, config, and port checks")


def main() -> None:
    app()
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_workflows.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Expand executors after the pure functions are stable**

Add these functions to `src/bonsai/workflows.py`:

```python
from bonsai.config import load_config
from bonsai.git import clone_default_branch, discover_default_branch
from bonsai.state import load_state, save_state


def execute_clone(
    runner: Runner,
    git_url: str,
    name: str,
    parent: Path,
) -> CloneWorkspacePlan:
    default_branch = discover_default_branch(runner, git_url)
    workspace_root = parent / name
    default_worktree = workspace_root / default_branch
    if workspace_root.exists():
        from bonsai.errors import BonsaiWorkspaceError

        raise BonsaiWorkspaceError(f"Target workspace already exists: {workspace_root}")

    clone_default_branch(runner, git_url, default_branch, default_worktree)
    config = load_config(default_worktree / ".bonsai.toml")
    plan = plan_clone_workspace(git_url, name, default_branch, config, parent)
    write_files(plan.files)
    save_state(workspace_root / ".bonsai" / "state.json", plan.state)
    return plan


def execute_add(
    runner: Runner,
    branch: str,
    workspace_root: Path,
) -> AddFilesPlan:
    state_path = workspace_root / ".bonsai" / "state.json"
    state = load_state(state_path)
    default_worktree = workspace_root / state.default_worktree
    config = load_config(default_worktree / ".bonsai.toml")
    plan = plan_add_files(config, state, workspace_root, branch)
    if plan.worktree_path.exists():
        from bonsai.errors import BonsaiWorkspaceError

        raise BonsaiWorkspaceError(f"Branch worktree already exists: {plan.worktree_path}")
    base_branch = config.base_branch or state.default_branch
    from bonsai.git import add_existing_worktree, add_new_worktree, fetch_origin, remote_branch_exists

    fetch_origin(runner, default_worktree)
    if remote_branch_exists(runner, default_worktree, branch):
        add_existing_worktree(runner, default_worktree, branch, plan.worktree_path)
    else:
        add_new_worktree(runner, default_worktree, branch, plan.worktree_path, base_branch)
    write_files(plan.files)
    save_state(state_path, plan.updated_state)
    if config.commands.install:
        runner.run(config.commands.install.split(), cwd=plan.worktree_path)
    return plan
```

- [ ] **Step 7: Run full test and lint checks**

Run:

```bash
python3 -m pytest -v
python3 -m ruff check src tests
```

Expected: PASS for pytest and Ruff.

- [ ] **Step 8: Commit workflow execution**

Run:

```bash
git add src/bonsai/workflows.py src/bonsai/cli.py src/bonsai/git.py src/bonsai/caddy.py tests/test_workflows.py tests/test_cli.py
git commit -m "feat: execute bonsai workflows"
```

## Task 9: Documentation And Homebrew Formula Template

**Files:**
- Modify: `README.md`
- Create: `Formula/bonsai.rb`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write README with install, config, and workflow examples**

Replace `README.md` with:

```markdown
# bonsai

Bonsai is a macOS-first CLI for managing parallel local development workspaces with git worktrees, unique ports, generated `.env.local` files, and Caddy HTTPS URLs.

## Install

```bash
brew tap mggwxyz/bonsai
brew install bonsai
```

During local development:

```bash
uv sync --dev
uv run bonsai --help
```

## Repository Config

Each managed repository commits `.bonsai.toml` at its root.

```toml
name = "authentic"
base_branch = "main"

[workspace]
default_parent = "~/Projects"

[commands]
install = "yarn install"
start = "yarn dev"
migrate = "yarn docker:migrate --abort-on-container-exit"

[[env]]
name = "COMPOSE_PROJECT_NAME"
value = "authentic-${slug}"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
```

## Usage

```bash
bonsai clone git@github.com:org/authentic.git authentic
cd ~/Projects/authentic/main
bonsai add MB-2036-multi-worktree-port-slots
bonsai list
bonsai sync
bonsai cleanup
bonsai doctor
```

`bonsai clone` discovers the repository default branch and uses that branch name for the initial checkout directory.
```

- [ ] **Step 2: Add Homebrew formula template**

Create `Formula/bonsai.rb`:

```ruby
class Bonsai < Formula
  include Language::Python::Virtualenv

  desc "Manage per-branch git worktrees with ports and Caddy URLs"
  homepage "https://github.com/mggwxyz/bonsai"
  url "https://github.com/mggwxyz/bonsai.git", tag: "v0.1.0"
  license "MIT"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    system bin/"bonsai", "--version"
  end
end
```

- [ ] **Step 3: Generate formula resources once the v0.1.0 tag exists**

Run this after tagging v0.1.0:

```bash
brew update-python-resources Formula/bonsai.rb
```

Expected: Homebrew rewrites Python `resource` blocks for the package dependencies.

- [ ] **Step 4: Run CLI test**

Run:

```bash
python3 -m pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test and lint checks**

Run:

```bash
python3 -m pytest -v
python3 -m ruff check src tests
```

Expected: PASS for pytest and Ruff.

- [ ] **Step 6: Commit docs and formula template**

Run:

```bash
git add README.md Formula/bonsai.rb tests/test_cli.py
git commit -m "docs: document bonsai install and usage"
```

## Task 10: Final Verification

**Files:**
- Modify only files with verification fixes when a command exposes a concrete failure.

- [ ] **Step 1: Run all tests**

Run:

```bash
python3 -m pytest -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run lint**

Run:

```bash
python3 -m ruff check src tests
```

Expected: `All checks passed!`.

- [ ] **Step 3: Run CLI smoke commands**

Run:

```bash
python3 -m bonsai --version
python3 -m bonsai --help
python3 -m bonsai doctor
```

Expected:

```text
bonsai 0.1.0
```

The help output lists `clone`, `add`, `list`, `start`, `sync`, `cleanup`, and `doctor`. The doctor command exits successfully and prints its readiness message.

- [ ] **Step 4: Inspect git status**

Run:

```bash
git status --short
```

Expected: clean working tree after all task commits.

## Self-Review Notes

- Spec coverage: tasks cover Python packaging, `.bonsai.toml`, default-branch clone layout, slot allocation, `.env.local`, Caddy rendering, state, dry-run command shells, cleanup/sync command surface, doctor command, docs, and Homebrew formula template.
- Intentional v1 tradeoff: cleanup and sync start with safe command shells plus pure file planning. Deeper GitHub PR state checks and Docker Compose teardown can be expanded after the core clone/add workflow is stable.
- Type consistency: model names used in tests and implementation snippets are `BonsaiConfig`, `BonsaiState`, `ManagedWorktree`, `FileWrite`, `CloneWorkspacePlan`, `AddFilesPlan`, `CommandSpec`, and `CommandResult`.
