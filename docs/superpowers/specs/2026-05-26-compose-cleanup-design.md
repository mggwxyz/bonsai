# Docker Compose Teardown for Remove and Cleanup

## Purpose

Bonsai should tear down Docker Compose resources that belong to a managed
worktree before removing that worktree. This prevents stale containers,
networks, and port bindings from surviving after `bonsai remove` or
`bonsai cleanup --apply`.

The first version is cleanup lifecycle support only. Bonsai will not supervise
Compose processes, start Compose services, inspect logs, restart containers, or
add a dedicated `[compose]` config section.

## Scope

This feature applies to:

- `bonsai remove <worktree>`
- `bonsai cleanup --apply`, through its existing call to `execute_remove`

It does not change:

- `bonsai start`
- `bonsai list`
- `bonsai status`
- `bonsai doctor`
- generated project config beyond existing `[[env]]` support

## Compose Ownership Rule

Bonsai only attempts Compose teardown when the target worktree contains one of
these files at the worktree root:

- `compose.yaml`
- `compose.yml`
- `docker-compose.yaml`
- `docker-compose.yml`

The Compose project name is resolved in this order:

1. Read `COMPOSE_PROJECT_NAME` from the worktree's `.env.local`.
2. If `.env.local` is missing or has no `COMPOSE_PROJECT_NAME`, use the
   worktree folder name.

This matches Docker Compose's native `COMPOSE_PROJECT_NAME` behavior while
keeping a safe default for projects that rely on Compose's directory-name
fallback.

## Remove Flow

`bonsai remove <worktree>` should:

1. Refuse to remove the default worktree.
2. Resolve the managed worktree by branch, path, or slug.
3. Refuse dirty worktrees unless `--force` is passed.
4. If a Compose file exists, resolve the Compose project name.
5. Run `docker compose -p <project-name> down` from the worktree directory.
6. Remove the git worktree.
7. Remove generated Caddy snippets for the worktree slug.
8. Remove the worktree from Bonsai state.
9. Reload Caddy.

Compose teardown must happen before git worktree removal so Docker Compose can
read the worktree's Compose file and associated env files.

## Cleanup Flow

`bonsai cleanup --apply` should keep its existing PR-aware eligibility checks.
For each merged, eligible worktree, it should call the same removal path used by
`bonsai remove`.

Dry-run cleanup should not call Docker. It should continue reporting planned
cleanup decisions without side effects.

## Failure Handling

If there is no Compose file in the worktree, Bonsai skips Docker teardown and
continues removal normally.

If `docker` is missing or `docker compose -p <project-name> down` fails, Bonsai
must block worktree removal. This applies even when `--force` is passed.
`--force` only bypasses dirty-worktree safety; it does not permit deleting files
under a Compose project that may still be running.

Bonsai should surface a clear error that includes the project name and the
worktree path.

## User-Facing Output

`bonsai remove` should mention Compose teardown when it happens. The exact
format can follow existing CLI style, but it should make the project name
visible, for example:

```text
compose down authentic-ma-123-auth-flow
Removed ma-123-auth-flow
```

`bonsai cleanup --apply` should continue listing the final cleanup action per
branch. It does not need a separate line for Compose teardown in this first
version, because teardown is part of removal.

## Implementation Shape

Add a small Compose helper module or workflow helper that can:

- detect a root-level Compose file
- parse simple `.env.local` assignments using the existing env parser
- resolve the project name
- run `docker compose -p <project-name> down`

Keep the helper independent from GitHub cleanup logic. `execute_remove` should
own the shared removal lifecycle so `cleanup --apply` inherits the same behavior.

Do not remove Compose volumes in this version. The command is intentionally
`down`, not `down --volumes`.

## Testing

Add focused tests for:

- `execute_remove` runs `docker compose -p <name> down` before git worktree
  removal when a Compose file exists
- `execute_cleanup(..., apply=True)` inherits Compose teardown through
  `execute_remove`
- missing Compose files skip Docker commands
- `.env.local` `COMPOSE_PROJECT_NAME` wins over the folder-name fallback
- folder-name fallback is used when `.env.local` is missing or lacks
  `COMPOSE_PROJECT_NAME`
- Docker command failure blocks removal and leaves Bonsai state unchanged

Tests should use recording or stub runners. They must not require real Docker.
