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

## Example

```toml
name = "my-app"
base_branch = "main"

[workspace]
default_parent = "~/Projects"

[commands]
install = "npm install"
setup = "npm db:migrate"
start = "npm dev"

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

Configured `install`, `setup`, and `start` commands run from the target worktree with Bonsai's generated environment values available in the subprocess environment.

## Repairing Generated Files

Use `sync` to compare generated files against current config and state:

```bash
bonsai sync
bonsai sync --apply
```

The dry run reports missing or stale generated files. `--apply` writes the changes and reloads Caddy when local routing changed.
