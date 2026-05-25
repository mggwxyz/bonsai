from pathlib import Path

import pytest
from test_config import VALID_CONFIG, write_config

from bonsai.config import load_config
from bonsai.rendering import render_caddy_snippets, render_env_local, render_root_caddyfile
from bonsai.slug import branch_slug
from bonsai.templates import render_template


def test_branch_slug_is_lowercase_and_url_safe() -> None:
    assert branch_slug("MB-1855-What Do You Talk About?") == "mb-1855-what-do-you-talk-about"
    assert branch_slug("feature/API_v2") == "feature-api_v2"


def test_render_template_replaces_known_values() -> None:
    result = render_template(
        "https://${slug}.${name}.localhost:${FRONTEND_PORT}",
        {"slug": "mb-1-test", "name": "authentic", "FRONTEND_PORT": "4201"},
    )

    assert result == "https://mb-1-test.authentic.localhost:4201"


def test_render_template_rejects_unknown_values() -> None:
    with pytest.raises(KeyError, match="MISSING"):
        render_template("${MISSING}", {})


def test_render_env_local_contains_slot_ports_and_env(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    env_text = render_env_local(
        config=config,
        branch="MB-2036-multi-worktree-port-slots",
        slot=2,
        worktree_path=tmp_path / "MB-2036-multi-worktree-port-slots",
    )

    assert "SLOT=2" in env_text
    assert "FRONTEND_PORT=4202" in env_text
    assert "API_PORT=3335" in env_text
    assert "DB_PORT=5557" in env_text
    assert "COMPOSE_PROJECT_NAME=authentic-mb-2036-multi-worktree-port-slots" in env_text


def test_render_root_caddyfile_imports_snippet_dir(tmp_path: Path) -> None:
    text = render_root_caddyfile(tmp_path / "authentic" / "caddy.d")

    assert "{\n\tlocal_certs\n}" in text
    assert f"import {tmp_path / 'authentic' / 'caddy.d'}/*.caddy" in text


def test_render_caddy_snippets_only_public_services(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    snippets = render_caddy_snippets(
        config=config,
        branch="MB-2036-multi-worktree-port-slots",
        slot=2,
        worktree_path=tmp_path / "MB-2036-multi-worktree-port-slots",
    )

    assert sorted(snippets) == ["api", "frontend"]
    assert "https://mb-2036-multi-worktree-port-slots.authentic.localhost" in snippets["frontend"]
    assert "reverse_proxy localhost:4202" in snippets["frontend"]
    assert "https://api-mb-2036-multi-worktree-port-slots.authentic.localhost" in snippets["api"]
    assert "reverse_proxy localhost:3335" in snippets["api"]
