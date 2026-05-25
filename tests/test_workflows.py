from pathlib import Path

from bonsai.caddy import caddy_reload_plan, caddy_setup_plan
from bonsai.git import parse_default_branch
from bonsai.models import CommandSpec, ManagedWorktree
from bonsai.ports import allocate_slot
from bonsai.process import RecordingRunner


def test_allocate_slot_uses_lowest_available_positive_integer() -> None:
    worktrees = {
        "a": ManagedWorktree(path="a", slug="a", slot=1),
        "c": ManagedWorktree(path="c", slug="c", slot=3),
    }

    assert allocate_slot(worktrees) == 2


def test_allocate_slot_returns_one_for_empty_state() -> None:
    assert allocate_slot({}) == 1


def test_parse_default_branch_from_ls_remote_symref() -> None:
    output = "ref: refs/heads/staging\tHEAD\nabc123\tHEAD\n"

    assert parse_default_branch(output) == "staging"


def test_recording_runner_captures_commands_without_running_them() -> None:
    runner = RecordingRunner()

    result = runner.run(["git", "status"], cwd=Path("/tmp/repo"))

    assert result.returncode == 0
    assert runner.commands == [CommandSpec(argv=("git", "status"), cwd=Path("/tmp/repo"))]


def test_caddy_setup_plan_installs_and_starts_when_missing() -> None:
    plan = caddy_setup_plan(
        auto_install=True,
        auto_start=True,
        caddy_exists=False,
        brew_exists=True,
    )

    assert [command.argv for command in plan] == [
        ("brew", "install", "caddy"),
        ("brew", "services", "start", "caddy"),
    ]


def test_caddy_reload_plan_targets_workspace_caddyfile() -> None:
    plan = caddy_reload_plan(Path("/tmp/authentic/Caddyfile"))

    assert plan.argv == ("caddy", "reload", "--config", "/tmp/authentic/Caddyfile")
