---
title: Troubleshooting
---

# Troubleshooting

## Check Workspace Health

Run:

```bash
bonsai doctor
bonsai doctor --format json
```

`doctor` checks workspace state, config, git worktrees, generated files, Caddy files, Caddy availability, and configured service port conflicts.

Failed repairable checks point to:

```bash
bonsai doctor --apply
```

`doctor --apply` runs safe workspace repairs: structural state repair, generated-file sync, and configured Caddy bootstrap when Homebrew/Caddy state allows it.

If state points at missing or mis-slotted managed worktrees, preview structural repair first:

```bash
bonsai repair
```

Apply the state repair, then refresh generated files:

```bash
bonsai repair --apply
bonsai sync --apply
```

If a branch worktree's configured service ports are already busy, preview a slot reassignment plan:

```bash
bonsai repair-ports
bonsai repair-ports --apply
bonsai repair-ports --format json
```

`repair-ports` proposes the lowest conflict-free slot for affected branch worktrees. It is a dry run by default; `repair-ports --apply` writes the proposed slots and regenerates Bonsai-managed files.

## Checkout Does Not Change Directories

Install shell integration and open a new shell:

```bash
bonsai install-shell zsh
```

If you prefer manual setup:

```zsh
eval "$(bonsai shell-init zsh)"
```

## Local URL Is Missing Or Stale

Regenerate Bonsai-managed files and reload Caddy when needed:

```bash
bonsai sync --apply
```

Then run:

```bash
bonsai open
```

## Start Cannot Find Generated Env

`bonsai start` requires the target worktree's generated `.env.local` file. Regenerate it:

```bash
bonsai sync --apply
```

Then start the current or named worktree:

```bash
bonsai start
bonsai start ma-123-implement-auth
```

## Find Lifecycle Command Output

Bonsai logs configured install, setup, and start commands under `.bonsai/logs/<worktree-slug>/`.

```bash
bonsai logs
bonsai logs ma-123-implement-auth --command install
bonsai logs ma-123-implement-auth --command setup
bonsai logs ma-123-implement-auth --command start
```

## Cleanup Skips A Worktree

`bonsai cleanup` is conservative. It skips branches with no PR, open PRs, unmerged closed PRs, or uncommitted changes.

Preview cleanup:

```bash
bonsai cleanup
```

Apply cleanup:

```bash
bonsai cleanup --apply
```

Remove eligible dirty worktrees only when you mean it:

```bash
bonsai cleanup --apply --force
```

## Removal Fails During Compose Teardown

If a removable worktree has a root-level `compose.yaml`, `compose.yml`, `docker-compose.yaml`, or `docker-compose.yml`, Bonsai runs Docker Compose teardown before removing the git worktree. The project name comes from `.env.local` `COMPOSE_PROJECT_NAME` when present, then falls back to the worktree folder name.

Check the Compose project directly:

```bash
docker compose -p <project> ps
docker compose -p <project> down
```
