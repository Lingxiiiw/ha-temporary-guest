"""Regression tests using HA dispatch, registry serializers and HTTP routing."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import probatio
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from homeassistant.auth.models import Group, User
from homeassistant.auth.permissions.models import PermissionLookup
from homeassistant.components import websocket_api
from homeassistant.components.api import APIConfigView
from homeassistant.components.config import entity_registry as registry_commands
from homeassistant.components.http import KEY_HASS
from homeassistant.components.websocket_api import ActiveConnection, commands
from homeassistant.components.websocket_api.connection import current_connection
from homeassistant.core import State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.http import KEY_AUTHENTICATED, HomeAssistantView

from custom_components.temporary_guest.api_guard import async_install_api_guard
from custom_components.temporary_guest.const import DATA_MANAGER, DOMAIN, RESTRICTED_GROUP_ID
from custom_components.temporary_guest.permissions import build_entity_policy


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(ActiveConnection, "async_handle", ActiveConnection.async_handle)
    monkeypatch.setattr(HomeAssistantView, "register", HomeAssistantView.register)
    token = current_connection.set(None)
    entries = {
        entity_id: er.RegistryEntry(
            entity_id=entity_id,
            unique_id="private-hardware-id",
            platform="test",
            device_id=device,
            area_id=area,
            capabilities=None,
            config_entry_id=None,
            config_subentry_id=None,
            created_at=datetime.now(UTC),
            disabled_by=None,
            entity_category=None,
            has_entity_name=False,
            hidden_by=None,
            id=None,
            object_id_base=None,
            options={},
            original_device_class=None,
            original_icon=None,
            original_name=None,
            suggested_object_id=None,
            supported_features=0,
            translation_key=None,
            unit_of_measurement=None,
        )
        for entity_id, device, area in (
            ("sensor.guest", "guest-device", "guest-area"),
            ("light.guest", "guest-device", "guest-area"),
            ("sensor.private", "private-device", "private-area"),
        )
    }
    entity_registry = SimpleNamespace(entities=entries, async_get=entries.get)
    device_registry = SimpleNamespace(async_get=lambda _id: None)
    monkeypatch.setattr(er, "async_get", lambda _hass: entity_registry)
    monkeypatch.setattr(dr, "async_get", lambda _hass: device_registry)
    group = Group(
        name="guest",
        id=RESTRICTED_GROUP_ID,
        policy=build_entity_policy(["sensor.guest", "camera.guest"], ["light.guest"]),
    )
    user = User(
        name="guest",
        id="guest",
        groups=[group],
        perm_lookup=PermissionLookup(entity_registry, device_registry),
    )
    config = {
        "latitude": 42,
        "longitude": 12,
        "elevation": 99,
        "internal_url": "http://private-host",
        "external_url": "https://private-host",
        "location_name": "private-home",
        "config_dir": "/private/config",
        "time_zone": "UTC",
        "unit_system": {},
        "version": "2026.10.1",
        "components": ["frontend", "lovelace", "private_integration"],
    }
    app = web.Application()
    tasks = []

    def schedule(coro, *_args, **_kwargs):
        tasks.append(asyncio.create_task(coro))
        return tasks[-1]

    hass = SimpleNamespace(
        data={
            "websocket_api": {},
            DOMAIN: {DATA_MANAGER: SimpleNamespace(guest_dashboard_path="/dashboard-guest/view")},
        },
        http=SimpleNamespace(app=app),
        is_stopping=False,
        config=SimpleNamespace(as_dict=lambda: config),
        states=SimpleNamespace(async_all=lambda: [State(key, "on") for key in entries]),
        services=SimpleNamespace(async_call=AsyncMock()),
        async_create_background_task=schedule,
    )
    app[KEY_HASS] = hass

    @web.middleware
    async def auth(request, handler):
        request[KEY_AUTHENTICATED] = True
        request["hass_user"] = user
        return await handler(request)

    app.middlewares.append(auth)
    messages = []
    connection = ActiveConnection(Mock(), hass, messages.append, user, None, None)
    try:
        yield SimpleNamespace(
            hass=hass,
            user=user,
            connection=connection,
            messages=messages,
            tasks=tasks,
            config=config,
            app=app,
            entries=entries,
        )
    finally:
        current_connection.reset(token)


def _result(env, command, **params):
    env.connection.async_handle({"id": env.connection.last_id + 1, "type": command, **params})
    message = env.messages[-1]
    return message if isinstance(message, dict) else json.loads(message)


def _register_result(env, command, value, fields=None):
    @websocket_api.websocket_command({"type": command, **(fields or {})})
    @callback
    def handler(hass, connection, msg):
        connection.send_result(msg["id"], value)

    websocket_api.async_register_command(env.hass, handler)
    return handler


@pytest.mark.parametrize("compact", [False, True])
def test_real_entity_registry_serializers_are_filtered(environment, compact):
    env = environment
    registry_commands.async_setup(env.hass)
    async_install_api_guard(env.hass)
    command = "config/entity_registry/list" + ("_for_display" if compact else "")
    result = _result(env, command)["result"]
    rows = result["entities"] if compact else result
    assert {item["ei" if compact else "entity_id"] for item in rows} == {
        "sensor.guest",
        "light.guest",
    }
    assert "private" not in json.dumps(result)
    assert "unique_id" not in json.dumps(result)


def test_entity_lookup_does_not_disclose_private_or_nonexistent_entities(environment):
    env = environment
    registry_commands.async_setup(env.hass)
    async_install_api_guard(env.hass)
    for entity_id in ("sensor.private", "sensor.not_registered"):
        result = _result(env, "config/entity_registry/get", entity_id=entity_id)
        assert result["error"]["code"] == "not_found"
    result = _result(
        env, "config/entity_registry/get_entries", entity_ids=["sensor.guest", "sensor.private"]
    )
    assert result["result"]["sensor.private"] is None
    assert result["result"]["sensor.guest"]["entity_id"] == "sensor.guest"
    assert "private-hardware-id" not in json.dumps(result)


def test_empty_acl_cannot_list_any_registry_entities(environment):
    env = environment
    env.user.groups[0].policy = build_entity_policy([], [])
    registry_commands.async_setup(env.hass)
    async_install_api_guard(env.hass)
    assert _result(env, "config/entity_registry/list")["result"] == []


@pytest.mark.parametrize(
    "command,rows,expected",
    [
        (
            "config/device_registry/list",
            [
                {
                    "id": "guest-device",
                    "name": "guest",
                    "serial_number": "secret",
                    "connections": ["mac"],
                },
                {"id": "private-device", "name": "private"},
            ],
            [{"id": "guest-device", "name": "guest"}],
        ),
        (
            "config/area_registry/list",
            [
                {
                    "area_id": "guest-area",
                    "name": "guest",
                    "temperature_entity_id": "sensor.private",
                },
                {"area_id": "private-area", "name": "private"},
            ],
            [{"area_id": "guest-area", "name": "guest"}],
        ),
        ("config/label_registry/list", [{"label_id": "private-label"}], []),
        (
            "person/list",
            {"storage": [{"user_id": "private-user"}], "config": []},
            {"storage": [], "config": []},
        ),
    ],
)
def test_metadata_is_limited_to_visible_entities(environment, command, rows, expected):
    env = environment
    _register_result(env, command, rows)
    async_install_api_guard(env.hass)
    assert _result(env, command)["result"] == expected


@pytest.mark.parametrize(
    "command",
    [
        "render_template",
        "search/related",
        "recorder/list_statistic_ids",
        "recorder/statistics_during_period",
        "media_source/browse_media",
        "energy/get_prefs",
        "config/device_registry/list_linked_devices",
        "config/entity_registry/get_automatic_entity_ids",
    ],
)
def test_dangerous_commands_registered_after_guard_are_denied(environment, command):
    env = environment
    async_install_api_guard(env.hass)
    _register_result(env, command, {"private": "must never be sent"})
    result = _result(env, command)
    assert result["error"]["code"] == "unauthorized"
    assert "result" not in result


@pytest.mark.asyncio
async def test_actual_template_handler_cannot_start_a_listener(environment, monkeypatch):
    env = environment
    start = Mock(side_effect=AssertionError("Template rendering must not run"))
    monkeypatch.setattr(commands, "_cached_template", start)
    websocket_api.async_register_command(env.hass, commands.handle_render_template)
    async_install_api_guard(env.hass)
    result = _result(env, "render_template", template="{{ states | count }}")
    assert result["error"]["code"] == "unauthorized"
    start.assert_not_called()
    assert not env.tasks and not env.connection.subscriptions


def test_global_config_and_other_dashboards_are_not_exposed(environment):
    env = environment
    websocket_api.async_register_command(env.hass, commands.handle_get_config)
    _register_result(
        env, "get_panels", {"dashboard-guest": {"title": "Guest"}, "private": {"title": "Private"}}
    )
    _register_result(env, "lovelace/config", {"views": []}, {probatio.Optional("url_path"): str})
    async_install_api_guard(env.hass)
    result = _result(env, "get_config")["result"]
    assert result["latitude"] == 0 and result["internal_url"] is None
    assert "private" not in json.dumps(result)
    assert set(_result(env, "get_panels")["result"]) == {"dashboard-guest"}
    assert _result(env, "lovelace/config", url_path="dashboard-guest")["success"]
    for path in (None, "private", "dashboard-guest-evil", "/dashboard-guest"):
        result = _result(env, "lovelace/config", **({"url_path": path} if path else {}))
        assert result["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("entity_id,permitted", [("camera.guest", True), ("camera.private", False)])
@pytest.mark.parametrize("command", ["camera/stream", "media_player/browse_media"])
def test_media_commands_require_entity_read_access(environment, entity_id, permitted, command):
    env = environment
    _register_result(env, command, "image", {"entity_id": str})
    async_install_api_guard(env.hass)
    result = _result(env, command, entity_id=entity_id)
    assert result["success"] is permitted


@pytest.mark.parametrize(
    "event_type,allowed",
    [
        ("state_changed", True),
        ("entity_registry_updated", False),
        ("device_registry_updated", False),
    ],
)
def test_only_permission_filtered_state_events_are_allowed(environment, event_type, allowed):
    env = environment
    _register_result(env, "subscribe_events", None, {probatio.Optional("event_type"): str})
    async_install_api_guard(env.hass)
    assert _result(env, "subscribe_events", event_type=event_type)["success"] is allowed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields,allowed",
    [
        ({"target": {"entity_id": "light.guest"}}, True),
        ({"service_data": {"entity_id": "light.guest"}}, True),
        ({"target": {"entity_id": "light.private"}}, False),
        (
            {
                "target": {"entity_id": "light.guest"},
                "service_data": {"entity_id": "light.private"},
            },
            False,
        ),
        ({"target": {"entity_id": "all"}}, False),
        ({"target": {"area_id": "guest-area"}}, False),
        ({}, False),
        ({"target": {"entity_id": "light.guest"}, "return_response": True}, False),
    ],
)
async def test_services_require_explicit_authorized_targets(environment, fields, allowed):
    env = environment
    websocket_api.async_register_command(env.hass, commands.handle_call_service)
    async_install_api_guard(env.hass)
    env.connection.async_handle(
        {"id": 1, "type": "call_service", "domain": "light", "service": "turn_on", **fields}
    )
    await asyncio.gather(*env.tasks)
    message = env.messages[-1]
    result = message if isinstance(message, dict) else json.loads(message)
    assert result["success"] is allowed
    assert env.hass.services.async_call.await_count == int(allowed)


def test_guard_preserves_protocol_validation_and_other_users(environment):
    env = environment
    registry_commands.async_setup(env.hass)
    async_install_api_guard(env.hass)
    first_handler = env.connection.async_handle.__func__
    async_install_api_guard(env.hass)
    assert env.connection.async_handle.__func__ is first_handler
    env.connection.async_handle(
        {"id": 1, "type": "config/entity_registry/list", "user_id": "owner"}
    )
    assert env.messages[-1]["error"]["code"] == "invalid_format"
    env.connection.async_handle({"id": 1, "type": "config/entity_registry/list"})
    assert env.messages[-1]["error"]["code"] == "id_reuse"
    # A different ordinary user, even with no entity permissions, follows HA.
    env.user.groups = []
    assert len(_result(env, "config/entity_registry/list")["result"]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix",
    [
        "camera_proxy",
        "camera_proxy_stream",
        "media_player_proxy",
        "image_proxy",
        "image_proxy_stream",
    ],
)
async def test_http_images_enforce_acl_even_after_router_freeze(environment, prefix):
    env = environment
    reached = []

    class ImageView(HomeAssistantView):
        url = "/api/" + prefix + "/{entity_id}"
        name = "test:image"
        requires_auth = False

        async def get(self, request, entity_id):
            reached.append(entity_id)
            return web.Response(body=b"image")

    ImageView().register(env.hass, env.app, env.app.router)
    async with TestClient(TestServer(env.app)) as client:
        async_install_api_guard(env.hass)
        assert (await client.get(f"/api/{prefix}/camera.private?token=ignored")).status == 403
        assert not reached
        assert (await client.get(f"/api/{prefix}/camera.guest")).status == 200
        assert reached == ["camera.guest"]


@pytest.mark.asyncio
async def test_late_http_registration_config_and_unmanaged_users(environment):
    env = environment
    async_install_api_guard(env.hass)
    APIConfigView().register(env.hass, env.app, env.app.router)
    async with TestClient(TestServer(env.app)) as client:
        response = await client.get("/api/config")
        result = await response.json()
        assert result["latitude"] == 0 and "private" not in json.dumps(result)
        env.user.groups = []
        response = await client.get("/api/config")
        assert (await response.json())["latitude"] == 42


@pytest.mark.asyncio
async def test_http_service_denies_before_execution_and_filters_indirect_states(environment):
    env = environment
    reached = []

    class ServiceView(HomeAssistantView):
        url = "/api/services/{domain}/{service}"
        name = "test:service"

        async def post(self, request, domain, service):
            reached.append((domain, service))
            return self.json([{"entity_id": "light.guest"}, {"entity_id": "sensor.private"}])

    async_install_api_guard(env.hass)
    ServiceView().register(env.hass, env.app, env.app.router)
    async with TestClient(TestServer(env.app)) as client:
        path = "/api/services/light/turn_on"
        assert (await client.post(path, json={})).status == 403
        assert (await client.post(path, json={"entity_id": "light.private"})).status == 403
        assert not reached
        response = await client.post(path, json={"entity_id": "light.guest"})
        assert response.status == 200
        assert await response.json() == [{"entity_id": "light.guest"}]
        assert len(reached) == 1


@pytest.mark.asyncio
async def test_api_and_logbook_guards_coexist_with_native_state_permissions(environment, monkeypatch):
    """The combined setup blocks logbook but preserves permission-filtered states."""
    from homeassistant.components.logbook import websocket_api as logbook_ws
    from homeassistant.components.logbook.processor import EventProcessor

    from custom_components.temporary_guest.logbook_guard import async_install_logbook_guard

    env = environment
    monkeypatch.setattr(EventProcessor, "__init__", EventProcessor.__init__)
    monkeypatch.setattr(logbook_ws, "async_determine_event_types", lambda *_args: [])
    logbook_ws.async_setup(env.hass)
    websocket_api.async_register_command(env.hass, commands.handle_get_states)
    async_install_logbook_guard(env.hass, env.user.id)
    async_install_api_guard(env.hass)
    env.connection.async_handle({"id": 1, "type": "logbook/get_events", "start_time": "2026-01-01T00:00:00+00:00"})
    await asyncio.gather(*env.tasks)
    assert env.messages[-1]["error"]["code"] == "unauthorized"
    env.connection.async_handle({"id": 2, "type": "get_states"})
    await asyncio.gather(*env.tasks)
    message = env.messages[-1]
    result = message if isinstance(message, dict) else json.loads(message)
    assert {row["entity_id"] for row in result["result"]} == {"sensor.guest", "light.guest"}
