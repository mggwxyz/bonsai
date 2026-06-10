from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from bonsai.models import CommandResult, ManagedWorktree, PortOwner
from bonsai.process import Runner


def owner_label(owner: PortOwner) -> str:
    label = f"{owner.command or 'process'}[{owner.pid}]"
    if owner.worktree_branch is not None:
        return f"{label} in {owner.worktree_branch}"
    if owner.cwd is not None:
        return f"{label} at {owner.cwd}"
    return label


def port_owner_payload(owner: PortOwner) -> dict[str, Any]:
    return {
        "pid": owner.pid,
        "command": owner.command,
        "user": owner.user,
        "cwd": str(owner.cwd) if owner.cwd is not None else None,
        "worktree_branch": owner.worktree_branch,
        "worktree_path": str(owner.worktree_path) if owner.worktree_path is not None else None,
    }


def allocate_slot(worktrees: dict[str, ManagedWorktree]) -> int:
    used = {worktree.slot for worktree in worktrees.values()}
    slot = 1
    while slot in used:
        slot += 1
    return slot


def parse_lsof_listener_output(output: str) -> tuple[PortOwner, ...]:
    owners: list[PortOwner] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if "p" not in current:
            return
        try:
            pid = int(current["p"])
        except ValueError:
            return
        owners.append(
            PortOwner(
                pid=pid,
                command=current.get("c", ""),
                user=current.get("u"),
            )
        )

    for line in output.splitlines():
        if not line:
            continue
        tag = line[0]
        value = line[1:]
        if tag == "p":
            flush()
            current = {"p": value}
            continue
        if tag in {"c", "u"}:
            current[tag] = value
    flush()
    return tuple(owners)


def _parse_lsof_cwd_output(output: str) -> Path | None:
    for line in output.splitlines():
        if line.startswith("n") and len(line) > 1:
            return Path(line[1:])
    return None


def _process_cwd(runner: Runner, pid: int) -> Path | None:
    try:
        result = runner.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return _parse_lsof_cwd_output(result.stdout)


def inspect_port_owners(runner: Runner, port: int) -> tuple[PortOwner, ...]:
    try:
        result: CommandResult = runner.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "pcun"],
            check=False,
        )
    except FileNotFoundError:
        return ()
    if result.returncode != 0:
        return ()

    owners = parse_lsof_listener_output(result.stdout)
    return tuple(
        replace(owner, cwd=_process_cwd(runner, owner.pid))
        for owner in owners
    )
