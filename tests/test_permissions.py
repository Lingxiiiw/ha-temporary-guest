"""Tests for the deny-by-default Home Assistant entity policy."""

from types import SimpleNamespace

from homeassistant.auth.permissions import PolicyPermissions
from homeassistant.auth.permissions.const import CAT_ENTITIES
from homeassistant.auth.permissions.entities import (
    ENTITY_ENTITY_IDS,
    ENTITY_POLICY_SCHEMA,
)
from homeassistant.auth.permissions.models import PermissionLookup

from custom_components.temporary_guest.permissions import build_entity_policy


def test_empty_lists_grant_no_entity_permissions() -> None:
    """An unconfigured whitelist must never expand to all entities."""
    policy = build_entity_policy([], [])
    assert policy == {CAT_ENTITIES: {ENTITY_ENTITY_IDS: {}}}
    assert ENTITY_POLICY_SCHEMA(policy[CAT_ENTITIES]) == policy[CAT_ENTITIES]


def test_control_overrides_read_only_and_includes_read() -> None:
    """Controllable entities remain visible and win an overlapping read entry."""
    policy = build_entity_policy(
        ["sensor.room", "light.guest"],
        ["light.guest", "switch.guest"],
    )
    assert policy[CAT_ENTITIES][ENTITY_ENTITY_IDS] == {
        "light.guest": {"read": True, "control": True},
        "sensor.room": {"read": True},
        "switch.guest": {"read": True, "control": True},
    }
    assert ENTITY_POLICY_SCHEMA(policy[CAT_ENTITIES]) == policy[CAT_ENTITIES]


def test_home_assistant_enforces_the_exact_entity_boundary() -> None:
    """Use HA's real policy compiler to prove allow/read/control/deny behavior."""
    empty_registry = SimpleNamespace(async_get=lambda _entity_id: None)
    permissions = PolicyPermissions(
        build_entity_policy(["sensor.room"], ["light.guest"]),
        PermissionLookup(empty_registry, empty_registry),
    )

    assert permissions.check_entity("sensor.room", "read") is True
    assert permissions.check_entity("sensor.room", "control") is False
    assert permissions.check_entity("light.guest", "read") is True
    assert permissions.check_entity("light.guest", "control") is True
    assert permissions.check_entity("lock.private", "read") is False
    assert permissions.check_entity("lock.private", "control") is False
