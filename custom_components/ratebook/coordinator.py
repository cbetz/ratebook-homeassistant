"""Data coordinator: recompute the price schedule and cheapest charge window."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CHARGE_HOURS,
    CONF_CURRENCY,
    CONF_TARIFF_JSON,
    CONF_TARIFF_SOURCE,
    CONF_TIER,
    CUSTOM,
    DEFAULT_CHARGE_HOURS,
    DEFAULT_CURRENCY,
    DEFAULT_TIER,
    DOMAIN,
    LOGGER,
)
from .vendor.ratebook_ha import pricing


class RatebookCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Loads the configured tariff once and recomputes prices on a fixed cadence."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, LOGGER, name=DOMAIN, update_interval=timedelta(minutes=5))
        self.entry = entry
        self.currency: str = entry.data.get(CONF_CURRENCY, DEFAULT_CURRENCY)
        self.charge_hours: int = entry.options.get(
            CONF_CHARGE_HOURS, entry.data.get(CONF_CHARGE_HOURS, DEFAULT_CHARGE_HOURS)
        )
        # 0-based tier index for the engine; the config stores the human 1-based choice.
        # Clamped at 0 so a malformed stored value can never index tiers[-1] (the TOP tier).
        self.tier: int = max(
            0, int(entry.options.get(CONF_TIER, entry.data.get(CONF_TIER, DEFAULT_TIER))) - 1
        )
        # Loaded off the event loop in async_load_tariff() — reading bundled tariff files is
        # blocking I/O and must not run inside Home Assistant's async loop.
        self._tariff = None

    async def async_load_tariff(self) -> None:
        """Load the configured tariff in an executor (the file read is blocking I/O)."""
        self._tariff = await self.hass.async_add_executor_job(self._load_tariff, self.entry)

    @staticmethod
    def _load_tariff(entry: ConfigEntry):
        source = entry.data[CONF_TARIFF_SOURCE]
        if source == CUSTOM:
            return pricing.load_tariff(entry.data[CONF_TARIFF_JSON])
        return pricing.load_bundled(source)

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.now()
        today = now.date()
        tomorrow = today + timedelta(days=1)
        tier = self.tier
        try:
            window = pricing.cheapest_window(
                self._tariff, today, days=2, charge_hours=self.charge_hours, after=now, tier=tier
            )
        except Exception as err:  # malformed tariff / out-of-range
            raise UpdateFailed(f"price computation failed: {err}") from err

        # The engine returns naive local datetimes; attach Home Assistant's timezone.
        tz = now.tzinfo
        start = dt_util.parse_datetime(window["start"])
        end = dt_util.parse_datetime(window["end"])
        forecast_start = now.replace(minute=0, second=0, microsecond=0)
        return {
            "current_price": pricing.current_price(self._tariff, now, tier=tier),
            "today": pricing.hourly_schedule(self._tariff, today, tier=tier),
            "tomorrow": pricing.hourly_schedule(self._tariff, tomorrow, tier=tier),
            # Nordpool-shaped [{start, end, value}] — the attribute format existing HA
            # price automations, blueprints, and ApexCharts configs already consume.
            # tz-aware like Nordpool's, so `as_datetime(start) <= now()` comparisons work.
            "raw_today": pricing.nordpool_schedule(self._tariff, today, tier=tier, tz=tz),
            "raw_tomorrow": pricing.nordpool_schedule(self._tariff, tomorrow, tier=tier, tz=tz),
            # evcc custom-tariff shape ([{start, end, value}]) for direct consumption by evcc's
            # http source pointed at this sensor via the Home Assistant REST API.
            "forecast": pricing.evcc_forecast(self._tariff, forecast_start, 48, tier=tier),
            "today_is_holiday": pricing.is_holiday(self._tariff, today),
            "tomorrow_is_holiday": pricing.is_holiday(self._tariff, tomorrow),
            "cheapest_window": {
                "start": start.replace(tzinfo=tz) if start else None,
                "end": end.replace(tzinfo=tz) if end else None,
                "avg_rate": window["avg_rate"],
                "hours": window["hours"],
            },
        }
