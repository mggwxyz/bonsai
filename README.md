# bonsai

Bonsai is a macOS-first CLI for managing parallel local development workspaces with git worktrees, unique ports, generated `.env.local` files, and Caddy HTTPS URLs.

## Local Development

```bash
uv sync --dev
uv run bonsai --help
uv run bonsai --version
```

## Release

Run the release script from a clean `main` checkout:

```bash
uv run python scripts/release.py 0.1.3 --dry-run
uv run python scripts/release.py 0.1.3
```

The script updates the package version, commits `chore: release <version>`,
tags and pushes `v<version>`, copies the formula to the `mggwxyz/homebrew-tap`
checkout, commits the tap formula, and pushes the tap. Pass `--tap-repo` if the
tap checkout is not discoverable through Homebrew.

## Homebrew Install

```bash
brew tap mggwxyz/tap
brew install bonsai
```

Bonsai is published from the personal Homebrew tap at
`mggwxyz/homebrew-tap`.

## Bonsai Config

Each managed workspace uses one `.bonsai.toml`. Bonsai first looks for a local
workspace config at the Bonsai workspace root, then falls back to a repo config
inside the default worktree:

```text
my-app/.bonsai.toml
my-app/main/.bonsai.toml
```

Use the workspace root config for local-only Bonsai settings. Move or copy the
file into the repo if teammates should share the same ports, commands, and URL
templates. If no config exists during `bonsai clone`, Bonsai starts a short
guided setup and writes a local workspace config before continuing. Use
`--no-interactive` to fail instead of prompting.

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

## Usage

```bash
bonsai clone git@github.com:org/my-app.git my-app
bonsai init
bonsai add ma-123-implement-auth
bonsai checkout ma-123-implement-auth
bonsai remove ma-123-implement-auth
bonsai start
bonsai logs
bonsai open
bonsai context
bonsai agent-guide
bonsai list
bonsai sync
bonsai cleanup
bonsai doctor
```

`bonsai clone` discovers the repository default branch and uses that branch name
for the initial checkout directory.

`bonsai init` runs the same guided `.bonsai.toml` setup inside an existing
checkout. When run inside a managed Bonsai workspace, it writes the local root
config; otherwise it writes to the current checkout.

Configured `install` and `setup` commands run from the target worktree with
Bonsai's generated `.env.local` values available in the subprocess environment.
Their output is streamed live and also saved under
`.bonsai/logs/<worktree>/`.

`bonsai start [branch]` runs the configured `[commands].start` command in the
target worktree. With no branch, it uses the current worktree. The process runs
in the foreground with values from the generated `.env.local` added to the
environment. Output streams live and is saved as a managed `start` log, but
Bonsai does not daemonize, supervise, or automatically restart the process.

`bonsai logs [branch]` prints the latest managed lifecycle log for a worktree.
Use `--command install`, `--command setup`, or `--command start` to filter to
the latest log for a specific command kind.

## Shell Integration

`bonsai checkout <worktree>` needs shell integration because a CLI child process
cannot change its parent shell directory. Add this to `~/.zshrc`:

```zsh
eval "$(bonsai shell-init zsh)"
```

Or let Bonsai append the marked block for you:

```bash
bonsai install-shell zsh
```

After opening a new shell, `bonsai checkout ma-123-implement-auth` changes into
the matching worktree. The lookup accepts the branch name or the worktree
directory name.

If `bonsai checkout <branch>` does not find a managed worktree, Bonsai prepares
one first. It fetches `origin`, uses the remote branch when it exists, or creates
a new branch from the configured base branch before changing directories through
the shell integration.

`bonsai remove <worktree>` removes a managed worktree and its directory. Bonsai
refuses to remove a worktree with uncommitted changes unless you pass `--force`.

`bonsai cleanup` is PR-aware cleanup for managed branch worktrees. It requires
the GitHub CLI (`gh`) to be installed and authenticated, checks each managed
branch for a merged pull request, and is a dry run by default. Use
`bonsai cleanup --apply` to remove eligible clean worktrees. Branches with no PR,
open PRs, unmerged closed PRs, or uncommitted changes are skipped; pass
`--force` with `--apply` to remove eligible dirty worktrees.

Run `bonsai open` from inside a worktree to open that worktree's primary local
URL in your default browser.

`bonsai context` prints the current worktree's Bonsai facts for humans and
automation: workspace root, branch, slot, generated `.env.local` status,
service ports, service URLs, and recommended Bonsai commands. Use
`bonsai context --format json` when an AI agent or script needs exact
worktree-scoped ports and URLs.

`bonsai agent-guide` prints package-level guidance for AI agents and automation.
It tells agents to avoid guessing ports, avoid hardcoded localhost URLs, prefer
`bonsai start`, use `bonsai context --format json` for current values, repair
generated files with `bonsai sync --apply`, and diagnose workspace issues with
`bonsai doctor`. Use `bonsai agent-guide --format json` for a machine-readable
contract.

`bonsai sync` compares generated `.env.local` files and Caddy files against the
current config and state. It is a dry run by default. Use `bonsai sync --apply`
to write missing or stale generated files, remove stale Bonsai Caddy snippets,
and reload Caddy when local routing changed.

`bonsai doctor` checks workspace state, config, git worktrees, generated files,
Caddy files, Caddy availability, and configured service port conflicts. Failed
repairable checks point to `bonsai sync --apply`.
