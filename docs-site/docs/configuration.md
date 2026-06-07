---
title: Configuration
---

# Configuration

Each managed workspace uses one `.bonsai.toml`. Bonsai looks for a local workspace config first, then falls back to a repo config inside the default worktree.

```text
my-app/.bonsai.toml
my-app/main/.bonsai.toml
```

Use the workspace root config for local-only Bonsai settings. Move or copy the file into the repo when teammates should share the same ports, commands, and URL templates.

When Bonsai creates this file interactively, it first shows a terminal review
menu with explanations for project identity, lifecycle commands, shared files,
and the primary service. Choose a section number to change those values, or save
the detected defaults when they look right.

## Example

```toml
name = "my-app"
base_branch = "main"

[workspace]
default_parent = "~/Projects"

[commands]
preinstall = "npm run preinstall"
install = "npm install"
postinstall = "npm run postinstall"
presetup = "npm run presetup"
setup = "npm db:migrate"
postsetup = "npm run postsetup"
prestart = "npm run prestart"
start = "npm dev"
poststart = "npm run poststart"

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

## Generated Values

Bonsai expands branch-specific values into generated files. The most common template value is `${slug}`, which is derived from the branch name and safe to use in ports, URLs, and environment names.

Configured `install`, `setup`, and `start` commands run from the target worktree with Bonsai's generated environment values available in the subprocess environment. Optional `preinstall`, `postinstall`, `presetup`, `postsetup`, `prestart`, and `poststart` hooks run around those lifecycle commands when configured. `install` and `setup` run while Bonsai prepares clone and branch worktrees. `start` runs through `bonsai start [branch]` or `bonsai add <branch> --start`; `poststart` runs after the foreground start command exits.

Bonsai streams lifecycle command output live and stores timestamped logs under `.bonsai/logs/<worktree-slug>/`. Use `bonsai logs [branch] --command install`, `setup`, `start`, or a hook kind such as `preinstall` to read the latest log for a command kind.

If a worktree uses Docker Compose, set `COMPOSE_PROJECT_NAME` through `[[env]]` so `bonsai remove` and applied `bonsai cleanup` can run `docker compose -p <project> down` for the correct branch-specific project.

## Repairing Generated Files

Use `sync` to compare generated files against current config and state:

```bash
bonsai sync
bonsai sync --apply
```

The dry run reports missing or stale generated files. `--apply` writes the changes and reloads Caddy when local routing changed.
