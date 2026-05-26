---
title: Troubleshooting
---

# Troubleshooting

## Check Workspace Health

Run:

```bash
bonsai doctor
```

`doctor` checks workspace state, config, git worktrees, generated files, Caddy files, Caddy availability, and configured service port conflicts.

Failed repairable checks point to:

```bash
bonsai sync --apply
```

## Checkout Does Not Change Directories

Install shell integration and open a new shell:

```bash
bonsai install-shell zsh
```

If you prefer manual setup:

```zsh
eval "$(bonsai shell-init zsh)"
```

## Local URL Is Missing Or Stale

Regenerate Bonsai-managed files and reload Caddy when needed:

```bash
bonsai sync --apply
```

Then run:

```bash
bonsai open
```

## Cleanup Skips A Worktree

`bonsai cleanup` is conservative. It skips branches with no PR, open PRs, unmerged closed PRs, or uncommitted changes.

Preview cleanup:

```bash
bonsai cleanup
```

Apply cleanup:

```bash
bonsai cleanup --apply
```

Remove eligible dirty worktrees only when you mean it:

```bash
bonsai cleanup --apply --force
```
