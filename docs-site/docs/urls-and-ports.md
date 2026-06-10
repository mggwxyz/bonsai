---
title: Ports & URLs
---

# Ports & URLs

Every worktree gets a stable slot, and every configured service port is
`base_port + slot`. The default worktree holds slot 0, so a service with
`base_port = 4200` listens on 4200 in the default worktree, 4201 in the
first branch worktree, and so on. Slots — and therefore ports and URLs —
stay stable for the life of a worktree.

## Caddy Routing Is Machine-Global

Bonsai maintains a single Caddy config at `~/.bonsai/Caddyfile` with
per-app snippet directories at `~/.bonsai/caddy.d/<app>/`, so multiple
Bonsai projects coexist and all their `.localhost` URLs work
simultaneously. Routes survive reboot via a managed block Bonsai keeps in
Homebrew's boot config (`$(brew --prefix)/etc/Caddyfile`).

Project names must be unique per machine — two projects sharing the same
name will collide on hostnames and snippet paths.

When Caddy is not installed, Bonsai falls back to direct
`http://localhost:<port>` URLs. Both forms are expected and work fine.

## Open a Worktree URL

```bash
bonsai open
bonsai open ma-123-implement-auth
bonsai open ma-123-implement-auth --service api
```

Run `open` from inside a worktree to open that worktree's primary local URL
in your default browser. Pass a branch name, worktree directory, or slug to
open a different worktree, and `--service <name>` to open a non-primary
public service URL such as an API route.

By default `open` confirms the URL responds before launching the browser;
pass `--no-interactive` to print the resolved URL without probing.

With a `[browser_extension]` `extension_id` configured (see
[Configuration](configuration.md)), `bonsai open --label "Feature tab"`
opens the URL through the browser extension with a labeled tab.

## Diagnose URLs

```bash
bonsai urls
bonsai urls ma-123-implement-auth --service api
bonsai urls --diagnose https://api-ma-123-implement-auth.my-app.localhost
bonsai urls --format json
```

`urls` prints configured public service URLs with route diagnostics: the
global Caddyfile, the per-worktree snippet, Caddy validation, app listener,
TLS, and local CA trust guidance. Filter by worktree or `--service`, or use
`--diagnose <url>` when a specific URL is not working.

## Inspect Port Ownership

```bash
bonsai ports
bonsai ports --format json
bonsai ps
```

`ports` prints every configured service port with listener ownership
metadata from `lsof` when available. Each port is classified as `free`,
`owned` by the matching worktree, `conflict` with another process or
worktree, or `unknown` when the port is listening but the owner cannot be
identified. `ps` shows the same data filtered to ports with listeners.

When a branch worktree's ports conflict, `bonsai repair-ports` proposes a
conflict-free slot — see [Troubleshooting](troubleshooting.md).
