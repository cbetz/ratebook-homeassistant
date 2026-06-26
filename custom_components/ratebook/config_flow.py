"""Config flow: pick a bundled tariff or paste a Ratebook tariff JSON."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_CHARGE_HOURS,
    CONF_CURRENCY,
    CONF_TARIFF_JSON,
    CONF_TARIFF_SOURCE,
    CUSTOM,
    DEFAULT_CHARGE_HOURS,
    DEFAULT_CURRENCY,
    DOMAIN,
)
from .vendor.ratebook_ha import pricing


def _validate(source: str, tariff_json: str) -> None:
    """Raise if the chosen tariff can't be loaded (runs in an executor — touches package files)."""
    if source == CUSTOM:
        pricing.load_tariff(tariff_json)
    else:
        pricing.load_bundled(source)


class RatebookConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ratebook."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        bundled = await self.hass.async_add_executor_job(pricing.list_bundled)

        if user_input is not None:
            source = user_input[CONF_TARIFF_SOURCE]
            try:
                await self.hass.async_add_executor_job(
                    _validate, source, user_input.get(CONF_TARIFF_JSON, "")
                )
            except Exception:
                errors["base"] = "invalid_tariff"
            if not errors:
                label = "Custom tariff" if source == CUSTOM else source
                return self.async_create_entry(title=f"Ratebook ({label})", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_TARIFF_SOURCE, default=bundled[0]): vol.In([*bundled, CUSTOM]),
                vol.Optional(CONF_TARIFF_JSON, default=""): str,
                vol.Optional(CONF_CHARGE_HOURS, default=DEFAULT_CHARGE_HOURS): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=24)
                ),
                vol.Optional(CONF_CURRENCY, default=DEFAULT_CURRENCY): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
