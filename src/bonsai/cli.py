from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from bonsai import __version__
from bonsai.errors import BonsaiError
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


@app.command()
def clone(git_url: str, name: str) -> None:
    try:
        plan = execute_clone(SubprocessRunner(), git_url, name, Path.cwd())
        console.print(f"Created workspace: {plan.workspace_root}")
        console.print(f"Default worktree: {plan.default_worktree}")
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
