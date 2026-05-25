from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from bonsai import __version__
from bonsai.config import load_config
from bonsai.errors import BonsaiConfigError, BonsaiError
from bonsai.git import current_branch
from bonsai.onboarding import (
    ProjectDefaults,
    StarterConfig,
    detect_project_defaults,
    write_starter_config,
)
from bonsai.process import SubprocessRunner
from bonsai.workflows import execute_add, execute_clone
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


def _optional_prompt(label: str, default: str | None) -> str | None:
    value = typer.prompt(label, default=default or "", show_default=bool(default)).strip()
    return value or None


def _prompt_starter_config(defaults: ProjectDefaults) -> StarterConfig:
    app_name = typer.prompt("App name", default=defaults.app_name).strip()
    base_branch = typer.prompt("Base branch", default=defaults.base_branch).strip()
    install_command = _optional_prompt("Install command", defaults.install_command)
    start_command = _optional_prompt("Start command", defaults.start_command)
    symlink_env = typer.confirm(
        "Symlink .env into each worktree",
        default=defaults.has_env_file,
    )
    service_name = typer.prompt("Primary service name", default=defaults.service_name).strip()
    port_env = typer.prompt("Port environment variable", default=defaults.port_env).strip()
    base_port = typer.prompt("Base port", default=defaults.base_port, type=int)
    url = typer.prompt("Local URL template", default=defaults.url).strip()
    return StarterConfig(
        name=app_name,
        base_branch=base_branch,
        install_command=install_command,
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
) -> Path:
    if config_path.exists() and not force:
        raise BonsaiConfigError(f".bonsai.toml already exists at {config_path}")
    defaults = detect_project_defaults(repo_path, fallback_name, base_branch)
    config = _prompt_starter_config(defaults)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    path = write_starter_config(config_path, config)
    load_config(path)
    return path


def _guided_config_initializer(
    config_path: Path,
    workspace_name: str,
    default_branch: str,
    default_worktree: Path,
) -> None:
    console.print(f"No .bonsai.toml found in {default_branch}.")
    console.print("Bonsai needs one config file to manage ports, env files, and local URLs.")
    console.print("Let's create it now.")
    path = write_guided_config(
        config_path=config_path,
        repo_path=default_worktree,
        fallback_name=workspace_name,
        base_branch=default_branch,
    )
    console.print(f"Created {path}")
    console.print("Review and commit this file so teammates can use Bonsai too.")


@app.command()
def clone(
    git_url: str,
    name: str,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive/--no-interactive",
            help="Create .bonsai.toml interactively when missing.",
        ),
    ] = True,
) -> None:
    try:
        config_initializer = _guided_config_initializer if interactive else None
        plan = execute_clone(
            SubprocessRunner(),
            git_url,
            name,
            Path.cwd(),
            config_initializer=config_initializer,
        )
        console.print(f"Created workspace: {plan.workspace_root}")
        console.print(f"Default worktree: {plan.default_worktree}")
    except BonsaiError as exc:
        _fail(exc)


@app.command("init")
def init_command(
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing .bonsai.toml."),
    ] = False,
) -> None:
    try:
        repo_path = Path.cwd()
        branch = current_branch(SubprocessRunner(), repo_path)
        path = write_guided_config(
            config_path=repo_path / ".bonsai.toml",
            repo_path=repo_path,
            fallback_name=repo_path.name,
            base_branch=branch,
            force=force,
        )
        console.print(f"Created {path}")
        console.print("Review and commit this file so teammates can use Bonsai too.")
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def add(branch: str) -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_add(SubprocessRunner(), branch, root_path)
        console.print(f"Prepared worktree: {plan.worktree_path}")
        console.print(f"Port slot: {plan.slot}")
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
def cleanup(
    apply: bool = typer.Option(False, "--apply", help="Remove eligible worktrees."),
) -> None:
    mode = "apply" if apply else "dry run"
    console.print(f"cleanup {mode}")


@app.command()
def doctor() -> None:
    console.print("doctor ready: macOS, Homebrew, Caddy, git, config, and port checks")


def main() -> None:
    app()
