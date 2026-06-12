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
bonsai list --all
bonsai list --format json
```

The text view is a simple list of branch, path, and kind for the default
worktree and every managed worktree. The JSON view is a full workspace
overview with slot, generated `.env.local` status, service ports, and
service URLs per worktree.

`list --all` uses Bonsai's machine-global workspace registry and groups
worktrees by workspace root. Workspaces self-register when Bonsai loads their
state; stale registry entries are pruned when their `.bonsai/state.json` is
gone. `list --all --format json` includes each workspace root so scripts can
jump directly to the right checkout.

## Background App Processes

```bash
bonsai ps
bonsai ps --format json
```

`ps` lists tracked app processes started by `bonsai up` across every registered
workspace. It reports workspace, worktree, PID, command, log path, and uptime
when available. Dead PID records are pruned while reading the process list.

## Current Worktree Status

```bash
bonsai status
bonsai status --format json
```

`status` prints the current worktree's Bonsai facts: workspace root, config
path, branch, slot, generated `.env.local` status, service ports, service
URLs, and recommended Bonsai commands. The default text output is optimized
for humans; the JSON payload also carries the worktree's generated
environment values, so an AI agent or script can read exact ports, URLs,
and env without guessing.

`bonsai context` is an alias of `status`, kept for agent-oriented
instructions.
