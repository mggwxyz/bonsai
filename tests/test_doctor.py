import json
from pathlib import Path

from typer.testing import CliRunner

from bonsai import cli
from bonsai.doctor import preflight_report
from bonsai.models import CommandResult
from bonsai.process import RecordingRunner

cli_runner = CliRunner()


class PreflightRunner(RecordingRunner):
    def __init__(self, available: set[str]) -> None:
        super().__init__()
        self.available = available

    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        self.commands.append((tuple(argv), cwd))
        return CommandResult(returncode=0 if argv[0] in self.available else 1)


def _write_shell_integration(home: Path) -> None:
    (home / ".zshrc").write_text(
        '# >>> bonsai shell integration >>>\neval "$(bonsai shell-init zsh)"\n',
        encoding="utf-8",
    )


def _check(report, check_id):
    return next(check for check in report.checks if check.id == check_id)


def test_preflight_missing_git_fails_with_hint(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_shell_integration(home)
    runner = PreflightRunner({"caddy", "brew"})

    report = preflight_report(runner, repo_path=tmp_path, home=home)

    git_check = _check(report, "git")
    assert git_check.status == "fail"
    assert git_check.hint and "git-scm.com" in git_check.hint
    assert report.failed is True


def test_preflight_missing_caddy_fails_with_brew_install_hint(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_shell_integration(home)
    runner = PreflightRunner({"git", "brew"})

    report = preflight_report(runner, repo_path=tmp_path, home=home)

    caddy_check = _check(report, "caddy")
    assert caddy_check.status == "fail"
    assert caddy_check.hint and "brew install caddy" in caddy_check.hint


def test_preflight_missing_shell_integration_fails_with_hint(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runner = PreflightRunner({"git", "caddy", "brew"})

    report = preflight_report(runner, repo_path=tmp_path, home=home)

    shell_check = _check(report, "shell-integration")
    assert shell_check.status == "fail"
    assert shell_check.hint and "bonsai shell-init zsh" in shell_check.hint


def test_preflight_compose_repo_with_docker_missing_fails(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_shell_integration(home)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    runner = PreflightRunner({"git", "caddy", "brew"})

    report = preflight_report(runner, repo_path=tmp_path, home=home)

    docker_check = _check(report, "docker")
    assert docker_check.status == "fail"
    assert docker_check.hint and "docker.com" in docker_check.hint


def test_preflight_non_compose_repo_emits_no_docker_check(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_shell_integration(home)
    runner = PreflightRunner({"git", "caddy", "brew"})

    report = preflight_report(runner, repo_path=tmp_path, home=home)

    assert all(check.id != "docker" for check in report.checks)


def test_preflight_all_present_passes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_shell_integration(home)
    runner = PreflightRunner({"git", "caddy", "brew", "docker"})

    report = preflight_report(runner, repo_path=tmp_path, home=home)

    assert report.failed is False
    assert all(check.status == "ok" for check in report.checks)


def test_preflight_no_repo_path_skips_docker(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_shell_integration(home)
    runner = PreflightRunner({"git", "caddy", "brew"})

    report = preflight_report(runner, home=home)

    assert all(check.id != "docker" for check in report.checks)


def test_doctor_preflight_json_returns_expected_structure() -> None:
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(
            cli.app, ["doctor", "--preflight", "--format", "json"]
        )

    assert result.exit_code in {0, 1}
    payload = json.loads(result.stdout)
    assert payload["schema"] == "bonsai.doctor.v1"
    check_ids = {check["id"] for check in payload["checks"]}
    assert {"git", "caddy", "brew", "shell-integration"} <= check_ids
    assert "docker" not in check_ids
    for check in payload["checks"]:
        assert set(check) >= {"id", "name", "status", "detail", "hint"}
