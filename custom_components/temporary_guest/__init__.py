"""Temporary Guest integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import CARD_FILENAME, CARD_URL, DATA_MANAGER, DOMAIN
from .manager import TemporaryGuestManager
from .websocket import async_register_websocket_commands


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the global WebSocket API and bundled card asset."""
    hass.data.setdefault(DOMAIN, {})
    async_register_websocket_commands(hass)

    card_path = Path(__file__).parent / "frontend" / CARD_FILENAME
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(card_path), cache_headers=False)]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the single managed guest account."""
    manager = TemporaryGuestManager(hass, entry)
    await manager.async_initialize()
    entry.runtime_data = manager
    hass.data[DOMAIN][DATA_MANAGER] = manager
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed schedule, entity permissions, and dynamic UI policy."""
    manager: TemporaryGuestManager = entry.runtime_data
    await manager.async_sync_permissions()
    manager.async_reschedule()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry."""
    manager: TemporaryGuestManager = entry.runtime_data
    await manager.async_unload()
    if hass.data.get(DOMAIN, {}).get(DATA_MANAGER) is manager:
        hass.data[DOMAIN].pop(DATA_MANAGER, None)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Fail closed when the config entry is deleted.

    The account is disabled and its sessions are revoked, but it is intentionally
    not deleted. The Owner can delete it later from Settings > People > Users.
    """
    manager = getattr(entry, "runtime_data", None)
    if not isinstance(manager, TemporaryGuestManager):
        manager = TemporaryGuestManager(hass, entry)
    await manager.async_disable_for_removal()
