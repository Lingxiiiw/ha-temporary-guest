"""Pin the Home Assistant internal auth API contract used by the integration."""

import inspect

from homeassistant.auth import AuthManager
from homeassistant.auth.providers.homeassistant import HassAuthProvider


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

