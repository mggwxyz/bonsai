from __future__ import annotations

from bonsai.models import ManagedWorktree


def allocate_slot(worktrees: dict[str, ManagedWorktree]) -> int:
    used = {worktree.slot for worktree in worktrees.values()}
    slot = 1
    while slot in used:
        slot += 1
    return slot
