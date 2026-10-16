"""Tests for the connection-bound UI policy WebSocket command."""

from types import SimpleNamespace

from custom_components.temporary_guest.const import DATA_MANAGER, DOMAIN
from custom_components.temporary_guest.websocket import _ui_policy_for_connection


def test_ui_policy_uses_only_the_authenticated_connection_user() -> None:
    """The command has no caller-controlled user id to spoof."""
    seen_user_ids = []

    class Manager:
        def ui_policy_for_user(self, user_id):
            seen_user_ids.append(user_id)
            return {"managed": user_id == "guest-id", "dashboard_path": "/guest"}

    hass = SimpleNamespace(data={DOMAIN: {DATA_MANAGER: Manager()}})
    connection = SimpleNamespace(user=SimpleNamespace(id="guest-id"))

    assert _ui_policy_for_connection(hass, connection) == {
        "managed": True,
        "dashboard_path": "/guest",
    }
    assert seen_user_ids == ["guest-id"]


def test_ui_policy_is_unmanaged_when_integration_is_not_loaded() -> None:
    """An unavailable manager does not expose stale policy data."""
    hass = SimpleNamespace(data={})
    connection = SimpleNamespace(user=SimpleNamespace(id="guest-id"))

    assert _ui_policy_for_connection(hass, connection) == {
        "managed": False,
        "dashboard_path": None,
    }
