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

`doctor` checks workspace state, config, git worktrees, generated files, Caddy files, Caddy availability, stale Docker Compose network references, and owner-aware configured service port conflicts.

Failed repairable checks point to:

```bash
bonsai doctor --apply
```

`doctor --apply` runs safe workspace repairs: structural state repair, generated-file sync, stopped stale Docker Compose container removal, and configured Caddy bootstrap when Homebrew/Caddy state allows it.

If state points at missing or mis-slotted managed worktrees, preview structural repair first:

```bash
bonsai repair
```

`repair` removes missing managed worktree entries from `.bonsai/state.json` and repacks surviving managed slots. Paths that still exist but are not git worktrees are reported as warnings and left in state.

Apply the state repair, then refresh generated files:

```bash
bonsai repair --apply
bonsai sync --apply
```

If a branch worktree's configured service ports conflict with another process or worktree, preview a slot reassignment plan:

```bash
bonsai repair-ports
bonsai repair-ports --apply
bonsai repair-ports --format json
```

`repair-ports` proposes the lowest conflict-free slot for affected branch worktrees. A listener whose cwd is inside the matching worktree is treated as expected and does not trigger a slot change. It is a dry run by default; `repair-ports --apply` writes the proposed slots and regenerates Bonsai-managed files.

## Docker Network Not Found

Docker Desktop restarts, daemon resets, or `docker network prune` can leave stopped Compose containers pinned to network IDs that no longer exist. The next setup or migration command may fail with an error like:

```text
failed to set up container networking: network <network-id> not found
```

Run:

```bash
bonsai doctor
bonsai doctor --format json
```

If Bonsai finds the issue, the `docker compose networks` check fails with repair `docker-compose-networks` and points to:

```bash
bonsai doctor --apply
```

`doctor --apply` removes only stopped Docker Compose containers from Bonsai-managed worktrees whose saved network IDs are missing. It does not remove running containers, Docker networks, named volumes, or database data. The next lifecycle command recreates the removed Compose containers against the current Docker network.

## Inspect Port Owners

Run:

```bash
bonsai ports
bonsai ports --format json
bonsai ports --busy
```

`ports` lists every configured service port and uses `lsof` to identify listening processes when available. `ports --busy` shows the same data filtered to ports with listeners. Port statuses are `free`, `owned`, `conflict`, or `unknown`.

## Stop Or Restart A Worktree App

Run:

```bash
bonsai stop
bonsai stop ma-123-implement-auth
bonsai restart ma-123-implement-auth
```

`stop` first terminates the worktree's tracked background process from `bonsai up`, then terminates listener processes on the selected worktree's configured service ports when ownership can be matched to that worktree by process cwd. External or unknown owners are skipped unless `--force` is passed. Use `bonsai stop --all` to stop matching processes for every worktree.

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

Ask Bonsai why a URL is not working:

```bash
bonsai urls
bonsai urls ma-123-implement-auth --service api
bonsai urls --diagnose https://api-ma-123-implement-auth.my-app.localhost
bonsai urls --format json
```

`urls` checks the root Caddyfile, generated route snippet, Caddy validation, app listener, TLS route setup, and local CA trust guidance for each configured public URL.

If the route or root Caddyfile is missing or stale, regenerate Bonsai-managed files and reload Caddy when needed:

```bash
bonsai sync --apply
```

If the app listener check warns, start the worktree app:

```bash
bonsai start ma-123-implement-auth
```

If the route validates but the browser reports a certificate warning, trust Caddy's local CA:

```bash
caddy trust
```

Then open the primary or named service URL:

```bash
bonsai open
bonsai open ma-123-implement-auth --service api
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
