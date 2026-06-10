---
title: Configuration
---

# Configuration

Each managed workspace uses one `.bonsai.toml`. Bonsai looks for a local workspace config first, then falls back to a repo config inside the default worktree:

```text
my-app/.bonsai.toml
my-app/main/.bonsai.toml
```

Use the workspace root config for local-only Bonsai settings. Move or copy the file into the repo when teammates should share the same ports, commands, and URL templates.

When Bonsai creates this file interactively, it first shows a terminal review
menu with explanations for project identity, lifecycle commands, shared files,
and the primary service. Choose a section number to change those values, or save
the detected defaults when they look right. Pass `--no-interactive` to
`bonsai clone` to fail instead of prompting when no config exists.

## Example

```toml
name = "my-app"
base_branch = "main"

[workspace]
default_parent = "~/Projects"

[commands]
install = "npm install"
setup = "npm run db:migrate"
start = "npm run dev"

[[shared_files]]
source = ".env"
target = ".env"

[[env]]
name = "COMPOSE_PROJECT_NAME"
value = "my-app-${slug}"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.my-app.localhost"
```

## Keys

### Top Level

- `name` (required) — workspace name. Used in hostnames and the global Caddy
  snippet directory, so it must be unique per machine.
- `base_branch` — branch new worktrees are created from.

### `[workspace]`

- `default_parent` (default `~/Projects`) — written by the guided setup and
  reserved for future use; `bonsai clone` currently creates the workspace
  under the directory you run it from.

### `[commands]`

`install`, `setup`, and `start`, each with optional `pre*` and `post*` hooks
(`preinstall`, `postinstall`, `presetup`, `postsetup`, `prestart`,
`poststart`). All are shell command strings. `install` and `setup` run while
Bonsai prepares clone and branch worktrees; `start` runs through
`bonsai start`, `bonsai up`, or `bonsai add <branch> --start`. See
[Running Apps](running-apps.md) for execution and log behavior.

### `[[shared_files]]`

Files symlinked from the default worktree into each branch worktree —
typically a local `.env` that should not be copied per branch.

- `source` — path relative to the default worktree.
- `target` — path relative to each branch worktree.
- `mode` (default `symlink`) — only `symlink` is supported.

### `[[env]]`

Extra entries for the generated `.env.local`, with `name` and `value`.
Values may use template values like `${slug}`. If a worktree uses Docker
Compose, set `COMPOSE_PROJECT_NAME` here so `bonsai remove` and applied
`bonsai cleanup` can run `docker compose -p <project> down` for the correct
branch-specific project.

### `[[services]]`

- `name` (required) — unique service name.
- `port_env` (required) — environment variable written to `.env.local`.
- `base_port` (required) — port for slot 0; each worktree listens on
  `base_port + slot`.
- `public` (default `true`) — public services get Caddy routes and require a
  `url`.
- `primary` (default `false`) — exactly one public service must be primary;
  it is the target of `bonsai open`.
- `url` — local URL template, for example
  `https://${slug}.my-app.localhost`.

### `[caddy]`

- `auto_install` (default `true`) — let Bonsai install Caddy through
  Homebrew when missing.
- `auto_start` (default `true`) — let Bonsai start and reload Caddy when
  routing changes.
- `root_caddyfile`, `snippets_dir` — **deprecated**. Routing is now
  machine-global under `~/.bonsai`. These keys are still parsed so old
  configs load without error, but they are ignored for routing and no longer
  written by `bonsai init`. `bonsai sync --apply` migrates any old
  per-workspace `Caddyfile` and `caddy.d/` to the global layout.

### `[browser_extension]`

- `extension_id` — a 32-character Chrome extension ID (lowercase `a`–`p`).
  Enables `bonsai open --label <text>` to open labeled tabs through the
  extension.

## Generated Values

Bonsai expands branch-specific values into generated files. The most common template value is `${slug}`, which is derived from the branch name and safe to use in ports, URLs, and environment names.

Configured lifecycle commands run from the target worktree with Bonsai's generated `.env.local` values available in the subprocess environment.

## Repairing Generated Files

Use `sync` to compare generated files against current config and state:

```bash
bonsai sync
bonsai sync --apply
```

The dry run reports missing or stale generated files. `--apply` writes
missing or stale generated files, removes stale snippets from
`~/.bonsai/caddy.d/<app>/`, updates the global `~/.bonsai/Caddyfile`, and
reloads Caddy when local routing changed.
