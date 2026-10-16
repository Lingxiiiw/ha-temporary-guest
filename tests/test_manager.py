"""Focused tests for password rotation and authorization behavior."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from homeassistant.auth.models import Group
from homeassistant.exceptions import ConfigEntryError, Unauthorized

from custom_components.temporary_guest.const import (
    CONF_AUTO_ROTATE,
    CONF_CONTROL_ENTITIES,
    CONF_DISPLAY_NAME,
    CONF_GUEST_DASHBOARD_PATH,
    CONF_LOCAL_ONLY,
    CONF_PASSWORD_LENGTH,
    CONF_READ_ENTITIES,
    CONF_ROTATION_TIME,
    CONF_USERNAME,
    RESTRICTED_GROUP_ID,
    RESTRICTED_GROUP_NAME,
)
from custom_components.temporary_guest.manager import (
    TemporaryGuestError,
    TemporaryGuestManager,
)
from custom_components.temporary_guest.permissions import build_entity_policy
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


class FakeAuthDisk:
    """Capture immediate auth-store persistence."""

    def __init__(self) -> None:
        self.saved = []

    async def async_save(self, value) -> None:
        """Record a persisted auth snapshot."""
        self.saved.append(deepcopy(value))


class FakeAuthStore:
    """Provide the pinned private auth-store surface used by the integration."""

    def __init__(self, group: Group) -> None:
        self._groups = {group.id: group}
        self._store = FakeAuthDisk()
        self.scheduled_saves = 0

    def _async_schedule_save(self) -> None:
        self.scheduled_saves += 1

    def _data_to_save(self):
        return {
            "groups": {
                group_id: deepcopy(group.policy)
                for group_id, group in self._groups.items()
            }
        }


def _build_manager():
    """Build a manager around small auth/config fakes."""
    token = object()
    credential = SimpleNamespace(
        auth_provider_type="homeassistant", data={"username": "mzuser"}
    )
    restricted_group = Group(
        name=RESTRICTED_GROUP_NAME,
        policy=build_entity_policy([], []),
        id=RESTRICTED_GROUP_ID,
        system_generated=False,
    )
    user = SimpleNamespace(
        id="mzuser-user-id",
        name="临时访客",
        is_owner=False,
        is_admin=False,
        system_generated=False,
        is_active=True,
        local_only=False,
        groups=[restricted_group],
        credentials=[credential],
        refresh_tokens={"session": token},
    )

    class FakeAuth:
        def __init__(self) -> None:
            self.removed_tokens = []
            self.updates = []
            self._store = FakeAuthStore(restricted_group)

        async def async_get_user(self, user_id: str):
            return user if user_id == user.id else None

        async def async_get_users(self):
            return [user]

        async def async_get_group(self, group_id: str):
            return self._store._groups.get(group_id)

        def async_remove_refresh_token(self, refresh_token) -> None:
            self.removed_tokens.append(refresh_token)
            for token_id, value in list(user.refresh_tokens.items()):
                if value is refresh_token:
                    user.refresh_tokens.pop(token_id)

        async def async_update_user(self, changed_user, **changes) -> None:
            self.updates.append((changed_user, changes))
            if "is_active" in changes:
                changed_user.is_active = changes["is_active"]
                if changes["is_active"] is False:
                    for refresh_token in list(changed_user.refresh_tokens.values()):
                        self.async_remove_refresh_token(refresh_token)
            if "group_ids" in changes:
                changed_user.groups = [
                    self._store._groups[group_id]
                    for group_id in changes["group_ids"]
                ]

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
            CONF_READ_ENTITIES: [],
            CONF_CONTROL_ENTITIES: [],
            CONF_GUEST_DASHBOARD_PATH: "/dashboard-mzuser",
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


@pytest.mark.asyncio
async def test_sync_permissions_replaces_the_all_entity_user_group() -> None:
    """Migration removes the normal all-entity group before restoring access."""
    manager, user, auth, _store, token = _build_manager()
    manager.entry.options[CONF_READ_ENTITIES] = ["sensor.guest_temperature"]
    manager.entry.options[CONF_CONTROL_ENTITIES] = ["light.guest_room"]
    user.groups = [
        Group(
            name="Users",
            policy={"entities": True},
            id="system-users",
            system_generated=True,
        )
    ]

    await manager.async_sync_permissions()

    assert user.is_active is True
    assert [group.id for group in user.groups] == [RESTRICTED_GROUP_ID]
    assert user.groups[0].policy == build_entity_policy(
        ["sensor.guest_temperature"], ["light.guest_room"]
    )
    assert auth.removed_tokens == [token]
    assert [changes for _changed_user, changes in auth.updates] == [
        {"is_active": False},
        {"group_ids": [RESTRICTED_GROUP_ID]},
        {"is_active": True},
    ]
    assert len(auth._store._store.saved) == 3


@pytest.mark.asyncio
async def test_permission_group_collision_disables_managed_user() -> None:
    """Another user in the dedicated group makes the managed account fail closed."""
    manager, user, auth, _store, _token = _build_manager()
    foreign_user = SimpleNamespace(
        id="foreign-user", groups=[auth._store._groups[RESTRICTED_GROUP_ID]]
    )

    async def async_get_users():
        return [user, foreign_user]

    auth.async_get_users = async_get_users

    with pytest.raises(ConfigEntryError, match="permission group is unsafe"):
        await manager.async_sync_permissions()

    assert user.is_active is False


def test_websocket_requires_owner() -> None:
    """A normal administrator is still rejected."""
    connection = SimpleNamespace(
        user=SimpleNamespace(is_owner=False), context=lambda _msg: None
    )
    with pytest.raises(Unauthorized):
        _require_owner(connection, {"id": 1})


def test_ui_policy_is_limited_to_the_managed_user() -> None:
    """Only the authenticated managed user receives a Dashboard path."""
    manager, user, _auth, _store, _token = _build_manager()

    assert manager.ui_policy_for_user(user.id) == {
        "managed": True,
        "dashboard_path": "/dashboard-mzuser",
    }
    assert manager.ui_policy_for_user("another-user") == {
        "managed": False,
        "dashboard_path": None,
    }
    assert manager.ui_policy_for_user(None) == {
        "managed": False,
        "dashboard_path": None,
    }
