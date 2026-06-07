import os
import shlex
import shutil
import subprocess
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console

from bonsai import __version__
from bonsai.agent import render_agent_context
from bonsai.command_results import (
    CommandRenderable,
    render_cleanup_result,
    render_doctor_result,
    render_port_repair_result,
    render_repair_result,
    render_stop_result,
    render_sync_result,
)
from bonsai.doctor import preflight_report, render_doctor_json, validate_doctor_format
from bonsai.errors import BonsaiConfigError, BonsaiError, BonsaiWorkspaceError
from bonsai.git import current_branch
from bonsai.models import OpenUrlPlan
from bonsai.onboarding import write_guided_config as onboarding_write_guided_config
from bonsai.port_repair import render_port_repair_json, validate_port_repair_format
from bonsai.process import SubprocessRunner
from bonsai.state import load_state
from bonsai.status import (
    render_workspace_list,
    render_workspace_ports,
    render_workspace_status,
    render_workspace_urls,
)
from bonsai.workflows import (
    check_workspace_health,
    execute_add,
    execute_checkout,
    execute_cleanup,
    execute_clone,
    execute_doctor_apply,
    execute_down,
    execute_init,
    execute_move,
    execute_port_repairs,
    execute_remove,
    execute_repair,
    execute_start,
    execute_stop_processes,
    execute_sync,
    execute_up,
    plan_agent_context,
    plan_command_log,
    plan_current_worktree_status,
    plan_open_url,
    plan_open_url_for_worktree,
    plan_port_repairs,
    plan_workspace_ports,
    plan_workspace_summary,
    plan_workspace_urls,
    repo_config_path,
    resolve_open_target,
    setup_caddy,
    url_liveness_ok,
    workspace_config_path,
    worktree_name_completions,
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

_bonsai_completion() {
  local bonsai_bin="${commands[bonsai]}"
  if [[ -z "$bonsai_bin" ]]; then
    return 1
  fi

  eval $(env \\
    _TYPER_COMPLETE_ARGS="${words[1,$CURRENT]}" \\
    _BONSAI_COMPLETE=complete_zsh \\
    "$bonsai_bin")
}

if (( $+functions[compdef] )); then
  compdef _bonsai_completion bonsai
fi
"""
SHELL_INTEGRATION_START = "# >>> bonsai shell integration >>>"
SHELL_INTEGRATION_END = "# <<< bonsai shell integration <<<"
ZSH_INTEGRATION_BLOCK = (
    f"{SHELL_INTEGRATION_START}\n"
    'eval "$(bonsai shell-init zsh)"\n'
    f"{SHELL_INTEGRATION_END}\n"
)


def ensure_shell_integration(
    home: Path,
    shell: str,
    *,
    offer: Callable[[], bool],
) -> Literal["installed", "already", "manual"]:
    """Install zsh integration without raising; never strands a guided run."""
    if shell != "zsh":
        return "manual"
    zshrc = home / ".zshrc"
    existing = zshrc.read_text(encoding="utf-8") if zshrc.exists() else ""
    if SHELL_INTEGRATION_START in existing:
        return "already"
    if not offer():
        return "manual"

    backup = home / ".zshrc.bonsai.bak"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(existing, encoding="utf-8")

    content = existing
    if content and not content.endswith("\n"):
        content += "\n"
    if content and not content.endswith("\n\n"):
        content += "\n"
    content += ZSH_INTEGRATION_BLOCK
    zshrc.write_text(content, encoding="utf-8")
    return "installed"


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


def _print_command_result(rendered: str | tuple[CommandRenderable, ...]) -> None:
    if isinstance(rendered, str):
        typer.echo(rendered, nl=False)
        return
    for item in rendered:
        console.print(item)


def _render_up_result(plan) -> str:
    lines: list[str] = []
    if plan.stale_pid is not None:
        lines.append(f"removed stale pid {plan.stale_pid}")
    lines.append(f"started {plan.branch} pid={plan.pid}")
    lines.append(f"log: {plan.log_path}")
    if plan.ready_ports:
        lines.append("ready ports: " + ", ".join(str(port) for port in plan.ready_ports))
    return "\n".join(lines) + "\n"


def _render_down_result(plan) -> str:
    if plan.pid is None:
        return f"{plan.action} {plan.branch}\n"
    return f"{plan.action} {plan.branch} pid={plan.pid}\n"


def _complete_worktree_names(incomplete: str) -> list[str]:
    return _complete_worktree_names_from_workspace(incomplete, include_default=True)


def _complete_managed_worktree_names(incomplete: str) -> list[str]:
    return _complete_worktree_names_from_workspace(incomplete, include_default=False)


# Typer applies a prefix-only post-filter after callbacks return. Bonsai already
# filters these values with its own alias matching, which includes substrings.
class _CompletionValue(str):
    def startswith(self, _prefix: str, *_args) -> bool:
        return True


def _complete_worktree_names_for_typer(
    ctx,
    args,
    incomplete: str,
) -> list[str]:
    _ = (ctx, args)
    return [_CompletionValue(value) for value in _complete_worktree_names(incomplete)]


def _complete_managed_worktree_names_for_typer(
    ctx,
    args,
    incomplete: str,
) -> list[str]:
    _ = (ctx, args)
    return [_CompletionValue(value) for value in _complete_managed_worktree_names(incomplete)]


def _complete_worktree_names_from_workspace(
    incomplete: str,
    *,
    include_default: bool,
) -> list[str]:
    try:
        workspace_root = find_workspace_root(Path.cwd())
        return list(
            worktree_name_completions(
                workspace_root,
                incomplete,
                include_default=include_default,
            )
        )
    except (BonsaiError, OSError, ValueError, KeyError):
        return []


def _resolve_editor_command(environ: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    configured = (env.get("VISUAL") or env.get("EDITOR") or "").strip()
    if configured:
        try:
            argv = shlex.split(configured)
        except ValueError as exc:
            raise BonsaiWorkspaceError(f"Invalid editor command: {configured}") from exc
        if argv and argv[0]:
            return argv
        raise BonsaiWorkspaceError(f"Invalid editor command: {configured}")

    path = None if environ is None else env.get("PATH", "")
    code = shutil.which("code", path=path)
    if code is not None:
        return [code]

    raise BonsaiWorkspaceError(
        "No editor configured. Set VISUAL or EDITOR, or install code on PATH."
    )


def _open_editor(worktree_path: Path) -> None:
    command = [*_resolve_editor_command(), str(worktree_path)]
    try:
        result = subprocess.run(command, check=False)
    except OSError as exc:
        raise BonsaiWorkspaceError(f"Failed to open editor: {shlex.join(command)}") from exc
    if result.returncode != 0:
        raise BonsaiWorkspaceError(
            f"Editor exited with code {result.returncode}: {shlex.join(command)}"
        )


def _print_resolved_url(plan: OpenUrlPlan) -> None:
    label = "Caddy route" if plan.via == "caddy" else f"port localhost:{plan.port}"
    console.print(f"{plan.url} ({label})")


def _open_url(plan: OpenUrlPlan) -> None:
    target = resolve_open_target(plan)
    if not url_liveness_ok(target):
        console.print(
            f"The app isn't responding on localhost:{target.port} yet — "
            f"run `bonsai up {target.branch}` then `bonsai open {target.branch}`."
        )
        raise typer.Exit(code=1)
    if not webbrowser.open(target.url):
        raise BonsaiWorkspaceError(f"Failed to open URL: {target.url}")
    console.print(f"Opened {target.url}")


def _open_primary_url(workspace_root: Path, name: str) -> None:
    _open_url(plan_open_url_for_worktree(workspace_root, name))


def _optional_prompt(label: str, default: str | None) -> str | None:
    value = typer.prompt(label, default=default or "", show_default=bool(default)).strip()
    return value or None


def _print_onboarding(message: str) -> None:
    console.print(message, markup=False)


def write_guided_config(
    config_path: Path,
    repo_path: Path,
    fallback_name: str,
    base_branch: str,
    force: bool = False,
) -> Path:
    return onboarding_write_guided_config(
        config_path=config_path,
        repo_path=repo_path,
        fallback_name=fallback_name,
        base_branch=base_branch,
        force=force,
        ask=typer.prompt,
        confirm=typer.confirm,
        ask_optional=_optional_prompt,
        say=_print_onboarding,
    )


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


def _preflight_check_failed(report, check_id: str) -> bool:
    return any(
        check.id == check_id and check.status == "fail" for check in report.checks
    )


@app.command("start-here")
def start_here(
    git_url: str,
    name: str,
    branch: Annotated[
        str | None,
        typer.Option("--branch", help="Branch to prepare as the first worktree."),
    ] = None,
    shell: Annotated[
        str,
        typer.Option("--shell", help="Shell to offer integration for."),
    ] = "zsh",
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive/--no-interactive",
            help="Run guided prompts and gate the final URL on a liveness probe. "
            "Use --no-interactive for a scripted run that prints the resolved URL.",
        ),
    ] = True,
) -> None:
    """Guide a newcomer from clone to a running app in one sequenced flow."""
    try:
        runner = SubprocessRunner()
        home = Path.home()

        report = preflight_report(runner, Path.cwd(), home)
        _print_command_result(render_doctor_result(report, apply=False))
        if _preflight_check_failed(report, "git"):
            console.print(
                "git is required. Run `brew install git`, then re-run "
                f"`bonsai start-here {git_url} {name}`."
            )
            raise typer.Exit(code=1)
        if _preflight_check_failed(report, "caddy"):
            console.print(
                "Caddy missing — using a port URL. Optional: `brew install caddy`."
            )
        if _preflight_check_failed(report, "docker"):
            console.print(
                "Docker missing for this compose repo. Start Docker Desktop, then "
                f"re-run `bonsai start-here {git_url} {name}`."
            )
            raise typer.Exit(code=1)

        config_initializer = _guided_config_initializer if interactive else None
        plan = execute_clone(
            runner,
            git_url,
            name,
            Path.cwd(),
            config_initializer=config_initializer,
        )
        console.print(f"Created workspace: {plan.workspace_root}")
        console.print(f"Default worktree: {plan.default_worktree}")
        workspace_root = plan.workspace_root

        offer = typer.confirm if interactive else (lambda: False)
        shell_result = ensure_shell_integration(home, shell, offer=offer)
        if shell_result == "installed":
            console.print(f"Installed {shell} integration in {home / '.zshrc'}")
        elif shell_result == "already":
            console.print(f"{shell} integration already installed")
        else:
            console.print(
                f'Shell integration skipped. Run `bonsai install-shell {shell}`, '
                'open a new shell, then re-run — or add '
                'eval "$(bonsai shell-init zsh)" to your shell config.'
            )

        worktree_branch = branch or plan.state.default_branch
        add_plan = execute_add(runner, worktree_branch, workspace_root)
        console.print(f"Prepared worktree: {add_plan.worktree_path}")
        console.print(f"Port slot: {add_plan.slot}")

        caddy_result = setup_caddy(runner, workspace_root)
        for check in caddy_result.checks:
            console.print(check.hint or check.detail)

        open_plan = plan_open_url_for_worktree(workspace_root, worktree_branch)
        target = resolve_open_target(open_plan)
        if not interactive:
            _print_resolved_url(target)
            return
        if url_liveness_ok(target):
            console.print(f"✅ done — your app is at {target.url}")
        else:
            console.print(
                f"The app isn't responding on localhost:{target.port} yet — "
                f"run `bonsai up {target.branch}` then `bonsai open {target.branch}`."
            )
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
        managed_workspace = False
        try:
            workspace_root = find_workspace_root(current_path)
        except BonsaiWorkspaceError:
            pass
        else:
            state_path = workspace_root / ".bonsai" / "state.json"
            if state_path.exists():
                managed_workspace = True
                state = load_state(state_path)
                config_path = workspace_config_path(workspace_root)
                fallback_name = workspace_root.name
                default_worktree_path = workspace_root / state.default_worktree
                if (
                    not force
                    and (
                        config_path.exists()
                        or repo_config_path(workspace_root, state.default_worktree).exists()
                    )
                ):
                    plan = execute_init(SubprocessRunner(), default_worktree_path)
                    console.print(f"Initialized workspace: {plan.workspace_root}")
                    console.print(f"Default worktree: {plan.default_worktree}")
                    return
                if current_path == workspace_root:
                    repo_path = default_worktree_path
        if not force and not managed_workspace and config_path.exists():
            plan = execute_init(SubprocessRunner(), current_path)
            console.print(f"Initialized workspace: {plan.workspace_root}")
            console.print(f"Default worktree: {plan.default_worktree}")
            return
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
def add(
    branch: str,
    base_branch: Annotated[
        str | None,
        typer.Option(
            "--base-branch",
            help="Base branch to use when creating a new branch worktree.",
        ),
    ] = None,
    editor: Annotated[
        bool,
        typer.Option("--editor", help="Open the prepared worktree in an editor."),
    ] = False,
    open_url: Annotated[
        bool,
        typer.Option("--open", help="Open the prepared worktree's primary local URL."),
    ] = False,
    start_app: Annotated[
        bool,
        typer.Option("--start", help="Run the configured start command after add."),
    ] = False,
) -> None:
    """Prepare a managed worktree for a branch."""
    try:
        current_path = Path.cwd()
        root_path = find_workspace_root(current_path)
        runner = SubprocessRunner()
        if base_branch is None:
            plan = execute_add(runner, branch, root_path)
        else:
            plan = execute_add(runner, branch, root_path, base_branch=base_branch)
        console.print(f"Prepared worktree: {plan.worktree_path}")
        console.print(f"Port slot: {plan.slot}")
        if editor:
            _open_editor(plan.worktree_path)
            console.print(f"Opened editor: {plan.worktree_path}")
        if open_url:
            _open_primary_url(root_path, branch)
        if start_app:
            console.print(f"Starting {branch}")
            exit_code = execute_start(SubprocessRunner(), root_path, branch, current_path)
            raise typer.Exit(code=exit_code)
    except BonsaiError as exc:
        _fail(exc)


@app.command("remove")
def remove_command(
    name: Annotated[str, typer.Argument(autocompletion=_complete_managed_worktree_names_for_typer)],
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


@app.command("move")
def move_command(
    name: Annotated[str, typer.Argument(autocompletion=_complete_worktree_names_for_typer)],
    new_folder: str,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Rename the default worktree (relocates the main tree, repairs secondaries).",
        ),
    ] = False,
) -> None:
    """Move a managed worktree folder.

    The worktree argument accepts a branch name, worktree directory, or worktree slug.
    Bonsai runs `git worktree move`, updates `.bonsai/state.json`, and refreshes
    generated files. Renaming the default worktree relocates the main working tree
    and repairs secondary worktrees; it requires `--force`.
    """
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_move(SubprocessRunner(), name, new_folder, root_path, force=force)
        console.print(
            f"Moved worktree: {plan.old_worktree_path} -> {plan.new_worktree_path}"
        )
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def checkout(
    name: Annotated[str, typer.Argument(autocompletion=_complete_worktree_names_for_typer)],
    path: Annotated[
        bool,
        typer.Option("--path", help="Print the resolved worktree path for shell integration."),
    ] = False,
    base_branch: Annotated[
        str | None,
        typer.Option(
            "--base-branch",
            help="Base branch to use when creating a new branch worktree.",
        ),
    ] = None,
) -> None:
    """Resolve or prepare a worktree for shell checkout."""
    try:
        root_path = find_workspace_root(Path.cwd())
        runner = SubprocessRunner()
        if base_branch is None:
            plan = execute_checkout(runner, name, root_path)
        else:
            plan = execute_checkout(runner, name, root_path, base_branch=base_branch)
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
def open_command(
    name: Annotated[
        str | None,
        typer.Argument(autocompletion=_complete_worktree_names_for_typer),
    ] = None,
    service: Annotated[
        str | None,
        typer.Option("--service", help="Open a specific public service URL."),
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive/--no-interactive",
            help="Launch a browser after confirming the URL responds. "
            "Use --no-interactive to print the resolved URL without probing.",
        ),
    ] = True,
) -> None:
    """Open a worktree's primary local URL."""
    try:
        root_path = find_workspace_root(Path.cwd())
        if name is None:
            plan = plan_open_url(root_path, Path.cwd(), service_name=service)
        else:
            plan = plan_open_url_for_worktree(root_path, name, service_name=service)
        if interactive:
            _open_url(plan)
        else:
            _print_resolved_url(resolve_open_target(plan))
    except BonsaiError as exc:
        _fail(exc)


@app.command("urls")
def urls_command(
    name: Annotated[
        str | None,
        typer.Argument(autocompletion=_complete_worktree_names_for_typer),
    ] = None,
    service_name: Annotated[
        str | None,
        typer.Option("--service", help="Filter diagnostics to one public service."),
    ] = None,
    diagnose_url: Annotated[
        str | None,
        typer.Option("--diagnose", help="Find diagnostics for a specific configured URL."),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Show configured local URLs and route diagnostics."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = plan_workspace_urls(
            SubprocessRunner(),
            root_path,
            name=name,
            service_name=service_name,
            diagnose_url=diagnose_url,
        )
        typer.echo(render_workspace_urls(plan, output_format), nl=False)
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
        home = Path.home()
        result = ensure_shell_integration(home, shell, offer=lambda: True)
        if result == "already":
            console.print("zsh integration already installed")
        else:
            console.print(f"Installed zsh integration in {home / '.zshrc'}")
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


@app.command("ports")
def ports_command(
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    """List configured service ports and listener ownership."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = plan_workspace_ports(SubprocessRunner(), root_path)
        rendered = render_workspace_ports(plan, output_format)
        if isinstance(rendered, str):
            typer.echo(rendered, nl=False)
        else:
            console.print(rendered)
    except BonsaiError as exc:
        _fail(exc)


@app.command("ps")
def ps_command(
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    """List configured service ports that currently have listeners."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = plan_workspace_ports(SubprocessRunner(), root_path)
        rendered = render_workspace_ports(plan, output_format, only_busy=True)
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
        rendered = render_workspace_status(
            status,
            output_format,
            color=output_format.lower() == "text",
        )
        if isinstance(rendered, str):
            typer.echo(rendered, nl=False)
        else:
            console.print(rendered, end="")
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def start(
    branch: Annotated[
        str | None,
        typer.Argument(autocompletion=_complete_worktree_names_for_typer),
    ] = None,
) -> None:
    """Run the configured start command in a worktree."""
    try:
        root_path = find_workspace_root(Path.cwd())
        label = branch or "current worktree"
        console.print(f"Starting {label}")
        exit_code = execute_start(SubprocessRunner(), root_path, branch, Path.cwd())
        raise typer.Exit(code=exit_code)
    except BonsaiError as exc:
        _fail(exc)


@app.command("up")
def up_command(
    name: Annotated[
        str | None,
        typer.Argument(autocompletion=_complete_worktree_names_for_typer),
    ] = None,
    wait_timeout: Annotated[
        float,
        typer.Option("--wait-timeout", help="Seconds to wait for the primary service port."),
    ] = 30.0,
) -> None:
    """Start the configured app command in the background and track its PID."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_up(
            SubprocessRunner(),
            root_path,
            name,
            Path.cwd(),
            readiness_timeout=wait_timeout,
        )
        typer.echo(_render_up_result(plan), nl=False)
    except BonsaiError as exc:
        _fail(exc)


@app.command("down")
def down_command(
    name: Annotated[
        str | None,
        typer.Argument(autocompletion=_complete_worktree_names_for_typer),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Seconds to wait before force killing the tracked PID."),
    ] = 5.0,
) -> None:
    """Stop a background app process started by `bonsai up`."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_down(
            root_path,
            name,
            Path.cwd(),
            terminate_timeout=timeout,
        )
        typer.echo(_render_down_result(plan), nl=False)
    except BonsaiError as exc:
        _fail(exc)


@app.command("stop")
def stop_command(
    name: Annotated[
        str | None,
        typer.Argument(autocompletion=_complete_worktree_names_for_typer),
    ] = None,
    all_worktrees: Annotated[
        bool,
        typer.Option("--all", help="Stop matching listeners for all worktrees."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Stop external or unknown owners of selected ports."),
    ] = False,
) -> None:
    """Stop listener processes for configured service ports."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_stop_processes(
            SubprocessRunner(),
            root_path,
            current_path=Path.cwd(),
            name=name,
            all_worktrees=all_worktrees,
            force=force,
        )
        _print_command_result(render_stop_result(plan))
    except BonsaiError as exc:
        _fail(exc)


@app.command("restart")
def restart_command(
    name: Annotated[
        str | None,
        typer.Argument(autocompletion=_complete_worktree_names_for_typer),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Stop external or unknown owners before starting."),
    ] = False,
    detach: Annotated[
        bool,
        typer.Option("--detach", help="Start in the background after stopping."),
    ] = False,
    wait_timeout: Annotated[
        float,
        typer.Option("--wait-timeout", help="Seconds to wait for detached readiness."),
    ] = 30.0,
) -> None:
    """Stop matching listeners, then run the configured start command."""
    try:
        root_path = find_workspace_root(Path.cwd())
        label = name or "current worktree"
        console.print(f"Restarting {label}")
        runner = SubprocessRunner()
        if detach:
            down_plan = execute_down(root_path, name, Path.cwd(), terminate_timeout=5.0)
            if down_plan.action != "not-running":
                typer.echo(_render_down_result(down_plan), nl=False)
        stop_plan = execute_stop_processes(
            runner,
            root_path,
            current_path=Path.cwd(),
            name=name,
            force=force,
        )
        _print_command_result(render_stop_result(stop_plan))
        if detach:
            up_plan = execute_up(
                runner,
                root_path,
                name,
                Path.cwd(),
                readiness_timeout=wait_timeout,
            )
            typer.echo(_render_up_result(up_plan), nl=False)
            return
        exit_code = execute_start(runner, root_path, name, Path.cwd())
        raise typer.Exit(code=exit_code)
    except BonsaiError as exc:
        _fail(exc)


@app.command("logs")
def logs_command(
    branch: Annotated[
        str | None,
        typer.Argument(autocompletion=_complete_worktree_names_for_typer),
    ] = None,
    command: Annotated[
        str | None,
        typer.Option("--command", help="Filter logs by lifecycle command kind."),
    ] = None,
    follow: Annotated[
        bool,
        typer.Option("--follow", "-f", help="Follow the selected log file."),
    ] = False,
) -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = plan_command_log(root_path, branch, Path.cwd(), command)
        if follow:
            exit_code = SubprocessRunner().run_stream(
                ["tail", "-n", "+1", "-f", str(plan.log_path)]
            )
            raise typer.Exit(code=exit_code)
        typer.echo(plan.content, nl=False)
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def sync(apply: bool = typer.Option(False, "--apply", help="Write regenerated files.")) -> None:
    """Compare or repair generated Bonsai files."""
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_sync(SubprocessRunner(), root_path, apply=apply)
        _print_command_result(render_sync_result(plan, apply=apply))
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def repair(
    apply: bool = typer.Option(False, "--apply", help="Write repaired workspace state."),
) -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_repair(SubprocessRunner(), root_path, apply=apply)
        _print_command_result(render_repair_result(plan, apply=apply))
    except BonsaiError as exc:
        _fail(exc)


@app.command("repair-ports")
def repair_ports(
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
    apply: bool = typer.Option(False, "--apply", help="Write repaired slots and sync files."),
) -> None:
    """Plan or apply slot reassignments for worktrees with conflicting ports."""
    try:
        output_format = validate_port_repair_format(output_format)
        root_path = find_workspace_root(Path.cwd())
        runner = SubprocessRunner()
        plan = (
            execute_port_repairs(runner, root_path, apply=True)
            if apply
            else plan_port_repairs(root_path, runner=runner)
        )
        if output_format == "json":
            typer.echo(render_port_repair_json(plan, root_path), nl=False)
            return

        _print_command_result(render_port_repair_result(plan, apply=apply))
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
        _print_command_result(render_cleanup_result(plan, apply=apply))
    except BonsaiError as exc:
        _fail(exc)


@app.command()
def doctor(
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
    apply: bool = typer.Option(False, "--apply", help="Apply safe workspace repairs."),
    preflight: bool = typer.Option(
        False,
        "--preflight",
        help="Check first-run prerequisites without a workspace.",
    ),
) -> None:
    """Check workspace health and report repair hints."""
    try:
        output_format = validate_doctor_format(output_format)
        if preflight:
            repo_path = Path.cwd()
            report = preflight_report(SubprocessRunner(), repo_path)
            if output_format == "json":
                typer.echo(render_doctor_json(report, repo_path), nl=False)
            else:
                _print_command_result(render_doctor_result(report, apply=False))
            if report.failed:
                raise typer.Exit(code=1)
            return
        root_path = find_workspace_root(Path.cwd())
        apply_plan = None
        runner = SubprocessRunner()
        if apply:
            apply_plan = execute_doctor_apply(runner, root_path)
        report = check_workspace_health(runner, root_path)
        if output_format == "json":
            typer.echo(render_doctor_json(report, root_path, apply_plan), nl=False)
            if report.failed:
                raise typer.Exit(code=1)
            return

        _print_command_result(render_doctor_result(report, apply=apply, apply_plan=apply_plan))
        if report.failed:
            raise typer.Exit(code=1)
    except BonsaiError as exc:
        _fail(exc)


def main() -> None:
    app()
