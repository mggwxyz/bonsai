import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from bonsai import __version__
from bonsai.agent import render_agent_context, render_agent_guide
from bonsai.config import load_config
from bonsai.errors import BonsaiConfigError, BonsaiError, BonsaiWorkspaceError
from bonsai.git import current_branch
from bonsai.onboarding import (
    ProjectDefaults,
    StarterConfig,
    detect_project_defaults,
    write_starter_config,
)
from bonsai.process import SubprocessRunner
from bonsai.state import load_state
from bonsai.status import render_workspace_list, render_workspace_status
from bonsai.workflows import (
    check_workspace_health,
    execute_add,
    execute_checkout,
    execute_cleanup,
    execute_clone,
    execute_remove,
    execute_repair,
    execute_start,
    execute_sync,
    plan_agent_context,
    plan_current_worktree_status,
    plan_open_url,
    plan_workspace_summary,
    workspace_config_path,
)
from bonsai.workspace import find_workspace_root

console = Console(width=200)
app = typer.Typer(help="Manage git worktree development workspaces.")

ZSH_SHELL_INIT = """bonsai() {
  local bonsai_bin="${commands[bonsai]}"
  if [[ -z "$bonsai_bin" ]]; then
    printf "%s\\n" "bonsai executable not found in PATH" >&2
    return 127
  fi

  if [[ "$1" == "checkout" ]]; then
    shift
    local checkout_path
    local bonsai_exit
    checkout_path="$("$bonsai_bin" checkout --path "$@")"
    bonsai_exit=$?
    if [[ $bonsai_exit -ne 0 ]]; then
      printf "%s\\n" "$checkout_path" >&2
      return $bonsai_exit
    fi
    cd "$checkout_path"
  else
    "$bonsai_bin" "$@"
  fi
}
"""
SHELL_INTEGRATION_START = "# >>> bonsai shell integration >>>"
SHELL_INTEGRATION_END = "# <<< bonsai shell integration <<<"
ZSH_INTEGRATION_BLOCK = (
    f"{SHELL_INTEGRATION_START}\n"
    'eval "$(bonsai shell-init zsh)"\n'
    f"{SHELL_INTEGRATION_END}\n"
)


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
    setup_command = _optional_prompt("Setup command", defaults.setup_command)
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
        setup_command=setup_command,
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
    console.print(f"No Bonsai config found for {default_branch}.")
    console.print("Bonsai needs one config file to manage ports, env files, and local URLs.")
    console.print("Let's create a local workspace config now.")
    path = write_guided_config(
        config_path=config_path,
        repo_path=default_worktree,
        fallback_name=workspace_name,
        base_branch=default_branch,
    )
    console.print(f"Created {path}")
    console.print("Move or copy it into the repo if teammates should share it.")


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
    """Clone a repository into a new Bonsai workspace."""
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


@app.command("agent-guide")
def agent_guide(
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Print package-level guidance for AI agents and automation."""
    try:
        typer.echo(render_agent_guide(output_format), nl=False)
    except BonsaiError as exc:
        _fail(exc)


@app.command("init")
def init_command(
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing .bonsai.toml."),
    ] = False,
) -> None:
    """Create a starter .bonsai.toml for the current checkout or workspace."""
    try:
        current_path = Path.cwd()
        repo_path = current_path
        fallback_name = current_path.name
        config_path = current_path / ".bonsai.toml"
        try:
            workspace_root = find_workspace_root(current_path)
        except BonsaiWorkspaceError:
            pass
        else:
            state_path = workspace_root / ".bonsai" / "state.json"
            if state_path.exists():
                state = load_state(state_path)
                config_path = workspace_config_path(workspace_root)
                fallback_name = workspace_root.name
                if current_path == workspace_root:
                    repo_path = workspace_root / state.default_worktree
        branch = current_branch(SubprocessRunner(), repo_path)
        path = write_guided_config(
            config_path=config_path,
            repo_path=repo_path,
            fallback_name=fallback_name,
            base_branch=branch,
            force=force,
        )
        console.print(f"Created {path}")
        console.print("Move or copy it into the repo if teammates should share it.")
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def add(branch: str) -> None:
    """Prepare a managed worktree for a branch."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_add(SubprocessRunner(), branch, root_path)
        console.print(f"Prepared worktree: {plan.worktree_path}")
        console.print(f"Port slot: {plan.slot}")
    except BonsaiError as exc:
        _fail(exc)


@app.command("remove")
def remove_command(
    name: str,
    force: Annotated[
        bool,
        typer.Option("--force", help="Remove a worktree with uncommitted changes."),
    ] = False,
) -> None:
    """Remove a managed worktree."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_remove(SubprocessRunner(), name, root_path, force=force)
        if getattr(plan, "compose_project_name", None) is not None:
            console.print(f"compose down {plan.compose_project_name}")
        console.print(f"Removed worktree: {plan.worktree_path}")
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def checkout(
    name: str,
    path: Annotated[
        bool,
        typer.Option("--path", help="Print the resolved worktree path for shell integration."),
    ] = False,
) -> None:
    """Resolve or prepare a worktree for shell checkout."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_checkout(SubprocessRunner(), name, root_path)
        if path:
            typer.echo(str(plan.worktree_path))
            return
        if plan.created:
            console.print(f"Prepared worktree: {plan.worktree_path}")
        console.print("Shell integration is required for checkout to change directories.")
        console.print(f"Resolved worktree: {plan.worktree_path}")
        console.print('Run: eval "$(bonsai shell-init zsh)"')
        raise typer.Exit(code=1)
    except BonsaiError as exc:
        _fail(exc)


@app.command("open")
def open_command() -> None:
    """Open the current worktree's primary local URL."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = plan_open_url(root_path, Path.cwd())
        if not webbrowser.open(plan.url):
            raise BonsaiWorkspaceError(f"Failed to open URL: {plan.url}")
        console.print(f"Opened {plan.url}")
    except BonsaiError as exc:
        _fail(exc)


@app.command("context")
def context_command(
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Print Bonsai facts for the current worktree."""
    try:
        root_path = find_workspace_root(Path.cwd())
        context = plan_agent_context(root_path, Path.cwd())
        typer.echo(render_agent_context(context, output_format), nl=False)
    except BonsaiError as exc:
        _fail(exc)


@app.command("shell-init")
def shell_init(shell: str) -> None:
    """Print shell integration code."""
    try:
        if shell != "zsh":
            raise BonsaiConfigError(f"Unsupported shell: {shell}")
        typer.echo(ZSH_SHELL_INIT, nl=False)
    except BonsaiError as exc:
        _fail(exc)


@app.command("install-shell")
def install_shell(shell: str) -> None:
    """Install shell integration for Bonsai checkout."""
    try:
        if shell != "zsh":
            raise BonsaiConfigError(f"Unsupported shell: {shell}")
        zshrc = Path.home() / ".zshrc"
        existing = zshrc.read_text(encoding="utf-8") if zshrc.exists() else ""
        if SHELL_INTEGRATION_START in existing:
            console.print("zsh integration already installed")
            return

        content = existing
        if content and not content.endswith("\n"):
            content += "\n"
        if content and not content.endswith("\n\n"):
            content += "\n"
        content += ZSH_INTEGRATION_BLOCK

        zshrc.parent.mkdir(parents=True, exist_ok=True)
        zshrc.write_text(content, encoding="utf-8")
        console.print(f"Installed zsh integration in {zshrc}")
    except BonsaiError as exc:
        _fail(exc)


@app.command("list")
def list_worktrees(
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    """List managed worktrees in the current workspace."""
    try:
        root_path = find_workspace_root(Path.cwd())
        summary = plan_workspace_summary(root_path)
        rendered = render_workspace_list(summary, output_format)
        if isinstance(rendered, str):
            typer.echo(rendered, nl=False)
        else:
            console.print(rendered)
    except BonsaiError as exc:
        _fail(exc)


@app.command("status")
def status_command(
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        status = plan_current_worktree_status(root_path, Path.cwd())
        typer.echo(render_workspace_status(status, output_format), nl=False)
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def start(branch: Annotated[str | None, typer.Argument()] = None) -> None:
    """Run the configured start command in a worktree."""
    try:
        root_path = find_workspace_root(Path.cwd())
        label = branch or "current worktree"
        console.print(f"Starting {label}")
        exit_code = execute_start(SubprocessRunner(), root_path, branch, Path.cwd())
        raise typer.Exit(code=exit_code)
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def sync(apply: bool = typer.Option(False, "--apply", help="Write regenerated files.")) -> None:
    """Compare or repair generated Bonsai files."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_sync(SubprocessRunner(), root_path, apply=apply)
        mode = "apply" if apply else "dry run"
        console.print(f"sync {mode}")
        if not plan.actions:
            console.print("No sync changes")
        for action in plan.actions:
            console.print(f"{action.kind} {action.path}")
        if apply and plan.reload_caddy:
            console.print("reload Caddy")
        elif not apply and plan.reload_caddy and plan.actions:
            console.print("reload Caddy after apply")
    except BonsaiError as exc:
        _fail(exc)


def _repair_action_label(action: str, apply: bool) -> str:
    if not apply:
        return action
    if action == "remove":
        return "removed"
    if action == "repack":
        return "repacked"
    return action


@app.command()
def repair(
    apply: bool = typer.Option(False, "--apply", help="Write repaired workspace state."),
) -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_repair(SubprocessRunner(), root_path, apply=apply)
        mode = "apply" if apply else "dry run"
        console.print(f"repair {mode}")
        if not plan.items:
            console.print("No state repairs needed")
        for item in plan.items:
            action = _repair_action_label(item.action, apply)
            console.print(f"{action} {item.branch} - {item.reason}")
        if plan.state_changed:
            console.print("Run: bonsai sync --apply")
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def cleanup(
    apply: bool = typer.Option(False, "--apply", help="Remove eligible worktrees."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Remove eligible worktrees with uncommitted changes.",
    ),
) -> None:
    """Remove branch worktrees whose pull requests were merged."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_cleanup(SubprocessRunner(), root_path, apply=apply, force=force)
        mode = "apply" if apply else "dry run"
        console.print(f"cleanup {mode}")
        if not plan.items:
            console.print("No managed worktrees")
        for item in plan.items:
            suffix = item.reason
            if item.pr_url is not None:
                suffix = f"{suffix} ({item.pr_url})"
            console.print(f"{item.action} {item.branch} - {suffix}")
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def doctor() -> None:
    """Check workspace health and report repair hints."""
    try:
        root_path = find_workspace_root(Path.cwd())
        report = check_workspace_health(SubprocessRunner(), root_path)
        table = Table(title="Bonsai doctor")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail")
        table.add_column("Hint")
        for check in report.checks:
            table.add_row(check.name, check.status, check.detail, check.hint or "")
        console.print(table)
        if report.failed:
            raise typer.Exit(code=1)
    except BonsaiError as exc:
        _fail(exc)


def main() -> None:
    app()
