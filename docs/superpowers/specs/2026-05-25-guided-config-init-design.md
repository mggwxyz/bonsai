# Guided Config Init Design

## Summary

Bonsai should not dead-end when a repository does not already contain
`.bonsai.toml`. When `bonsai clone` finishes cloning the default branch and the
config file is missing, the CLI should enter a guided setup by default, write a
starter `.bonsai.toml`, and continue the workspace setup. Existing repositories
should get the same guided setup through `bonsai init`.

## Goals

- Make `bonsai clone <git-url> <name>` useful for repositories that have not
  adopted Bonsai yet.
- Keep `.bonsai.toml` as the committed, team-shared source of repo-specific
  Bonsai behavior.
- Provide a non-interactive escape hatch for scripts and CI.
- Keep the first wizard small: generate a useful starter config, not a perfect
  full project model.

## Non-Goals

- Auto-commit `.bonsai.toml`.
- Infer every service in a monorepo.
- Modify package scripts, Caddy, Docker, or app source files.
- Add a background daemon or graphical setup flow.

## Command Behavior

### `bonsai clone <git-url> <name>`

Default behavior is interactive. If `<default-branch>/.bonsai.toml` exists,
Bonsai uses it exactly as it does today.

If the file is missing, Bonsai prints a short explanation, prompts for starter
config values, writes `.bonsai.toml` in the default worktree, validates it, then
continues writing workspace state and Caddy files.

The command accepts `--no-interactive`. With that option, missing config remains
a clear failure so scripts do not hang waiting for prompts.

### `bonsai init`

Runs the same guided setup in the current repository checkout. It writes
`.bonsai.toml` at the current working directory and tells the user to review and
commit it. If the file already exists, the command fails unless `--force` is
provided.

## Wizard Fields

The wizard asks for:

- app name
- base branch
- install command
- start command
- whether to symlink `.env` into each worktree when `.env` exists
- primary service name
- service port environment variable
- base port
- local URL template

Package defaults are inferred from `package.json` and lockfiles when possible.
For example, a repo with `package.json` and a `dev` script defaults to an
install command such as `npm install` and a start command such as `npm run dev`.

## Error Handling

- Missing config with `--no-interactive` raises the existing config error.
- Existing `.bonsai.toml` during `bonsai init` fails unless `--force` is used.
- The generated file is validated with the existing config loader before clone
  continues.
- The wizard writes the config only after collecting all values.

## Testing

- Pure tests cover config rendering and package default detection.
- Workflow tests cover missing config invoking an initializer during clone.
- CLI tests cover `clone` passing an initializer by default, `--no-interactive`
  disabling it, and `init` writing `.bonsai.toml`.
