import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from test_config import VALID_CONFIG, write_config

from bonsai.config import load_config
from bonsai.errors import BonsaiCommandError, BonsaiWorkspaceError
from bonsai.git import (
    move_worktree,
    remove_worktree,
)
from bonsai.models import (
    BonsaiState,
    CommandResult,
    CommandSpec,
    FileCopy,
    ManagedWorktree,
    SharedFileConfig,
)
from bonsai.process import RecordingRunner
from bonsai.state import load_state, save_state
from bonsai.workflows import (
    app_snippets_dir,
    execute_add,
    execute_add_pull_request,
    execute_checkout,
    execute_cleanup,
    execute_clone,
    execute_init,
    execute_move,
    execute_remove,
    execute_rename_default,
    global_caddy_paths,
    plan_add_files,
    plan_clone_workspace,
    plan_move_worktree,
    plan_rename_default,
    worktree_name_completions,
)
from bonsai.workflows import worktrees as wf_worktrees


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_remove_worktree_passes_force_when_requested() -> None:
    runner = RecordingRunner()

    remove_worktree(runner, Path("/tmp/repo/main"), Path("/tmp/repo/feature"), force=True)

    assert runner.commands == [
        CommandSpec(
            argv=(
                "git",
                "-C",
                "/tmp/repo/main",
                "worktree",
                "remove",
                "--force",
                "/tmp/repo/feature",
            )
        )
    ]


def test_move_worktree_uses_git_worktree_move() -> None:
    runner = RecordingRunner()

    move_worktree(
        runner,
        Path("/tmp/repo/main"),
        Path("/tmp/repo/mb-123-auth"),
        Path("/tmp/repo/MB-123-auth"),
    )

    assert runner.commands == [
        CommandSpec(
            argv=(
                "git",
                "-C",
                "/tmp/repo/main",
                "worktree",
                "move",
                "/tmp/repo/mb-123-auth",
                "/tmp/repo/MB-123-auth",
            )
        )
    ]


def test_plan_clone_workspace_uses_discovered_default_branch(tmp_path: Path) -> None:
    (tmp_path / "main").mkdir()
    config_path = write_config(tmp_path / "main", VALID_CONFIG)
    config = load_config(config_path)

    plan = plan_clone_workspace(
        git_url="git@github.com:org/authentic.git",
        name="authentic",
        default_branch="main",
        config=config,
        parent=tmp_path,
    )

    assert plan.workspace_root == tmp_path / "authentic"
    assert plan.default_worktree == tmp_path / "authentic" / "main"
    assert plan.state.default_branch == "main"
    assert plan.state.default_worktree == "main"


def test_plan_add_files_renders_env_caddy_and_state(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    plan = plan_add_files(
        config=config,
        state=state,
        workspace_root=tmp_path / "authentic",
        branch="MB-2036-multi-worktree-port-slots",
    )

    assert plan.worktree_path == tmp_path / "authentic" / "mb-2036-multi-worktree-port-slots"
    assert plan.slot == 1
    worktree = plan.updated_state.worktrees["MB-2036-multi-worktree-port-slots"]
    assert worktree.path == "mb-2036-multi-worktree-port-slots"
    assert worktree.slot == 1
    assert ".env.local" in {path.name for path in plan.files}
    assert "mb-2036-multi-worktree-port-slots-frontend.caddy" in {
        path.name for path in plan.files
    }
    assert "mb-2036-multi-worktree-port-slots-api.caddy" in {path.name for path in plan.files}
    assert plan.symlinks[0].source == tmp_path / "authentic" / "main" / ".env"
    assert plan.symlinks[0].target == (
        tmp_path / "authentic" / "mb-2036-multi-worktree-port-slots" / ".env"
    )


def test_plan_add_files_plans_copy_mode_shared_files(tmp_path: Path) -> None:
    config = replace(
        load_config(write_config(tmp_path, VALID_CONFIG)),
        shared_files=(SharedFileConfig(source=".env", target=".env", mode="copy"),),
    )
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    plan = plan_add_files(config, state, tmp_path / "authentic", "feature")

    assert plan.symlinks == ()
    assert plan.copies == (
        FileCopy(
            source=tmp_path / "authentic" / "main" / ".env",
            target=tmp_path / "authentic" / "feature" / ".env",
        ),
    )


def test_plan_add_files_plans_worktreeinclude_gitignored_file_copies(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    _init_git_repo(default_worktree)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".gitignore").write_text(
        ".env.shared\nconfig/local/\nnode_modules/\ndist/\n",
        encoding="utf-8",
    )
    (default_worktree / ".worktreeinclude").write_text(
        ".env.shared\nconfig/local/**\n!config/local/skip.json\nREADME.md\nmissing.local\n"
        "node_modules/**\ndist/**\n",
        encoding="utf-8",
    )
    (default_worktree / ".env.shared").write_text("SECRET=shared\n", encoding="utf-8")
    (default_worktree / "README.md").write_text("tracked docs are not copied\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=default_worktree,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (default_worktree / "config" / "local").mkdir(parents=True)
    (default_worktree / "config" / "local" / "settings.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (default_worktree / "config" / "local" / "skip.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (default_worktree / "node_modules").mkdir()
    (default_worktree / "node_modules" / "package.json").write_text("{}\n", encoding="utf-8")
    (default_worktree / "dist").mkdir()
    (default_worktree / "dist" / "bundle.js").write_text("build output\n", encoding="utf-8")
    config = load_config(default_worktree / ".bonsai.toml")
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    plan = plan_add_files(config, state, workspace_root, "feature")

    copied_paths = {
        copy.source.relative_to(default_worktree).as_posix()
        for copy in plan.copies
    }
    assert copied_paths == {
        ".env.shared",
        "config/local/settings.json",
    }
    assert {
        copy.target.relative_to(workspace_root / "feature").as_posix()
        for copy in plan.copies
    } == copied_paths


def test_plan_add_files_explicit_shared_files_win_over_worktreeinclude(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    _init_git_repo(default_worktree)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".gitignore").write_text(".env.shared\n", encoding="utf-8")
    (default_worktree / ".worktreeinclude").write_text(".env.shared\n", encoding="utf-8")
    (default_worktree / ".env.shared").write_text("SECRET=shared\n", encoding="utf-8")
    config = replace(
        load_config(default_worktree / ".bonsai.toml"),
        shared_files=(
            SharedFileConfig(source=".env.shared", target=".env.shared", mode="symlink"),
        ),
    )
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    plan = plan_add_files(config, state, workspace_root, "feature")

    assert plan.copies == ()
    assert plan.symlinks[0].source == default_worktree / ".env.shared"
    assert plan.symlinks[0].target == workspace_root / "feature" / ".env.shared"


def test_plan_move_worktree_updates_state_path_preserving_slug_and_slot(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    old_worktree = workspace_root / "mb-123-auth"
    old_worktree.mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "MB-123-auth": ManagedWorktree(
                path="mb-123-auth",
                slug="mb-123-auth",
                slot=4,
            )
        },
    )

    plan = plan_move_worktree(
        state,
        workspace_root,
        "MB-123-auth",
        "MB-123-auth",
    )

    assert plan.branch == "MB-123-auth"
    assert plan.old_worktree_path == old_worktree
    assert plan.new_worktree_path == workspace_root / "MB-123-auth"
    moved = plan.updated_state.worktrees["MB-123-auth"]
    assert moved.path == "MB-123-auth"
    assert moved.slug == "mb-123-auth"
    assert moved.slot == 4


def test_plan_move_worktree_rejects_default_worktree(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Cannot move the default worktree"):
        plan_move_worktree(state, tmp_path / "authentic", "main", "Main")


def test_plan_move_worktree_rejects_unknown_worktree(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Unknown worktree: missing"):
        plan_move_worktree(state, tmp_path / "authentic", "missing", "target")


def test_plan_move_worktree_rejects_missing_default_path_collision(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    target = workspace_root / "main"
    assert not target.exists()
    with pytest.raises(
        BonsaiWorkspaceError,
        match=re.escape(f"Worktree target already exists: {target}"),
    ):
        plan_move_worktree(state, workspace_root, "feature", "main")


def test_plan_move_worktree_rejects_existing_distinct_target(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    (workspace_root / "taken").mkdir()
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Worktree target already exists"):
        plan_move_worktree(state, workspace_root, "feature", "taken")


def test_plan_move_worktree_rejects_managed_path_collision_with_missing_directory(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
            "other": ManagedWorktree(path="taken", slug="other", slot=2),
        },
    )

    target = workspace_root / "taken"
    assert not target.exists()
    with pytest.raises(
        BonsaiWorkspaceError,
        match=re.escape(f"Worktree target already exists: {target}"),
    ):
        plan_move_worktree(state, workspace_root, "feature", "taken")


@pytest.mark.parametrize("target_name", ["other-branch", "other-slug"])
def test_plan_move_worktree_rejects_other_identifier_collision(
    tmp_path: Path,
    target_name: str,
) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
            "other-branch": ManagedWorktree(
                path="other-path",
                slug="other-slug",
                slot=2,
            ),
        },
    )

    target = workspace_root / target_name
    assert not target.exists()
    with pytest.raises(
        BonsaiWorkspaceError,
        match=re.escape(f"Worktree target already exists: {target}"),
    ):
        plan_move_worktree(state, workspace_root, "feature", target_name)


def test_plan_move_worktree_allows_own_branch_identifier_as_target(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    old_worktree = workspace_root / "old-folder"
    old_worktree.mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "MB-123": ManagedWorktree(
                path="old-folder",
                slug="mb-123",
                slot=1,
            )
        },
    )

    plan = plan_move_worktree(state, workspace_root, "MB-123", "MB-123")

    assert plan.branch == "MB-123"
    assert plan.old_worktree_path == old_worktree
    assert plan.new_worktree_path == workspace_root / "MB-123"
    moved = plan.updated_state.worktrees["MB-123"]
    assert moved.path == "MB-123"
    assert moved.slug == "mb-123"
    assert moved.slot == 1


def test_plan_move_worktree_allows_case_only_samefile_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    old_worktree = workspace_root / "mb-123"
    new_worktree = workspace_root / "MB-123"
    old_worktree.mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="mb-123", slug="mb-123", slot=1)},
    )

    original_exists = Path.exists

    def fake_exists(path: Path) -> bool:
        if path == new_worktree:
            return True
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(
        wf_worktrees,
        "_paths_refer_to_same_existing_path",
        lambda _left, _right: True,
    )

    plan = plan_move_worktree(state, workspace_root, "feature", "MB-123")

    assert plan.old_worktree_path == old_worktree
    assert plan.new_worktree_path == new_worktree
    assert plan.updated_state.worktrees["feature"].path == "MB-123"


def test_plan_move_worktree_rejects_samefile_alias_target(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    feature = workspace_root / "feature"
    alias = workspace_root / "alias"
    feature.mkdir(parents=True)
    alias.symlink_to(feature, target_is_directory=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    assert alias.samefile(feature)
    with pytest.raises(BonsaiWorkspaceError, match="Worktree target already exists"):
        plan_move_worktree(state, workspace_root, "feature", "alias")


def test_plan_move_worktree_rejects_dangling_symlink_target(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    feature = workspace_root / "feature"
    dangling = workspace_root / "dangling"
    feature.mkdir(parents=True)
    dangling.symlink_to("missing-target", target_is_directory=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    assert not dangling.exists()
    assert dangling.is_symlink()
    with pytest.raises(
        BonsaiWorkspaceError,
        match=re.escape(f"Worktree target already exists: {dangling}"),
    ):
        plan_move_worktree(state, workspace_root, "feature", "dangling")


def test_plan_move_worktree_rejects_case_only_samefile_symlink_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    feature = workspace_root / "feature"
    alias = workspace_root / "Feature"
    feature.mkdir(parents=True)
    try:
        alias.symlink_to(feature, target_is_directory=True)
    except FileExistsError:
        original_is_symlink = Path.is_symlink

        def fake_is_symlink(path: Path) -> bool:
            if path == alias:
                return True
            return original_is_symlink(path)

        monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
        monkeypatch.setattr(
            wf_worktrees,
            "_paths_refer_to_same_existing_path",
            lambda _left, _right: True,
        )
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    assert alias.is_symlink()
    with pytest.raises(BonsaiWorkspaceError, match="Worktree target already exists"):
        plan_move_worktree(state, workspace_root, "feature", "Feature")


def test_plan_move_worktree_rejects_unsafe_target_folder(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Invalid worktree folder"):
        plan_move_worktree(state, workspace_root, "feature", "../outside")


def test_plan_move_worktree_rejects_same_folder_name(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Worktree already uses folder"):
        plan_move_worktree(state, workspace_root, "feature", "feature")


def test_plan_rename_default_updates_default_worktree(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "MB-1": ManagedWorktree(path="mb-1", slug="mb-1", slot=1),
        },
    )

    plan = plan_rename_default(state, tmp_path / "authentic", "trunk")

    assert plan.branch == "main"
    assert plan.old_worktree_path == tmp_path / "authentic" / "main"
    assert plan.new_worktree_path == tmp_path / "authentic" / "trunk"
    assert plan.updated_state.default_worktree == "trunk"
    assert plan.updated_state.default_branch == "main"
    assert plan.updated_state.worktrees == state.worktrees


def test_plan_rename_default_rejects_same_folder(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Worktree already uses folder: main"):
        plan_rename_default(state, tmp_path / "authentic", "main")


def test_plan_rename_default_rejects_collision_with_secondary(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "feature").mkdir(parents=True)
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={
            "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
        },
    )

    with pytest.raises(BonsaiWorkspaceError, match="Worktree target already exists"):
        plan_rename_default(state, workspace_root, "feature")


def test_plan_rename_default_rejects_unsafe_folder(tmp_path: Path) -> None:
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Invalid worktree folder"):
        plan_rename_default(state, tmp_path / "authentic", "../escape")


def test_plan_clone_workspace_rejects_unsafe_workspace_name(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))

    with pytest.raises(BonsaiWorkspaceError, match="Invalid workspace name"):
        plan_clone_workspace(
            git_url="git@github.com:org/authentic.git",
            name="../escape",
            default_branch="main",
            config=config,
            parent=tmp_path,
        )


def test_plan_add_files_rejects_unsafe_service_name(tmp_path: Path) -> None:
    config_text = VALID_CONFIG.replace(
        '[[services]]\nname = "frontend"',
        '[[services]]\nname = "../frontend"',
    )
    config = load_config(
        write_config(tmp_path, config_text)
    )
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Invalid service name"):
        plan_add_files(
            config=config,
            state=state,
            workspace_root=tmp_path / "authentic",
            branch="MB-2036-multi-worktree-port-slots",
        )


@pytest.mark.parametrize(
    ("branch", "expected_slug"),
    [
        ("/tmp/outside", "tmp-outside"),
        ("../outside", "outside"),
    ],
)
def test_plan_add_files_uses_safe_slug_for_path_like_branch(
    tmp_path: Path,
    branch: str,
    expected_slug: str,
) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )
    workspace_root = tmp_path / "authentic"

    plan = plan_add_files(
        config=config,
        state=state,
        workspace_root=workspace_root,
        branch=branch,
    )

    assert plan.branch == branch
    assert plan.worktree_path == workspace_root / expected_slug
    assert plan.worktree_path.is_relative_to(workspace_root)
    assert plan.updated_state.worktrees[branch].path == expected_slug


def test_plan_add_files_rejects_branch_with_empty_slug(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, VALID_CONFIG))
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match="Invalid branch slug"):
        plan_add_files(
            config=config,
            state=state,
            workspace_root=tmp_path / "authentic",
            branch="???",
        )


@pytest.mark.parametrize(
    ("source", "target", "message"),
    [
        ("../.env", ".env", "Invalid shared file source"),
        ("/tmp/.env", ".env", "Invalid shared file source"),
        ("", ".env", "Invalid shared file source"),
        ("config/.env", ".env", "Invalid shared file source"),
        (".env", "../.env", "Invalid shared file target"),
        (".env", "/tmp/.env", "Invalid shared file target"),
        (".env", "", "Invalid shared file target"),
        (".env", "config/.env", "Invalid shared file target"),
    ],
)
def test_plan_add_files_rejects_unsafe_shared_file_path(
    tmp_path: Path,
    source: str,
    target: str,
    message: str,
) -> None:
    config = replace(
        load_config(write_config(tmp_path, VALID_CONFIG)),
        shared_files=(SharedFileConfig(source=source, target=target),),
    )
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={},
    )

    with pytest.raises(BonsaiWorkspaceError, match=message):
        plan_add_files(
            config=config,
            state=state,
            workspace_root=tmp_path / "authentic",
            branch="feature",
        )


def test_execute_clone_rejects_unsafe_name_before_git_commands(tmp_path: Path) -> None:
    class CloneRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            return CommandResult(returncode=0, stdout="ref: refs/heads/main\tHEAD\n")

    runner = CloneRunner()

    with pytest.raises(BonsaiWorkspaceError, match="Invalid workspace name"):
        execute_clone(runner, "git@github.com:org/authentic.git", "../escape", tmp_path)

    assert runner.commands == []


def test_execute_clone_initializes_missing_config_after_clone(tmp_path: Path) -> None:
    class MissingConfigCloneRunner(RecordingRunner):
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:3] == ["git", "ls-remote", "--symref"]:
                return CommandResult(returncode=0, stdout="ref: refs/heads/main\tHEAD\n")
            if argv[:3] == ["git", "clone", "--branch"]:
                Path(argv[-1]).mkdir(parents=True)
                return CommandResult(returncode=0)
            return CommandResult(returncode=0)

    runner = MissingConfigCloneRunner()
    initializer_calls = []

    def initializer(
        config_path: Path,
        workspace_name: str,
        default_branch: str,
        default_worktree: Path,
    ) -> None:
        initializer_calls.append(
            (config_path, workspace_name, default_branch, default_worktree)
        )
        config_path.write_text(VALID_CONFIG, encoding="utf-8")

    plan = execute_clone(
        runner,
        "git@github.com:org/authentic.git",
        "authentic",
        tmp_path,
        config_initializer=initializer,
    )

    assert initializer_calls == [
        (
            tmp_path / "authentic" / ".bonsai.toml",
            "authentic",
            "main",
            tmp_path / "authentic" / "main",
        )
    ]
    assert plan.workspace_root == tmp_path / "authentic"
    assert (tmp_path / "authentic" / ".bonsai" / "state.json").exists()
    assert (app_snippets_dir("authentic") / "main-frontend.caddy").exists()


def test_execute_clone_runs_install_and_setup_with_default_worktree_env(
    tmp_path: Path,
) -> None:
    class MissingConfigCloneRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:3] == ["git", "ls-remote", "--symref"]:
                return CommandResult(returncode=0, stdout="ref: refs/heads/main\tHEAD\n")
            if argv[:3] == ["git", "clone", "--branch"]:
                Path(argv[-1]).mkdir(parents=True)
                return CommandResult(returncode=0)
            return CommandResult(returncode=0)

    def initializer(
        config_path: Path,
        _workspace_name: str,
        _default_branch: str,
        _default_worktree: Path,
    ) -> None:
        config_path.write_text(VALID_CONFIG, encoding="utf-8")

    runner = MissingConfigCloneRunner()

    execute_clone(
        runner,
        "git@github.com:org/authentic.git",
        "authentic",
        tmp_path,
        config_initializer=initializer,
    )

    default_worktree = tmp_path / "authentic" / "main"
    assert (default_worktree / ".env.local").exists()
    install_command = runner.commands[-2]
    setup_command = runner.commands[-1]
    assert install_command.argv == ("yarn", "install")
    assert install_command.cwd == default_worktree
    assert install_command.log_path is not None
    assert install_command.log_path.parent == tmp_path / "authentic" / ".bonsai" / "logs" / "main"
    assert install_command.log_path.name.endswith("-install.log")
    assert setup_command.argv == ("yarn", "setup")
    assert setup_command.cwd == default_worktree
    assert setup_command.log_path is not None
    assert setup_command.log_path.parent == tmp_path / "authentic" / ".bonsai" / "logs" / "main"
    assert setup_command.log_path.name.endswith("-setup.log")
    setup_env = dict(setup_command.env)
    assert setup_env["COMPOSE_PROJECT_NAME"] == "authentic-main"
    assert setup_env["FRONTEND_PORT"] == "4200"
    assert setup_env["API_PORT"] == "3333"
    assert setup_env["DB_PORT"] == "5555"


def test_execute_clone_logs_default_branch_with_slash_under_slugged_directory(
    tmp_path: Path,
) -> None:
    class SlashDefaultBranchCloneRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:3] == ["git", "ls-remote", "--symref"]:
                return CommandResult(
                    returncode=0,
                    stdout="ref: refs/heads/release/2026\tHEAD\n",
                )
            if argv[:3] == ["git", "clone", "--branch"]:
                Path(argv[-1]).mkdir(parents=True)
                return CommandResult(returncode=0)
            return CommandResult(returncode=0)

    def initializer(
        config_path: Path,
        _workspace_name: str,
        _default_branch: str,
        _default_worktree: Path,
    ) -> None:
        config_path.write_text(VALID_CONFIG, encoding="utf-8")

    runner = SlashDefaultBranchCloneRunner()

    execute_clone(
        runner,
        "git@github.com:org/authentic.git",
        "authentic",
        tmp_path,
        config_initializer=initializer,
    )

    workspace_root = tmp_path / "authentic"
    expected_log_dir = workspace_root / ".bonsai" / "logs" / "release-2026"
    nested_log_dir = workspace_root / ".bonsai" / "logs" / "release" / "2026"
    install_command = runner.commands[-2]
    setup_command = runner.commands[-1]
    assert install_command.log_path is not None
    assert install_command.log_path.parent == expected_log_dir
    assert install_command.log_path.parent != nested_log_dir
    assert install_command.log_path.name.endswith("-install.log")
    assert setup_command.log_path is not None
    assert setup_command.log_path.parent == expected_log_dir
    assert setup_command.log_path.parent != nested_log_dir
    assert setup_command.log_path.name.endswith("-setup.log")


def test_execute_clone_uses_repo_config_when_root_config_is_missing(tmp_path: Path) -> None:
    class RepoConfigCloneRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            if argv[:3] == ["git", "ls-remote", "--symref"]:
                return CommandResult(returncode=0, stdout="ref: refs/heads/main\tHEAD\n")
            if argv[:3] == ["git", "clone", "--branch"]:
                target = Path(argv[-1])
                target.mkdir(parents=True)
                write_config(target, VALID_CONFIG)
            return CommandResult(returncode=0)

    plan = execute_clone(
        RepoConfigCloneRunner(),
        "git@github.com:org/authentic.git",
        "authentic",
        tmp_path,
    )

    assert plan.workspace_root == tmp_path / "authentic"
    assert (app_snippets_dir("authentic") / "main-frontend.caddy").exists()


def test_execute_init_adopts_existing_checkout_config(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)

    class ExistingCheckoutRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            _ = (cwd, check, env)
            if argv[-2:] == ["--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="main\n")
            if argv[-3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(
                    returncode=0,
                    stdout="git@github.com:org/authentic.git\n",
                )
            if argv[-3:] == ["worktree", "list", "--porcelain"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        f"worktree {default_worktree}\n"
                        "HEAD 0000000000000000000000000000000000000000\n"
                        "branch refs/heads/main\n"
                    ),
                )
            raise AssertionError(f"unexpected command: {argv}")

    plan = execute_init(ExistingCheckoutRunner(), default_worktree)

    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.name == "authentic"
    assert state.default_branch == "main"
    assert state.default_worktree == "main"
    assert state.repo_url == "git@github.com:org/authentic.git"
    assert state.worktrees == {}
    assert plan.workspace_root == workspace_root
    assert plan.default_worktree == default_worktree
    assert (default_worktree / ".bonsai.toml").read_text(encoding="utf-8") == VALID_CONFIG
    assert (default_worktree / ".env.local").exists()
    assert (app_snippets_dir("authentic") / "main-frontend.caddy").exists()


def test_execute_init_adopts_existing_sibling_worktrees(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "ma-123-auth"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)

    class ExistingCheckoutRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            _ = (cwd, check, env)
            if argv[-2:] == ["--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="main\n")
            if argv[-3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(
                    returncode=0,
                    stdout="git@github.com:org/authentic.git\n",
                )
            if argv[-3:] == ["worktree", "list", "--porcelain"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        f"worktree {default_worktree}\n"
                        "HEAD 0000000000000000000000000000000000000000\n"
                        "branch refs/heads/main\n"
                        "\n"
                        f"worktree {feature_worktree}\n"
                        "HEAD 1111111111111111111111111111111111111111\n"
                        "branch refs/heads/MA-123-auth\n"
                    ),
                )
            raise AssertionError(f"unexpected command: {argv}")

    execute_init(ExistingCheckoutRunner(), default_worktree)

    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.worktrees == {
        "MA-123-auth": ManagedWorktree(path="ma-123-auth", slug="ma-123-auth", slot=1)
    }
    assert (feature_worktree / ".env.local").exists()
    assert (app_snippets_dir("authentic") / "ma-123-auth-frontend.caddy").exists()


def test_execute_init_reconciles_existing_state_with_missing_worktrees(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    existing_worktree = workspace_root / "existing"
    missing_worktree = workspace_root / "ma-123-auth"
    default_worktree.mkdir(parents=True)
    existing_worktree.mkdir()
    missing_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "existing": ManagedWorktree(path="existing", slug="existing", slot=3)
            },
        ),
    )

    class ExistingCheckoutRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            _ = (cwd, check, env)
            if argv[-2:] == ["--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="main\n")
            if argv[-3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(
                    returncode=0,
                    stdout="git@github.com:org/authentic.git\n",
                )
            if argv[-3:] == ["worktree", "list", "--porcelain"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        f"worktree {default_worktree}\n"
                        "HEAD 0000000000000000000000000000000000000000\n"
                        "branch refs/heads/main\n"
                        "\n"
                        f"worktree {existing_worktree}\n"
                        "HEAD 1111111111111111111111111111111111111111\n"
                        "branch refs/heads/existing\n"
                        "\n"
                        f"worktree {missing_worktree}\n"
                        "HEAD 2222222222222222222222222222222222222222\n"
                        "branch refs/heads/MA-123-auth\n"
                    ),
                )
            raise AssertionError(f"unexpected command: {argv}")

    execute_init(ExistingCheckoutRunner(), default_worktree)

    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert state.worktrees == {
        "existing": ManagedWorktree(path="existing", slug="existing", slot=3),
        "MA-123-auth": ManagedWorktree(path="ma-123-auth", slug="ma-123-auth", slot=1),
    }
    assert (missing_worktree / ".env.local").exists()
    assert (app_snippets_dir("authentic") / "ma-123-auth-frontend.caddy").exists()


def test_execute_init_rejects_checkout_directory_that_does_not_match_branch(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    checkout = workspace_root / "app"
    checkout.mkdir(parents=True)
    write_config(checkout, VALID_CONFIG)

    class ExistingCheckoutRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            _ = (cwd, check, env)
            if argv[-2:] == ["--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="main\n")
            if argv[-3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(
                    returncode=0,
                    stdout="git@github.com:org/authentic.git\n",
                )
            raise AssertionError(f"unexpected command: {argv}")

    with pytest.raises(BonsaiWorkspaceError, match="checkout directory must match"):
        execute_init(ExistingCheckoutRunner(), checkout)

    assert not (workspace_root / ".bonsai" / "state.json").exists()


def test_execute_add_uses_slug_path_when_adding_git_worktree(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    plan = execute_add(runner, "../outside", workspace_root)

    assert plan.worktree_path == workspace_root / "outside"
    assert runner.commands[2].argv == (
        "git",
        "-C",
        str(default_worktree),
        "worktree",
        "add",
        "-b",
        "../outside",
        str(workspace_root / "outside"),
        "origin/main",
    )
    assert (workspace_root / "outside" / ".env").is_symlink()
    assert (workspace_root / "outside" / ".env").resolve() == default_worktree / ".env"


def test_execute_add_prefers_workspace_root_config_over_repo_config(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(
        default_worktree,
        VALID_CONFIG.replace('setup = "yarn setup"', 'setup = "yarn repo-setup"'),
    )
    root_config = VALID_CONFIG.replace(
        'setup = "yarn setup"',
        'setup = "python -c \\"print(2)\\""',
    ).replace("base_port = 5555", "base_port = 6000")
    write_config(workspace_root, root_config)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    execute_add(runner, "feature", workspace_root)

    assert runner.commands[-1].argv == ("python", "-c", "print(2)")
    assert dict(runner.commands[-1].env)["DB_PORT"] == "6001"


def test_execute_move_uses_temporary_path_for_case_only_rename(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class MovingRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[:5] == [
                "git",
                "-C",
                str(default_worktree),
                "worktree",
                "move",
            ]:
                Path(argv[5]).rename(Path(argv[6]))
                return CommandResult(returncode=0)
            return CommandResult(returncode=0)

    runner = MovingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    old_worktree = workspace_root / "mb-123"
    temp_worktree = workspace_root / ".bonsai-move-MB-123"
    new_worktree = workspace_root / "MB-123"
    default_worktree.mkdir(parents=True)
    old_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={"MB-123": ManagedWorktree(path="mb-123", slug="mb-123", slot=1)},
        ),
    )
    monkeypatch.setattr(
        "bonsai.workflows.worktrees._paths_refer_to_same_existing_path",
        lambda left, right: left == old_worktree and right == new_worktree,
    )

    execute_move(runner, "MB-123", "MB-123", workspace_root)

    assert CommandSpec(
        argv=(
            "git",
            "-C",
            str(default_worktree),
            "worktree",
            "move",
            str(old_worktree),
            str(temp_worktree),
        )
    ) in runner.commands
    assert CommandSpec(
        argv=(
            "git",
            "-C",
            str(default_worktree),
            "worktree",
            "move",
            str(temp_worktree),
            str(new_worktree),
        )
    ) in runner.commands
    assert new_worktree.exists()


def test_execute_move_default_without_force_raises_and_changes_nothing(
    tmp_path: Path,
) -> None:
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

    with pytest.raises(BonsaiWorkspaceError, match="pass --force"):
        execute_move(RecordingRunner(), "main", "trunk", workspace_root)

    assert default_worktree.exists()
    assert not (workspace_root / "trunk").exists()
    assert load_state(
        workspace_root / ".bonsai" / "state.json"
    ).default_worktree == "main"


def test_execute_move_default_with_force_renames(tmp_path: Path) -> None:
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
    runner = RecordingRunner()

    plan = execute_move(runner, "main", "trunk", workspace_root, force=True)

    new_default = workspace_root / "trunk"
    assert plan.new_worktree_path == new_default
    assert not default_worktree.exists()
    assert new_default.is_dir()
    assert load_state(
        workspace_root / ".bonsai" / "state.json"
    ).default_worktree == "trunk"
    assert CommandSpec(
        argv=("git", "-C", str(new_default), "worktree", "repair")
    ) in runner.commands


def test_execute_rename_default_uses_temporary_path_for_case_only_rename(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    new_default = workspace_root / "Main"
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
    monkeypatch.setattr(
        "bonsai.workflows.worktrees._paths_refer_to_same_existing_path",
        lambda left, right: left == default_worktree and right == new_default,
    )
    runner = RecordingRunner()

    plan = execute_rename_default(runner, workspace_root, "Main")

    assert plan.new_worktree_path == new_default
    assert load_state(
        workspace_root / ".bonsai" / "state.json"
    ).default_worktree == "Main"
    assert CommandSpec(
        argv=("git", "-C", str(new_default), "worktree", "repair")
    ) in runner.commands


def test_execute_add_rejects_conflicting_shared_file_target_without_saving_state(
    tmp_path: Path,
) -> None:
    class ExistingWorktreeRunner:
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            if argv[-2:] == ["rev-parse", "--is-inside-work-tree"]:
                return CommandResult(returncode=0, stdout="true\n")
            if argv[-3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="feature\n")
            return CommandResult(returncode=0)

    runner = ExistingWorktreeRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (branch_worktree / ".env").write_text("local secret\n", encoding="utf-8")
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

    with pytest.raises(BonsaiWorkspaceError, match="Shared file target already exists"):
        execute_add(runner, "feature", workspace_root)

    assert not (branch_worktree / ".env.local").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert "feature" not in state.worktrees


def test_execute_add_rejects_missing_shared_file_source_without_saving_state(
    tmp_path: Path,
) -> None:
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

    with pytest.raises(BonsaiWorkspaceError, match="Shared file source does not exist"):
        execute_add(runner, "feature", workspace_root)

    assert not (workspace_root / "feature" / ".env.local").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert "feature" not in state.worktrees


def test_execute_add_rejects_unrelated_existing_directory(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
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

    with pytest.raises(BonsaiWorkspaceError, match="not a git worktree"):
        execute_add(runner, "feature", workspace_root)

    assert not (branch_worktree / ".env.local").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert "feature" not in state.worktrees
    assert all(command.argv != ("yarn", "install") for command in runner.commands)


def test_execute_add_rejects_existing_worktree_for_different_branch(tmp_path: Path) -> None:
    class DifferentBranchRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["rev-parse", "--is-inside-work-tree"]:
                return CommandResult(returncode=0, stdout="true\n")
            if argv[-3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return CommandResult(returncode=0, stdout="other-branch\n")
            return CommandResult(returncode=0)

    runner = DifferentBranchRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
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

    with pytest.raises(BonsaiWorkspaceError, match="has branch other-branch"):
        execute_add(runner, "feature", workspace_root)

    assert not (branch_worktree / ".env.local").exists()
    state = load_state(workspace_root / ".bonsai" / "state.json")
    assert "feature" not in state.worktrees
    assert all(command.argv != ("yarn", "install") for command in runner.commands)


def test_execute_add_parses_quoted_install_command(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        'install = "yarn install"',
        'install = "python -c \\"print(1)\\""',
    )
    write_config(default_worktree, config_text)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    execute_add(runner, "feature", workspace_root)

    assert runner.commands[-2].argv == ("python", "-c", "print(1)")
    assert runner.commands[-1].argv == ("yarn", "setup")


def test_execute_add_can_override_base_branch_for_new_branch(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    execute_add(runner, "feature", workspace_root, base_branch="develop")

    assert runner.commands[2].argv == (
        "git",
        "-C",
        str(default_worktree),
        "worktree",
        "add",
        "-b",
        "feature",
        str(workspace_root / "feature"),
        "origin/develop",
    )


def test_execute_add_pull_request_uses_same_repo_branch(tmp_path: Path) -> None:
    class PullRequestRunner(RecordingRunner):
        def run(self, argv, cwd=None, check=True, env=None):
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0, stdout="gh version 2.0.0\n")
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:3] == ["gh", "pr", "view"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        '{"headRefName":"feature","isCrossRepository":false,'
                        '"state":"OPEN","title":"Feature","url":"https://example.test/pr/7"}'
                    ),
                )
            if argv[:6] == ["git", "-C", str(default_worktree), "ls-remote", "--heads", "origin"]:
                return CommandResult(returncode=0, stdout="abc\trefs/heads/feature\n")
            return CommandResult(returncode=0)

    runner = PullRequestRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    result = execute_add_pull_request(runner, 7, workspace_root)

    assert result.branch == "feature"
    assert result.read_only is False
    assert result.add_plan.worktree_path == workspace_root / "feature"
    assert CommandSpec(
        argv=(
            "git",
            "-C",
            str(default_worktree),
            "worktree",
            "add",
            str(workspace_root / "feature"),
            "feature",
        )
    ) in runner.commands


def test_execute_add_pull_request_fetches_fork_branch(tmp_path: Path) -> None:
    class ForkPullRequestRunner(RecordingRunner):
        def run(self, argv, cwd=None, check=True, env=None):
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0, stdout="gh version 2.0.0\n")
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:3] == ["gh", "pr", "view"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        '{"headRefName":"feature","isCrossRepository":true,'
                        '"state":"OPEN","title":"Feature","url":"https://example.test/pr/12"}'
                    ),
                )
            return CommandResult(returncode=0)

    runner = ForkPullRequestRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    result = execute_add_pull_request(runner, 12, workspace_root)

    assert result.branch == "bonsai/pr-12"
    assert result.read_only is True
    assert CommandSpec(
        argv=("git", "-C", str(default_worktree), "fetch", "origin", "pull/12/head:bonsai/pr-12")
    ) in runner.commands
    assert CommandSpec(
        argv=(
            "git",
            "-C",
            str(default_worktree),
            "worktree",
            "add",
            str(workspace_root / "bonsai-pr-12"),
            "bonsai/pr-12",
        )
    ) in runner.commands


def test_execute_add_pull_request_requires_force_for_closed_pr(tmp_path: Path) -> None:
    class ClosedPullRequestRunner(RecordingRunner):
        def run(self, argv, cwd=None, check=True, env=None):
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0, stdout="gh version 2.0.0\n")
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:3] == ["gh", "pr", "view"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        '{"headRefName":"feature","isCrossRepository":false,'
                        '"state":"CLOSED","title":"Feature","url":"https://example.test/pr/9"}'
                    ),
                )
            if argv[:6] == ["git", "-C", str(default_worktree), "ls-remote", "--heads", "origin"]:
                return CommandResult(returncode=0, stdout="abc\trefs/heads/feature\n")
            return CommandResult(returncode=0)

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    runner = ClosedPullRequestRunner()
    with pytest.raises(BonsaiWorkspaceError, match="requires --force"):
        execute_add_pull_request(runner, 9, workspace_root)
    assert all("worktree" not in command.argv for command in runner.commands)

    forced = ClosedPullRequestRunner()
    result = execute_add_pull_request(forced, 9, workspace_root, force=True)

    assert result.branch == "feature"


def test_execute_add_runs_setup_after_install(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        'setup = "yarn setup"',
        'setup = "python -c \\"print(2)\\""',
    )
    write_config(default_worktree, config_text)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    execute_add(runner, "feature", workspace_root)

    assert runner.commands[-2].argv == ("yarn", "install")
    assert runner.commands[-2].cwd == workspace_root / "feature"
    assert runner.commands[-1].argv == ("python", "-c", "print(2)")
    assert runner.commands[-1].cwd == workspace_root / "feature"


def test_execute_add_runs_setup_with_generated_worktree_env(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    execute_add(runner, "feature", workspace_root)

    setup_env = dict(runner.commands[-1].env)
    assert setup_env["COMPOSE_PROJECT_NAME"] == "authentic-feature"
    assert setup_env["FRONTEND_PORT"] == "4201"
    assert setup_env["API_PORT"] == "3334"
    assert setup_env["DB_PORT"] == "5556"


def test_execute_add_runs_pre_and_post_commands_with_generated_worktree_env(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        '[commands]\ninstall = "yarn install"\nsetup = "yarn setup"\nstart = "yarn dev"',
        "\n".join(
            [
                "[commands]",
                'preinstall = "echo preinstall"',
                'install = "yarn install"',
                'postinstall = "echo postinstall"',
                'presetup = "echo presetup"',
                'setup = "yarn setup"',
                'postsetup = "echo postsetup"',
                'start = "yarn dev"',
            ]
        ),
    )
    write_config(default_worktree, config_text)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    execute_add(runner, "feature", workspace_root)

    lifecycle_commands = runner.commands[-6:]
    assert [command.argv for command in lifecycle_commands] == [
        ("echo", "preinstall"),
        ("yarn", "install"),
        ("echo", "postinstall"),
        ("echo", "presetup"),
        ("yarn", "setup"),
        ("echo", "postsetup"),
    ]
    assert all(command.cwd == workspace_root / "feature" for command in lifecycle_commands)
    log_kinds = [
        command.log_path.name.removesuffix(".log").split("-", maxsplit=2)[-1]
        for command in lifecycle_commands
        if command.log_path is not None
    ]
    assert log_kinds == [
        "preinstall",
        "install",
        "postinstall",
        "presetup",
        "setup",
        "postsetup",
    ]
    for command in lifecycle_commands:
        command_env = dict(command.env)
        assert command_env["COMPOSE_PROJECT_NAME"] == "authentic-feature"
        assert command_env["FRONTEND_PORT"] == "4201"
        assert command_env["API_PORT"] == "3334"
        assert command_env["DB_PORT"] == "5556"


def test_execute_add_runs_postadd_after_prepare_commands(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        '[commands]\ninstall = "yarn install"\nsetup = "yarn setup"\nstart = "yarn dev"',
        "\n".join(
            [
                "[commands]",
                'install = "yarn install"',
                'setup = "yarn setup"',
                'postadd = "echo postadd"',
                'start = "yarn dev"',
            ]
        ),
    )
    write_config(default_worktree, config_text)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    execute_add(runner, "feature", workspace_root)

    assert [command.argv for command in runner.commands[-3:]] == [
        ("yarn", "install"),
        ("yarn", "setup"),
        ("echo", "postadd"),
    ]
    postadd = runner.commands[-1]
    assert postadd.cwd == workspace_root / "feature"
    assert postadd.log_path is not None
    assert postadd.log_path.name.endswith("-postadd.log")
    assert dict(postadd.env)["FRONTEND_PORT"] == "4201"


def test_execute_add_logs_install_and_setup_under_managed_worktree_slug(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    execute_add(runner, "feature/auth", workspace_root)

    logs_dir = workspace_root / ".bonsai" / "logs" / "feature-auth"
    install_command = runner.commands[-2]
    setup_command = runner.commands[-1]
    assert install_command.argv == ("yarn", "install")
    assert install_command.log_path is not None
    assert install_command.log_path.parent == logs_dir
    assert install_command.log_path.name.endswith("-install.log")
    assert setup_command.argv == ("yarn", "setup")
    assert setup_command.log_path is not None
    assert setup_command.log_path.parent == logs_dir
    assert setup_command.log_path.name.endswith("-setup.log")


def test_execute_remove_removes_clean_worktree_snippets_and_state(tmp_path: Path) -> None:
    class CleanRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = CleanRemoveRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    snippets = app_snippets_dir("authentic")
    snippets.mkdir(parents=True)
    (snippets / "feature-frontend.caddy").write_text("feature\n", encoding="utf-8")
    (snippets / "other-frontend.caddy").write_text("other\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
                "other": ManagedWorktree(path="other", slug="other", slot=2),
            },
        ),
    )

    plan = execute_remove(runner, "feature", workspace_root)

    assert plan.branch == "feature"
    assert plan.worktree_path == branch_worktree
    assert plan.removed_snippets == (snippets / "feature-frontend.caddy",)
    assert not (snippets / "feature-frontend.caddy").exists()
    assert (snippets / "other-frontend.caddy").exists()
    assert set(load_state(workspace_root / ".bonsai" / "state.json").worktrees) == {"other"}
    assert runner.commands[0] == CommandSpec(
        argv=("git", "-C", str(branch_worktree), "status", "--porcelain")
    )
    assert CommandSpec(
        argv=(
            "git",
            "-C",
            str(default_worktree),
            "worktree",
            "remove",
            str(branch_worktree),
        )
    ) in runner.commands


def test_execute_remove_runs_preremove_before_teardown(tmp_path: Path) -> None:
    class CleanRemoveRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = CleanRemoveRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        '[commands]\ninstall = "yarn install"\nsetup = "yarn setup"\nstart = "yarn dev"',
        "\n".join(
            [
                "[commands]",
                'install = "yarn install"',
                'setup = "yarn setup"',
                'preremove = "echo preremove"',
                'start = "yarn dev"',
            ]
        ),
    )
    write_config(default_worktree, config_text)
    (branch_worktree / ".env.local").write_text("FRONTEND_PORT=4201\n", encoding="utf-8")
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

    execute_remove(runner, "feature", workspace_root)

    preremove_index = next(
        index
        for index, command in enumerate(runner.commands)
        if command.argv == ("echo", "preremove")
    )
    git_remove_index = next(
        index
        for index, command in enumerate(runner.commands)
        if command.argv[:5] == ("git", "-C", str(default_worktree), "worktree", "remove")
    )
    assert preremove_index < git_remove_index
    assert runner.commands[preremove_index].cwd == branch_worktree
    assert dict(runner.commands[preremove_index].env)["FRONTEND_PORT"] == "4201"


def test_execute_remove_preremove_failure_aborts_unless_forced(tmp_path: Path) -> None:
    class FailingPreremoveRunner(RecordingRunner):
        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
            env=None,
        ) -> CommandResult:
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

        def run_stream_logged(
            self,
            argv: list[str],
            cwd: Path | None = None,
            env=None,
            log_path: Path | None = None,
            label: str | None = None,
        ) -> int:
            _ = label
            recorded_env = tuple(sorted(env.items())) if env is not None else ()
            self.commands.append(
                CommandSpec(argv=tuple(argv), cwd=cwd, env=recorded_env, log_path=log_path)
            )
            if log_path is not None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("", encoding="utf-8")
            return 17 if argv == ["false"] else 0

    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    config_text = VALID_CONFIG.replace(
        '[commands]\ninstall = "yarn install"\nsetup = "yarn setup"\nstart = "yarn dev"',
        "\n".join(
            [
                "[commands]",
                'install = "yarn install"',
                'setup = "yarn setup"',
                'preremove = "false"',
                'start = "yarn dev"',
            ]
        ),
    )
    write_config(default_worktree, config_text)
    (branch_worktree / ".env.local").write_text("FRONTEND_PORT=4201\n", encoding="utf-8")
    state = BonsaiState(
        version=1,
        name="authentic",
        default_branch="main",
        default_worktree="main",
        repo_url="git@github.com:org/authentic.git",
        worktrees={"feature": ManagedWorktree(path="feature", slug="feature", slot=1)},
    )
    save_state(workspace_root / ".bonsai" / "state.json", state)

    runner = FailingPreremoveRunner()
    with pytest.raises(BonsaiCommandError, match="Command failed \\(17\\)"):
        execute_remove(runner, "feature", workspace_root)
    assert all(
        command.argv[:5] != ("git", "-C", str(default_worktree), "worktree", "remove")
        for command in runner.commands
    )

    save_state(workspace_root / ".bonsai" / "state.json", state)
    forced_runner = FailingPreremoveRunner()
    execute_remove(forced_runner, "feature", workspace_root, force=True)
    assert any(
        command.argv[:5] == ("git", "-C", str(default_worktree), "worktree", "remove")
        for command in forced_runner.commands
    )


def test_execute_remove_tears_down_compose_before_git_remove(tmp_path: Path) -> None:
    class CleanRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = CleanRemoveRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (branch_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (branch_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
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

    plan = execute_remove(runner, "feature", workspace_root)

    assert plan.compose_project_name == "authentic-feature"
    status_index = runner.commands.index(
        CommandSpec(argv=("git", "-C", str(branch_worktree), "status", "--porcelain")),
    )
    docker_index = runner.commands.index(
        CommandSpec(
            argv=("docker", "compose", "-p", "authentic-feature", "down"),
            cwd=branch_worktree,
        ),
    )
    git_remove_index = runner.commands.index(
        CommandSpec(
            argv=(
                "git",
                "-C",
                str(default_worktree),
                "worktree",
                "remove",
                str(branch_worktree),
            )
        )
    )
    assert status_index < docker_index < git_remove_index


def test_execute_remove_skips_compose_without_compose_file(tmp_path: Path) -> None:
    class CleanRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = CleanRemoveRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (branch_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
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

    plan = execute_remove(runner, "feature", workspace_root)

    assert plan.compose_project_name is None
    assert all(command.argv[:2] != ("docker", "compose") for command in runner.commands)


def test_execute_remove_blocks_when_compose_teardown_fails(tmp_path: Path) -> None:
    class FailingComposeRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            if argv[:2] == ["docker", "compose"]:
                raise BonsaiCommandError("docker compose failed")
            return CommandResult(returncode=0)

    runner = FailingComposeRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (branch_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (branch_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
        encoding="utf-8",
    )
    snippets = app_snippets_dir("authentic")
    snippets.mkdir(parents=True, exist_ok=True)
    snippet = snippets / "feature-frontend.caddy"
    snippet.write_text("feature\n", encoding="utf-8")
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

    expected = (
        "Failed to tear down Docker Compose project "
        f"authentic-feature at {branch_worktree}"
    )
    with pytest.raises(BonsaiWorkspaceError, match=expected):
        execute_remove(runner, "feature", workspace_root, force=True)

    assert snippet.exists()
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].slot == 1
    assert all("remove" not in command.argv for command in runner.commands)


def test_execute_remove_refuses_dirty_worktree_without_force(tmp_path: Path) -> None:
    class DirtyRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            return CommandResult(returncode=0, stdout=" M README.md\n")

    runner = DirtyRemoveRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
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

    with pytest.raises(BonsaiWorkspaceError, match="has uncommitted changes"):
        execute_remove(runner, "feature", workspace_root)

    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].slot == 1
    assert all("remove" not in command.argv for command in runner.commands)


def test_execute_remove_forces_dirty_worktree_when_requested(tmp_path: Path) -> None:
    class DirtyForceRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout=" M README.md\n")
            return CommandResult(returncode=0)

    runner = DirtyForceRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
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

    execute_remove(runner, "feature", workspace_root, force=True)

    git_remove = (
        "git", "-C", str(default_worktree), "worktree", "remove",
        "--force", str(branch_worktree),
    )
    assert any(cmd.argv == git_remove for cmd in runner.commands)
    assert runner.commands[-1].argv == (
        "caddy",
        "reload",
        "--config",
        str(global_caddy_paths()[0]),
    )
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees == {}


def test_execute_remove_rejects_unknown_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
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

    with pytest.raises(BonsaiWorkspaceError, match="Unknown worktree: missing"):
        execute_remove(RecordingRunner(), "missing", workspace_root)


def test_execute_remove_rejects_default_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
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

    with pytest.raises(BonsaiWorkspaceError, match="Cannot remove the default worktree"):
        execute_remove(RecordingRunner(), "main", workspace_root)


def test_execute_remove_preserves_state_when_git_remove_fails(tmp_path: Path) -> None:
    class FailingRemoveRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            raise BonsaiCommandError("git worktree remove failed")

    runner = FailingRemoveRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    branch_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    branch_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    snippets = app_snippets_dir("authentic")
    snippets.mkdir(parents=True, exist_ok=True)
    snippet = snippets / "feature-frontend.caddy"
    snippet.write_text("feature\n", encoding="utf-8")
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

    with pytest.raises(BonsaiCommandError, match="git worktree remove failed"):
        execute_remove(runner, "feature", workspace_root)

    assert snippet.exists()
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].slot == 1


def test_execute_cleanup_requires_authenticated_github_cli(tmp_path: Path) -> None:
    class UnauthenticatedGhRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0, stdout="gh version 2.0.0\n")
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=1, stderr="not logged in\n")
            return CommandResult(returncode=0)

    runner = UnauthenticatedGhRunner()
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
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

    with pytest.raises(BonsaiWorkspaceError, match="gh auth login"):
        execute_cleanup(runner, workspace_root)

    assert runner.commands == [
        CommandSpec(argv=("gh", "--version")),
        CommandSpec(argv=("gh", "auth", "status"), cwd=workspace_root / "main"),
    ]


def test_execute_cleanup_dry_run_marks_merged_prs_and_skips_others(tmp_path: Path) -> None:
    class CleanupDryRunRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0, stdout="gh version 2.0.0\n")
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:4] == ["gh", "pr", "list", "--head"]:
                branch = argv[4]
                payload = {
                    "feature": '[{"state":"MERGED","mergedAt":"2026-05-01T00:00:00Z","url":"https://github.com/org/repo/pull/1"}]',
                    "open": '[{"state":"OPEN","mergedAt":null,"url":"https://github.com/org/repo/pull/2"}]',
                    "missing": "[]",
                }[branch]
                return CommandResult(returncode=0, stdout=payload)
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = CleanupDryRunRunner()
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    for name in ("feature", "open", "missing"):
        (workspace_root / name).mkdir()
    feature_worktree = workspace_root / "feature"
    (feature_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (feature_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
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
            worktrees={
                "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
                "open": ManagedWorktree(path="open", slug="open", slot=2),
                "missing": ManagedWorktree(path="missing", slug="missing", slot=3),
            },
        ),
    )

    plan = execute_cleanup(runner, workspace_root)

    assert [(item.branch, item.action, item.reason, item.pr_url) for item in plan.items] == [
        ("feature", "remove", "pull request is merged", "https://github.com/org/repo/pull/1"),
        ("missing", "skip", "no pull request found", None),
        ("open", "skip", "pull request is open", "https://github.com/org/repo/pull/2"),
    ]
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees.keys() == {
        "feature",
        "open",
        "missing",
    }
    assert all(command.argv[:2] != ("docker", "compose") for command in runner.commands)


def test_execute_cleanup_recognizes_fork_pr_branch_names(tmp_path: Path) -> None:
    class ForkCleanupRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0, stdout="gh version 2.0.0\n")
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:4] == ["gh", "pr", "view", "12"]:
                return CommandResult(
                    returncode=0,
                    stdout=(
                        '{"state":"MERGED","mergedAt":"2026-06-11T12:00:00Z",'
                        '"url":"https://github.com/org/repo/pull/12"}'
                    ),
                )
            return CommandResult(returncode=0)

    runner = ForkCleanupRunner()
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "bonsai/pr-12": ManagedWorktree(
                    path="bonsai-pr-12",
                    slug="bonsai-pr-12",
                    slot=1,
                )
            },
        ),
    )

    plan = execute_cleanup(runner, workspace_root)

    assert [(item.branch, item.action, item.reason, item.pr_url) for item in plan.items] == [
        ("bonsai/pr-12", "remove", "pull request is merged", "https://github.com/org/repo/pull/12")
    ]


def test_execute_cleanup_skips_dirty_merged_prs_without_force(tmp_path: Path) -> None:
    class DirtyCleanupRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0)
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:4] == ["gh", "pr", "list", "--head"]:
                return CommandResult(
                    returncode=0,
                    stdout='[{"state":"MERGED","mergedAt":"2026-05-01T00:00:00Z","url":"https://github.com/org/repo/pull/1"}]',
                )
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout=" M README.md\n")
            return CommandResult(returncode=0)

    runner = DirtyCleanupRunner()
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    (workspace_root / "feature").mkdir()
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

    plan = execute_cleanup(runner, workspace_root, apply=True)

    assert [(item.branch, item.action, item.reason) for item in plan.items] == [
        ("feature", "skip", "worktree has uncommitted changes")
    ]
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].slot == 1
    assert all("remove" not in command.argv for command in runner.commands)


def test_execute_cleanup_apply_removes_merged_clean_worktrees(tmp_path: Path) -> None:
    class ApplyCleanupRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0)
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:4] == ["gh", "pr", "list", "--head"]:
                branch = argv[4]
                if branch == "feature":
                    return CommandResult(
                        returncode=0,
                        stdout='[{"state":"MERGED","mergedAt":"2026-05-01T00:00:00Z","url":"https://github.com/org/repo/pull/1"}]',
                    )
                return CommandResult(
                    returncode=0,
                    stdout='[{"state":"OPEN","mergedAt":null,"url":"https://github.com/org/repo/pull/2"}]',
                )
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = ApplyCleanupRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    open_worktree = workspace_root / "open"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    open_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    snippets = app_snippets_dir("authentic")
    snippets.mkdir(parents=True, exist_ok=True)
    (snippets / "feature-frontend.caddy").write_text("feature\n", encoding="utf-8")
    (snippets / "open-frontend.caddy").write_text("open\n", encoding="utf-8")
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature": ManagedWorktree(path="feature", slug="feature", slot=1),
                "open": ManagedWorktree(path="open", slug="open", slot=2),
            },
        ),
    )

    plan = execute_cleanup(runner, workspace_root, apply=True)

    assert [(item.branch, item.action, item.reason) for item in plan.items] == [
        ("feature", "removed", "pull request is merged"),
        ("open", "skip", "pull request is open"),
    ]
    assert set(load_state(workspace_root / ".bonsai" / "state.json").worktrees) == {"open"}
    assert not (snippets / "feature-frontend.caddy").exists()
    assert (snippets / "open-frontend.caddy").exists()
    assert CommandSpec(
        argv=("git", "-C", str(default_worktree), "worktree", "remove", str(feature_worktree))
    ) in runner.commands


def test_execute_cleanup_apply_tears_down_compose_through_remove(tmp_path: Path) -> None:
    class ApplyCleanupRunner:
        def __init__(self) -> None:
            self.commands: list[CommandSpec] = []

        def run(
            self,
            argv: list[str],
            cwd: Path | None = None,
            check: bool = True,
        ) -> CommandResult:
            self.commands.append(CommandSpec(argv=tuple(argv), cwd=cwd))
            if argv == ["gh", "--version"]:
                return CommandResult(returncode=0)
            if argv == ["gh", "auth", "status"]:
                return CommandResult(returncode=0)
            if argv[:4] == ["gh", "pr", "list", "--head"]:
                return CommandResult(
                    returncode=0,
                    stdout='[{"state":"MERGED","mergedAt":"2026-05-01T00:00:00Z","url":"https://github.com/org/repo/pull/1"}]',
                )
            if argv[-2:] == ["status", "--porcelain"]:
                return CommandResult(returncode=0, stdout="")
            return CommandResult(returncode=0)

    runner = ApplyCleanupRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    feature_worktree = workspace_root / "feature"
    default_worktree.mkdir(parents=True)
    feature_worktree.mkdir()
    write_config(default_worktree, VALID_CONFIG)
    (feature_worktree / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (feature_worktree / ".env.local").write_text(
        "COMPOSE_PROJECT_NAME=authentic-feature\n",
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

    plan = execute_cleanup(runner, workspace_root, apply=True)

    assert [(item.branch, item.action, item.reason) for item in plan.items] == [
        ("feature", "removed", "pull request is merged")
    ]
    docker_index = next(
        index
        for index, command in enumerate(runner.commands)
        if command.argv[:2] == ("docker", "compose")
    )
    git_remove_index = next(
        index
        for index, command in enumerate(runner.commands)
        if command.argv[:5] == ("git", "-C", str(default_worktree), "worktree", "remove")
    )
    assert runner.commands[docker_index] == CommandSpec(
        argv=("docker", "compose", "-p", "authentic-feature", "down"),
        cwd=feature_worktree,
    )
    assert docker_index < git_remove_index


def test_execute_checkout_resolves_existing_managed_worktree(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    (workspace_root / "feature").mkdir()
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

    plan = execute_checkout(RecordingRunner(), "feature", workspace_root)

    assert plan.worktree_path == workspace_root / "feature"
    assert plan.created is False


def test_worktree_name_completions_include_matching_aliases(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    (workspace_root / "feature-authentication").mkdir()
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature/authentication": ManagedWorktree(
                    path="feature-authentication",
                    slug="feature-authentication",
                    slot=1,
                )
            },
        ),
    )

    assert worktree_name_completions(workspace_root, "auth") == (
        "feature/authentication",
        "feature-authentication",
    )


def test_execute_checkout_resolves_unique_fuzzy_worktree_match(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    (workspace_root / "feature-authentication").mkdir()
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature/authentication": ManagedWorktree(
                    path="feature-authentication",
                    slug="feature-authentication",
                    slot=1,
                )
            },
        ),
    )

    plan = execute_checkout(runner, "auth", workspace_root)

    assert plan.worktree_path == workspace_root / "feature-authentication"
    assert plan.created is False
    assert runner.commands == []


def test_execute_checkout_rejects_ambiguous_fuzzy_worktree_match(tmp_path: Path) -> None:
    workspace_root = tmp_path / "authentic"
    (workspace_root / "main").mkdir(parents=True)
    (workspace_root / "feature-authentication").mkdir()
    (workspace_root / "fix-auth-redirect").mkdir()
    save_state(
        workspace_root / ".bonsai" / "state.json",
        BonsaiState(
            version=1,
            name="authentic",
            default_branch="main",
            default_worktree="main",
            repo_url="git@github.com:org/authentic.git",
            worktrees={
                "feature/authentication": ManagedWorktree(
                    path="feature-authentication",
                    slug="feature-authentication",
                    slot=1,
                ),
                "fix/auth-redirect": ManagedWorktree(
                    path="fix-auth-redirect",
                    slug="fix-auth-redirect",
                    slot=2,
                ),
            },
        ),
    )

    with pytest.raises(BonsaiWorkspaceError, match="Ambiguous Bonsai worktree"):
        execute_checkout(RecordingRunner(), "auth", workspace_root)


def test_execute_checkout_adds_missing_branch_with_existing_add_workflow(tmp_path: Path) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    plan = execute_checkout(runner, "feature", workspace_root)

    assert plan.worktree_path == workspace_root / "feature"
    assert plan.created is True
    assert load_state(workspace_root / ".bonsai" / "state.json").worktrees["feature"].path == (
        "feature"
    )
    assert runner.commands[2].argv == (
        "git",
        "-C",
        str(default_worktree),
        "worktree",
        "add",
        "-b",
        "feature",
        str(workspace_root / "feature"),
        "origin/main",
    )


def test_execute_checkout_can_override_base_branch_for_missing_branch(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    workspace_root = tmp_path / "authentic"
    default_worktree = workspace_root / "main"
    default_worktree.mkdir(parents=True)
    write_config(default_worktree, VALID_CONFIG)
    (default_worktree / ".env").write_text("SECRET=value\n", encoding="utf-8")
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

    plan = execute_checkout(runner, "feature", workspace_root, base_branch="develop")

    assert plan.worktree_path == workspace_root / "feature"
    assert plan.created is True
    assert runner.commands[2].argv[-1] == "origin/develop"
