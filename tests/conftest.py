from __future__ import annotations

from pathlib import Path

import pytest

from bonsai.process import SubprocessRunner


@pytest.fixture(autouse=True)
def _isolate_home(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point $HOME at a per-test temp dir so Path.home() never touches real ~/.bonsai."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def _guard_machine_caddy_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from mutating the developer machine's Caddy service."""
    real_write_text = Path.write_text
    real_run = SubprocessRunner.run
    real_boot_caddyfile = Path("/opt/homebrew/etc/Caddyfile")

    def guarded_write_text(self: Path, *args, **kwargs):
        if self == real_boot_caddyfile:
            raise AssertionError(
                "tests must not write the real Homebrew Caddyfile; "
                "use a fake brew prefix"
            )
        return real_write_text(self, *args, **kwargs)

    def guarded_run(self, argv, cwd=None, check=True, env=None):
        if argv[:2] == ["brew", "--prefix"] or argv[:2] == ["caddy", "reload"]:
            raise AssertionError(
                "tests must not run real global Caddy routing commands; "
                "use RecordingRunner or a hermetic test runner"
            )
        return real_run(self, argv, cwd=cwd, check=check, env=env)

    monkeypatch.setattr(Path, "write_text", guarded_write_text)
    monkeypatch.setattr(SubprocessRunner, "run", guarded_run)
