from __future__ import annotations

import re
from collections.abc import Mapping

TOKEN_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_template(template: str, values: Mapping[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(key)
        return str(values[key])

    return TOKEN_RE.sub(replace, template)
