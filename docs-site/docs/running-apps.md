---
title: Running Apps
---

# Running Apps

Bonsai runs the lifecycle commands configured in `.bonsai.toml` — `install`,
`setup`, and `start`, plus optional `pre*` and `post*` hooks — from the
target worktree, with the generated `.env.local` values available in the
subprocess environment.

Output streams live and is saved as timestamped logs under
`.bonsai/logs/<worktree-slug>/`. Log kinds are `preinstall`, `install`,
`postinstall`, `presetup`, `setup`, `postsetup`, `prestart`, `start`, and
`poststart`; when multiple runs share a timestamp, Bonsai suffixes and
orders the log files consistently.

## Start in the Foreground

```bash
bonsai start
bonsai start ma-123-implement-auth
```

`start` runs the configured `[commands].start` command in the target
worktree, with `prestart` and `poststart` hooks running before and after
when configured. With no argument it uses the current worktree; with an
argument it accepts a branch name, worktree directory, or worktree slug.

The process runs in the foreground. Output streams live and is saved as a
managed `start` log, but `start` does not daemonize, supervise, or
automatically restart the process — use `up` for a background process.

`start` requires the target worktree's generated `.env.local`; run
`bonsai sync --apply` if it is missing.

## Run in the Background with Up and Down

```bash
bonsai up ma-123-implement-auth
bonsai down ma-123-implement-auth
```

`up` starts the configured start command detached and tracks its PID in
`.bonsai/pids/<worktree-slug>.json`. It then waits for the worktree's
configured service ports to start listening (30 seconds by default; tune
with `--wait-timeout`). If the app does not become ready in time, Bonsai
stops the process and reports the log path. If the worktree is already
running, `up` refuses and points you at `bonsai down`.

`down` terminates the tracked process, waiting `--timeout` seconds (5 by
default) before force-killing it. It reports when nothing is running or
when the tracked PID turned out to be stale.

## Stop and Restart by Port Ownership

```bash
bonsai stop
bonsai stop ma-123-implement-auth
bonsai stop --all
bonsai restart ma-123-implement-auth
```

`stop` terminates listener processes on the selected worktree's configured
service ports when ownership can be matched to that worktree by process
working directory. External or unknown owners are skipped unless `--force`
is passed. `--all` stops matching listeners for every worktree.

`restart` runs the same safe stop flow, then starts the selected worktree
in the foreground.

## Read Lifecycle Logs

```bash
bonsai logs
bonsai logs ma-123-implement-auth --command start
```

`logs` prints the latest managed lifecycle log for a worktree. With no
argument it resolves the current worktree; with an argument it accepts the
same branch, directory, or slug forms as `start`. Use `--command install`,
`--command setup`, `--command start`, or a hook kind such as
`--command preinstall` to filter to the latest log for a specific command
kind.
