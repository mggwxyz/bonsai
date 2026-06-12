# bonsai

Bonsai is a macOS-first CLI for managing parallel local development workspaces with git worktrees, unique ports, generated `.env.local` files, and Caddy HTTPS URLs.

**Documentation: [mggwxyz.github.io/bonsai](https://mggwxyz.github.io/bonsai/)** — quickstart, guides, command reference, and troubleshooting.

## Install

```bash
brew tap mggwxyz/tap
brew install bonsai
```

Bonsai is published from the personal Homebrew tap at `mggwxyz/homebrew-tap`.

## Start Here (5-Minute First Run)

macOS with Homebrew, git, and zsh, bash, or fish. Check prerequisites first — this reports anything missing along with the fix:

```bash
bonsai doctor --preflight
```

Then run the one command:

```bash
bonsai start-here git@github.com:org/my-app.git my-app
```

This runs preflight checks, clones the repo, guides you through config, offers shell integration, creates your first worktree, starts Caddy (or falls back to a direct port), and opens the app:

```
✅ done — your app is at <url>
```

The URL is either a Caddy HTTPS URL (`https://<slug>.<app>.localhost`) or a direct port URL (`http://localhost:<port>`) when Caddy isn't installed — both are expected and work fine. Prefer the manual steps? `clone` → `init` → `add` → `open`, covered in the [Quickstart](https://mggwxyz.github.io/bonsai/docs/quickstart).

## What Bonsai Manages

- A workspace root with one default worktree and any number of managed branch worktrees.
- Stable per-worktree port slots, generated `.env.local` files, and optional Caddy HTTPS routing via a machine-global config.
- Lifecycle commands for install, setup, add, remove, and start, with optional hooks, live output, and timestamped logs.
- Shell checkout with interactive worktree picking, editor/browser post-add automation, and agent-friendly context output.
- Workspace inspection through rich text and JSON `list`, `list --all`, `status`, and `ps` views.
- Process-aware port ownership inspection for configured worktree services.
- State repair, generated-file sync, workspace diagnostics, PR worktrees, PR-aware cleanup, and Docker Compose teardown during removal.

## Usage

```bash
bonsai add ma-123-implement-auth      # prepare a branch worktree
bonsai add --pr 123                   # or prepare a PR worktree
bonsai checkout ma-123-implement-auth # cd in (needs shell integration)
bonsai start                          # run the dev command in the foreground
bonsai up                             # or run it in the background
bonsai exec -- npm test               # run a command in the current worktree
bonsai each -- git status --short     # run a command across all worktrees
bonsai open                           # open the branch's local URL
bonsai list                           # see every worktree
bonsai list --all                     # see registered workspaces
bonsai ps                             # see tracked app processes
bonsai status                         # facts for the current worktree
bonsai doctor                         # check workspace health
bonsai cleanup --apply                # remove worktrees with merged PRs
```

Where to read more:

- [Worktrees](https://mggwxyz.github.io/bonsai/docs/worktrees) — `add`, `checkout`, `remove`, `move`, `cleanup`
- [Running Apps](https://mggwxyz.github.io/bonsai/docs/running-apps) — `start`, `up`, `stop`, `restart`, `logs`
- [Ports & URLs](https://mggwxyz.github.io/bonsai/docs/urls-and-ports) — port slots, Caddy routing, `open`, `urls`, `ports`
- [Workspace Views](https://mggwxyz.github.io/bonsai/docs/workspace-views) — `list` and `status` (text and JSON)
- [Shell Integration](https://mggwxyz.github.io/bonsai/docs/shell-integration) — checkout and completion setup
- [Command Reference](https://mggwxyz.github.io/bonsai/docs/commands) — every command and option
- [Troubleshooting](https://mggwxyz.github.io/bonsai/docs/troubleshooting) — `doctor`, `repair`, `repair-ports`, `sync`, common symptoms

## Configuration

Each managed workspace uses one `.bonsai.toml`, looked up at the workspace root first, then inside the default worktree (`my-app/.bonsai.toml`, then `my-app/main/.bonsai.toml`). Keep it at the root for local-only settings; move it into the repo to share ports, commands, and URL templates with teammates. Add `.bonsai.local.toml` beside the selected config for machine-local overrides, and keep that local file in `.gitignore`.

```toml
name = "my-app"
base_branch = "main"

[commands]
install = "npm install"
setup = "npm run db:migrate"
postadd = "npm run seed"
preremove = "npm run cleanup-worktree"
start = "npm run dev"

[[shared_files]]
source = ".env"
target = ".env"
mode = "copy"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.my-app.localhost"
```

Every key is documented in the [Configuration guide](https://mggwxyz.github.io/bonsai/docs/configuration).
Bonsai-generated `.env.local` files include service ports, `SLOT`, and stable
`BONSAI_*` values such as `BONSAI_BRANCH`, `BONSAI_SLOT`,
`BONSAI_WORKTREE_PATH`, `BONSAI_ROOT_PATH`, and `BONSAI_PRIMARY_URL`.

## Shell Integration

`bonsai checkout <worktree>` needs shell integration because a CLI child process cannot change its parent shell directory:

```bash
bonsai install-shell zsh   # or bash, or fish
```

Open a new shell, and `bonsai checkout <branch>` changes into the matching worktree. Details and completion setup are in the [Shell Integration guide](https://mggwxyz.github.io/bonsai/docs/shell-integration).

## Local Development

```bash
uv sync --dev
uv run bonsai --help
uv run pytest
uv run ruff check .
```

## Documentation Site

The Docusaurus docs site lives in `docs-site/` and deploys to GitHub Pages through the `Docs` workflow.

```bash
cd docs-site
npm install
npm run generate:cli
npm start
```

`npm run build` regenerates the CLI command reference from the Typer app before building the static site.

## Release

Run the release script from a clean `main` checkout:

```bash
uv run python scripts/release.py 0.6.0 --dry-run
uv run python scripts/release.py 0.6.0
```

The script updates the package version, commits `chore: release <version>`, tags and pushes `v<version>`, copies the formula to the `mggwxyz/homebrew-tap` checkout, commits the tap formula, and pushes the tap. Pass `--tap-repo` if the tap checkout is not discoverable through Homebrew.
