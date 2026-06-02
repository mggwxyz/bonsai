---
title: Command Reference
---

# Command Reference

Manage git worktree development workspaces.

**Usage**:

```console
$ bonsai [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--version`
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `clone`: Clone a repository into a new Bonsai...
* `init`: Create a starter .bonsai.toml for the...
* `add`: Prepare a managed worktree for a branch.
* `remove`: Remove a managed worktree.
* `move`: Move a managed worktree folder.
* `checkout`: Resolve or prepare a worktree for shell...
* `open`: Open a worktree&#x27;s primary local URL.
* `urls`: Show configured local URLs and route...
* `context`: Print Bonsai facts for the current worktree.
* `shell-init`: Print shell integration code.
* `install-shell`: Install shell integration for Bonsai...
* `list`: List managed worktrees in the current...
* `ports`: List configured service ports and listener...
* `ps`: List configured service ports that...
* `status`
* `start`: Run the configured start command in a...
* `up`: Start the configured app command in the...
* `down`: Stop a background app process started by...
* `stop`: Stop listener processes for configured...
* `restart`: Stop matching listeners, then run the...
* `logs`
* `sync`: Compare or repair generated Bonsai files.
* `repair`
* `repair-ports`: Plan or apply slot reassignments for...
* `cleanup`: Remove branch worktrees whose pull...
* `doctor`: Check workspace health and report repair...

## `bonsai clone`

Clone a repository into a new Bonsai workspace.

**Usage**:

```console
$ bonsai clone [OPTIONS] GIT_URL NAME
```

**Arguments**:

* `GIT_URL`: [required]
* `NAME`: [required]

**Options**:

* `--interactive / --no-interactive`: Create .bonsai.toml interactively when missing.  [default: interactive]
* `--help`: Show this message and exit.

## `bonsai init`

Create a starter .bonsai.toml for the current checkout or workspace.

**Usage**:

```console
$ bonsai init [OPTIONS]
```

**Options**:

* `--force`: Overwrite an existing .bonsai.toml.
* `--help`: Show this message and exit.

## `bonsai add`

Prepare a managed worktree for a branch.

**Usage**:

```console
$ bonsai add [OPTIONS] BRANCH
```

**Arguments**:

* `BRANCH`: [required]

**Options**:

* `--base-branch TEXT`: Base branch to use when creating a new branch worktree.
* `--editor`: Open the prepared worktree in an editor.
* `--open`: Open the prepared worktree&#x27;s primary local URL.
* `--start`: Run the configured start command after add.
* `--help`: Show this message and exit.

## `bonsai remove`

Remove a managed worktree.

**Usage**:

```console
$ bonsai remove [OPTIONS] NAME
```

**Arguments**:

* `NAME`: [required]

**Options**:

* `--force`: Remove a worktree with uncommitted changes.
* `--help`: Show this message and exit.

## `bonsai move`

Move a managed worktree folder.

The worktree argument accepts a branch name, worktree directory, or worktree slug.
Bonsai runs `git worktree move`, updates `.bonsai/state.json`, and refreshes
generated files. The default worktree cannot be moved.

**Usage**:

```console
$ bonsai move [OPTIONS] NAME NEW_FOLDER
```

**Arguments**:

* `NAME`: [required]
* `NEW_FOLDER`: [required]

**Options**:

* `--help`: Show this message and exit.

## `bonsai checkout`

Resolve or prepare a worktree for shell checkout.

**Usage**:

```console
$ bonsai checkout [OPTIONS] NAME
```

**Arguments**:

* `NAME`: [required]

**Options**:

* `--path`: Print the resolved worktree path for shell integration.
* `--base-branch TEXT`: Base branch to use when creating a new branch worktree.
* `--help`: Show this message and exit.

## `bonsai open`

Open a worktree&#x27;s primary local URL.

**Usage**:

```console
$ bonsai open [OPTIONS] [NAME]
```

**Arguments**:

* `[NAME]`

**Options**:

* `--service TEXT`: Open a specific public service URL.
* `--help`: Show this message and exit.

## `bonsai urls`

Show configured local URLs and route diagnostics.

**Usage**:

```console
$ bonsai urls [OPTIONS] [NAME]
```

**Arguments**:

* `[NAME]`

**Options**:

* `--service TEXT`: Filter diagnostics to one public service.
* `--diagnose TEXT`: Find diagnostics for a specific configured URL.
* `--format TEXT`: Output format: text or json.  [default: text]
* `--help`: Show this message and exit.

## `bonsai context`

Print Bonsai facts for the current worktree.

**Usage**:

```console
$ bonsai context [OPTIONS]
```

**Options**:

* `--format TEXT`: Output format: text or json.  [default: text]
* `--help`: Show this message and exit.

## `bonsai shell-init`

Print shell integration code.

**Usage**:

```console
$ bonsai shell-init [OPTIONS] SHELL
```

**Arguments**:

* `SHELL`: [required]

**Options**:

* `--help`: Show this message and exit.

## `bonsai install-shell`

Install shell integration for Bonsai checkout.

**Usage**:

```console
$ bonsai install-shell [OPTIONS] SHELL
```

**Arguments**:

* `SHELL`: [required]

**Options**:

* `--help`: Show this message and exit.

## `bonsai list`

List managed worktrees in the current workspace.

**Usage**:

```console
$ bonsai list [OPTIONS]
```

**Options**:

* `--format TEXT`: Output format: text or json.  [default: text]
* `--help`: Show this message and exit.

## `bonsai ports`

List configured service ports and listener ownership.

**Usage**:

```console
$ bonsai ports [OPTIONS]
```

**Options**:

* `--format TEXT`: Output format: text or json.  [default: text]
* `--help`: Show this message and exit.

## `bonsai ps`

List configured service ports that currently have listeners.

**Usage**:

```console
$ bonsai ps [OPTIONS]
```

**Options**:

* `--format TEXT`: Output format: text or json.  [default: text]
* `--help`: Show this message and exit.

## `bonsai status`

**Usage**:

```console
$ bonsai status [OPTIONS]
```

**Options**:

* `--format TEXT`: Output format: text or json.  [default: text]
* `--help`: Show this message and exit.

## `bonsai start`

Run the configured start command in a worktree.

**Usage**:

```console
$ bonsai start [OPTIONS] [BRANCH]
```

**Arguments**:

* `[BRANCH]`

**Options**:

* `--help`: Show this message and exit.

## `bonsai up`

Start the configured app command in the background and track its PID.

**Usage**:

```console
$ bonsai up [OPTIONS] [NAME]
```

**Arguments**:

* `[NAME]`

**Options**:

* `--wait-timeout FLOAT`: Seconds to wait for the primary service port.  [default: 30.0]
* `--help`: Show this message and exit.

## `bonsai down`

Stop a background app process started by `bonsai up`.

**Usage**:

```console
$ bonsai down [OPTIONS] [NAME]
```

**Arguments**:

* `[NAME]`

**Options**:

* `--timeout FLOAT`: Seconds to wait before force killing the tracked PID.  [default: 5.0]
* `--help`: Show this message and exit.

## `bonsai stop`

Stop listener processes for configured service ports.

**Usage**:

```console
$ bonsai stop [OPTIONS] [NAME]
```

**Arguments**:

* `[NAME]`

**Options**:

* `--all`: Stop matching listeners for all worktrees.
* `--force`: Stop external or unknown owners of selected ports.
* `--help`: Show this message and exit.

## `bonsai restart`

Stop matching listeners, then run the configured start command.

**Usage**:

```console
$ bonsai restart [OPTIONS] [NAME]
```

**Arguments**:

* `[NAME]`

**Options**:

* `--force`: Stop external or unknown owners before starting.
* `--detach`: Start in the background after stopping.
* `--wait-timeout FLOAT`: Seconds to wait for detached readiness.  [default: 30.0]
* `--help`: Show this message and exit.

## `bonsai logs`

**Usage**:

```console
$ bonsai logs [OPTIONS] [BRANCH]
```

**Arguments**:

* `[BRANCH]`

**Options**:

* `--command TEXT`: Filter logs by lifecycle command kind.
* `-f, --follow`: Follow the selected log file.
* `--help`: Show this message and exit.

## `bonsai sync`

Compare or repair generated Bonsai files.

**Usage**:

```console
$ bonsai sync [OPTIONS]
```

**Options**:

* `--apply`: Write regenerated files.
* `--help`: Show this message and exit.

## `bonsai repair`

**Usage**:

```console
$ bonsai repair [OPTIONS]
```

**Options**:

* `--apply`: Write repaired workspace state.
* `--help`: Show this message and exit.

## `bonsai repair-ports`

Plan or apply slot reassignments for worktrees with conflicting ports.

**Usage**:

```console
$ bonsai repair-ports [OPTIONS]
```

**Options**:

* `--format TEXT`: Output format: text or json.  [default: text]
* `--apply`: Write repaired slots and sync files.
* `--help`: Show this message and exit.

## `bonsai cleanup`

Remove branch worktrees whose pull requests were merged.

**Usage**:

```console
$ bonsai cleanup [OPTIONS]
```

**Options**:

* `--apply`: Remove eligible worktrees.
* `--force`: Remove eligible worktrees with uncommitted changes.
* `--help`: Show this message and exit.

## `bonsai doctor`

Check workspace health and report repair hints.

**Usage**:

```console
$ bonsai doctor [OPTIONS]
```

**Options**:

* `--format TEXT`: Output format: text or json.  [default: text]
* `--apply`: Apply safe workspace repairs.
* `--help`: Show this message and exit.
