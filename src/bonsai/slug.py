from __future__ import annotations

import re


def branch_slug(branch: str) -> str:
    slug = branch.lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")
