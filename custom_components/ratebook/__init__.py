"""The Ratebook integration: live electricity-price + cheapest-charge-window sensors."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import RatebookCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type RatebookConfigEntry = ConfigEntry[RatebookCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: RatebookConfigEntry) -> bool:
    """Set up Ratebook from a config entry."""
    coordinator = RatebookCoordinator(hass, entry)
    await coordinator.async_load_tariff()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RatebookConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: RatebookConfigEntry) -> None:
    """Reload when options (e.g. charge-window length) change."""
    await hass.config_entries.async_reload(entry.entry_id)
