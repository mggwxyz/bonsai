---
title: Overview
slug: /intro
---

# Bonsai

Bonsai is a macOS-first CLI for running multiple local development branches side by side. It creates and tracks git worktrees, assigns each branch a stable port slot, writes generated environment files, and keeps local HTTPS routing predictable through Caddy.

Use Bonsai when you often need to switch between feature branches, test several branches at once, or hand an AI agent exact per-worktree ports and URLs without guessing.

## What Bonsai Manages

- A workspace rooted at a project directory.
- A default worktree for the base branch.
- Managed worktrees for feature branches.
- Generated `.env.local` values for branch-specific ports and names.
- Optional Caddy snippets for local HTTPS URLs.
- Configured lifecycle commands and hooks with managed logs.
- Interactive shell checkout, PR worktrees, and ad hoc command execution per worktree or across all worktrees.
- Rich and JSON workspace, process, and global workspace summaries for humans, scripts, and AI agents.
- State repair, generated-file sync, workspace diagnostics, PR-aware cleanup, and Compose teardown during removal.

## Core Workflow

```bash
bonsai clone git@github.com:org/my-app.git my-app
bonsai add ma-123-implement-auth
# or: bonsai add --pr 123
bonsai checkout ma-123-implement-auth
bonsai start
bonsai logs --command start
bonsai status
bonsai open
```

`bonsai checkout` needs [shell integration](shell-integration.md) to change the parent shell's working directory. Without the integration, it prints the resolved path and setup instructions instead.

## Find Your Way Around

- [Install](install.md) and the [Quickstart](quickstart.md) — from zero to a running app.
- [Configuration](configuration.md) — everything `.bonsai.toml` controls.
- [Worktrees](worktrees.md) — add, checkout, remove, move, PR worktrees, and PR-aware cleanup.
- [Running Apps](running-apps.md) — foreground start, background up/down, stop, restart, exec, each, and logs.
- [Ports & URLs](urls-and-ports.md) — port slots, machine-global Caddy routing, open, urls, and ports.
- [Workspace Views](workspace-views.md) — list, list --all, ps, status, and agent context output.
- [Command Reference](commands.md) — every command and option, generated from the CLI.
- [Troubleshooting](troubleshooting.md) — doctor, repair, and common symptoms.
