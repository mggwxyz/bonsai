from pathlib import Path

from bonsai.models import BonsaiState
from bonsai.registry import read_workspace_registry, registry_path
from bonsai.state import load_state, save_state


def test_load_state_registers_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    state_path = workspace_root / ".bonsai" / "state.json"
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@example.com:org/repo.git",
        worktrees={},
    )
    save_state(state_path, state)

    loaded = load_state(state_path)

    assert loaded.name == "authentic"
    entries = read_workspace_registry()
    assert [(entry.name, entry.root) for entry in entries] == [("authentic", workspace_root)]
    assert registry_path().exists()


def test_read_workspace_registry_prunes_missing_state(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    state_path = workspace_root / ".bonsai" / "state.json"
    save_state(
        state_path,
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@example.com:org/repo.git",
            worktrees={},
        ),
    )
    load_state(state_path)
    state_path.unlink()

    assert read_workspace_registry() == ()
