# Guided Config Init Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add guided `.bonsai.toml` creation for missing clone config and an explicit `bonsai init` command.

**Architecture:** Add a focused onboarding module that detects package defaults and renders starter TOML. Keep interactive prompting in the CLI, and let the clone workflow accept an optional initializer callback when config is missing.

**Tech Stack:** Python 3.12+, Typer/Rich CLI, existing dataclass config models, pytest, ruff, Homebrew formula release.

---

## File Structure

- Create `src/bonsai/onboarding.py`: starter config dataclasses, package default detection, TOML rendering, and file writing.
- Modify `src/bonsai/workflows.py`: accept an optional config initializer in `execute_clone`.
- Modify `src/bonsai/cli.py`: add guided prompts, `clone --interactive/--no-interactive`, and `bonsai init`.
- Modify `tests/test_onboarding.py`: pure onboarding tests.
- Modify `tests/test_workflows.py`: clone initializer behavior.
- Modify `tests/test_cli.py`: CLI dispatch and init command behavior.
- Modify `README.md`, `pyproject.toml`, `src/bonsai/__init__.py`, and `Formula/bonsai.rb` for docs/version release.

## Tasks

### Task 1: Pure Starter Config Generation

- [ ] Write failing tests for package default detection and rendered TOML loading through `load_config`.
- [ ] Implement `ProjectDefaults`, `StarterConfig`, `detect_project_defaults`, `render_starter_config`, and `write_starter_config`.
- [ ] Run `uv run --no-sync pytest tests/test_onboarding.py -v`.
- [ ] Commit with `feat: render starter bonsai config`.

### Task 2: Clone Missing Config Initializer

- [ ] Write a failing workflow test proving `execute_clone` calls an initializer when `.bonsai.toml` is missing.
- [ ] Add an optional initializer callback to `execute_clone`.
- [ ] Run `uv run --no-sync pytest tests/test_workflows.py -v`.
- [ ] Commit with `feat: initialize missing clone config`.

### Task 3: Interactive CLI Wiring

- [ ] Write failing CLI tests for default clone initializer, `--no-interactive`, and `bonsai init`.
- [ ] Add CLI prompts, `clone --interactive/--no-interactive`, and `init`.
- [ ] Run `uv run --no-sync pytest tests/test_cli.py -v`.
- [ ] Commit with `feat: guide bonsai config setup`.

### Task 4: Release Polish

- [ ] Bump version to `0.1.2`.
- [ ] Update README with `bonsai init` and missing-config behavior.
- [ ] Update source formula tag to `v0.1.2`.
- [ ] Run full verification: pytest, ruff, formula style, build, CLI help/version.
- [ ] Commit with `chore: release 0.1.2`.

### Task 5: Publish Homebrew

- [ ] Push source main and tag `v0.1.2`.
- [ ] Update `mggwxyz/homebrew-tap` formula to `v0.1.2`.
- [ ] Run `brew update-python-resources --ignore-non-pypi-packages mggwxyz/tap/bonsai`.
- [ ] Run `brew style`, `brew audit`, `brew upgrade`, `brew test`, and installed CLI smoke tests.
