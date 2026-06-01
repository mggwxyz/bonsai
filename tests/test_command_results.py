from pathlib import Path

from rich.table import Table

from bonsai.command_results import (
    render_cleanup_result,
    render_doctor_result,
    render_port_repair_result,
    render_repair_result,
    render_sync_result,
)
from bonsai.models import (
    BonsaiState,
    CleanupItem,
    CleanupPlan,
    DoctorApplyAction,
    DoctorApplyPlan,
    DoctorCheck,
    DoctorReport,
    ManagedWorktree,
    PortRepairItem,
    PortRepairPlan,
    PortRepairServiceChange,
    RepairItem,
    RepairPlan,
    SyncFileAction,
    SyncPlan,
)


def make_state() -> BonsaiState:
    return BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@example.com:org/repo.git",
        worktrees={
            "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
        },
    )


def test_render_sync_result_keeps_dry_run_text() -> None:
    plan = SyncPlan(
        actions=(
            SyncFileAction(kind="write", path=Path("/workspace/main/.env.local")),
            SyncFileAction(kind="remove", path=Path("/workspace/caddy.d/old.caddy")),
        ),
        reload_caddy=True,
    )

    rendered = render_sync_result(plan, apply=False)

    assert rendered == (
        "sync dry run\n"
        "write /workspace/main/.env.local\n"
        "remove /workspace/caddy.d/old.caddy\n"
        "reload Caddy after apply\n"
    )


def test_render_sync_result_reports_apply_noop() -> None:
    plan = SyncPlan(actions=(), reload_caddy=False)

    rendered = render_sync_result(plan, apply=True)

    assert rendered == "sync apply\nNo sync changes\n"


def test_render_repair_result_uses_apply_action_labels() -> None:
    plan = RepairPlan(
        items=(
            RepairItem(
                branch="old-branch",
                worktree_path=Path("/workspace/old-branch"),
                action="remove",
                reason="missing /workspace/old-branch",
            ),
            RepairItem(
                branch="feature-c",
                worktree_path=Path("/workspace/feature-c"),
                action="repack",
                reason="slot 4 -> 2",
            ),
        ),
        updated_state=make_state(),
        state_changed=True,
    )

    rendered = render_repair_result(plan, apply=True)

    assert rendered == (
        "repair apply\n"
        "removed old-branch - missing /workspace/old-branch\n"
        "repacked feature-c - slot 4 -> 2\n"
        "Run: bonsai sync --apply\n"
    )


def test_render_port_repair_result_keeps_noop_short_circuit() -> None:
    plan = PortRepairPlan(items=())

    rendered = render_port_repair_result(plan, apply=False)

    assert rendered == "repair-ports dry run\nNo port repairs needed\n"


def test_render_port_repair_result_lists_service_port_changes() -> None:
    plan = PortRepairPlan(
        items=(
            PortRepairItem(
                branch="feature-a",
                slug="feature-a",
                current_slot=1,
                proposed_slot=5,
                services=(
                    PortRepairServiceChange(
                        name="frontend",
                        port_env="FRONTEND_PORT",
                        old_port=4201,
                        new_port=4205,
                    ),
                ),
            ),
        )
    )

    rendered = render_port_repair_result(plan, apply=True)

    assert rendered == (
        "repair-ports apply\n"
        "feature-a slot 1 -> 5\n"
        "  FRONTEND_PORT 4201 -> 4205\n"
        "Updated state and regenerated files\n"
    )


def test_render_cleanup_result_includes_pr_url_suffix() -> None:
    plan = CleanupPlan(
        items=(
            CleanupItem(
                branch="feature-a",
                worktree_path=Path("/workspace/feature-a"),
                action="remove",
                reason="pull request is merged",
                pr_url="https://github.com/org/repo/pull/1",
            ),
        )
    )

    rendered = render_cleanup_result(plan, apply=False)

    assert rendered == (
        "cleanup dry run\n"
        "remove feature-a - pull request is merged (https://github.com/org/repo/pull/1)\n"
    )


def test_render_doctor_result_returns_apply_lines_and_table() -> None:
    report = DoctorReport(
        checks=(
            DoctorCheck(
                name="workspace state",
                status="ok",
                detail="/workspace/.bonsai/state.json",
                hint=None,
            ),
        )
    )
    apply_plan = DoctorApplyPlan(
        actions=(DoctorApplyAction(kind="sync", detail="write /workspace/main/.env.local"),)
    )

    rendered = render_doctor_result(report, apply=True, apply_plan=apply_plan)

    assert rendered[0] == "doctor apply"
    assert rendered[1] == "sync write /workspace/main/.env.local"
    assert isinstance(rendered[2], Table)
    assert rendered[2].title == "Bonsai doctor"
    assert [column.header for column in rendered[2].columns] == [
        "Check",
        "Status",
        "Detail",
        "Hint",
    ]
