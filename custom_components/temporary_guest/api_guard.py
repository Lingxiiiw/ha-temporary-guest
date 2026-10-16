"""Guest authorization for HA API surfaces outside native entity permissions.

Keep native message validation/IDs and handlers for other users. Guard dispatch
instead of individual registrations so late-loaded commands are also covered.
HTTP routes are wrapped after authentication, including already-frozen routes.
"""

from __future__ import annotations

import json
from functools import wraps
from inspect import signature

import probatio
from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.helpers.json import json_bytes

from .const import DATA_MANAGER, DOMAIN
from .logbook_guard import _is_protected_user

_MARKER = "_temporary_guest_api_guard"
_ENTITY_FIELDS = {
    "entity_id",
    "id",
    "name",
    "original_name",
    "icon",
    "original_icon",
    "device_class",
    "original_device_class",
    "entity_category",
    "has_entity_name",
    "disabled_by",
    "hidden_by",
    "area_id",
    "device_id",
    "platform",
    "translation_key",
}
_DISPLAY_FIELDS = {"ei", "pl", "en", "ic", "ec", "hb", "hn", "dp", "ai", "di", "tk"}
_FILTERED = {
    "config/entity_registry/list",
    "config/entity_registry/list_for_display",
    "config/entity_registry/get",
    "config/entity_registry/get_entries",
    "config/device_registry/list",
    "config/area_registry/list",
    "config/floor_registry/list",
    "config/label_registry/list",
    "get_config",
    "get_services",
    "get_panels",
    "person/list",
    "lovelace/dashboards/list",
}
_DENIED = {"render_template", "search/related", "get_services_for_target"}
_DENIED_PREFIXES = ("recorder/", "energy/", "media_source/")
_GUARDED_PREFIXES = (
    "config/entity_registry/",
    "config/device_registry/",
    "config/area_registry/",
    "config/floor_registry/",
    "config/label_registry/",
    "config/category_registry/",
)


def _dashboard(hass: HomeAssistant) -> str | None:
    manager = hass.data.get(DOMAIN, {}).get(DATA_MANAGER)
    return manager.guest_dashboard_path.strip("/").split("/")[0] if manager else None


def _readable(user, entity_id) -> bool:
    return isinstance(entity_id, str) and user.permissions.check_entity(entity_id, POLICY_READ)


def _visible_metadata(hass, user):
    """Derive devices/areas only from readable entities, never caller input."""
    entities = [
        entry for entry in er.async_get(hass).entities.values() if _readable(user, entry.entity_id)
    ]
    devices = {entry.device_id for entry in entities if entry.device_id}
    areas = {entry.area_id for entry in entities if entry.area_id}
    registry = dr.async_get(hass)
    for device_id in devices:
        if (device := registry.async_get(device_id)) and device.area_id:
            areas.add(device.area_id)
    return devices, areas


def _safe_config(value):
    """Keep frontend formatting information without location or network details."""
    return {
        **{
            key: value[key]
            for key in ("unit_system", "time_zone", "version", "state", "currency", "language")
            if key in value
        },
        "latitude": 0,
        "longitude": 0,
        "elevation": 0,
        "location_name": "Guest",
        "internal_url": None,
        "external_url": None,
        "components": [
            component
            for component in value.get("components", [])
            if component
            in {
                "api",
                "http",
                "frontend",
                "websocket_api",
                "lovelace",
                "history",
                "temporary_guest",
            }
        ],
    }


def _filter_result(hass, user, command, value):
    if command == "get_config":
        return _safe_config(value)
    if command == "person/list":
        return {"storage": [], "config": []}
    if command == "config/label_registry/list":
        return []
    if command == "get_panels":
        dashboard = _dashboard(hass)
        return {key: panel for key, panel in value.items() if key == dashboard}
    if command == "lovelace/dashboards/list":
        return [item for item in value if item.get("url_path") == _dashboard(hass)]
    if command == "get_services":
        domains = {
            state.entity_id.split(".")[0]
            for state in hass.states.async_all()
            if user.permissions.check_entity(state.entity_id, POLICY_CONTROL)
        }
        return {domain: services for domain, services in value.items() if domain in domains}
    if command.startswith("config/entity_registry/"):

        def entry(item, compact=False):
            if item is None or not _readable(user, item.get("ei" if compact else "entity_id")):
                return None
            fields = _DISPLAY_FIELDS if compact else _ENTITY_FIELDS
            return {key: val for key, val in item.items() if key in fields}

        if command.endswith("/list_for_display"):
            return {
                "entity_categories": value["entity_categories"],
                "entities": [
                    filtered
                    for item in value["entities"]
                    if (filtered := entry(item, True)) is not None
                ],
            }
        if command.endswith("/list"):
            return [filtered for item in value if (filtered := entry(item)) is not None]
        if command.endswith("/get_entries"):
            return {key: entry(item) for key, item in value.items()}
        return entry(value)
    devices, areas = _visible_metadata(hass, user)
    if command == "config/device_registry/list":
        return [
            {
                key: val
                for key, val in item.items()
                if key in {"id", "name", "name_by_user", "area_id", "disabled_by"}
            }
            for item in value
            if item["id"] in devices
        ]
    if command == "config/area_registry/list":
        return [
            {
                key: val
                for key, val in item.items()
                if key in {"area_id", "name", "icon", "floor_id"}
            }
            for item in value
            if item["area_id"] in areas
        ]
    if command == "config/floor_registry/list":
        from homeassistant.helpers import area_registry as ar

        floors = {
            area.floor_id
            for area in ar.async_get(hass).async_list_areas()
            if area.id in areas and area.floor_id
        }
        return [
            {key: val for key, val in item.items() if key in {"floor_id", "name", "icon", "level"}}
            for item in value
            if item["floor_id"] in floors
        ]
    raise Unauthorized


class _FilteredConnection:
    """Forward protocol operations while filtering every successful result form."""

    def __init__(self, connection, command):
        self._connection = connection
        self._command = command

    def __getattr__(self, name):
        return getattr(self._connection, name)

    @callback
    def send_message(self, message):
        # Core handlers use dicts, cached JSON bytes and orjson fragments.
        decoded = json.loads(json_bytes(message) if isinstance(message, dict) else message)
        if decoded.get("success") and "result" in decoded:
            try:
                decoded["result"] = _filter_result(
                    self.hass, self.user, self._command, decoded["result"]
                )
            except Exception:  # noqa: BLE001 - never send an unfiltered fallback
                self._connection.send_error(decoded["id"], "unauthorized", "Unauthorized")
                return
        self._connection.send_message(json_bytes(decoded))

    @callback
    def send_result(self, msg_id, result=None):
        self.send_message({"id": msg_id, "type": "result", "success": True, "result": result})


def _check_service(user, domain, data, target=None):
    """Require explicit controllable entities; no global/area/device selectors."""
    target = target or {}
    data = data or {}
    if any(
        key in values
        for values in (data, target)
        for key in ("area_id", "device_id", "floor_id", "label_id")
    ):
        raise Unauthorized
    ids = []
    for values in (data, target):
        selected = values.get("entity_id", [])
        if isinstance(selected, str):
            selected = [part.strip() for part in selected.split(",")]
        if not isinstance(selected, list):
            raise Unauthorized
        ids.extend(selected)
    if not ids or any(
        not isinstance(entity_id, str)
        or "." not in entity_id
        or (domain != "homeassistant" and entity_id.split(".")[0] != domain)
        or not user.permissions.check_entity(entity_id, POLICY_CONTROL)
        for entity_id in ids
    ):
        raise Unauthorized


def _dispatch_guest(connection, msg, handler):
    command = msg["type"]
    if command in _FILTERED:
        if command == "config/entity_registry/get" and not _readable(
            connection.user, msg["entity_id"]
        ):
            connection.send_error(msg["id"], "not_found", "Entity not found")
            return
        handler(connection.hass, _FilteredConnection(connection, command), msg)
        return
    if command in _DENIED or command.startswith((*_GUARDED_PREFIXES, *_DENIED_PREFIXES)):
        raise Unauthorized
    if command == "subscribe_events" and msg.get("event_type") != "state_changed":
        raise Unauthorized
    if command == "lovelace/config":
        if (msg.get("url_path") or "lovelace") != _dashboard(connection.hass):
            raise Unauthorized
    if command == "call_service":
        if msg.get("return_response"):
            raise Unauthorized
        _check_service(connection.user, msg["domain"], msg.get("service_data"), msg.get("target"))
    if command.startswith(("camera/", "media_player/")):
        if not _readable(connection.user, msg.get("entity_id")):
            raise Unauthorized
    handler(connection.hass, connection, msg)


def _guarded_command(command):
    return (
        command in _FILTERED
        or command in _DENIED
        or command.startswith((*_GUARDED_PREFIXES, *_DENIED_PREFIXES, "camera/", "media_player/"))
        or command
        in {
            "subscribe_events",
            "lovelace/config",
            "call_service",
        }
    )


def _install_websocket_guard():
    original = ActiveConnection.async_handle
    if getattr(original, _MARKER, False):
        return
    if list(signature(original).parameters) != ["self", "msg"]:
        raise HomeAssistantError("Incompatible WebSocket dispatch")

    @callback
    @wraps(original)
    def guarded(self, msg):
        # Delegate malformed/reused IDs to HA so its protocol rules stay intact.
        if (
            not _is_protected_user(self.hass, self.user)
            or type(msg) is not dict
            or type(msg.get("id")) is not int
            or msg["id"] <= self.last_id
            or type(msg.get("type")) is not str
            or not _guarded_command(msg["type"])
            or msg["type"] not in self.handlers
        ):
            original(self, msg)
            return
        handler, schema = self.handlers[msg["type"]]
        try:
            if schema is False:
                if len(msg) > 2:
                    raise probatio.Invalid("extra keys not allowed")
                validated = msg
            else:
                validated = schema(msg)
            _dispatch_guest(self, validated, handler)
        except Exception as err:  # noqa: BLE001 - use HA's normal error mapping
            self.async_handle_exception(msg, err)
        self.last_id = msg["id"]

    setattr(guarded, _MARKER, True)
    ActiveConnection.async_handle = guarded


async def _guest_http(hass, request, handler):
    user = request.get("hass_user")
    if not _is_protected_user(hass, user):
        return await handler(request)
    path = request.path.rstrip("/")
    if path == "/api/config" and request.method == "GET":
        return web.Response(
            body=json_bytes(_safe_config(hass.config.as_dict())), content_type="application/json"
        )
    if path in {"/api/components", "/api/events", "/api/services"} and request.method == "GET":
        return web.json_response([])
    if path.startswith(
        (
            "/api/camera_proxy/",
            "/api/camera_proxy_stream/",
            "/api/media_player_proxy/",
            "/api/image_proxy/",
            "/api/image_proxy_stream/",
        )
    ):
        if not _readable(user, request.match_info.get("entity_id")):
            raise web.HTTPForbidden()
    if path.startswith("/api/services/") and request.method == "POST":
        if "return_response" in request.query:
            raise web.HTTPForbidden()
        try:
            data = await request.json()
            _check_service(user, request.match_info["domain"], data)
        except (Unauthorized, ValueError, TypeError, AttributeError) as err:
            raise web.HTTPForbidden() from err
        response = await handler(request)
        # REST service calls can collect indirectly changed private states.
        if response.status == 200:
            value = json.loads(response.body)
            if isinstance(value, dict):
                # Service response schemas are arbitrary and cannot be safely trimmed.
                value = {
                    "changed_states": [
                        state
                        for state in value.get("changed_states", [])
                        if _readable(user, state.get("entity_id"))
                    ]
                }
            elif isinstance(value, list):
                value = [state for state in value if _readable(user, state.get("entity_id"))]
            return web.Response(body=json_bytes(value), content_type="application/json")
        return response
    return await handler(request)


def _wrap_routes(hass, router):
    for route in router.routes():
        original = route.handler
        if getattr(original, _MARKER, False):
            continue
        if not hasattr(route, "_handler"):
            raise HomeAssistantError("Incompatible HTTP route backend")

        def wrap(handler):
            @wraps(handler)
            async def guarded(request):
                return await _guest_http(hass, request, handler)

            setattr(guarded, _MARKER, True)
            return guarded

        route._handler = wrap(original)


@callback
def async_install_api_guard(hass: HomeAssistant) -> None:
    """Protect existing connections/routes and views loaded later in this process."""
    _install_websocket_guard()
    original = HomeAssistantView.register
    if not getattr(original, _MARKER, False):
        if list(signature(original).parameters) != ["self", "hass", "app", "router"]:
            raise HomeAssistantError("Incompatible HTTP view registration")

        @wraps(original)
        def register(self, hass, app, router):
            original(self, hass, app, router)
            _wrap_routes(hass, router)

        setattr(register, _MARKER, True)
        HomeAssistantView.register = register
    _wrap_routes(hass, hass.http.app.router)
