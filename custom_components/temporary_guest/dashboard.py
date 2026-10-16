"""Validation helpers for the guest Dashboard path."""

from __future__ import annotations

import re
from typing import Any

_DASHBOARD_PATH = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9_-]*(?:/[A-Za-z0-9][A-Za-z0-9_-]*)*$")


def normalize_dashboard_path(value: Any) -> str:
    """Return a safe same-origin Dashboard base path.

    Dashboard paths are intentionally limited to ordinary Home Assistant URL
    slugs. This excludes origins, query strings, fragments, dot segments,
    encoded separators, whitespace, and protocol-relative URLs.
    """
    if not isinstance(value, str):
        raise ValueError("Dashboard path must be text")

    path = value.strip()
    if path != "/":
        path = path.rstrip("/")
    if path == "/" or not _DASHBOARD_PATH.fullmatch(path):
        raise ValueError("Dashboard path must be an absolute local path such as /dashboard-mzuser")
    return path
