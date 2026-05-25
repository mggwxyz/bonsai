# bonsai

Bonsai is a macOS-first CLI for managing parallel local development workspaces with git worktrees, unique ports, generated `.env.local` files, and Caddy HTTPS URLs.

## Local Development

```bash
uv sync --dev
uv run bonsai --help
uv run bonsai --version
```

## Homebrew Install

```bash
brew tap mggwxyz/tap
brew install bonsai
```

Bonsai is published from the personal Homebrew tap at
`mggwxyz/homebrew-tap`.

## Repository Config

Each managed repository commits `.bonsai.toml` at its root. If the file is
missing during `bonsai clone`, Bonsai starts a short guided setup and writes a
starter config before continuing. Use `--no-interactive` to fail instead of
prompting.

```toml
name = "my-app"
base_branch = "main"

[workspace]
default_parent = "~/Projects"

[commands]
install = "npm install"
start = "npm dev"
migrate = "npm db:migrate"

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
bonsai list
bonsai sync
bonsai cleanup
bonsai doctor
```

`bonsai clone` discovers the repository default branch and uses that branch name
for the initial checkout directory.

`bonsai init` runs the same guided `.bonsai.toml` setup inside an existing
checkout. Review and commit the generated file so teammates get the same Bonsai
workspace behavior.

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
