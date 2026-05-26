# Bonsai Move Worktree Design

## Goal

Add a `bonsai move` command that renames a managed worktree folder while keeping Git worktree metadata and Bonsai workspace state in sync.

## Command Shape

`bonsai move <worktree> <new-folder>`

`<worktree>` resolves the same way existing named worktree commands do: branch name, managed worktree folder name, or managed worktree slug. `<new-folder>` is the new directory name under the workspace root.

## Behavior

The command refuses to move the default worktree. It validates that `<new-folder>` is a safe single path segment, preserving capitalization exactly. It refuses to overwrite an existing path.

For managed worktrees, Bonsai resolves the source worktree, runs `git worktree move <old-path> <new-path>` from the default worktree context, updates `.bonsai/state.json` so the managed worktree `path` points at `<new-folder>`, then rewrites generated files using the existing sync behavior. The worktree slug does not change, so URLs, Caddy snippet names, and lifecycle log folders remain stable.

CLI output:

```text
Moved worktree: <old-path> -> <new-path>
```

## Data Flow

1. CLI finds the workspace root from the current directory.
2. Workflow loads state and resolves `<worktree>` with existing branch/path/slug matching.
3. Workflow validates the target folder name with the existing safe path segment rules.
4. Workflow calls the Git wrapper for `git worktree move`.
5. Workflow saves updated Bonsai state with the new folder path.
6. Workflow applies sync so generated `.env.local`, Caddy snippets, and root Caddyfile content match the new path where templates depend on it.

## Error Handling

The command raises a workspace error when:

- The requested worktree cannot be resolved.
- The requested worktree is the default worktree.
- The target folder name is unsafe or empty.
- The target path already exists.
- Git rejects the worktree move.

Git command failures use the existing command runner error handling.

## Testing

Add focused tests for:

- Planning and executing a move updates state path while preserving slug and slot.
- The Git wrapper calls `git worktree move <old-path> <new-path>`.
- The CLI command invokes the workflow and prints the moved path.
- The command rejects default worktree moves.
- The command rejects target path collisions and unsafe folder names.
- Generated files are refreshed after moving when templates use `WORKTREE_PATH`.
