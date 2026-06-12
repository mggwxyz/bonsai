"""Multiplexer backends for running service panes in tmux, herdr, or cmux.

Each backend exposes the same two-phase contract used by ``execute_mux``:
``find_session`` answers whether the deterministic session already exists and
returns its attach command, and ``create_session`` builds the session with one
pane per service command and returns the attach command.

tmux launches pane commands directly with explicit ``-e`` environment
arguments. herdr and cmux type commands into an interactive shell inside each
pane, so the worktree environment is injected by prefixing commands with
``env KEY=VALUE ...``.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from pathlib import Path

from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import CommandResult, MuxPanePlan
from bonsai.process import Runner

MUX_BACKEND_TMUX = "tmux"
MUX_BACKEND_HERDR = "herdr"
MUX_BACKEND_CMUX = "cmux"
MUX_BACKENDS = (MUX_BACKEND_TMUX, MUX_BACKEND_HERDR, MUX_BACKEND_CMUX)
MUX_BACKEND_AUTO = "auto"


def detect_mux_backend(environ: Mapping[str, str]) -> str:
    if environ.get("HERDR_ENV") == "1":
        return MUX_BACKEND_HERDR
    if environ.get("CMUX_SOCKET_PATH") or environ.get("CMUX_WORKSPACE_ID"):
        return MUX_BACKEND_CMUX
    return MUX_BACKEND_TMUX


def resolve_mux_backend(backend: str, environ: Mapping[str, str]) -> str:
    if backend == MUX_BACKEND_AUTO:
        return detect_mux_backend(environ)
    if backend not in MUX_BACKENDS:
        choices = ", ".join((MUX_BACKEND_AUTO, *MUX_BACKENDS))
        raise BonsaiWorkspaceError(
            f"Unknown multiplexer backend {backend!r}. Choose one of: {choices}"
        )
    return backend


def shell_env_command(env: Mapping[str, str], command: str) -> str:
    """Render a service command with its environment for shell-typed panes."""
    pairs = [f"{name}={value}" for name, value in sorted(env.items())]
    return shlex.join(["env", *pairs, *shlex.split(command)])


def _require_backend_binary(exc: FileNotFoundError, backend: str) -> BonsaiWorkspaceError:
    return BonsaiWorkspaceError(f"{backend} is required for the {backend} backend of bonsai mux")


def _json_payload(result: CommandResult, backend: str, command: str) -> object:
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise BonsaiWorkspaceError(
            f"Could not parse JSON from `{command}`: {result.stdout!r}"
        ) from exc


def _iter_nodes(node: object):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_nodes(value)


_ID_KEYS = ("pane_id", "workspace_id", "tab_id", "surface_id", "ref", "id", "index")


def _identifier(node: object) -> str | None:
    if isinstance(node, bool):
        return None
    if isinstance(node, (str, int)):
        return str(node)
    if isinstance(node, dict):
        for key in _ID_KEYS:
            if key in node:
                return _identifier(node[key])
    return None


def _find_key(payload: object, key: str) -> object | None:
    for node in _iter_nodes(payload):
        if key in node:
            return node[key]
    return None


def _find_labeled_identifier(payload: object, label_key: str, label: str) -> str | None:
    for node in _iter_nodes(payload):
        if node.get(label_key) == label:
            return _identifier(node)
    return None


def _find_prefixed_ref(payload: object, prefix: str) -> str | None:
    for node in _iter_nodes(payload):
        ref = node.get("ref")
        if isinstance(ref, str) and ref.startswith(prefix):
            return ref
    return None


# --- tmux ---


def _tmux_env_args(env: Mapping[str, str]) -> list[str]:
    args: list[str] = []
    for name, value in sorted(env.items()):
        args.extend(["-e", f"{name}={value}"])
    return args


def _tmux_shell_command(command: str) -> str:
    return shlex.join(shlex.split(command))


def tmux_attach_command(session_name: str) -> str:
    return f"tmux attach -t {shlex.quote(session_name)}"


def tmux_find_session(runner: Runner, session_name: str) -> str | None:
    try:
        existing = runner.run(["tmux", "has-session", "-t", session_name], check=False)
    except FileNotFoundError as exc:
        raise _require_backend_binary(exc, MUX_BACKEND_TMUX) from exc
    if existing.returncode == 0:
        return tmux_attach_command(session_name)
    return None


def tmux_create_session(
    runner: Runner,
    session_name: str,
    panes: tuple[MuxPanePlan, ...],
    cwd: Path,
    env: Mapping[str, str],
) -> str:
    window_target = f"{session_name}:services"
    try:
        first_pane = panes[0]
        runner.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session_name,
                "-n",
                "services",
                "-c",
                str(cwd),
                *_tmux_env_args(env),
                "--",
                _tmux_shell_command(first_pane.command),
            ]
        )
        for pane in panes[1:]:
            runner.run(
                [
                    "tmux",
                    "split-window",
                    "-d",
                    "-t",
                    window_target,
                    "-c",
                    str(cwd),
                    *_tmux_env_args(env),
                    "--",
                    _tmux_shell_command(pane.command),
                ]
            )
        if len(panes) > 1:
            runner.run(["tmux", "select-layout", "-t", window_target, "tiled"])
    except FileNotFoundError as exc:
        raise _require_backend_binary(exc, MUX_BACKEND_TMUX) from exc
    return tmux_attach_command(session_name)


# --- herdr ---


def _herdr_attach_command(workspace_id: str) -> str:
    return f"herdr workspace focus {shlex.quote(workspace_id)}"


def _herdr_run(runner: Runner, argv: list[str], context: str) -> CommandResult:
    try:
        result = runner.run(argv, check=False)
    except FileNotFoundError as exc:
        raise _require_backend_binary(exc, MUX_BACKEND_HERDR) from exc
    if result.returncode != 0:
        raise BonsaiWorkspaceError(
            f"herdr could not {context} (is bonsai running inside herdr?): {result.stderr.strip()}"
        )
    return result


def herdr_find_session(runner: Runner, session_name: str) -> str | None:
    result = _herdr_run(runner, ["herdr", "workspace", "list"], "list workspaces")
    payload = _json_payload(result, MUX_BACKEND_HERDR, "herdr workspace list")
    workspace_id = _find_labeled_identifier(payload, "label", session_name)
    if workspace_id is None:
        return None
    return _herdr_attach_command(workspace_id)


def herdr_create_session(
    runner: Runner,
    session_name: str,
    panes: tuple[MuxPanePlan, ...],
    cwd: Path,
    env: Mapping[str, str],
) -> str:
    result = _herdr_run(
        runner,
        [
            "herdr",
            "workspace",
            "create",
            "--cwd",
            str(cwd),
            "--label",
            session_name,
            "--no-focus",
        ],
        "create a workspace",
    )
    payload = _json_payload(result, MUX_BACKEND_HERDR, "herdr workspace create")
    workspace_id = _identifier(_find_key(payload, "workspace"))
    pane_id = _identifier(_find_key(payload, "root_pane"))
    if workspace_id is None or pane_id is None:
        raise BonsaiWorkspaceError(
            f"Unexpected `herdr workspace create` output: {result.stdout!r}"
        )

    _herdr_run(
        runner,
        ["herdr", "pane", "run", pane_id, shell_env_command(env, panes[0].command)],
        f"start the {panes[0].name} pane",
    )
    for index, pane in enumerate(panes[1:]):
        direction = "right" if index == 0 else "down"
        split = _herdr_run(
            runner,
            ["herdr", "pane", "split", pane_id, "--direction", direction, "--no-focus"],
            f"split a pane for {pane.name}",
        )
        split_payload = _json_payload(split, MUX_BACKEND_HERDR, "herdr pane split")
        new_pane_id = _identifier(_find_key(split_payload, "pane"))
        if new_pane_id is None:
            raise BonsaiWorkspaceError(f"Unexpected `herdr pane split` output: {split.stdout!r}")
        _herdr_run(
            runner,
            ["herdr", "pane", "run", new_pane_id, shell_env_command(env, pane.command)],
            f"start the {pane.name} pane",
        )
        pane_id = new_pane_id
    return _herdr_attach_command(workspace_id)


# --- cmux ---


def _cmux_attach_command(workspace_ref: str) -> str:
    return f"cmux select-workspace --workspace {shlex.quote(workspace_ref)}"


def _cmux_run(runner: Runner, argv: list[str], context: str) -> CommandResult:
    try:
        result = runner.run(argv, check=False)
    except FileNotFoundError as exc:
        raise _require_backend_binary(exc, MUX_BACKEND_CMUX) from exc
    if result.returncode != 0:
        raise BonsaiWorkspaceError(
            f"cmux could not {context} (is bonsai running inside cmux?): {result.stderr.strip()}"
        )
    return result


def cmux_find_session(runner: Runner, session_name: str) -> str | None:
    result = _cmux_run(runner, ["cmux", "list-workspaces", "--json"], "list workspaces")
    payload = _json_payload(result, MUX_BACKEND_CMUX, "cmux list-workspaces --json")
    workspace_ref = _find_labeled_identifier(payload, "title", session_name)
    if workspace_ref is None:
        return None
    return _cmux_attach_command(workspace_ref)


def cmux_create_session(
    runner: Runner,
    session_name: str,
    panes: tuple[MuxPanePlan, ...],
    cwd: Path,
    env: Mapping[str, str],
) -> str:
    result = _cmux_run(
        runner,
        [
            "cmux",
            "new-workspace",
            "--cwd",
            str(cwd),
            "--name",
            session_name,
            "--command",
            shell_env_command(env, panes[0].command),
            "--json",
        ],
        "create a workspace",
    )
    payload = _json_payload(result, MUX_BACKEND_CMUX, "cmux new-workspace --json")
    workspace_ref = _find_labeled_identifier(payload, "title", session_name)
    if workspace_ref is None:
        workspace_ref = _find_prefixed_ref(payload, "workspace:")
    if workspace_ref is None:
        raise BonsaiWorkspaceError(f"Unexpected `cmux new-workspace` output: {result.stdout!r}")

    surface_ref: str | None = None
    for index, pane in enumerate(panes[1:]):
        direction = "right" if index == 0 else "down"
        if surface_ref is None:
            target_args = ["--workspace", workspace_ref]
        else:
            target_args = ["--surface", surface_ref]
        split = _cmux_run(
            runner,
            ["cmux", "new-split", direction, *target_args, "--json"],
            f"split a surface for {pane.name}",
        )
        split_payload = _json_payload(split, MUX_BACKEND_CMUX, "cmux new-split --json")
        surface_ref = _find_prefixed_ref(split_payload, "surface:")
        if surface_ref is None:
            surface_ref = _identifier(_find_key(split_payload, "surface"))
        if surface_ref is None:
            raise BonsaiWorkspaceError(f"Unexpected `cmux new-split` output: {split.stdout!r}")
        _cmux_run(
            runner,
            ["cmux", "send", "--surface", surface_ref, shell_env_command(env, pane.command) + "\n"],
            f"start the {pane.name} surface",
        )
    return _cmux_attach_command(workspace_ref)


_FIND_SESSION = {
    MUX_BACKEND_TMUX: tmux_find_session,
    MUX_BACKEND_HERDR: herdr_find_session,
    MUX_BACKEND_CMUX: cmux_find_session,
}

_CREATE_SESSION = {
    MUX_BACKEND_TMUX: tmux_create_session,
    MUX_BACKEND_HERDR: herdr_create_session,
    MUX_BACKEND_CMUX: cmux_create_session,
}


def find_mux_session(runner: Runner, backend: str, session_name: str) -> str | None:
    return _FIND_SESSION[backend](runner, session_name)


def create_mux_session(
    runner: Runner,
    backend: str,
    session_name: str,
    panes: tuple[MuxPanePlan, ...],
    cwd: Path,
    env: Mapping[str, str],
) -> str:
    return _CREATE_SESSION[backend](runner, session_name, panes, cwd, env)
