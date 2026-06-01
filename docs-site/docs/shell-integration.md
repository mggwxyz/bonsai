---
title: Shell Integration
---

# Shell Integration

`bonsai checkout <worktree>` needs shell integration because a CLI child process cannot change its parent shell directory.

Install the zsh integration:

```bash
bonsai install-shell zsh
```

Or add the integration manually to `~/.zshrc`:

```zsh
eval "$(bonsai shell-init zsh)"
```

After opening a new shell, checkout changes into the matching worktree:

```bash
bonsai checkout ma-123-implement-auth
```

The lookup accepts the branch name, worktree directory, or worktree slug. If no
exact worktree matches, `checkout` resolves a unique fuzzy match before preparing
a missing branch. If multiple existing worktrees match, Bonsai asks for a more
specific name.

## Shell Completion

Bonsai's zsh integration also registers shell completion for managed worktree
aliases on `checkout`, `start`, `logs`, `open`, `remove`, and `move`. If you add
the integration manually, place `eval "$(bonsai shell-init zsh)"` after zsh
completion is initialized.

## Checkout Behavior

If the requested worktree does not exist, Bonsai prepares one first. It fetches `origin`, uses the remote branch when it exists, or creates a new branch from the configured base branch.
Pass `--base-branch <branch>` to create a missing branch from a different base branch for that checkout.

Without shell integration, `checkout` prints the resolved path and exits with setup instructions.
