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

    rendered = TOKEN_RE.sub(replace, template)
    unmatched_template_text = TOKEN_RE.sub("", template)
    if "${" in unmatched_template_text:
        raise ValueError(f"Malformed template placeholder in {template!r}")
    return rendered
