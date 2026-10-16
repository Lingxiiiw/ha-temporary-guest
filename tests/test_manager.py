"""Focused tests for password rotation and authorization behavior."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import Unauthorized

from custom_components.temporary_guest.const import (
    CONF_AUTO_ROTATE,
    CONF_DISPLAY_NAME,
    CONF_LOCAL_ONLY,
    CONF_PASSWORD_LENGTH,
    CONF_ROTATION_TIME,
    CONF_USERNAME,
)
from custom_components.temporary_guest.manager import (
    TemporaryGuestError,
    TemporaryGuestManager,
)
from custom_components.temporary_guest.websocket import _require_owner


class FakeStore:
    """Capture writes without touching Home Assistant storage."""

    def __init__(self) -> None:
        self.saved = None
        self.removed = False

    async def async_save(self, value) -> None:
        """Capture a deep copy like a JSON store would."""
        self.saved = deepcopy(value)

    async def async_load(self):
        """Return the last saved value."""
        return deepcopy(self.saved)

    async def async_remove(self) -> None:
        """Record removal."""
        self.removed = True


class FakeProvider:
    """Track local-provider password operations."""

    def __init__(self, current_password: str) -> None:
        self.current_password = current_password
        self.changes: list[tuple[str, str]] = []

    async def async_change_password(self, username: str, password: str) -> None:
        """Capture and apply the changed password."""
        self.changes.append((username, password))
        self.current_password = password

    async def async_validate_login(self, username: str, password: str) -> None:
        """Use the real provider exception type for mismatches."""
        from homeassistant.auth.providers.homeassistant import InvalidAuth

        if username != "mzuser" or password != self.current_password:
            raise InvalidAuth


def _build_manager():
    """Build a manager around small auth/config fakes."""
    token = object()
    credential = SimpleNamespace(
        auth_provider_type="homeassistant", data={"username": "mzuser"}
    )
    user = SimpleNamespace(
        id="mzuser-user-id",
        name="临时访客",
        is_owner=False,
        is_admin=False,
        system_generated=False,
        is_active=True,
        local_only=False,
        credentials=[credential],
        refresh_tokens={"session": token},
    )

    class FakeAuth:
        def __init__(self) -> None:
            self.removed_tokens = []
            self.updates = []

        async def async_get_user(self, user_id: str):
            return user if user_id == user.id else None

        def async_remove_refresh_token(self, refresh_token) -> None:
            self.removed_tokens.append(refresh_token)

        async def async_update_user(self, changed_user, **changes) -> None:
            self.updates.append((changed_user, changes))
            if "is_active" in changes:
                changed_user.is_active = changes["is_active"]

    entry = SimpleNamespace(
        data={
            CONF_USERNAME: "mzuser",
            CONF_DISPLAY_NAME: "临时访客",
            CONF_LOCAL_ONLY: False,
        },
        options={
            CONF_AUTO_ROTATE: True,
            CONF_ROTATION_TIME: "08:00:00",
            CONF_PASSWORD_LENGTH: 24,
        },
        entry_id="test-entry",
    )
    auth = FakeAuth()
    config_entries = SimpleNamespace(async_update_entry=lambda *_args, **_kwargs: None)
    hass = SimpleNamespace(auth=auth, config_entries=config_entries)
    store = FakeStore()
    manager = object.__new__(TemporaryGuestManager)
    manager.hass = hass
    manager.entry = entry
    manager._lock = asyncio.Lock()
    manager._unsub_timer = None
    manager._store = store
    manager._state = {
        "user_id": user.id,
        "password": "OldPassword1!OldPassword",
        "last_rotated": None,
        "last_auto_rotation_date": None,
    }
    return manager, user, auth, store, token


@pytest.mark.asyncio
async def test_rotate_changes_password_revokes_sessions_and_saves(monkeypatch) -> None:
    """A successful rotation performs all three security-critical effects."""
    manager, _user, auth, store, token = _build_manager()
    provider = FakeProvider("OldPassword1!OldPassword")
    monkeypatch.setattr(
        "custom_components.temporary_guest.manager.auth_ha.async_get_provider",
        lambda _hass: provider,
    )

    password = await manager.async_rotate()

    assert len(password) == 24
    assert password != "OldPassword1!OldPassword"
    assert provider.changes == [("mzuser", password)]
    assert auth.removed_tokens == [token]
    assert store.saved["password"] == password
    assert store.saved["last_rotated"]


@pytest.mark.asyncio
async def test_reveal_rejects_a_password_changed_outside_integration(monkeypatch) -> None:
    """The card must never return a stale stored password."""
    manager, _user, _auth, _store, _token = _build_manager()
    provider = FakeProvider("DifferentPassword1!")
    monkeypatch.setattr(
        "custom_components.temporary_guest.manager.auth_ha.async_get_provider",
        lambda _hass: provider,
    )

    with pytest.raises(TemporaryGuestError, match="changed outside Temporary Guest"):
        await manager.async_reveal_password()


@pytest.mark.asyncio
async def test_disable_uses_auth_manager() -> None:
    """Disabling is a real account update, not a UI-only flag."""
    manager, user, auth, _store, _token = _build_manager()
    await manager.async_set_active(False)
    assert user.is_active is False
    assert auth.updates == [(user, {"is_active": False})]


def test_websocket_requires_owner() -> None:
    """A normal administrator is still rejected."""
    connection = SimpleNamespace(
        user=SimpleNamespace(is_owner=False), context=lambda _msg: None
    )
    with pytest.raises(Unauthorized):
        _require_owner(connection, {"id": 1})
