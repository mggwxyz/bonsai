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

## Core Workflow

```bash
bonsai clone git@github.com:org/my-app.git my-app
bonsai add ma-123-implement-auth
bonsai checkout ma-123-implement-auth
bonsai start
bonsai open
```

`bonsai checkout` needs shell integration to change the parent shell's working directory. Without the integration, it prints the resolved path and setup instructions instead.
