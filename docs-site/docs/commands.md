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
* `agent-guide`: Print package-level guidance for AI agents...
* `init`: Create a starter .bonsai.toml for the...
* `add`: Prepare a managed worktree for a branch.
* `remove`: Remove a managed worktree.
* `move`: Move a managed worktree folder.
* `checkout`: Resolve or prepare a worktree for shell...
* `open`: Open the current worktree&#x27;s primary local...
* `context`: Print Bonsai facts for the current worktree.
* `shell-init`: Print shell integration code.
* `install-shell`: Install shell integration for Bonsai...
* `list`: List managed worktrees in the current...
* `status`
* `start`: Run the configured start command in a...
* `logs`
* `sync`: Compare or repair generated Bonsai files.
* `repair`
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

## `bonsai agent-guide`

Print package-level guidance for AI agents and automation.

**Usage**:

```console
$ bonsai agent-guide [OPTIONS]
```

**Options**:

* `--format TEXT`: Output format: text or json.  [default: text]
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

**Usage**:

```console
$ bonsai move [OPTIONS] NAME NEW_FOLDER
```

**Arguments**:

* `NAME`: [required]
* `NEW_FOLDER`: [required]

**Options**:

* `--help`: Show this message and exit.

The worktree argument accepts a branch name, worktree directory, or worktree
slug. Bonsai runs `git worktree move`, updates `.bonsai/state.json`, and
refreshes generated files. The default worktree cannot be moved.

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
* `--help`: Show this message and exit.

## `bonsai open`

Open the current worktree&#x27;s primary local URL.

**Usage**:

```console
$ bonsai open [OPTIONS]
```

**Options**:

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

## `bonsai logs`

**Usage**:

```console
$ bonsai logs [OPTIONS] [BRANCH]
```

**Arguments**:

* `[BRANCH]`

**Options**:

* `--command TEXT`: Filter logs by lifecycle command kind.
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

* `--help`: Show this message and exit.
