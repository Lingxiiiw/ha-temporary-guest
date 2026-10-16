"""Version-pinned Home Assistant entity-permission helpers.

Home Assistant 2026.10 exposes user group assignment publicly but does not
expose group creation. The small private-store boundary in this module is kept
isolated and covered by contract tests so an incompatible HA upgrade fails
closed instead of silently granting the normal user's all-entity policy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from homeassistant.auth import AuthManager
from homeassistant.auth.models import Group, User
from homeassistant.auth.permissions.const import (
    CAT_ENTITIES,
    POLICY_CONTROL,
    POLICY_READ,
)
from homeassistant.auth.permissions.entities import ENTITY_ENTITY_IDS
from homeassistant.exceptions import HomeAssistantError

from .const import RESTRICTED_GROUP_ID, RESTRICTED_GROUP_NAME


class PermissionBackendError(HomeAssistantError):
    """Raised when the pinned HA auth-store contract is unavailable or unsafe."""


def build_entity_policy(
    read_entities: Sequence[str], control_entities: Sequence[str]
) -> dict[str, Any]:
    """Build a deny-by-default entity policy.

    Controllable entities are always readable. An empty pair of lists produces
    an empty entity-id mapping, which grants no entity permission.
    """
    entity_permissions: dict[str, dict[str, bool]] = {
        entity_id: {POLICY_READ: True} for entity_id in sorted(set(read_entities))
    }
    for entity_id in sorted(set(control_entities)):
        entity_permissions[entity_id] = {
            POLICY_READ: True,
            POLICY_CONTROL: True,
        }
    return {CAT_ENTITIES: {ENTITY_ENTITY_IDS: entity_permissions}}


def permission_configuration_matches(
    user: User, group: Group | None, policy: Mapping[str, Any]
) -> bool:
    """Return whether the user has exactly the managed restrictive group."""
    return bool(
        group is not None
        and not group.system_generated
        and group.name == RESTRICTED_GROUP_NAME
        and group.policy == policy
        and [assigned.id for assigned in user.groups] == [RESTRICTED_GROUP_ID]
    )


async def async_ensure_restricted_group(
    auth: AuthManager,
    policy: dict[str, Any],
    *,
    managed_user_id: str | None,
) -> Group:
    """Create or update the dedicated group using the pinned HA 2026.10 store."""
    store = getattr(auth, "_store", None)
    groups = getattr(store, "_groups", None)
    schedule_save = getattr(store, "_async_schedule_save", None)
    if not isinstance(groups, dict) or not callable(schedule_save):
        raise PermissionBackendError(
            "Home Assistant auth group storage is incompatible with Temporary Guest"
        )

    for user in await auth.async_get_users():
        if user.id != managed_user_id and any(
            group.id == RESTRICTED_GROUP_ID for group in user.groups
        ):
            raise PermissionBackendError(
                "The Temporary Guest permission group is assigned to another user"
            )

    group = await auth.async_get_group(RESTRICTED_GROUP_ID)
    changed = False
    if group is None:
        group = Group(
            name=RESTRICTED_GROUP_NAME,
            policy=policy,
            id=RESTRICTED_GROUP_ID,
            system_generated=False,
        )
        groups[RESTRICTED_GROUP_ID] = group
        changed = True
    else:
        if group.system_generated or group.name != RESTRICTED_GROUP_NAME:
            raise PermissionBackendError(
                "The Temporary Guest permission group id is already in use"
            )
        if group.policy != policy:
            group.policy = policy
            changed = True

    if changed:
        schedule_save()
    return group


async def async_persist_auth_store(auth: AuthManager) -> None:
    """Immediately persist the pinned auth store after a security boundary change."""
    store = getattr(auth, "_store", None)
    storage = getattr(store, "_store", None)
    data_to_save = getattr(store, "_data_to_save", None)
    save = getattr(storage, "async_save", None)
    if not callable(data_to_save) or not callable(save):
        raise PermissionBackendError(
            "Home Assistant auth persistence is incompatible with Temporary Guest"
        )
    await save(data_to_save())
