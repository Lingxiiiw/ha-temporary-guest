"""Managed temporary Home Assistant user."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.auth.models import User
from homeassistant.auth.providers import homeassistant as auth_ha
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AUTO_ROTATE,
    CONF_CONTROL_ENTITIES,
    CONF_DISPLAY_NAME,
    CONF_LOCAL_ONLY,
    CONF_PASSWORD_LENGTH,
    CONF_READ_ENTITIES,
    CONF_ROTATION_TIME,
    CONF_USERNAME,
    DEFAULT_AUTO_ROTATE,
    DEFAULT_CONTROL_ENTITIES,
    DEFAULT_PASSWORD_LENGTH,
    DEFAULT_READ_ENTITIES,
    DEFAULT_ROTATION_TIME,
    RESTRICTED_GROUP_ID,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .password import generate_password
from .permissions import (
    async_ensure_restricted_group,
    async_persist_auth_store,
    build_entity_policy,
    permission_configuration_matches,
)

_LOGGER = logging.getLogger(__name__)


class TemporaryGuestError(HomeAssistantError):
    """Base error exposed to the WebSocket layer without sensitive details."""


class TemporaryGuestManager:
    """Create and manage one non-admin local Home Assistant user."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self._lock = asyncio.Lock()
        self._unsub_timer = None
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry.entry_id}",
            private=True,
            atomic_writes=True,
        )
        self._state: dict[str, Any] = {}

    @property
    def username(self) -> str:
        """Return the configured login name."""
        return str(self.entry.data[CONF_USERNAME])

    @property
    def display_name(self) -> str:
        """Return the configured display name."""
        return str(self.entry.data[CONF_DISPLAY_NAME])

    @property
    def auto_rotate(self) -> bool:
        """Return whether scheduled rotation is enabled."""
        return bool(self.entry.options.get(CONF_AUTO_ROTATE, DEFAULT_AUTO_ROTATE))

    @property
    def rotation_time(self) -> str:
        """Return the configured local rotation time."""
        return str(
            self.entry.options.get(CONF_ROTATION_TIME, DEFAULT_ROTATION_TIME)
        )

    @property
    def password_length(self) -> int:
        """Return the configured generated-password length."""
        return int(
            self.entry.options.get(CONF_PASSWORD_LENGTH, DEFAULT_PASSWORD_LENGTH)
        )

    @property
    def read_entities(self) -> list[str]:
        """Return the configured read-only entity ids."""
        return _entity_id_option(
            self.entry.options.get(CONF_READ_ENTITIES, DEFAULT_READ_ENTITIES)
        )

    @property
    def control_entities(self) -> list[str]:
        """Return the configured readable and controllable entity ids."""
        return _entity_id_option(
            self.entry.options.get(CONF_CONTROL_ENTITIES, DEFAULT_CONTROL_ENTITIES)
        )

    async def async_initialize(self) -> None:
        """Load state, create the managed user on first run, and start timer."""
        loaded = await self._store.async_load()
        self._state = dict(loaded or {})

        if self._state.get("user_id"):
            user = await self._async_require_user()
            self._validate_managed_user(user)
            await self.async_sync_permissions()
            if not self._state.get("password"):
                await self.async_rotate()
            elif not await self._async_stored_password_matches(user):
                # Recover from an interrupted cross-store write or an external
                # password change. The newly generated value becomes authoritative.
                await self.async_rotate()
        else:
            await self._async_create_managed_user()

        self.async_reschedule()

    async def async_unload(self) -> None:
        """Cancel runtime listeners."""
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    def async_reschedule(self) -> None:
        """Replace the daily timer with the current option values."""
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

        if not self.auto_rotate:
            return

        hour, minute, second = _parse_time(self.rotation_time)
        self._unsub_timer = async_track_time_change(
            self.hass,
            self._async_scheduled_rotation,
            hour=hour,
            minute=minute,
            second=second,
        )

    async def _async_scheduled_rotation(self, now: datetime) -> None:
        """Rotate once for the scheduled local calendar date."""
        if not self.auto_rotate:
            return

        local_date = now.date().isoformat()
        if self._state.get("last_auto_rotation_date") == local_date:
            return

        try:
            await self.async_rotate(automatic_date=local_date)
        except Exception:
            # Deliberately omit exception details: provider errors can contain input.
            _LOGGER.exception("Scheduled temporary guest password rotation failed")

    async def async_status(self) -> dict[str, Any]:
        """Return non-sensitive status for the owner-only card."""
        async with self._lock:
            user = await self._async_require_user()
            self._validate_managed_user(user)
            await self._async_apply_permissions(user)
            control_entities = set(self.control_entities)
            read_only_entities = set(self.read_entities) - control_entities
            return {
                "username": self.username,
                "display_name": user.name,
                "active": user.is_active,
                "local_only": user.local_only,
                "auto_rotate": self.auto_rotate,
                "rotation_time": self.rotation_time,
                "last_rotated": self._state.get("last_rotated"),
                "next_rotation": self._next_rotation_iso(),
                "password_available": bool(self._state.get("password")),
                "read_only_entity_count": len(read_only_entities),
                "control_entity_count": len(control_entities),
            }

    async def async_reveal_password(self) -> str:
        """Return the current password to an already-authorized Owner caller."""
        async with self._lock:
            user = await self._async_require_user()
            self._validate_managed_user(user)
            await self._async_apply_permissions(user)
            password = self._state.get("password")
            if not isinstance(password, str) or not password:
                raise TemporaryGuestError("Current password is unavailable")
            if not await self._async_stored_password_matches(user):
                raise TemporaryGuestError(
                    "Current password was changed outside Temporary Guest; rotate it now"
                )
            return password

    async def async_rotate(self, automatic_date: str | None = None) -> str:
        """Change the password, revoke all sessions, then persist the new value."""
        async with self._lock:
            user = await self._async_require_user()
            self._validate_managed_user(user)
            await self._async_apply_permissions(user)
            credential = _homeassistant_credential(user)
            if credential is None:
                raise TemporaryGuestError("Managed user credentials are missing")

            username = str(credential.data["username"])
            new_password = generate_password(self.password_length)
            old_state = dict(self._state)
            old_password = old_state.get("password")
            provider = auth_ha.async_get_provider(self.hass)

            await provider.async_change_password(username, new_password)
            self._revoke_all_sessions(user)

            self._state["password"] = new_password
            self._state["last_rotated"] = dt_util.now().isoformat()
            if automatic_date is not None:
                self._state["last_auto_rotation_date"] = automatic_date

            try:
                await self._store.async_save(self._state)
            except Exception:
                self._state = old_state
                if isinstance(old_password, str) and old_password:
                    await provider.async_change_password(username, old_password)
                raise

            return new_password

    async def async_set_active(self, active: bool) -> None:
        """Enable or disable the managed user."""
        async with self._lock:
            user = await self._async_require_user()
            self._validate_managed_user(user)
            if active:
                await self._async_apply_permissions(user)
            await self.hass.auth.async_update_user(user, is_active=active)
            await async_persist_auth_store(self.hass.auth)

    async def async_sync_permissions(self) -> None:
        """Apply the current entity whitelist and revoke sessions if it changed."""
        async with self._lock:
            user = await self._async_require_user()
            self._validate_managed_user(user)
            await self._async_apply_permissions(user)

    async def async_set_auto_rotate(self, enabled: bool) -> None:
        """Update the persisted config-entry option."""
        options = dict(self.entry.options)
        options[CONF_AUTO_ROTATE] = enabled
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def async_disable_for_removal(self) -> None:
        """Disable the managed account before the integration is removed."""
        if not self._state:
            self._state = dict(await self._store.async_load() or {})
        user_id = self._state.get("user_id")
        if not isinstance(user_id, str):
            await self._store.async_remove()
            return

        user = await self.hass.auth.async_get_user(user_id)
        if user is not None:
            self._validate_managed_user(user)
            await self.hass.auth.async_update_user(user, is_active=False)
            await async_persist_auth_store(self.hass.auth)
        await self._store.async_remove()

    async def _async_create_managed_user(self) -> None:
        """Create a normal user, local credentials, and the initial password."""
        if await self.hass.auth.async_get_owner() is None:
            raise ConfigEntryError(
                "Home Assistant onboarding must create an Owner before Temporary Guest"
            )

        provider = auth_ha.async_get_provider(self.hass)
        password = generate_password(self.password_length)
        policy = build_entity_policy(self.read_entities, self.control_entities)
        await async_ensure_restricted_group(
            self.hass.auth, policy, managed_user_id=None
        )
        await async_persist_auth_store(self.hass.auth)
        user = await self.hass.auth.async_create_user(
            self.display_name,
            group_ids=[RESTRICTED_GROUP_ID],
            local_only=bool(self.entry.data[CONF_LOCAL_ONLY]),
        )
        auth_added = False
        try:
            await provider.async_add_auth(self.username, password)
            auth_added = True
            credentials = await provider.async_get_or_create_credentials(
                {"username": self.username}
            )
            await self.hass.auth.async_link_user(user, credentials)
            await async_persist_auth_store(self.hass.auth)
            self._state = {
                "user_id": user.id,
                "password": password,
                "last_rotated": dt_util.now().isoformat(),
                "last_auto_rotation_date": None,
            }
            await self._store.async_save(self._state)
        except Exception:
            # A partially created account must never be left active.
            try:
                await self.hass.auth.async_remove_user(user)
            finally:
                if auth_added and not user.credentials:
                    try:
                        await provider.async_remove_auth(self.username)
                    except Exception:  # noqa: BLE001 - best-effort rollback
                        pass
                try:
                    await async_persist_auth_store(self.hass.auth)
                except Exception:  # noqa: BLE001 - preserve the creation failure
                    pass
            raise

    async def _async_apply_permissions(self, user: User) -> None:
        """Ensure an exclusive deny-by-default group, failing closed on errors."""
        policy = build_entity_policy(self.read_entities, self.control_entities)
        group = await self.hass.auth.async_get_group(RESTRICTED_GROUP_ID)

        if permission_configuration_matches(user, group, policy):
            try:
                await async_ensure_restricted_group(
                    self.hass.auth, policy, managed_user_id=user.id
                )
            except Exception as err:
                await self._async_fail_closed(user)
                raise ConfigEntryError(
                    "Temporary Guest entity permission group is unsafe"
                ) from err
            return

        was_active = user.is_active
        try:
            await self.hass.auth.async_update_user(user, is_active=False)
            await async_persist_auth_store(self.hass.auth)
            await async_ensure_restricted_group(
                self.hass.auth, policy, managed_user_id=user.id
            )
            await self.hass.auth.async_update_user(
                user, group_ids=[RESTRICTED_GROUP_ID]
            )
            await async_persist_auth_store(self.hass.auth)
            if was_active:
                await self.hass.auth.async_update_user(user, is_active=True)
                await async_persist_auth_store(self.hass.auth)
        except Exception as err:
            await self._async_fail_closed(user)
            raise ConfigEntryError(
                "Unable to apply the Temporary Guest entity whitelist; account disabled"
            ) from err

    async def _async_fail_closed(self, user: User) -> None:
        """Best-effort persistent deactivation after a permission failure."""
        try:
            await self.hass.auth.async_update_user(user, is_active=False)
            await async_persist_auth_store(self.hass.auth)
        except Exception:
            _LOGGER.exception("Failed to persistently disable temporary guest")

    async def _async_require_user(self) -> User:
        """Return the managed user or fail closed."""
        user_id = self._state.get("user_id")
        if not isinstance(user_id, str):
            raise TemporaryGuestError("Managed user has not been created")
        user = await self.hass.auth.async_get_user(user_id)
        if user is None:
            raise TemporaryGuestError("Managed user no longer exists")
        return user

    def _validate_managed_user(self, user: User) -> None:
        """Refuse to touch an owner/admin/system account or changed credentials."""
        if user.is_owner or user.is_admin or user.system_generated:
            raise ConfigEntryError("Refusing to manage a privileged Home Assistant user")
        credential = _homeassistant_credential(user)
        if credential is None or credential.data.get("username") != self.username:
            raise ConfigEntryError("Managed user credentials no longer match the config")

    def _revoke_all_sessions(self, user: User) -> None:
        """Invalidate every browser/app session belonging to the guest."""
        for refresh_token in list(user.refresh_tokens.values()):
            self.hass.auth.async_remove_refresh_token(refresh_token)

    async def _async_stored_password_matches(self, user: User) -> bool:
        """Check that the retrievable password still matches HA's password hash."""
        password = self._state.get("password")
        credential = _homeassistant_credential(user)
        if not isinstance(password, str) or credential is None:
            return False
        provider = auth_ha.async_get_provider(self.hass)
        try:
            await provider.async_validate_login(
                str(credential.data["username"]), password
            )
        except auth_ha.InvalidAuth:
            return False
        return True

    def _next_rotation_iso(self) -> str | None:
        """Return the next scheduled local wall-clock time."""
        if not self.auto_rotate:
            return None
        hour, minute, second = _parse_time(self.rotation_time)
        now = dt_util.now()
        next_rotation = now.replace(
            hour=hour, minute=minute, second=second, microsecond=0
        )
        if next_rotation <= now:
            next_rotation += timedelta(days=1)
        return next_rotation.isoformat()


def _homeassistant_credential(user: User):
    """Return the built-in Home Assistant credential for a user."""
    return next(
        (
            credential
            for credential in user.credentials
            if credential.auth_provider_type == "homeassistant"
        ),
        None,
    )


def _parse_time(value: str) -> tuple[int, int, int]:
    """Parse a Home Assistant time selector value."""
    try:
        parts = [int(part) for part in value.split(":")]
        if len(parts) == 2:
            parts.append(0)
        hour, minute, second = parts
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            raise ValueError
    except (TypeError, ValueError) as err:
        raise ConfigEntryError(f"Invalid rotation time: {value}") from err
    return hour, minute, second


def _entity_id_option(value: Any) -> list[str]:
    """Return a safe, deduplicated entity-id list from config-entry options."""
    if not isinstance(value, (list, tuple)):
        return []
    return sorted(
        {
            entity_id
            for entity_id in value
            if isinstance(entity_id, str) and entity_id and "." in entity_id
        }
    )
