import json
from pathlib import Path

from rich.table import Table

from bonsai.models import (
    WorkspaceServiceSummary,
    WorkspaceStatus,
    WorkspaceSummary,
    WorktreeSummary,
)
from bonsai.status import render_workspace_list, render_workspace_status


def make_worktree_summary() -> WorktreeSummary:
    return WorktreeSummary(
        branch="feature",
        worktree_path=Path("/workspace/authentic/feature"),
        relative_path="feature",
        slug="feature",
        slot=1,
        kind="managed",
        env_file_path=Path("/workspace/authentic/feature/.env.local"),
        env_file_status="current",
        services=(
            WorkspaceServiceSummary(
                name="frontend",
                port_env="FRONTEND_PORT",
                port=4201,
                public=True,
                primary=True,
                url="https://feature.authentic.localhost",
            ),
            WorkspaceServiceSummary(
                name="db",
                port_env="DB_PORT",
                port=5556,
                public=False,
                primary=False,
                url=None,
            ),
        ),
    )


def make_workspace_summary() -> WorkspaceSummary:
    return WorkspaceSummary(
        workspace_name="authentic",
        workspace_root=Path("/workspace/authentic"),
        default_branch="main",
        default_worktree="main",
        config_path=Path("/workspace/authentic/main/.bonsai.toml"),
        worktrees=(make_worktree_summary(),),
        commands={
            "status": "bonsai status",
            "list": "bonsai list",
            "start": "bonsai start",
            "open": "bonsai open",
            "sync": "bonsai sync --apply",
            "doctor": "bonsai doctor",
        },
    )


def test_render_workspace_list_text_returns_rich_table() -> None:
    rendered = render_workspace_list(make_workspace_summary(), "text")

    assert isinstance(rendered, Table)
    assert rendered.title == "Worktrees for authentic"
    assert [column.header for column in rendered.columns] == [
        "Branch",
        "Path",
        "Slot",
        "Kind",
        "Env",
        "Ports",
        "URLs",
    ]


def test_render_workspace_list_json_payload() -> None:
    rendered = render_workspace_list(make_workspace_summary(), "json")

    payload = json.loads(rendered)
    assert payload["schema"] == "bonsai.list.v1"
    assert payload["workspace"]["name"] == "authentic"
    assert payload["workspace"]["root"] == "/workspace/authentic"
    assert payload["workspace"]["config"] == "/workspace/authentic/main/.bonsai.toml"
    assert payload["commands"]["list"] == "bonsai list"
    assert payload["worktrees"][0]["branch"] == "feature"
    assert payload["worktrees"][0]["env_file"]["status"] == "current"
    assert payload["worktrees"][0]["services"][0]["port"] == 4201
    assert (
        payload["worktrees"][0]["services"][0]["url"]
        == "https://feature.authentic.localhost"
    )


def test_render_workspace_status_text_includes_current_details() -> None:
    status = WorkspaceStatus(
        workspace_name="authentic",
        workspace_root=Path("/workspace/authentic"),
        default_branch="main",
        default_worktree="main",
        config_path=Path("/workspace/authentic/main/.bonsai.toml"),
        current=make_worktree_summary(),
        commands=make_workspace_summary().commands,
    )

    rendered = render_workspace_status(status, "text")

    assert "Bonsai status" in rendered
    assert "Workspace: authentic" in rendered
    assert "Branch: feature" in rendered
    assert "Kind: managed" in rendered
    assert "Env file: /workspace/authentic/feature/.env.local (current)" in rendered
    assert "FRONTEND_PORT=4201" in rendered
    assert "https://feature.authentic.localhost" in rendered
    assert "List worktrees: bonsai list" in rendered


def test_render_workspace_status_json_payload() -> None:
    status = WorkspaceStatus(
        workspace_name="authentic",
        workspace_root=Path("/workspace/authentic"),
        default_branch="main",
        default_worktree="main",
        config_path=Path("/workspace/authentic/main/.bonsai.toml"),
        current=make_worktree_summary(),
        commands=make_workspace_summary().commands,
    )

    rendered = render_workspace_status(status, "json")

    payload = json.loads(rendered)
    assert payload["schema"] == "bonsai.status.v1"
    assert payload["workspace"]["name"] == "authentic"
    assert payload["current"]["branch"] == "feature"
    assert payload["current"]["slot"] == 1
    assert payload["current"]["services"][0]["port_env"] == "FRONTEND_PORT"
    assert payload["commands"]["doctor"] == "bonsai doctor"
