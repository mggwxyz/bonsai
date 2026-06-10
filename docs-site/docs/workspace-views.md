---
title: Workspace Views
---

# Workspace Views

Bonsai prints workspace facts as human-readable text by default and as
stable JSON with `--format json`. The JSON payloads carry versioned schema
identifiers (for example `bonsai.list.v1`), so scripts and AI agents can
rely on their shape.

## List Worktrees

```bash
bonsai list
bonsai list --format json
```

The text view is a simple list of branch, path, and kind for the default
worktree and every managed worktree. The JSON view is a full workspace
overview with slot, generated `.env.local` status, service ports, and
service URLs per worktree.

## Current Worktree Status

```bash
bonsai status
bonsai status --format json
```

`status` prints the current worktree's Bonsai facts: workspace root, config
path, branch, slot, generated `.env.local` status, service ports, service
URLs, and recommended Bonsai commands. The default text output is optimized
for humans; use JSON when a script needs the current worktree summary.

## Agent Context

```bash
bonsai context
bonsai context --format json
```

`context` prints the same worktree-scoped facts in an agent-oriented form.
Use `--format json` when an AI agent or script needs exact ports, URLs,
generated environment values, and recommended commands for the current
worktree without guessing.
