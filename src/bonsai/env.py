from __future__ import annotations

from bonsai.errors import BonsaiWorkspaceError


def parse_env_content(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise BonsaiWorkspaceError(f"Invalid environment line: {line}")
        name, value = stripped.split("=", 1)
        values[name] = value
    return values
