"""Tests for Dashboard path validation."""

import pytest

from custom_components.temporary_guest.dashboard import normalize_dashboard_path


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/dashboard-mzuser", "/dashboard-mzuser"),
        (" /lovelace/guest/ ", "/lovelace/guest"),
        ("/guest_2", "/guest_2"),
    ],
)
def test_normalize_dashboard_path(value: str, expected: str) -> None:
    """Safe local Dashboard paths are normalized deterministically."""
    assert normalize_dashboard_path(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "/",
        "dashboard-mzuser",
        "//dashboard-mzuser",
        "https://example.com/dashboard-mzuser",
        "/dashboard-mzuser?admin=true",
        "/dashboard-mzuser#secret",
        "/dashboard-mzuser//home",
        "/dashboard-mzuser/../lovelace",
        "/dashboard%2fmzuser",
        "/dashboard mzuser",
        None,
    ],
)
def test_reject_unsafe_dashboard_path(value: object) -> None:
    """Origins, ambiguous paths, and URL decorations are rejected."""
    with pytest.raises(ValueError):
        normalize_dashboard_path(value)
