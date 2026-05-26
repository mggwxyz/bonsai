# Managed Command Logs Design

Date: 2026-05-26

## Summary

Bonsai will stream lifecycle command output live by default and also save the
same output to managed log files. This applies to `[commands].install`,
`[commands].setup`, and foreground `bonsai start`.

This milestone intentionally does not add detached processes, daemon
supervision, automatic restarts, or background process management. It creates
the logging foundation those features can build on later.

## Goals

- Show install and setup output live during `bonsai clone`, `bonsai add`, and
  implicit add through `bonsai checkout`.
- Tee lifecycle command output to files under `.bonsai/logs/<worktree>/`.
- Include log paths in command failure messages.
- Add `bonsai logs` for viewing the latest saved log for a worktree.
- Keep `bonsai start` foreground-only while saving its output to a managed log.

## Non-Goals

- No daemon mode.
- No automatic process restarts.
- No long-running background supervision.
- No log rotation or retention policy beyond timestamped log files.
- No structured command history database.

## User Behavior

When Bonsai runs an install, setup, or start command, it prints a phase line
before the command begins:

```text
Running install: yarn install
```

The command's stdout and stderr stream live to the user's terminal. Bonsai also
writes the same output to a timestamped log file:

```text
.bonsai/logs/main/20260526-143012-install.log
.bonsai/logs/feature-auth/20260526-143245-setup.log
.bonsai/logs/feature-auth/20260526-144001-start.log
```

If install or setup fails, Bonsai reports the failing command and includes the
log path. The existing failure semantics remain: clone/add/setup workflows stop
when install or setup fails, and `bonsai start` exits with the subprocess exit
code.

## CLI

Add a command:

```bash
bonsai logs [branch] --command install
bonsai logs [branch] --command setup
bonsai logs [branch] --command start
bonsai logs [branch]
```

Behavior:

- With no branch, resolve the current Bonsai worktree.
- With a branch, resolve the same targets accepted by `bonsai start [branch]`.
- With no `--command`, print the latest log for that worktree.
- With `--command install|setup|start`, print the latest matching log.
- If no matching log exists, fail with a clear Bonsai error.
- Print log contents to stdout without opening a pager.

## Architecture

Add a small logged streaming path in `bonsai.process`. The subprocess layer is
responsible for:

- merging the provided env with `os.environ`;
- opening the destination log file;
- streaming stdout and stderr live;
- writing the same bytes to the log file;
- returning the subprocess exit code.

Keep command orchestration in `bonsai.workflows`. Add a lifecycle command helper
that accepts:

- command kind: `install`, `setup`, or `start`;
- command string from config;
- worktree target;
- generated env values;
- workspace log root.

The helper resolves the log path, prints the phase line through the CLI-facing
runner, and calls the logged streaming process method.

`execute_clone`, `execute_add`, and `execute_start` use this helper instead of
quiet `runner.run` or plain `runner.run_stream` for lifecycle commands.

## Log Paths

Logs live inside workspace-local Bonsai state:

```text
<workspace-root>/.bonsai/logs/<worktree-slug>/<timestamp>-<kind>.log
```

For the default worktree, use the configured default worktree name as the log
directory. For managed worktrees, use the existing `ManagedWorktree.slug`.

Timestamps use local time in `YYYYMMDD-HHMMSS` format. If a log path collision
occurs within the same second, append a numeric suffix such as
`20260526-143012-install-2.log`.

## Data Flow

1. Workflow renders or reads the generated `.env.local` values.
2. Workflow chooses a lifecycle command kind.
3. Workflow resolves the worktree's log directory.
4. Process runner streams command output and tees it to the log.
5. Workflow returns normally on success. Install/setup failures raise the
   existing Bonsai command error type with log path context. Start failures
   return the subprocess exit code.
6. `bonsai logs` resolves the target worktree, finds matching files, and prints
   the newest log.

## Error Handling

- Missing `[commands].start` keeps the current `commands.start` config error.
- Missing `.env.local` for `bonsai start` keeps the current sync hint.
- Invalid `--command` values are rejected by Typer or Bonsai validation.
- Missing log directories or matching log files produce a Bonsai workspace
  error that names the target worktree and command filter.
- Failed install/setup commands include the log file path in the raised error.
- Failed start commands write logs but keep returning the subprocess exit code.

## Testing

Add focused tests for:

- `execute_clone` streams/logs install and setup in order.
- `execute_add` streams/logs install and setup in order.
- generated env values are still passed to install/setup.
- log paths are under `.bonsai/logs/<worktree>/`.
- `execute_start` remains foreground-style but writes a start log.
- lifecycle command failures include the log path.
- `bonsai logs` resolves the current worktree and prints the latest log.
- `bonsai logs --command install` filters to install logs.
- missing logs fail with a clear Bonsai error.

## Documentation

Update the README to describe:

- install/setup output streams live during clone/add/checkout-created worktrees;
- lifecycle command logs are saved under `.bonsai/logs`;
- `bonsai logs` shows the latest log for a worktree;
- `bonsai start` remains foreground-only and is not supervised or restarted.
