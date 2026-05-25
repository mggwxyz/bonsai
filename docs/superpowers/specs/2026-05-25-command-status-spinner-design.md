# Command Status Spinner Design

## Context

Bonsai runs external commands through `SubprocessRunner.run()` in
`src/bonsai/process.py`. Higher-level workflows call that runner for git, Caddy,
and configured project commands such as install and setup. Because output is
captured, long-running commands currently leave the CLI with no visible progress.

## Goal

Show a small spinner with a useful status while every real external command is
running. The status should reassure users that Bonsai is still working without
changing command behavior or leaking spinner output into shell-integration stdout.

## Approach

Add status rendering at the runner boundary rather than at each workflow call
site. `SubprocessRunner` will wrap each `subprocess.run()` invocation in Rich
status output. This covers existing and future subprocess calls consistently,
including git commands, Caddy reloads, and configured install/setup commands.

`RecordingRunner` remains unchanged so workflow tests can continue to inspect
commands without terminal behavior.

## Status Text

The status should use the shell-style command summary when possible:

- `git fetch origin`
- `cd /path/to/worktree && yarn install`

The command text should be shell-quoted the same way workflow dry-run summaries
are quoted, so paths and arguments with spaces remain readable.

## Output Streams

Spinner/status output must not corrupt stdout for commands whose stdout is
machine-readable. The most important case is `bonsai checkout --path`, where the
zsh shell integration captures stdout and treats it as the directory to `cd`
into.

Status output should therefore render on stderr, while command stdout remains
captured and returned through `CommandResult`.

## Non-Interactive Behavior

When Bonsai is run without an interactive terminal, status output should not add
noisy control characters. Rich's console behavior should be configured so
non-interactive output degrades cleanly or is skipped.

## Errors

The runner should preserve existing error semantics:

- commands still run with captured stdout and stderr
- nonzero exit codes still raise `BonsaiCommandError` when `check=True`
- the failure message still includes the command and stderr

## Tests

Add focused tests around the runner and CLI behavior:

- `SubprocessRunner` emits running status to stderr for a successful command
- command stdout is still returned in `CommandResult`
- `checkout --path` stdout remains exactly the worktree path when a workflow is
  executed through the CLI
- existing workflow tests using `RecordingRunner` remain unaffected
