# Remove And Checkout Worktrees Design

## Summary

Bonsai should support the full lifecycle of a managed worktree from daily
checkout through removal. `bonsai remove <name>` should remove the git worktree,
delete the worktree directory through git, clean up generated Caddy snippets,
and update `.bonsai/state.json`. `bonsai checkout <name>` should keep resolving
existing worktrees, and when no managed worktree matches, it should prepare the
branch with the same Bonsai setup as `bonsai add` before handing shell
integration the new directory path.

## Goals

- Add an explicit command for removing one managed worktree and its directory.
- Refuse destructive removal when a worktree has uncommitted changes unless the
  user passes `--force`.
- Remove Bonsai-generated Caddy snippet files for the worktree being removed.
- Keep state updates transactional enough that Bonsai does not forget a worktree
  before git has removed it.
- Make `bonsai checkout <branch>` feel like `git checkout <branch>` for missing
  local worktrees: fetch origin, use an existing remote branch when present, or
  create a new branch from the configured base branch.

## Non-Goals

- Delete remote branches.
- Prune every stale worktree automatically.
- Remove the default worktree.
- Remove files outside the Bonsai workspace root.
- Change the shell integration contract beyond letting checkout prepare missing
  worktrees before printing the path.

## Command Behavior

### `bonsai remove <name> [--force]`

`<name>` resolves against the managed worktree branch key, stored path, or slug.
The default worktree is not removable. Unknown names fail with a clear error.

For a clean worktree, Bonsai runs `git worktree remove <path>` from the default
worktree repository, removes generated Caddy snippets matching
`caddy.d/<slug>-*.caddy`, then saves state without the removed branch. The git
command removes the worktree directory.

For a dirty worktree, Bonsai fails before removal and explains that `--force` is
required. With `--force`, Bonsai passes `--force` to `git worktree remove` and
then performs the same snippet and state cleanup.

### `bonsai checkout <name>`

Checkout keeps its current first step: resolve `<name>` to an existing managed
worktree and either print the path for shell integration or show the integration
message.

If no managed worktree matches, checkout treats `<name>` as a branch name and
runs the add workflow. The existing add workflow already fetches origin and uses
`git worktree add <path> <branch>` when the remote branch exists; otherwise it
creates a new branch from the configured base branch. After add succeeds,
checkout returns the new path to shell integration. If add fails, checkout fails
and prints no path for the wrapper to `cd` into.

## Architecture

Add workflow-level helpers instead of keeping removal logic in the CLI:

- A resolver maps branch key, path, or slug to a branch and `ManagedWorktree`.
- `execute_remove(runner, name, workspace_root, force=False)` loads state,
  validates the target, checks dirty status, calls git removal, deletes snippet
  files, and saves updated state.
- `execute_checkout(runner, name, workspace_root)` resolves an existing
  worktree or delegates to `execute_add` when missing.

The CLI remains thin: it finds the workspace root, calls the workflow, and
prints paths or summaries.

## Git Integration

Add small wrappers in `bonsai.git` for:

- `worktree_has_changes(runner, repo)`: uses `git -C <path> status --porcelain`
  and treats any output as dirty.
- `remove_worktree(runner, repo, target, force=False)`: runs
  `git -C <repo> worktree remove [--force] <target>`.

The default worktree path is used as the git command repository because it is the
stable checkout that remains after removal.

## Error Handling

- Unknown worktree: `Unknown worktree: <name>`.
- Default worktree removal: `Cannot remove the default worktree`.
- Dirty worktree without force: fail before running git removal.
- Git removal failure: bubble the command error and leave state unchanged.
- Snippet cleanup only deletes files matching the removed worktree slug prefix
  inside the configured snippets directory.

## Testing

- Workflow tests cover successful removal, dirty refusal, forced removal,
  unknown worktree errors, default worktree refusal, snippet deletion, and state
  preservation when git removal fails.
- Checkout workflow tests cover resolving existing worktrees and delegating to
  add when missing.
- CLI tests cover `remove` wiring, `remove --force`, checkout path output for a
  newly prepared branch, and error reporting.
