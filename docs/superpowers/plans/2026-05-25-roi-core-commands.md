# ROI Core Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement real `bonsai start`, `bonsai doctor`, and `bonsai sync` repair behavior while leaving rich status/list and cleanup work separate.

**Architecture:** Keep Typer commands thin and put behavior in `bonsai.workflows`. Add small result dataclasses in `bonsai.models`, use the existing rendering/config/state helpers for generated files, and add one runner method for foreground command execution. If the in-progress status-spinner work has already changed `SubprocessRunner.run()`, preserve that behavior and keep `run_stream()` unwrapped so long-running app output goes directly to the terminal.

**Tech Stack:** Python 3.12, Typer, Rich, pytest, existing Bonsai runner/workflow/config modules.

---

## Scope Check

This spec covers one cohesive local-workspace batch: run the configured app command, check workspace health, and repair generated Bonsai files. It does not include rich list/status, PR cleanup, Docker Compose lifecycle management, editor/browser automation, or background process supervision.

## File Structure

- Modify `src/bonsai/models.py`: add small dataclasses for configured worktree targets, sync actions, sync plans, doctor checks, and doctor reports.
- Modify `src/bonsai/process.py`: add `run_stream()` to the runner protocol and concrete runners so `bonsai start` can hand foreground output to the user's terminal.
- Modify `src/bonsai/workflows.py`: add target resolution, start execution, sync planning/apply, doctor checks, and focused helper functions.
- Modify `src/bonsai/cli.py`: wire real `start`, `sync`, and `doctor` commands to workflows and render concise output.
- Modify `tests/test_workflows.py`: add workflow tests for start, sync planning/apply, and doctor health checks.
- Modify `tests/test_cli.py`: replace stub command tests with real CLI integration tests using monkeypatched workflow calls where needed.
- Modify `README.md`: update the command descriptions once behavior exists.

---

### Task 1: Add Foreground Runner Support

**Files:**
- Modify: `src/bonsai/process.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Write the failing test**

Add this test near `test_recording_runner_captures_commands_without_running_them` in `tests/test_workflows.py`:

```python
def test_recording_runner_captures_stream_commands() -> None:
    runner = RecordingRunner()

    result = runner.run_stream(
        ["yarn", "dev"],
        cwd=Path("/tmp/worktree"),
        env={"FRONTEND_PORT": "4201"},
    )

    assert result == 0
    assert runner.commands == [
        CommandSpec(
            argv=("yarn", "dev"),
            cwd=Path("/tmp/worktree"),
            env=(("FRONTEND_PORT", "4201"),),
        )
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/test_workflows.py::test_recording_runner_captures_stream_commands -v
```

Expected: FAIL with `AttributeError: 'RecordingRunner' object has no attribute 'run_stream'`.

- [ ] **Step 3: Implement the minimal runner method**

In `src/bonsai/process.py`, update `Runner`, `SubprocessRunner`, and `RecordingRunner`:

```python
class Runner(Protocol):
    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        ...

    def run_stream(
        self,
        argv: list[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> int:
        ...
```

Add this method to `SubprocessRunner`:

```python
def run_stream(
    self,
    argv: list[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    process_env = None
    if env is not None:
        process_env = os.environ.copy()
        process_env.update(env)
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=process_env,
        check=False,
    )
    return completed.returncode
```

Add this method to `RecordingRunner`:

```python
def run_stream(
    self,
    argv: list[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    recorded_env = tuple(sorted(env.items())) if env is not None else ()
    self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
    return 0
```

- [ ] **Step 4: Run the focused test**

Run:

```bash
uv run pytest tests/test_workflows.py::test_recording_runner_captures_stream_commands -v
```

Expected: PASS.

- [ ] **Step 5: Run existing runner-adjacent tests**

Run:

```bash
uv run pytest tests/test_workflows.py::test_recording_runner_captures_commands_without_running_them tests/test_workflows.py::test_command_summary_formats_command_and_cwd -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bonsai/process.py tests/test_workflows.py
git commit -m "feat: support foreground command runner"
```

---

### Task 2: Implement Start Workflow

**Files:**
- Modify: `src/bonsai/models.py`
- Modify: `src/bonsai/workflows.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Write failing start workflow tests**

Add imports in `tests/test_workflows.py`:

```python
from bonsai.errors import BonsaiConfigError
from bonsai.workflows import execute_start, resolve_start_target
```

Add these tests near the checkout tests:

```python
def test_resolve_start_target_includes_default_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    target = resolve_start_target(workspace_root, "main", default_worktree)

    assert target.branch == "main"
    assert target.worktree.path == "main"
    assert target.worktree.slot == 0
    assert target.worktree_path == default_worktree
```

```python
def test_execute_start_runs_configured_command_with_generated_env(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    (feature_worktree / ".env.local").write_text(
        "FRONTEND_PORT=4201\nCOMPOSE_PROJECT_NAME=authentic-feature\n",
        encoding="utf-8",
    )
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    exit_code = execute_start(runner, workspace_root, "feature", feature_worktree)

    assert exit_code == 0
    assert runner.commands == [
        CommandSpec(
            argv=("yarn", "dev"),
            cwd=feature_worktree,
            env=(
                ("COMPOSE_PROJECT_NAME", "authentic-feature"),
                ("FRONTEND_PORT", "4201"),
            ),
        )
    ]
```

```python
def test_execute_start_fails_when_start_command_is_missing(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG.replace('start = "yarn dev"\n', ""))
    (default_worktree / ".env.local").write_text("FRONTEND_PORT=4200\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(BonsaiConfigError, match=r"commands.start"):
        execute_start(runner, workspace_root, None, default_worktree)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest tests/test_workflows.py::test_resolve_start_target_includes_default_worktree tests/test_workflows.py::test_execute_start_runs_configured_command_with_generated_env tests/test_workflows.py::test_execute_start_fails_when_start_command_is_missing -v
```

Expected: FAIL with import errors for `execute_start` and `resolve_start_target`.

- [ ] **Step 3: Add start dataclasses**

In `src/bonsai/models.py`, add:

```python
@dataclass(frozen=True)
class WorktreeTarget:
    branch: str
    worktree: ManagedWorktree
    worktree_path: Path
```

- [ ] **Step 4: Implement target resolution and start execution**

In `src/bonsai/workflows.py`, import `WorktreeTarget`. Add these helpers after `_resolve_current_worktree`:

```python
def _configured_worktree_targets(
    state: BonsaiState,
    workspace_root: Path,
) -> tuple[WorktreeTarget, ...]:
    default = WorktreeTarget(
        branch=state.default_branch,
        worktree=ManagedWorktree(
            path=state.default_worktree,
            slug=branch_slug(state.default_branch),
            slot=0,
        ),
        worktree_path=workspace_root / state.default_worktree,
    )
    managed = tuple(
        WorktreeTarget(
            branch=branch,
            worktree=worktree,
            worktree_path=workspace_root / worktree.path,
        )
        for branch, worktree in state.worktrees.items()
    )
    return (default, *managed)
```

Add the public functions:

```python
def resolve_start_target(
    workspace_root: Path,
    name: str | None,
    current_path: Path,
) -> WorktreeTarget:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    if name is None:
        branch, worktree, worktree_path = _resolve_current_worktree(
            state,
            workspace_root,
            current_path,
        )
        return WorktreeTarget(branch=branch, worktree=worktree, worktree_path=worktree_path)

    for target in _configured_worktree_targets(state, workspace_root):
        if name in {target.branch, target.worktree.path, target.worktree.slug}:
            return target

    raise BonsaiWorkspaceError(f"Unknown Bonsai worktree: {name}")
```

```python
def execute_start(
    runner: Runner,
    workspace_root: Path,
    name: str | None,
    current_path: Path,
) -> int:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    if config.commands.start is None:
        raise BonsaiConfigError("Missing config key commands.start")

    target = resolve_start_target(workspace_root, name, current_path)
    env_path = target.worktree_path / ".env.local"
    if not env_path.exists():
        raise BonsaiWorkspaceError(
            f"Missing generated env file at {env_path}. Run: bonsai sync --apply"
        )
    env = parse_env_content(env_path.read_text(encoding="utf-8"))
    return runner.run_stream(
        shlex.split(config.commands.start),
        cwd=target.worktree_path,
        env=env,
    )
```

- [ ] **Step 5: Run focused start workflow tests**

Run:

```bash
uv run pytest tests/test_workflows.py::test_resolve_start_target_includes_default_worktree tests/test_workflows.py::test_execute_start_runs_configured_command_with_generated_env tests/test_workflows.py::test_execute_start_fails_when_start_command_is_missing -v
```

Expected: PASS.

- [ ] **Step 6: Add missing-env start test**

Add:

```python
def test_execute_start_requires_generated_env_file(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    with pytest.raises(BonsaiWorkspaceError, match=r"bonsai sync --apply"):
        execute_start(RecordingRunner(), workspace_root, None, default_worktree)
```

- [ ] **Step 7: Run the missing-env test to verify it passes**

Run:

```bash
uv run pytest tests/test_workflows.py::test_execute_start_requires_generated_env_file -v
```

Expected: PASS because Step 4 already implemented the missing-env guard.

- [ ] **Step 8: Run all workflow start tests**

Run:

```bash
uv run pytest tests/test_workflows.py -k "start or stream" -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/bonsai/models.py src/bonsai/workflows.py tests/test_workflows.py
git commit -m "feat: run configured start command"
```

---

### Task 3: Implement Sync Planning

**Files:**
- Modify: `src/bonsai/models.py`
- Modify: `src/bonsai/workflows.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Write failing sync planner test**

Add imports in `tests/test_workflows.py`:

```python
from bonsai.workflows import plan_sync
```

Add:

```python
def test_plan_sync_reports_missing_and_stale_generated_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    (feature_worktree / ".env.local").write_text("STALE=1\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
        ),
    )

    plan = plan_sync(workspace_root)

    actions = {(action.kind, action.path.relative_to(workspace_root)) for action in plan.actions}
    assert ("write", Path("main/.env.local")) in actions
    assert ("write", Path("feature/.env.local")) in actions
    assert ("write", Path("Caddyfile")) in actions
    assert ("write", Path("caddy.d/main-frontend.caddy")) in actions
    assert ("write", Path("caddy.d/feature-frontend.caddy")) in actions
    assert plan.reload_caddy is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/test_workflows.py::test_plan_sync_reports_missing_and_stale_generated_files -v
```

Expected: FAIL with import error for `plan_sync`.

- [ ] **Step 3: Add sync dataclasses**

In `src/bonsai/models.py`, add:

```python
@dataclass(frozen=True)
class SyncFileAction:
    kind: str
    path: Path
    content: str | None = None


@dataclass(frozen=True)
class SyncPlan:
    actions: tuple[SyncFileAction, ...]
    reload_caddy: bool
```

- [ ] **Step 4: Implement desired generated-file planning**

In `src/bonsai/workflows.py`, import `SyncFileAction` and `SyncPlan`. Add:

```python
def _desired_sync_files(
    config: BonsaiConfig,
    state: BonsaiState,
    workspace_root: Path,
) -> dict[Path, str]:
    snippets_dir_name = _safe_path_segment(config.caddy.snippets_dir, "caddy snippets_dir")
    root_caddyfile = _safe_path_segment(config.caddy.root_caddyfile, "caddy root_caddyfile")
    snippets_dir = workspace_root / snippets_dir_name
    desired: dict[Path, str] = {
        workspace_root / root_caddyfile: render_root_caddyfile(snippets_dir),
    }
    for target in _configured_worktree_targets(state, workspace_root):
        desired[target.worktree_path / ".env.local"] = render_env_local(
            config,
            target.branch,
            target.worktree.slot,
            target.worktree_path,
        )
        for service_name, content in render_caddy_snippets(
            config,
            target.branch,
            target.worktree.slot,
            target.worktree_path,
        ).items():
            service_name = _safe_path_segment(service_name, "service name")
            desired[snippets_dir / f"{target.worktree.slug}-{service_name}.caddy"] = content
    return desired
```

Add:

```python
def plan_sync(workspace_root: Path) -> SyncPlan:
    state = load_state(workspace_root / ".bonsai" / "state.json")
    config = load_workspace_config(workspace_root, state)
    desired = _desired_sync_files(config, state, workspace_root)
    actions: list[SyncFileAction] = []
    for path, content in sorted(desired.items(), key=lambda item: str(item[0])):
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            actions.append(SyncFileAction(kind="write", path=path, content=content))
    return SyncPlan(actions=tuple(actions), reload_caddy=bool(config.public_services()))
```

- [ ] **Step 5: Run focused sync planner test**

Run:

```bash
uv run pytest tests/test_workflows.py::test_plan_sync_reports_missing_and_stale_generated_files -v
```

Expected: PASS.

- [ ] **Step 6: Add dry-run no-write test**

Add import:

```python
from bonsai.workflows import execute_sync
```

Add:

```python
def test_execute_sync_dry_run_does_not_write_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    plan = execute_sync(RecordingRunner(), workspace_root, apply=False)

    assert any(action.path == default_worktree / ".env.local" for action in plan.actions)
    assert not (default_worktree / ".env.local").exists()
```

- [ ] **Step 7: Implement dry-run execute_sync**

Add:

```python
def execute_sync(runner: Runner, workspace_root: Path, apply: bool = False) -> SyncPlan:
    plan = plan_sync(workspace_root)
    if not apply:
        return plan
    for action in plan.actions:
        if action.kind == "write" and action.content is not None:
            action.path.parent.mkdir(parents=True, exist_ok=True)
            action.path.write_text(action.content, encoding="utf-8")
    return plan
```

- [ ] **Step 8: Run sync dry-run tests**

Run:

```bash
uv run pytest tests/test_workflows.py::test_plan_sync_reports_missing_and_stale_generated_files tests/test_workflows.py::test_execute_sync_dry_run_does_not_write_files -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/bonsai/models.py src/bonsai/workflows.py tests/test_workflows.py
git commit -m "feat: plan generated file sync"
```

---

### Task 4: Implement Sync Apply, Stale Snippet Removal, and Caddy Reload

**Files:**
- Modify: `src/bonsai/workflows.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Write failing apply test**

Add:

```python
def test_execute_sync_apply_writes_files_and_reloads_caddy(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    plan = execute_sync(runner, workspace_root, apply=True)

    assert (default_worktree / ".env.local").exists()
    assert (workspace_root / "Caddyfile").exists()
    assert (workspace_root / "caddy.d" / "main-frontend.caddy").exists()
    assert plan.reload_caddy is True
    assert runner.commands[-1] == caddy_reload_plan(workspace_root / "Caddyfile")
```

- [ ] **Step 2: Run the apply test to verify it fails**

Run:

```bash
uv run pytest tests/test_workflows.py::test_execute_sync_apply_writes_files_and_reloads_caddy -v
```

Expected: FAIL because `execute_sync` does not reload Caddy.

- [ ] **Step 3: Reload Caddy after applying writes**

Update `execute_sync` in `src/bonsai/workflows.py`:

```python
def execute_sync(runner: Runner, workspace_root: Path, apply: bool = False) -> SyncPlan:
    plan = plan_sync(workspace_root)
    if not apply:
        return plan
    for action in plan.actions:
        if action.kind == "write" and action.content is not None:
            action.path.parent.mkdir(parents=True, exist_ok=True)
            action.path.write_text(action.content, encoding="utf-8")
        elif action.kind == "remove":
            action.path.unlink(missing_ok=True)
    if plan.reload_caddy:
        state = load_state(workspace_root / ".bonsai" / "state.json")
        config = load_workspace_config(workspace_root, state)
        reload_workspace_caddy(runner, config, workspace_root)
    return plan
```

- [ ] **Step 4: Run the apply test**

Run:

```bash
uv run pytest tests/test_workflows.py::test_execute_sync_apply_writes_files_and_reloads_caddy -v
```

Expected: PASS.

- [ ] **Step 5: Write failing stale snippet test**

Add:

```python
def test_plan_sync_removes_stale_configured_service_snippets(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    snippets_dir = workspace_root / "caddy.d"
    default_worktree.mkdir(parents=True)
    snippets_dir.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    stale = snippets_dir / "old-feature-frontend.caddy"
    stale.write_text("https://old.authentic.localhost {\n}\n", encoding="utf-8")
    keep = snippets_dir / "handwritten.caddy"
    keep.write_text("http://example.localhost {\n}\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    plan = plan_sync(workspace_root)

    remove_paths = {action.path for action in plan.actions if action.kind == "remove"}
    assert stale in remove_paths
    assert keep not in remove_paths
```

- [ ] **Step 6: Run stale snippet test to verify it fails**

Run:

```bash
uv run pytest tests/test_workflows.py::test_plan_sync_removes_stale_configured_service_snippets -v
```

Expected: FAIL because `plan_sync` does not add remove actions.

- [ ] **Step 7: Add stale snippet detection**

In `src/bonsai/workflows.py`, add:

```python
def _stale_generated_snippet_actions(
    config: BonsaiConfig,
    workspace_root: Path,
    desired_paths: set[Path],
) -> tuple[SyncFileAction, ...]:
    snippets_dir_name = _safe_path_segment(config.caddy.snippets_dir, "caddy snippets_dir")
    snippets_dir = workspace_root / snippets_dir_name
    if not snippets_dir.exists():
        return ()
    service_suffixes = tuple(f"-{service.name}.caddy" for service in config.public_services())
    actions: list[SyncFileAction] = []
    for path in sorted(snippets_dir.glob("*.caddy")):
        if path in desired_paths:
            continue
        if any(path.name.endswith(suffix) for suffix in service_suffixes):
            actions.append(SyncFileAction(kind="remove", path=path))
    return tuple(actions)
```

Update `plan_sync` after write actions are collected:

```python
actions.extend(
    _stale_generated_snippet_actions(
        config,
        workspace_root,
        set(desired),
    )
)
```

- [ ] **Step 8: Run stale snippet and apply tests**

Run:

```bash
uv run pytest tests/test_workflows.py::test_plan_sync_removes_stale_configured_service_snippets tests/test_workflows.py::test_execute_sync_apply_writes_files_and_reloads_caddy -v
```

Expected: PASS.

- [ ] **Step 9: Add no-public-services reload test**

Add:

```python
def test_execute_sync_apply_skips_caddy_reload_without_public_services(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(
        default_worktree,
        """
name = "authentic"
base_branch = "main"

[commands]
start = "yarn dev"

[[services]]
name = "db"
port_env = "DB_PORT"
base_port = 5555
public = false
""",
    )
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )

    plan = execute_sync(runner, workspace_root, apply=True)

    assert plan.reload_caddy is False
    assert runner.commands == []
```

- [ ] **Step 10: Run sync workflow tests**

Run:

```bash
uv run pytest tests/test_workflows.py -k "sync" -v
```

Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/bonsai/workflows.py tests/test_workflows.py
git commit -m "feat: apply generated file sync"
```

---

### Task 5: Implement Doctor Workflow

**Files:**
- Modify: `src/bonsai/models.py`
- Modify: `src/bonsai/workflows.py`
- Test: `tests/test_workflows.py`

- [ ] **Step 1: Write failing healthy doctor test**

Add imports:

```python
from bonsai.workflows import check_workspace_health
```

Add:

```python
def test_check_workspace_health_passes_for_complete_workspace(tmp_path: Path, monkeypatch) -> None:
    class HealthyRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[0] == "git" and "rev-parse" in argv:
                return CommandResult(returncode=0, stdout="true\n")
            if argv[0] == "caddy":
                return CommandResult(returncode=0, stdout="v2.8.0\n")
            return CommandResult(returncode=0)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )
    execute_sync(RecordingRunner(), workspace_root, apply=True)
    monkeypatch.setattr("bonsai.workflows._check_port_listening", lambda _port: False)

    report = check_workspace_health(HealthyRunner(), workspace_root)

    assert report.failed is False
    assert all(check.status != "fail" for check in report.checks)
```

- [ ] **Step 2: Run the healthy doctor test to verify it fails**

Run:

```bash
uv run pytest tests/test_workflows.py::test_check_workspace_health_passes_for_complete_workspace -v
```

Expected: FAIL with import error for `check_workspace_health`.

- [ ] **Step 3: Add doctor dataclasses**

In `src/bonsai/models.py`, add:

```python
@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    hint: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def failed(self) -> bool:
        return any(check.status == "fail" for check in self.checks)
```

- [ ] **Step 4: Implement basic doctor checks**

In `src/bonsai/workflows.py`, import `DoctorCheck` and `DoctorReport`. Add:

```python
def _check_port_listening(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False
```

Add:

```python
def check_workspace_health(runner: Runner, workspace_root: Path) -> DoctorReport:
    checks: list[DoctorCheck] = []
    state_path = workspace_root / ".bonsai" / "state.json"
    if not state_path.exists():
        return DoctorReport(
            checks=(
                DoctorCheck(
                    name="workspace state",
                    status="fail",
                    detail=f"Missing {state_path}",
                ),
            )
        )

    state = load_state(state_path)
    config = load_workspace_config(workspace_root, state)
    checks.append(DoctorCheck("workspace state", "ok", str(state_path)))
    checks.append(DoctorCheck("config", "ok", str(config.path)))

    git_result = runner.run(["git", "--version"], check=False)
    checks.append(
        DoctorCheck(
            "git",
            "ok" if git_result.returncode == 0 else "fail",
            git_result.stdout.strip() or "git command failed",
        )
    )

    for target in _configured_worktree_targets(state, workspace_root):
        if not target.worktree_path.exists():
            checks.append(
                DoctorCheck(
                    f"worktree {target.branch}",
                    "fail",
                    f"Missing {target.worktree_path}",
                )
            )
            continue
        if not is_git_worktree(runner, target.worktree_path):
            checks.append(
                DoctorCheck(
                    f"worktree {target.branch}",
                    "fail",
                    f"Not a git worktree: {target.worktree_path}",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    f"worktree {target.branch}",
                    "ok",
                    str(target.worktree_path),
                )
            )

        env_path = target.worktree_path / ".env.local"
        if env_path.exists():
            checks.append(DoctorCheck(f"env {target.branch}", "ok", str(env_path)))
        else:
            checks.append(
                DoctorCheck(
                    f"env {target.branch}",
                    "fail",
                    f"Missing {env_path}",
                    "Run: bonsai sync --apply",
                )
            )

    expected_sync = plan_sync(workspace_root)
    for action in expected_sync.actions:
        if action.kind == "write" and action.path.name.endswith(".caddy"):
            checks.append(
                DoctorCheck(
                    f"caddy snippet {action.path.name}",
                    "fail",
                    f"Missing or stale {action.path}",
                    "Run: bonsai sync --apply",
                )
            )

    if config.public_services():
        root_caddyfile = workspace_root / _safe_path_segment(
            config.caddy.root_caddyfile,
            "caddy root_caddyfile",
        )
        checks.append(
            DoctorCheck(
                "root Caddyfile",
                "ok" if root_caddyfile.exists() else "fail",
                str(root_caddyfile),
                None if root_caddyfile.exists() else "Run: bonsai sync --apply",
            )
        )
        caddy_result = runner.run(["caddy", "version"], check=False)
        checks.append(
            DoctorCheck(
                "caddy",
                "ok" if caddy_result.returncode == 0 else "fail",
                caddy_result.stdout.strip() or "caddy command failed",
            )
        )

    for target in _configured_worktree_targets(state, workspace_root):
        for service in config.services:
            port = service.base_port + target.worktree.slot
            if _check_port_listening(port):
                checks.append(
                    DoctorCheck(
                        f"port {port}",
                        "fail",
                        f"{service.name} port is already in use",
                    )
                )
            else:
                checks.append(DoctorCheck(f"port {port}", "ok", service.name))

    return DoctorReport(checks=tuple(checks))
```

- [ ] **Step 5: Run the healthy doctor test**

Run:

```bash
uv run pytest tests/test_workflows.py::test_check_workspace_health_passes_for_complete_workspace -v
```

Expected: PASS.

- [ ] **Step 6: Write missing generated file doctor test**

Add:

```python
def test_check_workspace_health_fails_for_missing_generated_env(tmp_path: Path, monkeypatch) -> None:
    class GitRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            if argv[0] == "git" and "rev-parse" in argv:
                return CommandResult(returncode=0, stdout="true\n")
            return CommandResult(returncode=0, stdout="ok\n")

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )
    monkeypatch.setattr("bonsai.workflows._check_port_listening", lambda _port: False)

    report = check_workspace_health(GitRunner(), workspace_root)

    assert report.failed is True
    assert any(check.name == "env main" and check.hint == "Run: bonsai sync --apply" for check in report.checks)
```

- [ ] **Step 7: Run missing generated file test**

Run:

```bash
uv run pytest tests/test_workflows.py::test_check_workspace_health_fails_for_missing_generated_env -v
```

Expected: PASS.

- [ ] **Step 8: Write port conflict doctor test**

Add:

```python
def test_check_workspace_health_reports_port_conflicts(tmp_path: Path, monkeypatch) -> None:
    class GitRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> CommandResult:
            if argv[0] == "git" and "rev-parse" in argv:
                return CommandResult(returncode=0, stdout="true\n")
            return CommandResult(returncode=0, stdout="ok\n")

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={},
        ),
    )
    execute_sync(RecordingRunner(), workspace_root, apply=True)
    monkeypatch.setattr("bonsai.workflows._check_port_listening", lambda port: port == 4200)

    report = check_workspace_health(GitRunner(), workspace_root)

    assert report.failed is True
    assert any(check.name == "port 4200" and check.status == "fail" for check in report.checks)
```

- [ ] **Step 9: Run doctor workflow tests**

Run:

```bash
uv run pytest tests/test_workflows.py -k "health or doctor" -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/bonsai/models.py src/bonsai/workflows.py tests/test_workflows.py
git commit -m "feat: check workspace health"
```

---

### Task 6: Wire CLI Commands

**Files:**
- Modify: `src/bonsai/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI start test**

In `tests/test_cli.py`, add:

```python
def test_start_executes_workflow(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_start(_runner, root: Path, branch: str | None, current_path: Path) -> int:
        calls.append((root, branch, current_path))
        return 7

    monkeypatch.setattr(cli, "execute_start", fake_execute_start, raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["start", "feature"])

    assert result.exit_code == 7
    assert calls == [(tmp_path, "feature", tmp_path)]
    assert "Starting feature" in result.stdout
```

- [ ] **Step 2: Run CLI start test to verify it fails**

Run:

```bash
uv run pytest tests/test_cli.py::test_start_executes_workflow -v
```

Expected: FAIL because the current stub does not call `execute_start`.

- [ ] **Step 3: Update CLI imports and start command**

In `src/bonsai/cli.py`, import:

```python
from bonsai.workflows import (
    check_workspace_health,
    execute_add,
    execute_checkout,
    execute_clone,
    execute_remove,
    execute_start,
    execute_sync,
    plan_open_url,
    workspace_config_path,
)
```

Replace the `start` command with:

```python
@app.command()
def start(branch: str | None = None) -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        label = branch or "current worktree"
        console.print(f"Starting {label}")
        exit_code = execute_start(SubprocessRunner(), root_path, branch, Path.cwd())
        raise typer.Exit(code=exit_code)
    except BonsaiError as exc:
        _fail(exc)
```

- [ ] **Step 4: Run CLI start test**

Run:

```bash
uv run pytest tests/test_cli.py::test_start_executes_workflow -v
```

Expected: PASS.

- [ ] **Step 5: Write failing CLI sync tests**

Replace `test_sync_dry_run_command_exists` with:

```python
def test_sync_dry_run_reports_planned_actions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_sync(_runner, root: Path, apply: bool = False):
        assert root == tmp_path
        assert apply is False
        return SimpleNamespace(
            actions=[
                SimpleNamespace(kind="write", path=tmp_path / "main" / ".env.local"),
                SimpleNamespace(kind="remove", path=tmp_path / "caddy.d" / "old.caddy"),
            ],
            reload_caddy=True,
        )

    monkeypatch.setattr(cli, "execute_sync", fake_execute_sync, raising=False)

    result = runner.invoke(cli.app, ["sync"])

    assert result.exit_code == 0
    assert "sync dry run" in result.stdout.lower()
    assert "write" in result.stdout
    assert "remove" in result.stdout
    assert "reload Caddy" in result.stdout
```

Add:

```python
def test_sync_apply_passes_apply_true(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_execute_sync(_runner, root: Path, apply: bool = False):
        calls.append((root, apply))
        return SimpleNamespace(actions=[], reload_caddy=False)

    monkeypatch.setattr(cli, "execute_sync", fake_execute_sync, raising=False)

    result = runner.invoke(cli.app, ["sync", "--apply"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, True)]
    assert "No sync changes" in result.stdout
```

- [ ] **Step 6: Run CLI sync tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cli.py::test_sync_dry_run_reports_planned_actions tests/test_cli.py::test_sync_apply_passes_apply_true -v
```

Expected: FAIL because the current stub does not call `execute_sync`.

- [ ] **Step 7: Replace CLI sync command**

In `src/bonsai/cli.py`, replace `sync` with:

```python
@app.command()
def sync(apply: bool = typer.Option(False, "--apply", help="Write regenerated files.")) -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        plan = execute_sync(SubprocessRunner(), root_path, apply=apply)
        mode = "apply" if apply else "dry run"
        console.print(f"sync {mode}")
        if not plan.actions:
            console.print("No sync changes")
        for action in plan.actions:
            console.print(f"{action.kind} {action.path}")
        if apply and plan.reload_caddy:
            console.print("reload Caddy")
        elif not apply and plan.reload_caddy and plan.actions:
            console.print("reload Caddy after apply")
    except BonsaiError as exc:
        _fail(exc)
```

- [ ] **Step 8: Run CLI sync tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_sync_dry_run_reports_planned_actions tests/test_cli.py::test_sync_apply_passes_apply_true -v
```

Expected: PASS.

- [ ] **Step 9: Write failing CLI doctor tests**

Replace `test_doctor_command_exists` with:

```python
def test_doctor_reports_failed_checks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)

    def fake_check_workspace_health(_runner, root: Path):
        assert root == tmp_path
        return SimpleNamespace(
            failed=True,
            checks=[
                SimpleNamespace(
                    name="env main",
                    status="fail",
                    detail="Missing .env.local",
                    hint="Run: bonsai sync --apply",
                )
            ],
        )

    monkeypatch.setattr(cli, "check_workspace_health", fake_check_workspace_health, raising=False)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 1
    assert "env main" in result.stdout
    assert "Missing .env.local" in result.stdout
    assert "bonsai sync --apply" in result.stdout
```

Add:

```python
def test_doctor_exits_zero_when_checks_pass(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "find_workspace_root", lambda _path: tmp_path)
    monkeypatch.setattr(
        cli,
        "check_workspace_health",
        lambda _runner, _root: SimpleNamespace(
            failed=False,
            checks=[SimpleNamespace(name="config", status="ok", detail="loaded", hint=None)],
        ),
        raising=False,
    )

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "config" in result.stdout
    assert "loaded" in result.stdout
```

- [ ] **Step 10: Run CLI doctor tests to verify they fail**

Run:

```bash
uv run pytest tests/test_cli.py::test_doctor_reports_failed_checks tests/test_cli.py::test_doctor_exits_zero_when_checks_pass -v
```

Expected: FAIL because the current stub does not call `check_workspace_health`.

- [ ] **Step 11: Replace CLI doctor command**

In `src/bonsai/cli.py`, replace `doctor` with:

```python
@app.command()
def doctor() -> None:
    try:
        root_path = find_workspace_root(Path.cwd())
        report = check_workspace_health(SubprocessRunner(), root_path)
        table = Table(title="Bonsai doctor")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail")
        table.add_column("Hint")
        for check in report.checks:
            table.add_row(check.name, check.status, check.detail, check.hint or "")
        console.print(table)
        if report.failed:
            raise typer.Exit(code=1)
    except BonsaiError as exc:
        _fail(exc)
```

- [ ] **Step 12: Run CLI command tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_start_executes_workflow tests/test_cli.py::test_sync_dry_run_reports_planned_actions tests/test_cli.py::test_sync_apply_passes_apply_true tests/test_cli.py::test_doctor_reports_failed_checks tests/test_cli.py::test_doctor_exits_zero_when_checks_pass -v
```

Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add src/bonsai/cli.py tests/test_cli.py
git commit -m "feat: wire core roi commands"
```

---

### Task 7: Update README and Run Full Verification

**Files:**
- Modify: `README.md`
- Test: full test suite and lint

- [ ] **Step 1: Update README command descriptions**

In `README.md`, replace the `bonsai start`, `bonsai sync`, and `bonsai doctor` usage description with text covering:

```markdown
`bonsai start [branch]` runs the configured `[commands].start` command in the
target worktree. With no branch, it uses the current worktree. The process runs
in the foreground with values from the generated `.env.local` added to the
environment.

`bonsai sync` compares generated `.env.local` files and Caddy files against the
current config/state. It is a dry run by default. Use `bonsai sync --apply` to
write missing or stale generated files, remove stale Bonsai Caddy snippets, and
reload Caddy when public services are configured.

`bonsai doctor` checks workspace state, config, git worktrees, generated files,
Caddy files, Caddy availability, and configured service port conflicts. Failed
repairable checks point to `bonsai sync --apply`.
```

- [ ] **Step 2: Run format/lint**

Run:

```bash
uv run ruff check .
```

Expected: PASS. If it fails for import ordering or formatting, run `uv run ruff check . --fix`, review the diff, and rerun `uv run ruff check .`.

- [ ] **Step 3: Run full tests**

Run:

```bash
uv run pytest
```

Expected: PASS.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git diff --stat HEAD
git diff HEAD -- src/bonsai/models.py src/bonsai/process.py src/bonsai/workflows.py src/bonsai/cli.py tests/test_workflows.py tests/test_cli.py README.md
```

Expected: Diff only contains the ROI core command implementation, tests, and README update.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: describe core roi commands"
```

- [ ] **Step 6: Final verification after commits**

Run:

```bash
uv run ruff check .
uv run pytest
git status --short
```

Expected: lint passes, tests pass, and `git status --short` is empty.
