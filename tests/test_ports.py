from pathlib import Path

from bonsai.models import CommandResult
from bonsai.ports import inspect_port_owners, parse_lsof_listener_output


class LsofRunner:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        argv: list[str],
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        self.commands.append(tuple(argv))
        if argv[:4] == ["lsof", "-nP", "-iTCP:4201", "-sTCP:LISTEN"]:
            return CommandResult(returncode=0, stdout="p123\ncnode\numichael\nnTCP *:4201\n")
        if argv == ["lsof", "-a", "-p", "123", "-d", "cwd", "-Fn"]:
            return CommandResult(returncode=0, stdout=f"p123\nn{self.cwd}\n")
        return CommandResult(returncode=1)


def test_parse_lsof_listener_output_groups_process_records() -> None:
    owners = parse_lsof_listener_output(
        "p123\ncnode\numichael\nnTCP *:4201\np456\ncpython\nuroot\n"
    )

    assert [(owner.pid, owner.command, owner.user) for owner in owners] == [
        (123, "node", "michael"),
        (456, "python", "root"),
    ]


def test_inspect_port_owners_enriches_listener_with_cwd(tmp_path: Path) -> None:
    runner = LsofRunner(tmp_path / "feature-a")

    owners = inspect_port_owners(runner, 4201)

    assert len(owners) == 1
    assert owners[0].pid == 123
    assert owners[0].command == "node"
    assert owners[0].cwd == tmp_path / "feature-a"
    assert ("lsof", "-a", "-p", "123", "-d", "cwd", "-Fn") in runner.commands
