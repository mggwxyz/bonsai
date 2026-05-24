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
