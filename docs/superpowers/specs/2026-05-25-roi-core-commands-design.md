# ROI Core Commands Design

## Summary

Implement the next high-value Bonsai commands after workspace creation:
`bonsai start`, `bonsai doctor`, and `bonsai sync`. This batch should make a
managed workspace runnable and repairable without adding process supervision,
status/list enhancements, PR cleanup, Docker integration, or editor/browser
automation.

## Goals

- Make `bonsai start [branch]` run the configured development command from the
  right worktree with the generated environment.
- Make `bonsai doctor` explain whether a workspace is healthy and show concrete
  problems users can fix.
- Make `bonsai sync` show and optionally apply repairs for generated Bonsai
  files.
- Keep all behavior local, deterministic, and aligned with existing config,
  state, rendering, and runner patterns.

## Non-Goals

- Rich `bonsai list` or `bonsai status` output. That work is separate.
- Background process supervision, daemons, managed logs, or automatic restarts.
- GitHub or PR-aware cleanup.
- Docker Compose lifecycle management.
- Opening editors or browsers as part of this batch.
- Repacking slot assignments during sync. Sync repairs generated files for the
  current state instead of changing assigned ports.

## `bonsai start [branch]`

`bonsai start` resolves a target worktree. With no branch argument, it resolves
the current directory to the containing managed worktree. With a branch argument,
it accepts the same managed worktree lookup used by `bonsai checkout`: branch
name or worktree directory name. This command does not create missing worktrees;
users should use `bonsai checkout` or `bonsai add` for that.

The default worktree participates in start, doctor, and sync as the configured
default branch with slot `0`. Managed branch worktrees continue using their
stored state slots.

After resolving the worktree, Bonsai loads the workspace config and requires
`[commands].start`. If no start command is configured, the command fails with a
clear config error naming the config key.

Bonsai runs the configured start command in the foreground from the target
worktree. The subprocess inherits the generated `.env.local` values by parsing
the worktree's `.env.local` and overlaying those values onto the process
environment. The command should stream output directly to the user's terminal
and return the subprocess exit code.

This foreground behavior avoids daemon state and keeps control with the user's
terminal, tmux session, or editor.

## `bonsai doctor`

`bonsai doctor` is read-only. It checks the current Bonsai workspace and prints
a compact report with one row per check:

- workspace root and `.bonsai/state.json` are discoverable
- workspace config exists and loads successfully
- git is available
- the default worktree and each managed worktree path exists and is a git
  worktree
- the default worktree and each managed worktree has a generated `.env.local`
- root Caddyfile exists when public services are configured
- each expected public-service Caddy snippet exists
- Caddy is available when public services are configured
- configured service ports are not already occupied by unrelated processes

The command exits `0` when every check passes and exits `1` when any check
fails. Warnings may be shown for dirty worktrees, but dirty worktrees do not
fail doctor because they are normal during development.

Doctor should avoid mutating files, installing packages, starting services, or
reloading Caddy. When a failed check can be repaired by Bonsai, the message
should point at `bonsai sync --apply`.

## `bonsai sync [--apply]`

`bonsai sync` compares desired generated files against the current workspace.
It uses the existing config, state, rendering, and path derivation logic to
calculate:

- root Caddyfile content
- one Caddy snippet per public service for the default worktree and each
  managed worktree
- `.env.local` content for the default worktree and each managed worktree
- missing generated files
- stale generated files whose content differs from the desired content
- stale Bonsai-generated snippets for worktrees no longer present in state

Dry-run mode is the default. It prints a summary of planned changes and exits
without writing. `--apply` writes missing or stale generated files, removes
stale Bonsai-generated Caddy snippets, and reloads Caddy when public services
are configured.

Sync does not change slot assignments. Existing state remains the source of
truth for managed branch worktree-to-slot mappings, and the default worktree
always uses slot `0`. If state and actual git worktrees disagree, sync reports
the inconsistency and leaves structural repair to a later command.

## Architecture

Add small planning/data structures in `bonsai.models` for start, doctor, and
sync results. Keep command logic in `bonsai.workflows` so Typer commands stay
thin.

Suggested workflow functions:

- `resolve_start_target(runner, workspace_root, branch_or_name)`
- `execute_start(runner, workspace_root, branch_or_name)`
- `check_workspace_health(runner, workspace_root)`
- `plan_sync(workspace_root)`
- `execute_sync(runner, workspace_root, apply)`

Reuse existing helpers where possible:

- `load_workspace_config`
- `resolve_managed_worktree`
- `_resolve_current_worktree`
- `plan_add_files`
- `render_env_local`
- `render_root_caddyfile`
- `render_caddy_snippets`
- `generated_worktree_env`
- `reload_workspace_caddy`

Start needs one runner path that streams command output and returns the exit
code instead of always capturing output. This can be a new method on the runner
protocol or a focused helper used only by `execute_start`.

## Error Handling

- Missing workspace state keeps using `BonsaiWorkspaceError`.
- Missing or invalid config keeps using `BonsaiConfigError`.
- Missing `[commands].start` raises `BonsaiConfigError`.
- Unknown start target raises `BonsaiWorkspaceError`.
- Missing generated `.env.local` during start raises a workspace error that
  recommends `bonsai sync --apply`.
- `sync --apply` writes files before Caddy reload. If reload fails, the file
  repairs remain in place and the command reports the reload error.
- Dry-run sync should never write or remove files.

## CLI Output

`bonsai start` prints a short line naming the worktree and command, then hands
over to the subprocess. It should not wrap the long-running app process in the
status spinner.

`bonsai doctor` uses a Rich table or concise symbol-prefixed rows. Output should
be readable in plain terminals and include enough detail to act on failures.

`bonsai sync` prints a dry-run or applied summary grouped by action:

- write `.env.local`
- write root Caddyfile
- write Caddy snippet
- remove stale Caddy snippet
- reload Caddy

## Testing

Add focused tests around workflows and CLI behavior:

- `start` resolves the current managed worktree and runs `[commands].start` in
  that worktree.
- `start <branch>` resolves by branch and by worktree directory name.
- `start` fails clearly when `[commands].start` is missing.
- `start` overlays parsed `.env.local` values into the subprocess environment.
- `doctor` passes for a complete fixture workspace.
- `doctor` fails for missing generated files and recommends sync.
- `doctor` reports port conflicts without mutating the workspace.
- `sync` dry-run reports missing/stale generated files without writing.
- `sync --apply` writes stale `.env.local`, root Caddyfile, and snippets.
- `sync --apply` removes stale generated Caddy snippets.
- `sync --apply` reloads Caddy only when public services are configured.
- Existing add, checkout, remove, clone, and init tests remain passing.
