# Design: The Parallel Agents Release

Status: Proposal
Target: bonsai 0.9.0
Author: design spec

## 1. Why

Running multiple AI coding agents in parallel — each on its own git worktree —
became the dominant power-user workflow in early 2026. A category of tools grew
up around it (cmux, Conductor, Claude Squad, Vibe Kanban) and Claude Code itself
now ships `isolation: worktree` for subagents.

Almost all of these isolate the **code** and then hand you a **diff**. They do
not give each agent a **running, browsable app of its own**. Bonsai already
solves the hard part of that: stable per-worktree ports, generated `.env.local`,
Caddy HTTPS URLs, and process lifecycle. We have the engine; we have not yet
told the story.

**Positioning:** *Spin up N agents in parallel and get a live URL for each one's
running app — review the result, not just the diff.*

This release turns bonsai from "a worktree + ports utility" into "the parallel
agent workspace." It has three parts that ship together:

1. **`dispatch`** — make the agent a first-class lifecycle, not just `start`.
2. **`dash`** — a live mission-control TUI: every worktree, its agent, its app
   URL, its health, its diff.
3. **`review` + `promote`** — close the loop: compare attempts, merge the
   winner, clean up the losers.

`dispatch` produces the worktrees, `dash` is how you watch them, and
`review`/`promote` is how you finish. None of the three is compelling alone;
together they are the workflow.

## 2. Non-goals

- We do not bundle, install, or update any specific agent CLI. Bonsai launches
  whatever command the user configures (`claude`, `codex`, `aider`, a shell
  script). Bonsai is the orchestrator, not the agent.
- We do not implement an agent protocol or parse agent stdout for semantic
  meaning. Agent *state* is inferred from process liveness, exit code, and
  optional lightweight signals (see §3.3), not from understanding agent output.
- No GUI/web/mobile dashboard in this release. `dash` is a terminal TUI. (A web
  dashboard is a strong follow-up — it is the documented gap in cmux — but it is
  out of scope here.)
- Cross-platform (Linux) support is tracked separately. This release must not
  add *new* macOS-only assumptions, but it does not deliver Linux itself.

## 3. Feature 1 — `bonsai dispatch`

### 3.1 Concept

Today bonsai runs the project's *dev command* (`commands.start`). An agent is a
second, parallel kind of long-running process attached to a worktree. We model
it the same way we already model the app: a configured command, run detached,
tracked by a per-worktree record file, with logs and reattach.

`dispatch` does in one step what is currently several:

```
bonsai dispatch "implement password reset flow"
```

is equivalent to: pick/derive a branch name → `add` a worktree → write a task
file the agent can read → launch the configured agent command in that worktree
→ record it for `ps`/`dash`.

### 3.2 Config

New optional `[agent]` table in `.bonsai.toml`, parsed in `config.py` alongside
`[run]` and `[caddy]`:

```toml
[agent]
# Command bonsai launches per worktree. Receives the task via $BONSAI_TASK and
# a task file at $BONSAI_TASK_FILE; templating uses the same engine as service
# URLs.
command = "claude --dangerously-skip-permissions -p \"$BONSAI_TASK\""

# Optional: how a worktree branch name is derived from a free-text task when the
# user does not pass one. Default: slugify + short random suffix.
branch_prefix = "agent"

# Optional: command bonsai runs to ask the agent whether it is idle/waiting.
# Exit 0 = done, 2 = waiting-for-input, anything else = working. Optional; when
# absent, dash falls back to process-liveness + log-mtime heuristics (§3.3).
status_command = ""
```

Add `AgentConfig` to `models.py` and an `_agent(...)` parser in `config.py`
following the exact shape of `_run(...)`. `command` is required iff the
`[agent]` table is present; validation lives in `_validate`.

### 3.3 Agent state model

`dash` and `ps` need a single derived status per worktree's agent. Inference
order, cheapest first:

| State | How it is determined |
|-------|----------------------|
| `none` | No agent record for this worktree. |
| `working` | Record exists, pid alive, recent log writes. |
| `idle` | Record exists, pid alive, no log writes for > N seconds. |
| `waiting` | `agent.status_command` exits 2 (optional signal). |
| `done` | Pid exited 0. |
| `failed` | Pid exited non-zero. |

This reuses the existing app-process machinery: add an agent record path next to
`_app_process_record_path` (e.g. `.bonsai/agents/<slug>.json`) holding
`{branch, worktree_path, pid, argv, log_path, started_at, exit_code}`. Liveness
uses the existing `_process_is_alive`; log mtime gives the working/idle split
with no agent cooperation required. `status_command` is a pure enhancement.

### 3.4 Command surface

```
bonsai dispatch "<task>"            # add worktree + launch agent (the headline)
bonsai dispatch <branch> "<task>"   # explicit branch name
bonsai dispatch --pr 123 "<task>"   # dispatch into a PR worktree
bonsai agent stop [name]            # stop the agent in a/this worktree
bonsai agent logs [name]            # tail agent logs (wraps existing logs)
bonsai agent restart [name] "<task>"
```

`dispatch` is the marketing verb and lives at top level. The rarer verbs live
under an `agent` sub-app to keep the top-level surface clean. `dispatch` is
additive: it composes `execute_add` + a new `execute_dispatch` in
`workflows/processes.py` that mirrors `execute_up` (detached launch + record).

### 3.5 Fan-out

The workflow people actually want is N attempts at one task:

```
bonsai dispatch "fix the flaky checkout test" --fanout 3
```

Creates three worktrees (`...-1`, `...-2`, `...-3`), launches the agent in each,
and prints the three live URLs. This single line is the demo. It is a thin loop
over single dispatch; the value is that ports, env, and URLs are already
isolated per worktree, so three running apps Just Work.

## 4. Feature 2 — `bonsai dash`

### 4.1 Concept

A live full-terminal TUI (Rich `Live` + a refreshing table; no new dependency —
`rich` is already a dep). One row per worktree:

```
WORKTREE              AGENT      APP        URL                                  DIFF
agent-reset-1         ● working  ● ready    https://reset-1.my-app.localhost     +142 −7  3 files
agent-reset-2         ◐ idle     ● ready    https://reset-2.my-app.localhost     +98  −3  2 files
agent-reset-3         ✓ done     ○ stopped  https://reset-3.my-app.localhost     +210 −40 6 files
main                  – none     ● ready    https://my-app.localhost             clean
```

Columns:

- **Agent** — the §3.3 state.
- **App** — reuse readiness probing already in `execute_up`/`probes.py`
  (`ready`/`starting`/`stopped`).
- **URL** — the primary public service URL straight from `WorktreeFacts`
  (`build_worktree_facts` already computes these).
- **Diff** — `+added −removed N files` vs the base branch, from `git.py`
  (`git diff --shortstat` against `base_branch`).

### 4.2 Implementation

- New `workflows/dashboard.py` building a `DashboardSnapshot` (pure data) per
  refresh by composing existing pieces: registry → worktrees → `WorktreeFacts`
  for URLs, app records for app health, agent records for agent state, `git.py`
  for diff stats. Keeping snapshot-building pure keeps it unit-testable without a
  terminal.
- `cli.py` adds `@app.command("dash")` rendering the snapshot on a timer
  (default 2s). `--once` prints a single frame and exits — this is the
  agent-friendly / CI / screenshot path and what tests assert against.
- `--json` emits the snapshot as JSON, consistent with `list --json` /
  `status --json`, so the dashboard data is scriptable.

### 4.3 Interactivity (minimal)

Keep v1 mostly read-only to bound scope. Supported keys: `o` open focused
worktree's URL (reuse `execute_open`), `l` tail focused agent logs, `q` quit.
Promote/merge from inside `dash` is deferred to a follow-up — `review`/`promote`
exist as commands first (§5).

## 5. Feature 3 — `bonsai review` and `bonsai promote`

### 5.1 The problem

After a fan-out you have several attempts and one question: *which one, and then
clean up the rest.* No tool closes this well today.

### 5.2 `bonsai review`

```
bonsai review                 # summarize all worktrees with an active/finished agent
bonsai review reset-1 reset-2 # focus a subset
```

Prints, per worktree: branch, agent state, live URL, diff shortstat, and the
last N lines of agent log. With `--diff` it shells to the user's git pager for a
full diff per worktree. With `--open` it opens every worktree's URL in labeled
browser tabs (the labeled-open machinery already exists). The point: judge N
running apps in one command instead of N manual checkouts.

### 5.3 `bonsai promote`

```
bonsai promote reset-2        # the winner
```

Atomically:

1. Verify `reset-2` is clean / committed (refuse or `--stash` otherwise).
2. Merge (or rebase, configurable) `reset-2` into `base_branch`.
3. Offer to remove the *other* sibling worktrees from the same fan-out
   (`--cleanup`, prompted by default), reusing `execute_cleanup` /
   `execute_remove` — including their app/agent processes and ports.

`promote` is `merge + targeted cleanup` expressed as the user's actual intent.
It composes existing remove/cleanup workflows rather than reimplementing them.
Sibling grouping comes from a `fanout_id` written into the agent record at
dispatch time.

## 6. Architecture fit (summary)

| New thing | Built on existing |
|-----------|-------------------|
| `[agent]` config | `config.py` parsers, `models.py` dataclasses, `_validate` |
| agent records | `_app_process_record_path` pattern, `_process_is_alive`, logs |
| `dispatch` | `execute_add` + `execute_up`-style detached launch |
| `dash` snapshot | registry, `WorktreeFacts`, app records, `git.py`, `probes.py` |
| `review` | `WorktreeFacts` URLs, labeled-open, logs, git diff |
| `promote` | `git.py` merge + `execute_cleanup`/`execute_remove` |

No new runtime dependency. Every new command has a `--json` and/or `--once`
non-interactive path so the existing test style (assert on plans/JSON) holds.

## 7. Phasing

1. **Phase 1 — dispatch + agent records.** Config, models, records, `dispatch`,
   `agent stop/logs`, `ps` shows agents. Shippable and useful alone.
2. **Phase 2 — dash.** Snapshot + TUI + `--once`/`--json`. The demo.
3. **Phase 3 — review + promote + fan-out grouping.** Closes the loop.

Ship 1 and 2 together as the marketable moment; 3 follows fast.

## 8. Risks / open questions

- **Agent state accuracy.** The log-mtime heuristic for working/idle is coarse.
  `status_command` mitigates but is opt-in. Acceptable for v1; revisit if users
  report flapping.
- **Detached agent processes that need a TTY.** Some agent CLIs misbehave when
  not attached to a terminal. Mitigation: route through the existing multiplexer
  backends (tmux/herdr/cmux) so each agent gets a real pane and is reattachable —
  bonsai already has this machinery. Decide per-backend default in Phase 1.
- **Secrets in task text.** `$BONSAI_TASK` and the task file may contain
  sensitive prompts; write the task file with `0600` and keep it inside the
  worktree's gitignored bonsai area.
- **Naming.** `dispatch` vs `spawn` vs `run-agent`. `dispatch` reads as
  intentional and is unclaimed by the existing surface; recommend it.
