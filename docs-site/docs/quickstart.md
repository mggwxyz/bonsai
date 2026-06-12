---
title: Quickstart
---

# Quickstart

## The One Command

Check prerequisites first — this reports anything missing along with the fix:

```bash
bonsai doctor --preflight
```

Then run:

```bash
bonsai start-here git@github.com:org/my-app.git my-app
```

`<name>` becomes the workspace folder. `start-here` runs preflight checks,
clones the repo, guides you through config, offers shell integration,
creates your first worktree, starts Caddy (or falls back to a direct port),
and opens the app.

### What You'll Be Asked

**Guided config review** — Bonsai auto-detects your stack
(Node/Python/Go/Rails/Docker Compose, etc.) and opens a terminal review
menu. Each section explains what the setting controls. Press Enter to save
the detected values, or choose a section number to revise it and return to
the review screen:

- **Project identity** — workspace name and base branch
- **Lifecycle commands** — install, setup, and start commands
- **Shared files** — whether `.env` is symlinked or copied into each worktree
- **Primary service** — service name, port environment variable, base port,
  and local URL template

**Shell integration offer** — asked once per machine. Accepting installs the
checkout wrapper for your selected shell (`zsh`, `bash`, or `fish`). After that
you need to open a new shell (or source the updated shell config) before
`bonsai checkout <branch>` can cd into a worktree.

### What Success Looks Like

```
✅ done — your app is at <url>
```

The URL is either a Caddy HTTPS URL (`https://<slug>.<app>.localhost`) or a
direct port URL (`http://localhost:<port>`) when Caddy isn't installed —
both are expected and work fine.

### If Something's Missing

- **git not found** — `brew install git`, then re-run `bonsai start-here`
- **Shell integration skipped** — `bonsai install-shell zsh` (or `bash` or
  `fish`), open a new shell, then `bonsai checkout <branch>` to cd in
- **App not up yet** — `bonsai up <branch>` to start it in the background,
  then `bonsai open <branch>` to open the URL

## The Manual Flow

Prefer separate steps? Clone a repository into a Bonsai workspace:

```bash
bonsai clone git@github.com:org/my-app.git my-app
```

`clone` discovers the repository default branch and uses that branch name
for the initial checkout directory. If Bonsai cannot find a `.bonsai.toml`,
it starts the same guided review menu and writes a local workspace config.
Pass `--no-interactive` to fail instead of prompting.

Or initialize an existing checkout that already has `.bonsai.toml`:

```bash
cd my-app/main
bonsai init
```

This adopts the checkout as the default worktree, imports existing sibling
git worktrees, writes Bonsai workspace state beside it, and renders
generated files. The checkout directory must match the current branch,
matching Bonsai's `my-app/main` workspace layout. If state already exists
but is missing sibling worktrees, rerunning `bonsai init` reconciles state
from the existing config.

Create a branch worktree and switch into it:

```bash
bonsai add ma-123-implement-auth
bonsai checkout ma-123-implement-auth
```

`checkout` needs [shell integration](shell-integration.md) installed once
per machine:

```bash
bonsai install-shell zsh   # or bash, or fish
```

You can also prepare a GitHub pull request directly:

```bash
bonsai add --pr 123
bonsai checkout --pr 123
```

Start the configured dev command and open the app:

```bash
bonsai start
bonsai open
```

## Where to Go Next

- [Worktrees](worktrees.md) — add, checkout, remove, move, PR worktrees, and PR-aware cleanup
- [Running Apps](running-apps.md) — foreground start, background up/down, stop, restart, exec, each, logs
- [Ports & URLs](urls-and-ports.md) — port slots, Caddy routing, open, urls, ports
- [Workspace Views](workspace-views.md) — list, list --all, ps, status, and agent context
