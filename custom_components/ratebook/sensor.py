"""Ratebook sensors: current electricity price (+ today/tomorrow schedule) and the cheapest
charge window."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import RatebookConfigEntry
from .const import DOMAIN
from .coordinator import RatebookCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RatebookConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [RatebookPriceSensor(coordinator, entry), RatebookChargeWindowSensor(coordinator, entry)]
    )


class _RatebookEntity(CoordinatorEntity[RatebookCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: RatebookCoordinator, entry: RatebookConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Ratebook",
            manufacturer="Ratebook",
            entry_type=DeviceEntryType.SERVICE,
        )


class RatebookPriceSensor(_RatebookEntity):
    """Current marginal electricity price, with the day's schedule as attributes."""

    _attr_translation_key = "electricity_price"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 5
    # The schedule attributes are ~150 dicts recomputed every 5 minutes — live for
    # automations/templates but excluded from the recorder so they don't bloat history.
    _unrecorded_attributes = frozenset(
        {"today", "tomorrow", "raw_today", "raw_tomorrow", "forecast"}
    )

    def __init__(self, coordinator: RatebookCoordinator, entry: RatebookConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_price"
        self._attr_native_unit_of_measurement = f"{coordinator.currency}/kWh"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data["current_price"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "today": self.coordinator.data["today"],
            "tomorrow": self.coordinator.data["tomorrow"],
            # Nordpool-shaped [{start, end, value}] so existing price automations,
            # blueprints, and ApexCharts configs work unchanged — and unlike day-ahead
            # markets, tomorrow's prices are known all day.
            "raw_today": self.coordinator.data["raw_today"],
            "raw_tomorrow": self.coordinator.data["raw_tomorrow"],
            "tomorrow_valid": True,
            # evcc-shaped [{start, end, value}] forecast for price-aware chargers.
            "forecast": self.coordinator.data["forecast"],
            "today_is_holiday": self.coordinator.data["today_is_holiday"],
            "tomorrow_is_holiday": self.coordinator.data["tomorrow_is_holiday"],
            # 1-based tier this sensor prices at (tiered plans: 1 = within baseline).
            "tier": self.coordinator.tier + 1,
            "currency": self.coordinator.currency,
        }


class RatebookChargeWindowSensor(_RatebookEntity):
    """Start time of the cheapest upcoming charge window (searched through end of tomorrow)."""

    _attr_translation_key = "cheapest_charge_window"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: RatebookCoordinator, entry: RatebookConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charge_window"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data["cheapest_window"]["start"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        window = self.coordinator.data["cheapest_window"]
        return {
            "end": window["end"],
            "avg_rate": window["avg_rate"],
            "hours": window["hours"],
        }
