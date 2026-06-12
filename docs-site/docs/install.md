---
title: Install
---

# Install

Bonsai is distributed through a personal Homebrew tap.

```bash
brew tap mggwxyz/tap
brew install bonsai
```

Check the installed version:

```bash
bonsai --version
```

## Requirements

Bonsai is currently macOS-first and expects:

- Homebrew
- Python 3.12, installed by the Homebrew formula
- git
- Caddy, installed by the Homebrew formula
- GitHub CLI (`gh`) for PR worktrees and PR-aware cleanup

Check prerequisites at any time — this reports anything missing along with
the fix:

```bash
bonsai doctor --preflight
```

## From Source

For local development:

```bash
uv sync --dev
uv run bonsai --help
uv run pytest
```
