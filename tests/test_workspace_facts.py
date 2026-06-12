from pathlib import Path

from bonsai.models import (
    BonsaiConfig,
    CaddyConfig,
    CommandsConfig,
    EnvConfig,
    ManagedWorktree,
    ServiceConfig,
    WorktreeTarget,
)
from bonsai.workspace_facts import build_worktree_facts


def test_build_worktree_facts_projects_env_services_and_summary(tmp_path: Path) -> None:
    worktree_path = tmp_path / "ma-123-test"
    worktree_path.mkdir()
    config = BonsaiConfig(
        name="authentic",
        base_branch="main",
        caddy=CaddyConfig(),
        commands=CommandsConfig(),
        env=(
            EnvConfig(
                name="DATABASE_URL",
                value="postgres://localhost:${DB_PORT}/${slug}",
            ),
        ),
        services=(
            ServiceConfig(
                name="frontend",
                port_env="PORT",
                base_port=3000,
                primary=True,
                url="https://${slug}.authentic.localhost",
            ),
            ServiceConfig(
                name="db",
                port_env="DB_PORT",
                base_port=5555,
                public=False,
            ),
        ),
        path=tmp_path / ".bonsai.toml",
    )
    target = WorktreeTarget(
        branch="MA-123-test",
        worktree=ManagedWorktree(path="ma-123-test", slug="ma-123-test", slot=2),
        worktree_path=worktree_path,
    )

    facts = build_worktree_facts(config, target, kind="managed")

    assert facts.summary.branch == "MA-123-test"
    assert facts.summary.relative_path == "ma-123-test"
    assert facts.summary.env_file_status == "missing"
    assert facts.summary.services[0].port == 3002
    assert facts.summary.services[0].url == "https://ma-123-test.authentic.localhost"
    assert facts.summary.services[1].port == 5557
    assert facts.generated_env == {
        "SLOT": "2",
        "PORT": "3002",
        "DB_PORT": "5557",
        "BONSAI_WORKSPACE_NAME": "authentic",
        "BONSAI_BRANCH": "MA-123-test",
        "BONSAI_SLUG": "ma-123-test",
        "BONSAI_SLOT": "2",
        "BONSAI_WORKTREE_PATH": str(worktree_path),
        "BONSAI_ROOT_PATH": str(tmp_path),
        "BONSAI_DEFAULT_BRANCH": "main",
        "BONSAI_PRIMARY_URL": "https://ma-123-test.authentic.localhost",
        "DATABASE_URL": "postgres://localhost:5557/ma-123-test",
    }
