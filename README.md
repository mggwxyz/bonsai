# bonsai

Bonsai is a macOS-first CLI for managing parallel local development workspaces with git worktrees, unique ports, generated `.env.local` files, and Caddy HTTPS URLs.

## Install

```bash
brew tap mggwxyz/bonsai
brew install bonsai
```

During local development:

```bash
uv sync --dev
uv run bonsai --help
```

## Repository Config

Each managed repository commits `.bonsai.toml` at its root.

```toml
name = "authentic"
base_branch = "main"

[workspace]
default_parent = "~/Projects"

[commands]
install = "yarn install"
start = "yarn dev"
migrate = "yarn docker:migrate --abort-on-container-exit"

[[env]]
name = "COMPOSE_PROJECT_NAME"
value = "authentic-${slug}"

[[services]]
name = "frontend"
port_env = "FRONTEND_PORT"
base_port = 4200
primary = true
url = "https://${slug}.authentic.localhost"
```

## Usage

```bash
bonsai clone git@github.com:org/authentic.git authentic
cd ~/Projects/authentic/main
bonsai add MB-2036-multi-worktree-port-slots
bonsai list
bonsai sync
bonsai cleanup
bonsai doctor
```

`bonsai clone` discovers the repository default branch and uses that branch name for the initial checkout directory.
