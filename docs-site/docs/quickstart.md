---
title: Quickstart
---

# Quickstart

Clone a repository into a Bonsai workspace:

```bash
bonsai clone git@github.com:org/my-app.git my-app
```

If Bonsai cannot find a `.bonsai.toml`, it starts a guided setup and writes a local workspace config.

Create or prepare a branch worktree:

```bash
cd my-app/main
bonsai add ma-123-implement-auth
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

Open the worktree's primary local URL:

```bash
bonsai open
```

Inspect the current worktree context:

```bash
bonsai context
bonsai context --format json
```
