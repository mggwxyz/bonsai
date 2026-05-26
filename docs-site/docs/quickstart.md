---
title: Quickstart
---

# Quickstart

Clone a repository into a Bonsai workspace:

```bash
bonsai clone git@github.com:org/my-app.git my-app
```

If Bonsai cannot find a `.bonsai.toml`, it starts a guided setup and writes a local workspace config.

Initialize an existing checkout that already has `.bonsai.toml`:

```bash
cd my-app/main
bonsai init
```

This adopts the checkout as the default worktree, imports existing sibling git worktrees, writes Bonsai workspace state beside it, and renders generated files.
The checkout directory must match the current branch, matching Bonsai's `my-app/main` workspace layout.
If state already exists but is missing sibling worktrees, rerunning `bonsai init` reconciles state from the existing config.

Create or prepare a branch worktree:

```bash
cd my-app/main
bonsai add ma-123-implement-auth
```

When you want Bonsai to open the new working context immediately:

```bash
bonsai add ma-123-implement-auth --editor --open --start
```

Install shell integration once:

```bash
bonsai install-shell zsh
```

Open a new shell, then switch into the worktree:

```bash
bonsai checkout ma-123-implement-auth
```

Start the configured dev command:

```bash
bonsai start
```

You can also start a named branch, worktree directory, or worktree slug:

```bash
bonsai start ma-123-implement-auth
```

Open the worktree's primary local URL:

```bash
bonsai open
```

Inspect the current workspace and worktree context:

```bash
bonsai list
bonsai list --format json
bonsai status
bonsai status --format json
bonsai context
bonsai context --format json
```

Read managed lifecycle logs for install, setup, and start commands:

```bash
bonsai logs
bonsai logs ma-123-implement-auth --command start
```

Preview and apply repair or cleanup work:

```bash
bonsai sync
bonsai repair
bonsai cleanup
bonsai repair --apply
bonsai sync --apply
bonsai cleanup --apply
```
