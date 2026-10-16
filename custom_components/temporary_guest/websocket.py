"""WebSocket API for Temporary Guest management and UI policy."""

from __future__ import annotations

import logging
from typing import Any

import probatio
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, Unauthorized

from .const import (
    DATA_MANAGER,
    DOMAIN,
    WS_REVEAL,
    WS_ROTATE,
    WS_SET_ACTIVE,
    WS_SET_AUTO_ROTATE,
    WS_STATUS,
    WS_UI_POLICY,
)
from .manager import TemporaryGuestError, TemporaryGuestManager

_LOGGER = logging.getLogger(__name__)


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register all card commands."""
    websocket_api.async_register_command(hass, websocket_status)
    websocket_api.async_register_command(hass, websocket_reveal)
    websocket_api.async_register_command(hass, websocket_rotate)
    websocket_api.async_register_command(hass, websocket_set_active)
    websocket_api.async_register_command(hass, websocket_set_auto_rotate)
    websocket_api.async_register_command(hass, websocket_ui_policy)


def _require_owner(
    connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Require the real Home Assistant Owner, not merely an administrator."""
    if connection.user is None or not connection.user.is_owner:
        raise Unauthorized(context=connection.context(msg))


def _manager(hass: HomeAssistant) -> TemporaryGuestManager | None:
    """Return the single configured manager."""
    return hass.data.get(DOMAIN, {}).get(DATA_MANAGER)


def _ui_policy_for_connection(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection
) -> dict[str, Any]:
    """Build a policy without trusting any caller-supplied identity."""
    manager = _manager(hass)
    user_id = connection.user.id if connection.user is not None else None
    if manager is None:
        return {"managed": False, "dashboard_path": None}
    return manager.ui_policy_for_user(user_id)


async def _async_result(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    operation,
) -> None:
    """Run an owner-only operation and send a sanitized result."""
    _require_owner(connection, msg)
    manager = _manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_configured", "Integration is not configured")
        return
    try:
        result = await operation(manager)
    except (TemporaryGuestError, ConfigEntryError) as err:
        connection.send_error(msg["id"], "operation_failed", str(err))
        return
    except Exception:
        _LOGGER.exception("Temporary guest operation failed")
        connection.send_error(
            msg["id"], "operation_failed", "Operation failed; check Home Assistant logs"
        )
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({probatio.Required("type"): WS_STATUS})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return non-sensitive managed-user status."""

    async def operation(manager: TemporaryGuestManager) -> dict[str, Any]:
        return await manager.async_status()

    await _async_result(hass, connection, msg, operation)


@websocket_api.websocket_command({probatio.Required("type"): WS_REVEAL})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_reveal(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Reveal the current password to the Owner only."""

    async def operation(manager: TemporaryGuestManager) -> dict[str, Any]:
        return {"password": await manager.async_reveal_password()}

    await _async_result(hass, connection, msg, operation)


@websocket_api.websocket_command({probatio.Required("type"): WS_ROTATE})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_rotate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Rotate the password and return it once to the Owner."""

    async def operation(manager: TemporaryGuestManager) -> dict[str, Any]:
        password = await manager.async_rotate()
        return {**await manager.async_status(), "password": password}

    await _async_result(hass, connection, msg, operation)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): WS_SET_ACTIVE,
        probatio.Required("active"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_set_active(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Enable or disable the managed user."""

    async def operation(manager: TemporaryGuestManager) -> dict[str, Any]:
        await manager.async_set_active(msg["active"])
        return await manager.async_status()

    await _async_result(hass, connection, msg, operation)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): WS_SET_AUTO_ROTATE,
        probatio.Required("enabled"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_set_auto_rotate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Enable or disable scheduled rotation."""

    async def operation(manager: TemporaryGuestManager) -> dict[str, Any]:
        await manager.async_set_auto_rotate(msg["enabled"])
        return await manager.async_status()

    await _async_result(hass, connection, msg, operation)


@websocket_api.websocket_command({probatio.Required("type"): WS_UI_POLICY})
@websocket_api.async_response
async def websocket_ui_policy(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the current connection's non-sensitive Dashboard policy."""
    if _manager(hass) is None:
        connection.send_error(
            msg["id"], "not_configured", "Integration is not configured"
        )
        return
    connection.send_result(msg["id"], _ui_policy_for_connection(hass, connection))
