# bonsai

Bonsai is a macOS-first CLI for managing parallel local development workspaces with git worktrees, unique ports, generated `.env.local` files, and Caddy HTTPS URLs.

## Local Development

```bash
uv sync --dev
uv run bonsai --help
uv run bonsai --version
```

## Planned Homebrew Install

```bash
brew tap mggwxyz/bonsai
brew install bonsai
```

The Homebrew install path will be available after the personal tap, the `v0.1.0` tag, and generated Homebrew Python resources are published.

## Repository Config

Each managed repository commits `.bonsai.toml` at its root.

```toml
name = "my-app"
base_branch = "main"

[workspace]
default_parent = "~/Projects"

[commands]
install = "yarn install"
start = "yarn dev"
migrate = "yarn docker:migrate --abort-on-container-exit"

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
bonsai add ma-123-implement-auth
bonsai list
bonsai sync
bonsai cleanup
bonsai doctor
```

These commands describe the target v1 workflow. The current CLI is still in progress, and commands such as `bonsai clone` and `bonsai add` may print readiness text before the full workflow is enabled.

`bonsai clone` discovers the repository default branch and uses that branch name for the initial checkout directory.
