"""Exercise the real HA logbook handlers and their shared permission boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from homeassistant.auth import AuthManager
from homeassistant.auth.models import Group, User
from homeassistant.components.http import KEY_HASS
from homeassistant.components.http.request_context import setup_request_context
from homeassistant.components.logbook import rest_api
from homeassistant.components.logbook import websocket_api as logbook_ws
from homeassistant.components.logbook.processor import EventProcessor
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.components.websocket_api import auth as websocket_auth
from homeassistant.components.websocket_api.connection import current_connection
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers.http import KEY_AUTHENTICATED, current_request

from custom_components.temporary_guest.const import DOMAIN, RESTRICTED_GROUP_ID
from custom_components.temporary_guest.logbook_guard import (
    LogbookGuardError,
    async_install_logbook_guard,
)


def _user(*, restricted=True, user_id="guest", owner=False):
    return User(
        perm_lookup=Mock(),
        name="test",
        id=user_id,
        is_owner=owner,
        groups=[Group(name="guest", id=RESTRICTED_GROUP_ID, policy={})]
        if restricted
        else [],
    )


@pytest.fixture
def guard_environment(monkeypatch):
    """Restore process-wide hooks and task contexts after each case."""
    monkeypatch.setattr(EventProcessor, "__init__", EventProcessor.__init__)
    ws_token = current_connection.set(None)
    http_token = current_request.set(None)
    hass = SimpleNamespace(data={"websocket_api": {}}, is_stopping=False)
    try:
        yield hass
    finally:
        current_connection.reset(ws_token)
        current_request.reset(http_token)


def _connection(hass, user):
    messages = []
    connection = ActiveConnection(Mock(), hass, messages.append, user, None, None)
    return connection, messages


def _no_backend_access(*_args, **_kwargs):
    pytest.fail("A rejected guest request reached the logbook backend")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["ws_get_events", "ws_event_stream"])
@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"entity_ids": []},
        {"entity_ids": ["light.guest_room"]},
        {"entity_ids": ["light.private_room"]},
        {"device_ids": ["private-device"]},
    ],
)
async def test_real_websocket_handlers_deny_guest(
    guard_environment, monkeypatch, command, filters
):
    """Unfiltered, explicit and empty filters cannot authorize any guest logs."""
    hass = guard_environment
    tasks = []

    def schedule(coro, *_args, **_kwargs):
        tasks.append(asyncio.create_task(coro))
        return tasks[-1]

    hass.async_create_background_task = schedule
    # Register the actual schema/handler before installing protection.
    logbook_ws.async_setup(hass)
    async_install_logbook_guard(hass, "guest")
    connection, messages = _connection(hass, _user())
    monkeypatch.setattr(logbook_ws, "async_filter_entities", lambda _hass, ids: ids)
    monkeypatch.setattr(logbook_ws, "async_determine_event_types", lambda *_args: [])
    monkeypatch.setattr(logbook_ws, "get_instance", _no_backend_access)
    monkeypatch.setattr(logbook_ws, "async_subscribe_events", _no_backend_access)
    msg = {
        "id": 1,
        "type": "logbook/" + command.removeprefix("ws_"),
        "start_time": "2026-01-01T00:00:00+00:00",
        **filters,
    }
    # Exercise HA dispatch, schema, child-task context inheritance and errors.
    connection.async_handle(msg)
    await asyncio.gather(*tasks)
    assert len(messages) == 1
    response = messages[0]
    assert response["success"] is False
    assert response["error"]["code"] == "unauthorized"
    assert "result" not in response
    assert connection.subscriptions == {}


@pytest.mark.asyncio
async def test_websocket_context_filter_does_not_bypass_guard(guard_environment, monkeypatch):
    hass = guard_environment
    async_install_logbook_guard(hass, "guest")
    connection, _messages = _connection(hass, _user())
    monkeypatch.setattr(logbook_ws, "async_determine_event_types", lambda *_args: [])
    monkeypatch.setattr(logbook_ws, "get_instance", _no_backend_access)
    with pytest.raises(Unauthorized):
        await logbook_ws.ws_get_events.__wrapped__(
            hass,
            connection,
            {
                "id": 1,
                "type": "logbook/get_events",
                "start_time": "2026-01-01T00:00:00+00:00",
                "context_id": "private-context",
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/logbook",
        "/api/logbook/2026-01-01T00:00:00+00:00",
        "/api/logbook?entity=light.guest_room",
        "/api/logbook?entity=light.private_room",
        "/api/logbook?context_id=private-context",
    ],
)
async def test_registered_http_routes_deny_guest(guard_environment, monkeypatch, path):
    """Existing aiohttp routes remain protected when the guard loads later."""
    hass = guard_environment
    app = web.Application()
    app[KEY_HASS] = hass
    setup_request_context(app, current_request)

    @web.middleware
    async def authenticated_guest(request, handler):
        request[KEY_AUTHENTICATED] = True
        request["hass_user"] = _user()
        return await handler(request)

    app.middlewares.append(authenticated_guest)
    rest_api.LogbookView({}, None, None).register(hass, app, app.router)
    monkeypatch.setattr(rest_api, "async_determine_event_types", lambda *_args: [])
    monkeypatch.setattr(rest_api, "get_instance", _no_backend_access)
    async with TestClient(TestServer(app)) as client:
        # The router/middleware stack is already frozen, as with a late install.
        async_install_logbook_guard(hass, "guest")
        response = await client.get(path)
        assert response.status == 401
        assert "private-context" not in await response.text()


def test_guard_survives_manager_unload_and_group_migration(guard_environment):
    hass = guard_environment
    async_install_logbook_guard(hass, "guest")
    # No manager remains; a pre-migration user's normal group must not bypass it.
    assert "manager" not in hass.data[DOMAIN]
    _connection(hass, _user(restricted=False))
    with pytest.raises(Unauthorized):
        EventProcessor(hass, [])


def test_restricted_group_is_protected_without_saved_user_id(guard_environment):
    hass = guard_environment
    async_install_logbook_guard(hass)
    _connection(hass, _user(user_id="new-or-residual-guest"))
    with pytest.raises(Unauthorized):
        EventProcessor(hass, [])


@pytest.mark.parametrize("actor", ["owner", "admin", "ordinary", "internal"])
def test_other_users_and_internal_calls_are_unchanged(guard_environment, monkeypatch, actor):
    hass = guard_environment
    calls = []

    def original_init(self, hass, *args, **kwargs):
        calls.append((hass, args, kwargs))

    monkeypatch.setattr(EventProcessor, "__init__", original_init)
    async_install_logbook_guard(hass, "guest")
    guarded = EventProcessor.__init__
    async_install_logbook_guard(hass, "guest")
    assert EventProcessor.__init__ is guarded
    if actor != "internal":
        user = _user(restricted=False, user_id=actor, owner=actor == "owner")
        if actor == "admin":
            user.groups = [Group(name="admin", id="system-admin", policy={})]
        _connection(hass, user)
    EventProcessor(hass, ["state_changed"], entity_ids=["light.private_room"])
    assert calls == [(hass, (["state_changed"],), {"entity_ids": ["light.private_room"]})]


def test_incompatible_logbook_backend_is_rejected(guard_environment, monkeypatch):
    monkeypatch.setattr(rest_api, "EventProcessor", object)
    with pytest.raises(LogbookGuardError, match="incompatible"):
        async_install_logbook_guard(guard_environment, "guest")


@pytest.mark.asyncio
async def test_ha_token_revocation_cancels_authenticated_websocket(guard_environment, monkeypatch):
    """Pin the real HA callback path used to terminate pre-upgrade streams."""
    hass = guard_environment
    auth = object.__new__(AuthManager)
    auth._store = Mock()
    auth._revoke_callbacks = {}
    token = SimpleNamespace(id="token", user=_user())
    monkeypatch.setattr(auth, "async_validate_access_token", Mock(return_value=token))
    hass.auth = auth
    monkeypatch.setattr(websocket_auth, "async_user_not_allowed_do_auth", lambda *_args: None)
    monkeypatch.setattr(websocket_auth, "process_success_login", lambda *_args: None)
    cancel_ws = Mock()
    phase = websocket_auth.AuthPhase(
        Mock(), hass, Mock(), cancel_ws, SimpleNamespace(remote=None), AsyncMock()
    )
    connection = await phase.async_handle({"type": "auth", "access_token": "test-token"})
    assert "auth" in connection.subscriptions
    auth.async_remove_refresh_token(token)
    cancel_ws.assert_called_once_with()
