# Editor and Browser Automation Design

Date: 2026-05-26

## Summary

Add opt-in post-add automation flags to `bonsai add` so a user can prepare a
managed worktree and immediately open it in an editor, open the primary service
URL, and optionally start the app.

The default `bonsai add <branch>` behavior remains unchanged. Automation only
runs when the user explicitly requests it with flags.

## Goals

- Add `--editor` to open the prepared worktree in the user's editor.
- Add `--open` to open the prepared worktree's primary public URL.
- Add `--start` to run the configured start command for the prepared worktree.
- Preserve existing add, setup, Caddy reload, and state update behavior.
- Run post-add actions in deterministic order: editor, browser, then start.
- Keep `bonsai start` foreground semantics. When `--start` is used, it runs
  last and owns the terminal until the app exits.

## Non-Goals

- No detached or background app process management.
- No automatic browser readiness polling.
- No new combined command such as `bonsai launch`.
- No persisted editor preference in `.bonsai.toml`.
- No change to shell integration or automatic directory changes.

## CLI

Extend `bonsai add`:

```bash
bonsai add BRANCH --editor
bonsai add BRANCH --open
bonsai add BRANCH --start
bonsai add BRANCH --editor --open --start
```

Behavior:

- With no flags, output stays as it is today:
  - `Prepared worktree: <path>`
  - `Port slot: <slot>`
- `--editor` opens the prepared worktree path.
- `--open` opens the prepared worktree's primary public URL.
- `--start` runs `[commands].start` in the prepared worktree.
- When multiple flags are present, actions run in this order:
  1. editor
  2. browser
  3. start

## Editor Resolution

Editor resolution is local to the CLI layer because it interacts with the host
machine rather than Bonsai workspace state.

Resolution order:

1. `$VISUAL`
2. `$EDITOR`
3. `code` when it is available on `PATH`

The resolved editor command is parsed with `shlex.split`, then the worktree path
is appended as the final argument. For example:

```text
VISUAL="code --reuse-window"
```

runs:

```bash
code --reuse-window <worktree-path>
```

If no editor is configured and `code` is unavailable, Bonsai fails with a clear
workspace error explaining that `$VISUAL`, `$EDITOR`, or `code` is required.

## Browser Opening

`--open` should reuse the same URL rendering behavior as `bonsai open`, but it
must target the newly prepared worktree rather than `Path.cwd()`.

Add or extend a workflow-level URL planner that resolves a named Bonsai
worktree target and returns an `OpenUrlPlan`. The existing `bonsai open`
behavior should continue to resolve the current worktree exactly as it does
today.

## Start Behavior

`--start` delegates to the same start workflow used by `bonsai start`, passing
the branch that was just prepared. This preserves:

- existing target resolution rules;
- generated `.env.local` requirement;
- generated env injection;
- missing `[commands].start` errors;
- foreground process behavior and exit code propagation.

Because start is foreground and may run indefinitely, it always runs after
editor and browser automation.

## Architecture

Keep `src/bonsai/cli.py` responsible for host automation:

- parse new `bonsai add` flags;
- call `execute_add`;
- print the existing add result;
- call a small editor helper when `--editor` is set;
- call a browser-opening helper when `--open` is set;
- call `execute_start` when `--start` is set.

Keep `src/bonsai/workflows.py` responsible for workspace-derived facts:

- retain `execute_add` as the single add implementation;
- retain `execute_start` as the single start implementation;
- add a named-target primary URL planner or generalize `plan_open_url` without
  breaking the current-worktree caller.

The editor helper should be small enough to test from the CLI module without
creating a separate abstraction unless the tests reveal duplication.

## Error Handling

- If `execute_add` fails, no post-add automation runs.
- If `--editor` cannot resolve an editor, fail before opening the browser or
  starting the app.
- If the editor subprocess cannot be spawned or exits non-zero immediately,
  report a Bonsai workspace error.
- If `--open` cannot resolve or open the primary URL, report the same style of
  Bonsai error used by `bonsai open`.
- If `--start` fails due to missing config, missing `.env.local`, or a failed
  subprocess, preserve existing `bonsai start` behavior.
- If `--start` returns a non-zero process exit code, `bonsai add --start` exits
  with that code.

## Testing

Add focused CLI tests for:

- default `bonsai add` still calls only `execute_add` and prints the existing
  result.
- `bonsai add --editor` opens the returned worktree path using a resolved
  editor command.
- editor resolution prefers `$VISUAL` over `$EDITOR`.
- editor resolution falls back to `code` when available.
- missing editor configuration fails clearly.
- `bonsai add --open` opens the primary URL for the newly added branch.
- `bonsai add --editor --open --start` performs actions in editor, browser,
  start order.
- `bonsai add --start` exits with the start command's exit code.

Add workflow tests for:

- named-target URL planning renders the primary URL for a managed worktree.
- the existing current-worktree URL planner keeps working for `bonsai open`.
- named-target URL planning rejects an unknown worktree with a clear Bonsai
  workspace error.

## Documentation

Update the README and generated command docs to show the new `bonsai add`
options and a short example:

```bash
bonsai add ma-123-implement-auth --editor --open --start
```

Document that `--start` runs in the foreground and should be placed last by
Bonsai's fixed post-add action order.
