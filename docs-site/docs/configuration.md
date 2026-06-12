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

For machine-local overrides, add `.bonsai.local.toml` next to the selected
`.bonsai.toml`. Bonsai deep-merges tables such as `[commands]`, `[run]`,
`[caddy]`, and `[browser_extension]`; arrays such as `[[services]]`, `[[env]]`,
and `[[shared_files]]` replace the shared array when present in the local file.
Add `.bonsai.local.toml` to `.gitignore` because it is intended for personal
paths, ports, browser extension IDs, and local command overrides.

When the shared config lives in the default worktree, Bonsai also checks for a
workspace-root local override:

```text
my-app/main/.bonsai.toml        # shared repo config
my-app/.bonsai.local.toml       # machine-local workspace override
my-app/main/.bonsai.local.toml  # optional repo-local override
```

When Bonsai creates this file interactively, it first shows a terminal review
menu with explanations for project identity, lifecycle commands, shared files,
and the primary service. Choose a section number to change those values, or save
the detected defaults when they look right. Pass `--no-interactive` to
`bonsai clone` to fail instead of prompting when no config exists.

## Example

```toml
name = "my-app"
base_branch = "main"

[commands]
install = "npm install"
setup = "npm run db:migrate"
postadd = "npm run seed"
preremove = "npm run cleanup-worktree"
start = "npm run dev"

[run]
mode = "concurrent"

[[shared_files]]
source = ".env"
target = ".env"
mode = "symlink"

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

### `[run]`

- `mode` (default `concurrent`) — `concurrent` lets multiple worktrees have
  tracked `bonsai up` app processes at the same time. Use `single` for projects
  that cannot run multiple worktrees concurrently because they share singleton
  local resources, fixed dev services, or other process-global state.

In `single` mode, `bonsai up` and `bonsai restart --detach` refuse to start a
worktree when another worktree in the same Bonsai workspace has a live tracked
PID. Bonsai reports the running worktree and suggests `bonsai stop <name>` or
`bonsai stop --all`; it does not kill the other process automatically.
Foreground `bonsai start` is not tracked by this guard.

### `[commands]`

`install`, `setup`, `postadd`, `preremove`, and `start`, plus optional `pre*`
and `post*` hooks for install, setup, and start (`preinstall`, `postinstall`,
`presetup`, `postsetup`, `prestart`, `poststart`). All are shell command
strings. `install`, `setup`, and `postadd` run while Bonsai prepares clone,
branch, and PR worktrees. `preremove` runs before removal or applied cleanup.
`start` runs through `bonsai start`, `bonsai up`, or
`bonsai add <branch> --start`. See [Running Apps](running-apps.md) for execution
and log behavior.

### `[[shared_files]]`

Files shared from the default worktree into each branch worktree. Use
`mode = "symlink"` for files that should stay identical and `mode = "copy"` for
seed-once files that may diverge per worktree.

- `source` — path relative to the default worktree.
- `target` — path relative to each branch worktree.
- `mode` (default `symlink`) — `symlink` or `copy`.

Copy-mode shared files are copied only when the target is missing. After that,
Bonsai treats the file as worktree-local and will not overwrite local edits.
`bonsai sync --apply` recreates missing copy-mode targets but preserves existing
diverged copies.

### `.worktreeinclude`

Add `.worktreeinclude` to the default worktree root when every new branch
worktree should receive the same gitignored local files at the same relative
path. The file uses gitignore-style patterns:

```gitignore
.env.local
config/secrets.json
certs/local/**
```

Bonsai copies only matching files that already exist in the default worktree and
are ignored by Git. It skips common dependency and build directories such as
`node_modules`, `dist`, `build`, `.next`, `target`, and `coverage`.

Use `.worktreeinclude` for simple seed-once copies of ignored local files. Use
`[[shared_files]]` when you need an explicit source/target mapping, a symlink, or
a rule that should override `.worktreeinclude`. Explicit `[[shared_files]]`
entries win over generated `.worktreeinclude` copies. Like copy-mode
`[[shared_files]]`, `bonsai sync --apply` recreates missing `.worktreeinclude`
copies but does not overwrite existing diverged files.

### `[[env]]`

Extra entries for the generated `.env.local`, with `name` and `value`.
Values may use template values like `${slug}`. If a worktree uses Docker
Compose, set `COMPOSE_PROJECT_NAME` here so `bonsai remove` and applied
`bonsai cleanup` can run `docker compose -p <project> down` for the correct
branch-specific project. `BONSAI_*` names are reserved for Bonsai-provided
values and cannot be overridden by `[[env]]`.

### `[[services]]`

- `name` (required) — unique service name.
- `port_env` (required) — environment variable written to `.env.local`.
  It cannot be `SLOT` or one of Bonsai's reserved `BONSAI_*` names.
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

Retired keys from earlier versions (`[workspace] default_parent`, `[caddy]
root_caddyfile`, `[caddy] snippets_dir`) are ignored, so old configs load
without error.

### `[browser_extension]`

- `extension_id` — a 32-character Chrome extension ID (lowercase `a`–`p`).
  Enables `bonsai open --label <text>` to open labeled tabs through the
  extension.

## Generated Values

Bonsai expands branch-specific values into generated files. The most common
template value is `${slug}`, which is derived from the branch name and safe to
use in ports, URLs, and environment names. Bonsai also writes stable standard
environment variables to `.env.local` and makes them available to lifecycle
commands, `bonsai start`, `bonsai up`, and `bonsai exec`:

- `BONSAI_WORKSPACE_NAME` — configured workspace name.
- `BONSAI_BRANCH` — git branch for the target worktree.
- `BONSAI_SLUG` — URL/path-safe branch slug.
- `BONSAI_SLOT` — numeric Bonsai port slot.
- `BONSAI_WORKTREE_PATH` — absolute path to the target worktree.
- `BONSAI_ROOT_PATH` — workspace root containing all Bonsai worktrees.
- `BONSAI_DEFAULT_BRANCH` — workspace default branch.
- `BONSAI_PRIMARY_URL` — rendered URL for the primary public service, or empty
  when no primary URL is available.

Configured lifecycle commands run from the target worktree with Bonsai's generated `.env.local` values available in the subprocess environment.

## Repairing Generated Files

Use `sync` to compare generated files against current config and state:

```bash
bonsai sync
bonsai sync --apply
```

The dry run reports missing or stale generated files. `--apply` writes
missing or stale generated files, removes stale snippets from
`~/.bonsai/caddy.d/<app>/`, recreates missing copy-mode shared files, updates
the global `~/.bonsai/Caddyfile`, and reloads Caddy when local routing changed.
