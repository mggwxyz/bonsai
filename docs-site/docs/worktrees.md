---
title: Worktrees
---

# Worktrees

A Bonsai workspace holds one default worktree for the base branch (for
example `my-app/main`) plus any number of managed branch worktrees as
siblings. These commands manage that set.

## Add a Branch Worktree

```bash
bonsai add ma-123-implement-auth
```

`add` fetches `origin`, checks out the remote branch when it exists, or
creates a new branch from the configured base branch. It assigns the
worktree a stable port slot, writes the generated `.env.local`, creates the
Caddy route snippet, applies shared files and `.worktreeinclude` copies, and
runs the configured `install`, `setup`, and `postadd` commands.

Create a missing branch from a different base branch for one command:

```bash
bonsai add ma-123-implement-auth --base-branch develop
```

## Add a Pull Request Worktree

```bash
bonsai add --pr 123
bonsai checkout --pr 123
```

PR worktrees require the GitHub CLI (`gh`) to be installed and authenticated.
For same-repository PRs, Bonsai fetches the head branch and prepares a normal,
pushable branch worktree. For fork PRs, Bonsai fetches
`pull/<number>/head:bonsai/pr-<number>` and labels the resulting worktree as
read-only.

If a PR is closed or merged, Bonsai warns and refuses by default. Pass `--force`
when you intentionally need to inspect that PR anyway.

### Post-Add Actions

Pass explicit flags to open the new working context immediately:

```bash
bonsai add ma-123-implement-auth --editor --open --start
```

- `--editor` opens the new worktree using `$VISUAL`, `$EDITOR`, or `code`.
- `--open` opens the prepared branch's primary local URL.
- `--start` runs the configured start command in the foreground.

When multiple flags are passed, Bonsai opens the editor, opens the browser,
then starts the app.

## Switch with Checkout

```bash
bonsai checkout ma-123-implement-auth
```

`checkout` changes your shell into the matching worktree and requires
[shell integration](shell-integration.md). The lookup accepts a branch name,
worktree directory, or worktree slug, and resolves a unique fuzzy match when
nothing matches exactly. Run `bonsai checkout` with no argument to choose from
existing worktrees through `fzf` or Bonsai's built-in picker. If the worktree
does not exist yet, Bonsai prepares it first, exactly like `add` (including
`--base-branch`).

## Remove a Worktree

```bash
bonsai remove ma-123-implement-auth
```

Run `bonsai remove` with no argument to pick an existing branch worktree.
Bonsai refuses to remove a worktree with uncommitted changes unless you pass
`--force`. If `[commands].preremove` is configured, it runs before any teardown;
a failure aborts removal unless `--force` is set. Removal also deletes the
worktree's Caddy route snippet and updates workspace state.

If the worktree has a root-level `compose.yaml`, `compose.yml`,
`docker-compose.yaml`, or `docker-compose.yml`, Bonsai first runs
`docker compose -p <project> down`, using `.env.local`
`COMPOSE_PROJECT_NAME` when present and the worktree folder name otherwise.
If Compose teardown fails, removal stops before the git worktree is removed.

## Move (Rename) a Worktree

```bash
bonsai move ma-123-implement-auth ma-123-auth
```

`move` renames a managed worktree directory. The lookup accepts a branch
name, worktree directory, or worktree slug. Bonsai uses `git worktree move`
underneath, updates `.bonsai/state.json`, and rewrites generated files so
path-dependent template values stay current.

Renaming the default worktree requires `--force`: because
`git worktree move` cannot relocate the main working tree, Bonsai moves the
directory and runs `git worktree repair` to re-point every secondary
worktree.

## PR-Aware Cleanup

```bash
bonsai cleanup
bonsai cleanup --apply
```

`cleanup` checks each managed branch for a merged pull request and removes
the eligible worktrees. It recognizes normal branches and fork PR worktrees
created as `bonsai/pr-<number>`. It requires the GitHub CLI (`gh`) to be
installed and authenticated, and is a dry run by default.

Branches with no PR, open PRs, unmerged closed PRs, or uncommitted changes
are skipped; pass `--force` with `--apply` to remove eligible dirty
worktrees. Applied cleanup uses the same removal lifecycle as
`bonsai remove`, including `preremove`, Caddy snippet cleanup, and Docker
Compose teardown.
