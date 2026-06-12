---
title: Shell Integration
---

# Shell Integration

`bonsai checkout <worktree>` needs shell integration because a CLI child process cannot change its parent shell directory.

Install integration for your shell:

```bash
bonsai install-shell zsh
bonsai install-shell bash
bonsai install-shell fish
```

Or add the integration manually:

```zsh
eval "$(bonsai shell-init zsh)"
```

```bash
eval "$(bonsai shell-init bash)"
```

```fish
bonsai shell-init fish | source
```

`install-shell zsh` updates `~/.zshrc` with Bonsai markers. `install-shell
bash` does the same for `~/.bashrc`. `install-shell fish` writes Fish's native
drop-in file at `~/.config/fish/conf.d/bonsai.fish`.

After opening a new shell, checkout changes into the matching worktree:

```bash
bonsai checkout ma-123-implement-auth
```

Run `bonsai checkout` with no argument to pick from existing worktrees. Bonsai
uses `fzf` when it is installed, otherwise it falls back to a built-in numbered
picker. The lookup also accepts the branch name, worktree directory, or worktree
slug. If no exact worktree matches, `checkout` resolves a unique fuzzy match
before preparing a missing branch. If multiple existing worktrees match, Bonsai
opens the picker filtered to those matches.

## Shell Completion

Bonsai's shell integration registers completion for managed worktree aliases on
commands that accept a worktree name, including `checkout`, `remove`, `move`,
`logs`, `start`, `up`, `stop`, `restart`, `open`, and `urls`. If you add zsh
integration manually, place `eval "$(bonsai shell-init zsh)"` after zsh
completion is initialized.

## Checkout Behavior

If the requested worktree does not exist, Bonsai prepares one first. It fetches `origin`, uses the remote branch when it exists, or creates a new branch from the configured base branch.
Pass `--base-branch <branch>` to create a missing branch from a different base branch for that checkout.
Use `bonsai checkout --pr <number>` to prepare a pull request worktree and switch
into it.

Without shell integration, `checkout` prints the resolved path and exits with setup instructions.
