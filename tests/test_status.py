import json
from pathlib import Path

from rich.text import Text

from bonsai.models import (
    UrlCheck,
    WorkspaceServiceSummary,
    WorkspaceStatus,
    WorkspaceSummary,
    WorkspaceUrl,
    WorkspaceUrlsPlan,
    WorktreeSummary,
)
from bonsai.status import render_workspace_list, render_workspace_status, render_workspace_urls


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


def make_workspace_urls_plan() -> WorkspaceUrlsPlan:
    return WorkspaceUrlsPlan(
        workspace_root=Path("/workspace/authentic"),
        caddyfile=Path("/workspace/authentic/Caddyfile"),
        urls=(
            WorkspaceUrl(
                branch="feature",
                worktree_path=Path("/workspace/authentic/feature"),
                service_name="frontend",
                port_env="FRONTEND_PORT",
                port=4201,
                primary=True,
                url="https://feature.authentic.localhost",
                caddy_snippet_path=Path("/workspace/authentic/caddy.d/feature-frontend.caddy"),
                checks=(
                    UrlCheck(
                        name="Caddy route",
                        status="ok",
                        detail="route file is current",
                    ),
                    UrlCheck(
                        name="app listener",
                        status="warn",
                        detail="no listener detected on localhost:4201",
                        hint="Run: bonsai start feature",
                    ),
                ),
            ),
        ),
    )


def test_render_workspace_list_text_returns_simple_list() -> None:
    rendered = render_workspace_list(make_workspace_summary(), "text")

    assert isinstance(rendered, str)
    lines = rendered.splitlines()
    assert lines[0] == "Worktrees for authentic"
    assert lines[2] == "  feature  ./feature  managed"
    assert "FRONTEND_PORT" not in rendered
    assert "https://feature.authentic.localhost" not in rendered


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


def test_render_workspace_urls_text_includes_diagnostic_checks() -> None:
    rendered = render_workspace_urls(make_workspace_urls_plan(), "text")

    assert "Bonsai URLs" in rendered
    assert "feature / frontend" in rendered
    assert "https://feature.authentic.localhost" in rendered
    assert "[ok] Caddy route: route file is current" in rendered
    assert "[warn] app listener: no listener detected on localhost:4201" in rendered
    assert "Run: bonsai start feature" in rendered


def test_render_workspace_urls_json_payload() -> None:
    rendered = render_workspace_urls(make_workspace_urls_plan(), "json")

    payload = json.loads(rendered)
    assert payload["schema"] == "bonsai.urls.v1"
    assert payload["workspace"]["root"] == "/workspace/authentic"
    assert payload["caddyfile"] == "/workspace/authentic/Caddyfile"
    assert payload["urls"][0]["branch"] == "feature"
    assert payload["urls"][0]["service"] == "frontend"
    assert payload["urls"][0]["checks"][1]["hint"] == "Run: bonsai start feature"


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


def test_render_workspace_status_text_includes_workspace_root_location() -> None:
    status = WorkspaceStatus(
        workspace_name="authentic",
        workspace_root=Path("/workspace/authentic"),
        default_branch="main",
        default_worktree="main",
        config_path=Path("/workspace/authentic/main/.bonsai.toml"),
        current=None,
        commands=make_workspace_summary().commands,
        location_kind="workspace_root",
        location_path=Path("/workspace/authentic"),
    )

    rendered = render_workspace_status(status, "text")

    assert "Location: workspace root (parent folder)" in rendered
    assert "Path: /workspace/authentic" in rendered
    assert "List worktrees: bonsai list" in rendered


def test_render_workspace_status_color_text_preserves_plain_output() -> None:
    status = WorkspaceStatus(
        workspace_name="authentic",
        workspace_root=Path("/workspace/authentic"),
        default_branch="main",
        default_worktree="main",
        config_path=Path("/workspace/authentic/main/.bonsai.toml"),
        current=make_worktree_summary(),
        commands=make_workspace_summary().commands,
    )

    rendered = render_workspace_status(status, "text", color=True)
    plain = render_workspace_status(status, "text")

    assert isinstance(rendered, Text)
    assert rendered.plain == plain
    styled_fragments = {
        rendered.plain[span.start : span.end]: str(span.style) for span in rendered.spans
    }
    assert styled_fragments["Bonsai status"] == "bold green"
    assert styled_fragments["Workspace:"] == "bold cyan"
    assert styled_fragments["Services:"] == "bold"
    assert styled_fragments["current"] == "green"
    assert styled_fragments["yes"] == "green"
    assert styled_fragments["no"] == "dim"


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
