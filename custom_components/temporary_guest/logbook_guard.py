"""Close HA 2026.10's logbook permission gap for the managed guest.

Entity filters are query options, not authorization: even a permitted entity's
logbook entry can contain another entity, user or automation in its context.
Until HA offers a permission-aware logbook API, deny guest logbook reads.

REST, get_events and event_stream all construct EventProcessor on the request
task before querying Recorder or subscribing to events. Guard that shared
boundary, including handlers already registered with aiohttp/WebSocket. Keep
the guard installed for the process lifetime so unloading an entry cannot
reopen access for a still-existing restricted account.
"""

from __future__ import annotations

from functools import wraps
from inspect import signature
from typing import Any

from homeassistant.auth.models import User
from homeassistant.components.http import KEY_HASS
from homeassistant.components.websocket_api.connection import current_connection
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers.http import current_request

from .const import DOMAIN, RESTRICTED_GROUP_ID

_PROTECTED_USER_IDS = "logbook_protected_user_ids"
_GUARD_MARKER = "_temporary_guest_logbook_guard"


class LogbookGuardError(HomeAssistantError):
    """The pinned logbook interception contract is unavailable."""


def _is_protected_user(hass: HomeAssistant, user: User | None) -> bool:
    """Use server-side identity, including before permission-group migration."""
    if user is None:
        return False
    protected_ids = hass.data.get(DOMAIN, {}).get(_PROTECTED_USER_IDS, ())
    return user.id in protected_ids or any(
        group.id == RESTRICTED_GROUP_ID for group in user.groups
    )


def _check_logbook_access(hass: HomeAssistant) -> None:
    """Check the authenticated task context before any log data is processed."""
    connection = current_connection.get()
    if (
        connection is not None
        and connection.hass is hass
        and _is_protected_user(hass, connection.user)
    ):
        raise Unauthorized

    request = current_request.get()
    if (
        request is not None
        and request.app.get(KEY_HASS) is hass
        and _is_protected_user(hass, request.get("hass_user"))
    ):
        raise Unauthorized


@callback
def async_install_logbook_guard(
    hass: HomeAssistant, user_id: str | None = None
) -> None:
    """Install once and retain protected IDs across config-entry unload/reload.

Imports are deferred until entry setup, after manifest dependencies are ready.
The HA context variables are set by its authenticated HTTP/WebSocket request
tasks and inherited by their child tasks; no caller-supplied identity is used.
"""
    if user_id is not None:
        hass.data.setdefault(DOMAIN, {}).setdefault(_PROTECTED_USER_IDS, set()).add(
            user_id
        )

    try:
        from homeassistant.components.logbook import rest_api
        from homeassistant.components.logbook import websocket_api as logbook_ws
        from homeassistant.components.logbook.processor import EventProcessor
    except ImportError as err:
        raise LogbookGuardError("Home Assistant logbook backend is unavailable") from err

    # These module bindings are looked up at request time, even by a handler
    # registered before this integration was loaded. Refuse unexpected backends.
    if (
        rest_api.EventProcessor is not EventProcessor
        or logbook_ws.EventProcessor is not EventProcessor
        or list(signature(EventProcessor.__init__).parameters)[:2] != ["self", "hass"]
    ):
        raise LogbookGuardError("Home Assistant logbook backend is incompatible")

    original_init = EventProcessor.__init__
    if getattr(original_init, _GUARD_MARKER, False):
        return

    @wraps(original_init)
    def guarded_init(self: Any, hass: HomeAssistant, *args: Any, **kwargs: Any) -> None:
        _check_logbook_access(hass)
        original_init(self, hass, *args, **kwargs)

    setattr(guarded_init, _GUARD_MARKER, True)
    EventProcessor.__init__ = guarded_init
