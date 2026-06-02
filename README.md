# bonsai

Bonsai is a macOS-first CLI for managing parallel local development workspaces with git worktrees, unique ports, generated `.env.local` files, and Caddy HTTPS URLs.

## What Bonsai Manages

- A workspace root with one default worktree and any number of managed branch worktrees.
- Stable per-worktree port slots, generated `.env.local` files, and optional Caddy HTTPS snippets.
- Lifecycle commands for install, setup, and start, with optional pre/post hooks,
  live output, and timestamped logs.
- Shell checkout, editor/browser post-add automation, and agent-friendly context output.
- Workspace inspection through rich text and JSON `list`, `status`, and `context` views.
- Process-aware port ownership inspection for configured worktree services.
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

## Usage

```bash
bonsai clone git@github.com:org/my-app.git my-app
bonsai init
bonsai add ma-123-implement-auth
bonsai checkout ma-123-implement-auth
bonsai remove ma-123-implement-auth
bonsai start
bonsai start ma-123-implement-auth
bonsai stop
bonsai stop ma-123-implement-auth
bonsai restart ma-123-implement-auth
bonsai logs
bonsai logs ma-123-implement-auth --command start
bonsai open
bonsai open ma-123-implement-auth
bonsai open ma-123-implement-auth --service api
bonsai urls
bonsai urls ma-123-implement-auth --service api
bonsai urls --diagnose https://api-ma-123-implement-auth.my-app.localhost
bonsai context
bonsai list
bonsai list --format json
bonsai ports
bonsai ports --format json
bonsai ps
bonsai status
bonsai status --format json
bonsai sync
bonsai repair
bonsai repair --apply
bonsai repair-ports
bonsai repair-ports --apply
bonsai repair-ports --format json
bonsai cleanup
bonsai doctor
bonsai doctor --format json
bonsai doctor --apply
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

Configured `install` and `setup` commands, plus their optional `pre*` and
`post*` hooks, run from the target worktree with Bonsai's generated `.env.local`
values available in the subprocess environment.
Their output is streamed live and saved as timestamped lifecycle logs under
`.bonsai/logs/<worktree-slug>/`. Log kinds are `preinstall`, `install`,
`postinstall`, `presetup`, `setup`, `postsetup`, `prestart`, `start`, and
`poststart`; when multiple runs share a timestamp, Bonsai suffixes and orders
the log files consistently.

`bonsai start [branch]` runs the configured `[commands].start` command in the
target worktree, with optional `prestart` and `poststart` hooks running before
and after the foreground start command. With no branch, it uses the current
worktree; with an argument, it accepts a branch name, worktree directory, or
worktree slug. The process runs in the foreground with values from the generated
`.env.local` added to the environment. Output streams live and is saved as a
managed `start` log, but Bonsai does not daemonize, supervise, or automatically
restart the process.

`bonsai stop [branch]` terminates listener processes on the selected worktree's
configured service ports when ownership can be matched to that worktree by
process cwd. External or unknown owners are skipped unless `--force` is passed.
Use `bonsai stop --all` to stop matching listeners for every worktree.
`bonsai restart [branch]` runs the same safe stop flow, then starts the selected
worktree in the foreground.

`bonsai logs [branch]` prints the latest managed lifecycle log for a worktree.
With no branch, it resolves the current worktree; with an argument, it accepts
the same branch name, worktree directory, or slug forms as `bonsai start`. Use
`--command install`, `--command setup`, `--command start`, or a hook kind such as
`--command preinstall` to filter to the latest log for a specific command kind.

`bonsai add <branch> --editor --open --start` runs explicit post-add actions
after the worktree is prepared. `--editor` opens the new worktree using
`$VISUAL`, `$EDITOR`, or `code`; `--open` opens the prepared branch's primary
local URL; and `--start` runs the configured start command in the foreground.
When multiple flags are passed, Bonsai opens the editor, opens the browser, then
starts the app.
Pass `--base-branch <branch>` to create a missing branch worktree from a
different base branch for that command.

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
the matching worktree. The lookup accepts the branch name, worktree directory,
or worktree slug. If no exact worktree matches, `checkout` resolves a unique
fuzzy match before preparing a missing branch. If multiple existing worktrees
match, Bonsai asks for a more specific name.

Bonsai's zsh integration also registers shell completion for managed worktree
aliases on `checkout`, `start`, `logs`, `open`, `remove`, and `move`. If you add
the integration manually, place `eval "$(bonsai shell-init zsh)"` after zsh
completion is initialized.

If `bonsai checkout <branch>` does not find a managed worktree, Bonsai prepares
one first. It fetches `origin`, uses the remote branch when it exists, or creates
a new branch from the configured base branch before changing directories through
the shell integration. Pass `--base-branch <branch>` to create a missing branch
from a different base branch for that checkout.

`bonsai remove <worktree>` removes a managed worktree and its directory. Bonsai
refuses to remove a worktree with uncommitted changes unless you pass `--force`.
If the worktree has a root-level `compose.yaml`, `compose.yml`,
`docker-compose.yaml`, or `docker-compose.yml`, Bonsai first runs
`docker compose -p <project> down`, using `.env.local` `COMPOSE_PROJECT_NAME`
when present and the worktree folder name otherwise. If Compose teardown fails,
removal stops before the git worktree is removed.

`bonsai move <worktree> <new-folder>` renames a managed worktree directory.
The lookup accepts a branch name, worktree directory, or worktree slug. Bonsai
uses `git worktree move` underneath, updates `.bonsai/state.json`, and rewrites
generated files so path-dependent template values stay current. The default
worktree cannot be moved.

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
URL in your default browser. Pass a branch name, worktree directory, or slug to
open a different worktree's primary URL. Pass `--service <name>` to open a
non-primary public service URL such as an API route.

`bonsai urls` prints configured public service URLs with route diagnostics for
the root Caddyfile, generated Caddy snippet, Caddy validation, app listener,
TLS, and local CA trust guidance. Filter by worktree, by `--service <name>`, or
use `--diagnose <url>` when a specific URL is not working. Use
`bonsai urls --format json` for automation.

`bonsai list` prints the default worktree and every managed worktree with
branch, path, slot, kind, generated `.env.local` status, service ports, and
service URLs. The default text output is a rich table. Use
`bonsai list --format json` for a machine-readable workspace overview.

`bonsai ports` prints every configured service port with listener ownership
metadata from `lsof` when available. Each port is classified as `free`, `owned`
by the matching worktree, `conflict` with another process or worktree, or
`unknown` when the port is listening but the owner cannot be identified. Use
`bonsai ports --format json` for automation. `bonsai ps` shows the same data
filtered to ports with listeners.

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

`bonsai repair-ports` previews slot changes for branch worktrees whose configured
service ports conflict with another process or worktree. A listener whose cwd is
inside the matching worktree is treated as expected and does not trigger a slot
change. Bonsai reserves the default worktree slot and existing non-conflicted
branch slots, then proposes the lowest conflict-free slot for each affected
branch. It is a dry run by default. Use
`bonsai repair-ports --apply` to write the proposed slots and regenerate
Bonsai-managed files; use `bonsai repair-ports --format json` for
machine-readable plans.

`bonsai doctor` checks workspace state, config, git worktrees, generated files,
Caddy files, Caddy availability, stale Docker Compose network references, and
owner-aware configured service port conflicts. Use
`bonsai doctor --format json` for machine-readable checks and
`bonsai doctor --apply` to run safe workspace repairs: state repair, generated
file sync, stopped stale Docker Compose container removal, and configured Caddy
bootstrap when possible. Structural state drift can still be previewed with
`bonsai repair`.
