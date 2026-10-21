"""Branch names from issues."""
from __future__ import annotations

import re
import unicodedata

from .errors import ValidationError

BRANCH_TYPES = ("feature", "bugfix", "hotfix", "chore")


def slugify(text: str, limit: int = 40) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if len(slug) > limit:
        slug = slug[:limit].rsplit("-", 1)[0] if "-" in slug[:limit] else slug[:limit]
    return slug


def branch_name(key: str, summary: str, issue_type: str, override: str = "") -> str:
    if override and override not in BRANCH_TYPES:
        raise ValidationError(f"Invalid branch type: {override}", hint="Use one of: " + ", ".join(BRANCH_TYPES) + ".")
    kind = override or ("bugfix" if issue_type.casefold() == "bug" else "feature")
    slug = slugify(summary)
    return f"{kind}/{key}" + (f"-{slug}" if slug else "")
