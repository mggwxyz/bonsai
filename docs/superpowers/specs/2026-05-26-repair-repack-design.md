# Bonsai Repair And Slot Repack Design

## Goal

Add a safe state repair command for structural workspace problems that `bonsai sync`
cannot fix. `sync` makes generated files match `.bonsai/state.json`; repair makes
`.bonsai/state.json` match the surviving managed worktrees and compacts
slot numbers.

## Command

Add `bonsai repair`.

The command is a dry run by default and writes state only with `--apply`.

Example dry run:

```text
repair dry run
remove old-branch - missing /workspace/authentic/old-branch
repack feature-c - slot 4 -> 2
Run: bonsai sync --apply
```

Example apply:

```text
repair apply
removed old-branch - missing /workspace/authentic/old-branch
repacked feature-c - slot 4 -> 2
Run: bonsai sync --apply
```

If no repair is needed:

```text
repair dry run
No state repairs needed
```

## Scope

Repair only mutates `.bonsai/state.json`.

It does not:

- Remove directories.
- Run `git worktree remove`.
- Rewrite `.env.local`.
- Rewrite Caddy files.
- Reload Caddy.
- Touch the default worktree entry.

When repair changes state, the CLI prints `Run: bonsai sync --apply` so generated
files can be refreshed explicitly afterward.

## Repair Rules

Repair evaluates managed branch entries in state. The default worktree is checked
by `bonsai doctor`, but repair does not modify it.

For each managed branch:

- If the recorded path exists and is a git worktree, keep it.
- If the recorded path is missing, remove that branch from state.
- If the recorded path exists but is not a git worktree, skip it and report a
  warning action. Do not remove the state entry.

After stale missing-path entries are removed, repair repacks slots for the
remaining managed worktrees into contiguous slots starting at `1`. Slot assignment
is deterministic: sort remaining managed branches case-insensitively by branch
name, then assign `1..N`.

Slot repacking can change ports and local URLs. The command must report every slot
change so the user can see the blast radius before applying.

## Data Model

Add repair-specific plan models:

- `RepairItem`: `branch`, `worktree_path`, `action`, `reason`, `old_slot`,
  `new_slot`.
- `RepairPlan`: `items`, `updated_state`, and `state_changed`.

Actions:

- `remove`: missing managed worktree will be removed from state.
- `repack`: managed worktree slot will change.
- `warn`: existing path is not a git worktree and will be left untouched.

For dry-run output, use imperative labels (`remove`, `repack`, `warn`). For apply
output, successful mutating actions may use past-tense labels (`removed`,
`repacked`) while warnings remain `warn`.

## Workflow API

Add workflow functions:

- `plan_repair(runner, workspace_root) -> RepairPlan`
- `execute_repair(runner, workspace_root, apply=False) -> RepairPlan`

`plan_repair` loads state, checks existing managed paths with `is_git_worktree`,
builds the updated state, and returns all planned actions. It must not write files.

`execute_repair` calls `plan_repair`. If `apply` is false, it returns the plan
without writing. If `apply` is true and `state_changed` is true, it saves the
updated state to `.bonsai/state.json`.

## Errors And Safety

The command fails normally through existing Bonsai errors when the workspace root
or state file cannot be found or parsed.

Repair does not require clean worktrees because it does not alter directories or
git branches. Dirty-worktree safety remains the responsibility of `remove` and
`cleanup`.

If a path exists but is not a git worktree, repair reports a warning and leaves the
entry in state. This avoids treating an arbitrary directory as a safe replacement
or silently deleting user-created files from Bonsai state.

## Testing

Add workflow tests for:

- Missing managed worktree path produces a remove action and an updated state
  without that branch.
- Existing managed path that is not a git worktree produces a warning and leaves
  state unchanged for that branch.
- Remaining managed worktrees are repacked deterministically into contiguous
  slots.
- Dry run does not write state.
- Apply writes the repaired state.

Add CLI tests for:

- `bonsai repair` calls `execute_repair` with `apply=False` and prints dry-run
  actions.
- `bonsai repair --apply` calls `execute_repair` with `apply=True` and prints
  apply actions.
- A no-op plan prints `No state repairs needed`.
- A changed plan prints `Run: bonsai sync --apply`.

## Documentation

Update `README.md` with a short section:

`bonsai repair` fixes structural state drift. It removes missing managed worktree
entries from state and repacks managed slots in a dry run by default. Use
`bonsai repair --apply` to write `.bonsai/state.json`, then run
`bonsai sync --apply` to refresh generated env and Caddy files.
