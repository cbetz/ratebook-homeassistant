"""Constants for the Ratebook Home Assistant integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "ratebook"
LOGGER: Final = logging.getLogger(__package__)

CONF_TARIFF_SOURCE: Final = "tariff_source"  # a bundled tariff name, or "custom"
CONF_TARIFF_JSON: Final = "tariff_json"
CONF_CHARGE_HOURS: Final = "charge_hours"
CONF_CURRENCY: Final = "currency"
CONF_TIER: Final = "tier"  # 1-based tier to price at ("1" = baseline)

CUSTOM: Final = "custom"
DEFAULT_CHARGE_HOURS: Final = 4
DEFAULT_CURRENCY: Final = "USD"
DEFAULT_TIER: Final = "1"
TIER_CHOICES: Final = ("1", "2", "3")
