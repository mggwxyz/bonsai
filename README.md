# bonsai

Bonsai is a macOS-first CLI for managing parallel local development workspaces with git worktrees, unique ports, generated `.env.local` files, and Caddy HTTPS URLs.

## What Bonsai Manages

- A workspace root with one default worktree and any number of managed branch worktrees.
- Stable per-worktree port slots, generated `.env.local` files, and optional Caddy HTTPS snippets.
- Lifecycle commands for install, setup, and start, with live output and timestamped logs.
- Shell checkout, editor/browser post-add automation, and agent-friendly context output.
- Workspace inspection through rich text and JSON `list`, `status`, and `context` views.
- State repair, generated-file sync, workspace diagnostics, PR-aware cleanup, and Docker Compose teardown during removal.

## Local Development

```bash
uv sync --dev
uv run bonsai --help
uv run bonsai --version
```

## Documentation Site

The Docusaurus docs site lives in `docs-site/`.

```bash
cd docs-site
npm install
npm run generate:cli
npm start
```

`npm run build` regenerates the CLI command reference from the Typer app before
building the static site. GitHub Pages deployment is handled by the `Docs`
workflow.

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
bonsai start ma-123-implement-auth
bonsai logs
bonsai logs ma-123-implement-auth --command start
bonsai open
bonsai context
bonsai agent-guide
bonsai list
bonsai list --format json
bonsai status
bonsai status --format json
bonsai sync
bonsai repair
bonsai repair --apply
bonsai cleanup
bonsai doctor
```

To prepare a branch and immediately open the working context, pass explicit
post-add actions:

```bash
bonsai add ma-123-implement-auth --editor --open --start
```

`bonsai clone` discovers the repository default branch and uses that branch name
for the initial checkout directory.

`bonsai init` runs inside an existing checkout. If the checkout already has a
`.bonsai.toml` and is in the Bonsai layout (`my-app/main`, with the checkout
directory matching the current branch), Bonsai adopts it as the default
worktree, imports existing sibling git worktrees, writes workspace state beside
it, and renders generated files.
Otherwise it runs the guided `.bonsai.toml` setup. When run inside a managed
Bonsai workspace with an existing root or default-worktree config, it reconciles
state and generated files; without an existing config, it writes the local root
config.

Configured `install` and `setup` commands run from the target worktree with
Bonsai's generated `.env.local` values available in the subprocess environment.
Their output is streamed live and saved as timestamped lifecycle logs under
`.bonsai/logs/<worktree-slug>/`. Log kinds are `install`, `setup`, and `start`;
when multiple runs share a timestamp, Bonsai suffixes and orders the log files
consistently.

`bonsai start [branch]` runs the configured `[commands].start` command in the
target worktree. With no branch, it uses the current worktree; with an argument,
it accepts a branch name, worktree directory, or worktree slug. The process runs
in the foreground with values from the generated `.env.local` added to the
environment. Output streams live and is saved as a managed `start` log, but
Bonsai does not daemonize, supervise, or automatically restart the process.

`bonsai logs [branch]` prints the latest managed lifecycle log for a worktree.
With no branch, it resolves the current worktree; with an argument, it accepts
the same branch name, worktree directory, or slug forms as `bonsai start`. Use
`--command install`, `--command setup`, or `--command start` to filter to the
latest log for a specific command kind.

`bonsai add <branch> --editor --open --start` runs explicit post-add actions
after the worktree is prepared. `--editor` opens the new worktree using
`$VISUAL`, `$EDITOR`, or `code`; `--open` opens the prepared branch's primary
local URL; and `--start` runs the configured start command in the foreground.
When multiple flags are passed, Bonsai opens the editor, opens the browser, then
starts the app.

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
If the worktree has a root-level `compose.yaml`, `compose.yml`,
`docker-compose.yaml`, or `docker-compose.yml`, Bonsai first runs
`docker compose -p <project> down`, using `.env.local` `COMPOSE_PROJECT_NAME`
when present and the worktree folder name otherwise. If Compose teardown fails,
removal stops before the git worktree is removed.

`bonsai cleanup` is PR-aware cleanup for managed branch worktrees. It requires
the GitHub CLI (`gh`) to be installed and authenticated, checks each managed
branch for a merged pull request, and is a dry run by default. Use
`bonsai cleanup --apply` to remove eligible clean worktrees. Branches with no PR,
open PRs, unmerged closed PRs, or uncommitted changes are skipped; pass
`--force` with `--apply` to remove eligible dirty worktrees. Applied cleanup uses
the same removal lifecycle as `bonsai remove`, including Caddy snippet cleanup
and Docker Compose teardown when a removable worktree has a root-level Compose
file.

Run `bonsai open` from inside a worktree to open that worktree's primary local
URL in your default browser.

`bonsai list` prints the default worktree and every managed worktree with
branch, path, slot, kind, generated `.env.local` status, service ports, and
service URLs. The default text output is a rich table. Use
`bonsai list --format json` for a machine-readable workspace overview.

`bonsai status` prints the current worktree's Bonsai facts: workspace root,
config path, branch, slot, generated `.env.local` status, service ports, service
URLs, and recommended Bonsai commands. The default text output is optimized for
humans. Use `bonsai status --format json` when a script needs the current
worktree summary.

`bonsai context` prints the current worktree's agent-oriented Bonsai facts for
humans and automation: workspace root, branch, slot, generated `.env.local`
status, service ports, service URLs, and recommended Bonsai commands. Use
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

`bonsai repair` fixes structural state drift. It removes missing managed
worktree entries from `.bonsai/state.json` and repacks surviving managed slots in
a dry run by default. Paths that still exist but are not git worktrees are
reported as warnings and left in state. Use `bonsai repair --apply` to write
state, then run `bonsai sync --apply` to refresh generated `.env.local` and
Caddy files.

`bonsai doctor` checks workspace state, config, git worktrees, generated files,
Caddy files, Caddy availability, and configured service port conflicts.
Generated-file failures point to `bonsai sync --apply`; structural state drift
can be previewed with `bonsai repair`.
