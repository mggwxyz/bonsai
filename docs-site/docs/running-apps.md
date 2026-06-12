---
title: Running Apps
---

# Running Apps

Bonsai runs the lifecycle commands configured in `.bonsai.toml` — `install`,
`setup`, `postadd`, `preremove`, and `start`, plus optional `pre*` and `post*`
hooks for install, setup, and start — from the target worktree, with the
generated `.env.local` values available in the subprocess environment.

Output streams live and is saved as timestamped logs under
`.bonsai/logs/<worktree-slug>/`. Log kinds are `preinstall`, `install`,
`postinstall`, `presetup`, `setup`, `postsetup`, `postadd`, `preremove`,
`prestart`, `start`, and `poststart`; when multiple runs share a timestamp,
Bonsai suffixes and orders the log files consistently.

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

## Run in the Background with Up

```bash
bonsai up ma-123-implement-auth
```

`up` starts the configured start command detached and tracks its PID in
`.bonsai/pids/<worktree-slug>.json`. It then waits for the worktree's
configured service ports to start listening (30 seconds by default; tune
with `--wait-timeout`). If the app does not become ready in time, Bonsai
stops the process and reports the log path. If the worktree is already
running, `up` refuses and points you at `bonsai stop`.

By default, `[run] mode = "concurrent"` lets multiple worktrees run tracked
background app processes at once. Set `[run] mode = "single"` for projects that
cannot run multiple worktrees concurrently. In single mode, `up` and
`restart --detach` refuse when another worktree in the same workspace already
has a live tracked process, and suggest `bonsai stop <name>` or
`bonsai stop --all` instead of killing it automatically.

Use `bonsai ps` to list tracked background app processes across every
registered Bonsai workspace.

## Run in a Reattachable Tmux Session

```bash
bonsai tmux ma-123-implement-auth
bonsai tmux ma-123-implement-auth --detach
```

`tmux` starts configured service startup commands in a deterministic tmux
session for the target worktree, with each service in its own pane in one
window. Add `start = "..."` to individual `[[services]]` entries to use this
multi-pane mode. If no services define `start`, Bonsai falls back to the
single `[commands].start` command. The generated `.env.local` values and
standard Bonsai environment variables are available to every pane. By default,
Bonsai attaches to the tmux session after creating or finding it. Use
`--detach` to leave the session running and print the exact
`tmux attach -t ...` command instead.

If the session already exists, Bonsai does not start another copy; it reports
the existing session, then attaches unless `--detach` is set. Session names
include the workspace name, worktree slug, and a short workspace-root hash to
avoid collisions between similarly named workspaces.

## Stop and Restart

```bash
bonsai stop
bonsai stop ma-123-implement-auth
bonsai stop --all
bonsai restart ma-123-implement-auth
```

`stop` first terminates the worktree's tracked background process from
`bonsai up`, then terminates listener processes on the worktree's
configured service ports when ownership can be matched to that worktree by
process working directory. External or unknown owners are skipped unless
`--force` is passed. `--all` stops matching processes for every worktree.

`restart` runs the same safe stop flow, then starts the selected worktree
in the foreground (or in the background with `--detach`).

## Run Ad Hoc Commands

```bash
bonsai exec -- npm test
bonsai exec ma-123-implement-auth -- npm test
bonsai each -- git status --short
bonsai each --skip-default -- npm install
```

`exec` runs an arbitrary command in one worktree with that worktree's generated
`.env.local` values loaded. With no worktree name it uses the current worktree;
with a worktree name it accepts the same branch, directory, or slug forms as
`start`.

`each` runs the command sequentially across the default worktree and every
managed worktree. It keeps going after failures, prints one exit code per
worktree, and exits non-zero if any command fails. Pass `--skip-default` when
the command should run only in branch worktrees.

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
