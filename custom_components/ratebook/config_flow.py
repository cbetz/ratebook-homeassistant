"""Config flow: pick a bundled utility plan or paste a Ratebook tariff JSON."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

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
    TIER_CHOICES,
)
from .vendor.ratebook_ha import pricing


def _validate(source: str, tariff_json: str) -> None:
    """Raise if the chosen tariff can't be loaded (runs in an executor — touches package files)."""
    if source == CUSTOM:
        pricing.load_tariff(tariff_json)
    else:
        pricing.load_bundled(source)


class RatebookConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ratebook (initial setup and in-place reconfigure)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._tariff_form("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # Lets the user switch tariff / charge-window / currency without deleting + re-adding.
        return await self._tariff_form("reconfigure", user_input)

    async def _tariff_form(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        bundled = await self.hass.async_add_executor_job(pricing.bundled_index)
        labels = {e["slug"]: e["label"] for e in bundled}
        current = self._get_reconfigure_entry().data if step_id == "reconfigure" else {}

        if user_input is not None:
            source = user_input[CONF_TARIFF_SOURCE]
            try:
                await self.hass.async_add_executor_job(
                    _validate, source, user_input.get(CONF_TARIFF_JSON, "")
                )
            except Exception:
                errors["base"] = "invalid_tariff"
            if not errors:
                label = "Custom tariff" if source == CUSTOM else labels.get(source, source)
                if step_id == "reconfigure":
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(),
                        data_updates=user_input,
                        title=f"Ratebook ({label})",
                    )
                return self.async_create_entry(title=f"Ratebook ({label})", data=user_input)

        d = user_input or current
        tariff_options = [SelectOptionDict(value=e["slug"], label=e["label"]) for e in bundled] + [
            SelectOptionDict(value=CUSTOM, label="Custom tariff JSON…")
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TARIFF_SOURCE,
                    default=d.get(CONF_TARIFF_SOURCE, bundled[0]["slug"] if bundled else CUSTOM),
                ): SelectSelector(
                    SelectSelectorConfig(options=tariff_options, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_TARIFF_JSON, default=d.get(CONF_TARIFF_JSON, "")): TextSelector(
                    TextSelectorConfig(multiline=True)
                ),
                vol.Optional(
                    CONF_TIER, default=str(d.get(CONF_TIER, DEFAULT_TIER))
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(TIER_CHOICES),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="tier",
                    )
                ),
                vol.Optional(
                    CONF_CHARGE_HOURS, default=d.get(CONF_CHARGE_HOURS, DEFAULT_CHARGE_HOURS)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=24)),
                vol.Optional(CONF_CURRENCY, default=d.get(CONF_CURRENCY, DEFAULT_CURRENCY)): str,
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)
