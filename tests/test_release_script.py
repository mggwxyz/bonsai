from __future__ import annotations

import importlib.util
from pathlib import Path


def load_release_module():
    script_path = Path(__file__).parents[1] / "scripts" / "release.py"
    assert script_path.exists()
    spec = importlib.util.spec_from_file_location("release_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_release_fixture(root: Path) -> None:
    (root / "src" / "bonsai").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "Formula").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "bonsai"\nversion = "0.1.2"\n',
        encoding="utf-8",
    )
    (root / "src" / "bonsai" / "__init__.py").write_text(
        '__version__ = "0.1.2"\n',
        encoding="utf-8",
    )
    (root / "tests" / "test_cli.py").write_text(
        'def test_version():\n    assert "bonsai 0.1.2"\n',
        encoding="utf-8",
    )
    (root / "Formula" / "bonsai.rb").write_text(
        'url "https://github.com/mggwxyz/bonsai.git", tag: "v0.1.2"\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        (
            '[[package]]\nname = "bonsai"\nversion = "0.1.2"\n\n'
            '[[package]]\nname = "mdurl"\nversion = "0.1.2"\n'
        ),
        encoding="utf-8",
    )


def test_bump_project_versions_updates_release_files(tmp_path: Path) -> None:
    release = load_release_module()
    write_release_fixture(tmp_path)

    previous = release.bump_project_versions(tmp_path, "0.1.3")

    assert previous == "0.1.2"
    assert 'version = "0.1.3"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '__version__ = "0.1.3"' in (
        tmp_path / "src" / "bonsai" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "bonsai 0.1.3" in (tmp_path / "tests" / "test_cli.py").read_text(
        encoding="utf-8"
    )
    assert 'tag: "v0.1.3"' in (tmp_path / "Formula" / "bonsai.rb").read_text(
        encoding="utf-8"
    )
    lockfile = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "bonsai"\nversion = "0.1.3"' in lockfile
    assert 'name = "mdurl"\nversion = "0.1.2"' in lockfile


def test_sync_tap_formula_copies_project_formula(tmp_path: Path) -> None:
    release = load_release_module()
    write_release_fixture(tmp_path / "project")
    tap = tmp_path / "homebrew-tap"
    (tap / "Formula").mkdir(parents=True)
    (tap / "Formula" / "bonsai.rb").write_text("old formula\n", encoding="utf-8")

    release.bump_project_versions(tmp_path / "project", "0.1.3")
    release.sync_tap_formula(tmp_path / "project", tap)

    assert (tap / "Formula" / "bonsai.rb").read_text(encoding="utf-8") == (
        tmp_path / "project" / "Formula" / "bonsai.rb"
    ).read_text(encoding="utf-8")


def test_project_publish_commands_commit_tag_and_push() -> None:
    release = load_release_module()

    commands = release.project_publish_commands("0.1.3")

    assert commands == [
        [
            "git",
            "add",
            "pyproject.toml",
            "src/bonsai/__init__.py",
            "tests/test_cli.py",
            "Formula/bonsai.rb",
            "uv.lock",
        ],
        ["git", "commit", "-m", "chore: release 0.1.3"],
        ["git", "tag", "v0.1.3"],
        ["git", "push"],
        ["git", "push", "origin", "v0.1.3"],
    ]


def test_tap_publish_commands_commit_and_push_formula() -> None:
    release = load_release_module()

    commands = release.tap_publish_commands("0.1.3")

    assert commands == [
        ["git", "add", "Formula/bonsai.rb"],
        ["git", "commit", "-m", "bonsai 0.1.3"],
        ["git", "push"],
    ]
