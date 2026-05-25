from pathlib import Path

import pytest

from bonsai.errors import BonsaiWorkspaceError
from bonsai.models import BonsaiState, ManagedWorktree
from bonsai.state import load_state, remove_worktree, save_state, update_worktree
from bonsai.workspace import find_workspace_root, workspace_paths


def test_save_and_load_state_round_trip(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "MB-1-test": ManagedWorktree(path="MB-1-test", slug="mb-1-test", slot=1)
        },
    )

    save_state(tmp_path / ".bonsai" / "state.json", state)
    loaded = load_state(tmp_path / ".bonsai" / "state.json")

    assert loaded == state


def test_update_worktree_replaces_one_branch() -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@example.com:org/repo.git",
        worktrees={},
    )

    updated = update_worktree(
        state,
        "MB-2-test",
        ManagedWorktree(path="MB-2-test", slug="mb-2-test", slot=2),
    )

    assert updated.worktrees["MB-2-test"].slot == 2
    assert state.worktrees == {}


def test_remove_worktree_removes_one_branch_without_mutating_original() -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
            "other": ManagedWorktree(path="other", slug="other", slot=2),
        },
    )

    updated = remove_worktree(state, "feature")

    assert set(updated.worktrees) == {"other"}
    assert set(state.worktrees) == {"feature", "other"}


def test_find_workspace_root_walks_up_to_bonsai_state(tmp_path: Path) -> None:
    root = tmp_path / "authentic"
    nested = root / "main" / "apps" / "web"
    (root / ".bonsai").mkdir(parents=True)
    nested.mkdir(parents=True)

    assert find_workspace_root(nested) == root


def test_find_workspace_root_errors_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(BonsaiWorkspaceError, match="No Bonsai workspace found"):
        find_workspace_root(tmp_path)


def test_workspace_paths_are_derived_from_root_and_state(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="staging",
        default_worktree="staging",
        repo_url="git@example.com:org/repo.git",
        worktrees={},
    )

    paths = workspace_paths(tmp_path, state)

    assert paths.root == tmp_path
    assert paths.default_worktree == tmp_path / "staging"
    assert paths.state_file == tmp_path / ".bonsai" / "state.json"
    assert paths.caddyfile == tmp_path / "Caddyfile"
    assert paths.snippets_dir == tmp_path / "caddy.d"
