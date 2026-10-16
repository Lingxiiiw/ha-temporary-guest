"""Pin the Home Assistant internal auth API contract used by the integration."""

import inspect

from homeassistant.auth import AuthManager
from homeassistant.auth.auth_store import AuthStore
from homeassistant.auth.models import Group
from homeassistant.auth.providers.homeassistant import HassAuthProvider
from homeassistant.helpers.storage import Store


def test_password_provider_signature() -> None:
    """Fail CI if Home Assistant changes the password API we rely on."""
    parameters = list(inspect.signature(HassAuthProvider.async_change_password).parameters)
    assert parameters == ["self", "username", "new_password"]


def test_auth_manager_contract() -> None:
    """Fail CI if session revocation or account activation APIs disappear."""
    assert hasattr(AuthManager, "async_update_user")
    assert hasattr(AuthManager, "async_remove_refresh_token")
    update_parameters = inspect.signature(AuthManager.async_update_user).parameters
    assert "is_active" in update_parameters
    assert "group_ids" in update_parameters


def test_private_group_store_contract_is_pinned() -> None:
    """Fail CI before an HA upgrade can silently weaken entity restrictions."""
    assert hasattr(AuthStore, "_async_schedule_save")
    assert hasattr(AuthStore, "_data_to_save")
    assert hasattr(Store, "async_save")
    group = Group(name="test", policy={"entities": {"entity_ids": {}}})
    assert group.system_generated is False
