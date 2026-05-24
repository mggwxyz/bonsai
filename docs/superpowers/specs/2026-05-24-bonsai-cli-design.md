# Bonsai CLI Design

Date: 2026-05-24

## Summary

Bonsai is a macOS-first Python CLI for setting up and managing parallel local development workspaces with git worktrees. It turns a repository that contains a committed `.bonsai.toml` into a managed workspace where each branch gets its own worktree, non-conflicting ports, generated environment overrides, and Caddy-backed local HTTPS URLs.

The first version is based on the existing `authentic` workflow:

- create a branch-specific worktree from an existing or new git branch
- allocate deterministic, non-conflicting local ports
- write `.env.local` for the worktree
- generate Caddy route snippets per service
- optionally install dependencies, run migrations, start the app, and open the URL
- clean up worktrees after merged pull requests

## Goals

- Provide a reusable CLI instead of project-specific shell scripts.
- Support a from-scratch `bonsai clone` flow for new managed workspaces.
- Keep repository-specific behavior in a committed `.bonsai.toml`.
- Make the default developer flow fast: clone once, then `bonsai add <branch>`.
- Support multiple local URLs per worktree through configurable services.
- Install through Homebrew from a personal tap for v1.
- Keep v1 intentionally macOS-only.

## Non-Goals

- Support Linux or Windows in v1.
- Adopt or migrate an existing unmanaged workspace in v1.
- Replace Docker, Caddy, git, GitHub CLI, package managers, or editor tools.
- Infer arbitrary project setup without `.bonsai.toml`.
- Build a GUI or background daemon.

## Command Surface

### `bonsai clone <git-url> <name>`

Creates a fresh managed workspace.

Example:

```bash
bonsai clone git@github.com:org/authentic.git authentic
```

Expected layout:

```text
~/Projects/authentic/
  authentic-main/
  Caddyfile
  caddy.d/
  .bonsai/
    state.json
```

The command:

1. verifies macOS prerequisites
2. installs Caddy with Homebrew if possible and missing
3. clones the repository into `<name>-main`
4. reads `<name>-main/.bonsai.toml`
5. validates that `main_worktree` is absent or matches `<name>-main`
6. writes a managed root `Caddyfile`
7. starts or reloads Caddy where possible
8. records workspace metadata in `.bonsai/state.json`
9. prints the next useful commands

If `.bonsai.toml` is missing, `bonsai clone` fails with a clear message explaining that the repository must commit one before Bonsai can manage it. A later version can add `bonsai init`.

### `bonsai add <branch>`

Creates a worktree for a branch.

Example:

```bash
bonsai add MB-2036-multi-worktree-port-slots
```

The command:

1. locates the managed workspace from the current directory
2. fetches `origin`
3. creates a worktree for an existing remote branch, or creates a new branch from the configured base branch
4. symlinks configured shared files such as `.env`
5. allocates the lowest available slot
6. computes service ports from each service `base_port + slot`
7. writes `.env.local`
8. writes one Caddy snippet per configured public service URL
9. reloads Caddy if possible
10. runs the configured install command unless disabled by a flag
11. optionally opens the editor, starts the app, and opens the primary URL

The v1 branch argument is a full branch name. Jira-ticket shorthand can be added later through config, but the initial behavior is exact and unambiguous.

### `bonsai start [branch]`

Runs the configured development start flow for a worktree. If no branch is supplied, the current worktree is used.

For projects with a migration command, Bonsai runs migration first when configured, then runs the start command. For v1, process supervision is delegated to the user's terminal, editor, or workspace tool rather than managed by a daemon.

### `bonsai list`

Prints managed worktrees with branch, slot, path, ports, and URLs.

### `bonsai sync`

Re-packs slot assignments for surviving worktrees and regenerates `.env.local` plus Caddy snippets.

Default mode is dry-run. `--apply` writes changes.

Bonsai refuses to re-pack slots while matching Docker Compose projects are running unless an explicit force flag is provided.

### `bonsai cleanup`

Removes worktrees whose pull requests are merged.

Default mode is dry-run. `--apply` removes worktrees.

Safety rules:

- never remove the main worktree
- skip dirty worktrees unless an explicit force flag is provided
- skip branches without a merged pull request
- tear down configured Docker Compose projects before removing a worktree when possible
- remove related Caddy snippets

### `bonsai doctor`

Checks local prerequisites and workspace health:

- macOS
- Homebrew
- Caddy
- git
- Python runtime
- optional GitHub CLI for cleanup
- root `Caddyfile`
- Caddy snippets
- port conflicts
- missing or invalid `.bonsai.toml`

## Configuration

Each managed repository commits `.bonsai.toml` at repository root.

Example:

```toml
name = "authentic"
base_branch = "main"
main_worktree = "authentic-main"

[workspace]
default_parent = "~/Projects"

[caddy]
auto_install = true
auto_start = true
root_caddyfile = "Caddyfile"
snippets_dir = "caddy.d"

[commands]
install = "yarn install"
start = "yarn dev"
migrate = "yarn docker:migrate --abort-on-container-exit --exit-code-from quiller-seed-migrate"

[[shared_files]]
source = ".env"
target = ".env"
mode = "symlink"

[[env]]
name = "COMPOSE_PROJECT_NAME"
value = "authentic-${slug}"

[[env]]
name = "DATABASE_URL"
value = "postgresql://username:password@localhost:${DB_PORT}/authentic"

[[env]]
name = "REDIS_URL"
value = "redis://127.0.0.1:${REDIS_PORT}"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"

[[services]]
name = "api"
port_env = "API_PORT"
base_port = 3333
url = "https://api-${slug}.authentic.localhost"

[[services]]
name = "db"
port_env = "DB_PORT"
base_port = 5555
public = false

[[services]]
name = "redis"
port_env = "REDIS_PORT"
base_port = 6379
public = false

[[services]]
name = "inspect"
port_env = "INSPECT_PORT"
base_port = 9229
public = false
```

### Template Variables

Supported v1 template variables:

- `${name}`: configured project name
- `${branch}`: raw git branch name
- `${slug}`: URL-safe branch slug
- `${slot}`: allocated numeric slot
- `${WORKTREE_PATH}`: absolute worktree path
- service port variables such as `${FRONTEND_PORT}` or `${API_PORT}`

## State

Bonsai stores workspace-level state outside the main worktree:

```json
{
  "version": 1,
  "name": "authentic",
  "main_worktree": "authentic-main",
  "repo_url": "git@github.com:org/authentic.git",
  "worktrees": {
    "MB-2036-multi-worktree-port-slots": {
      "path": "MB-2036-multi-worktree-port-slots",
      "slug": "mb-2036-multi-worktree-port-slots",
      "slot": 32
    }
  }
}
```

The state file is an optimization and audit trail, not the only source of truth. Commands also inspect git worktrees, `.env.local`, and existing Caddy snippets so Bonsai can recover from partial runs.

## Caddy Integration

Bonsai owns a root Caddyfile in the managed workspace:

```caddyfile
{
	local_certs
}

import /Users/michael/Projects/authentic/caddy.d/*.caddy
```

Each public service gets a generated snippet:

```caddyfile
https://mb-2036-multi-worktree-port-slots.authentic.localhost {
	tls internal
	reverse_proxy localhost:4232
}
```

For v1, Bonsai targets Homebrew-managed Caddy on macOS. It attempts to:

- install Caddy with `brew install caddy` when missing
- start Caddy with `brew services start caddy`
- reload Caddy after snippet changes
- print exact manual recovery commands if automatic setup fails

## Packaging

Bonsai is a Python package with a `bonsai` console script.

The recommended implementation stack:

- Python 3.12
- Typer or Click for CLI commands
- Rich for terminal output
- stdlib `tomllib` for config parsing
- stdlib `subprocess` for external commands
- pytest for tests

Distribution starts with a personal Homebrew tap:

```bash
brew tap <personal-github>/bonsai
brew install bonsai
```

The Bonsai source repository publishes tagged releases. The tap repository contains `Formula/bonsai.rb`, using Homebrew's Python virtualenv install flow.

## Error Handling

Bonsai should fail before making changes when required inputs are invalid:

- missing `.bonsai.toml`
- malformed config
- duplicate service names
- no primary public service when public services are configured
- multiple primary public services
- invalid URL templates
- branch worktree already exists
- target workspace already exists during `clone`

For multi-step operations, Bonsai should be resumable:

- if the worktree exists but `.env.local` is missing, regenerate it
- if `.env.local` exists but Caddy snippets are missing, regenerate snippets
- if Caddy reload fails, keep generated files and print the reload command
- if install fails, leave the worktree in place and print next steps

Dry-run mode is required for destructive or broad rewrite commands such as `sync` and `cleanup`.

## Testing

Tests should cover:

- config parsing and validation
- branch slug generation
- slot allocation from existing state and `.env.local` files
- `.env.local` rendering
- Caddy snippet rendering
- command planning for `clone`, `add`, `sync`, and `cleanup`
- safety behavior for dirty worktrees and running Docker Compose projects

Unit tests should avoid invoking real Homebrew, Caddy, Docker, or GitHub. Integration tests can use temporary git repositories and stub executable scripts on `PATH`.

## Migration From Current Scripts

The first implementation should preserve the useful behavior from the current `authentic` scripts:

- slot 0 is effectively reserved for the main worktree
- non-main worktrees use slots starting at 1
- ports are calculated as `base_port + slot`
- branch slugs are lowercase with unsupported URL characters replaced by `-`
- `.env` can be symlinked from the main worktree
- `.env.local` is generated per worktree
- Caddy snippets live in `caddy.d`
- cleanup is dry-run by default
- sync is dry-run by default

The implementation should not preserve hardcoded `authentic` paths, branch names, or port values. Those belong in `.bonsai.toml`.
