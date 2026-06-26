"""Pure pricing adapter over the Ratebook engine — no Home Assistant imports, fully testable.

The Home Assistant integration (``custom_components/ratebook``) is a thin shell over these
functions; keeping the logic here means the price math is unit-tested without the HA harness.
Functions take an explicit ``now``/``day`` so behavior is deterministic and timezone handling
stays in the HA layer. Prices are returned as floats (HA sensor states) computed from the
engine's exact Decimals.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from importlib import resources
from typing import Any

from ..ratebook import (
    BillingWindow,
    Tariff,
    cheapest_charge_window,
    hourly_marginal_prices,
)

# Resolve bundled tariffs relative to THIS package, so it works whether ratebook_ha is
# pip-installed or vendored into the Home Assistant integration under another package name.
_TARIFFS = resources.files(__package__).joinpath("tariffs")


def list_bundled() -> list[str]:
    """Names of the bundled example tariffs (without the .json extension)."""
    return sorted(p.name[:-5] for p in _TARIFFS.iterdir() if p.name.endswith(".json"))


def load_bundled(name: str) -> Tariff:
    return Tariff.from_json(json.loads(_TARIFFS.joinpath(f"{name}.json").read_text()))


def load_tariff(source: str | dict[str, Any]) -> Tariff:
    """Load a tariff from a JSON string or an already-parsed dict (the schema's to_json form)."""
    return Tariff.from_json(json.loads(source) if isinstance(source, str) else source)


def hourly_schedule(tariff: Tariff, day: date) -> list[dict[str, Any]]:
    """24 ``{start, price}`` entries for ``day`` — the marginal $/kWh each hour."""
    prices = hourly_marginal_prices(tariff, BillingWindow(day, 1))
    return [
        {"start": datetime.combine(day, time(h)).isoformat(), "price": float(prices[h])}
        for h in range(24)
    ]


def current_price(tariff: Tariff, now: datetime) -> float:
    """Marginal $/kWh at ``now``."""
    prices = hourly_marginal_prices(tariff, BillingWindow(now.date(), 1))
    return float(prices[now.hour])


def cheapest_window(tariff: Tariff, start: date, *, days: int, charge_hours: int) -> dict[str, Any]:
    """Cheapest contiguous ``charge_hours`` block over ``days`` from ``start`` (EV charging)."""
    cw = cheapest_charge_window(tariff, BillingWindow(start, days), charge_hours)
    return {
        "start": cw.start.isoformat(),
        "end": (cw.start + timedelta(hours=charge_hours)).isoformat(),
        "avg_rate": float(cw.avg_rate),
        "hours": charge_hours,
    }


def _hourly_rate(tariff: Tariff, ts: datetime, _cache: dict[date, Any]) -> float:
    day = ts.date()
    if day not in _cache:
        _cache[day] = hourly_marginal_prices(tariff, BillingWindow(day, 1))
    return float(_cache[day][ts.hour])


def evcc_forecast(tariff: Tariff, start: datetime, hours: int) -> list[dict[str, Any]]:
    """Hourly price forecast in evcc's custom-tariff shape: ``[{start, end, value}, ...]``.

    Timestamps are RFC3339 (``start.isoformat()`` — pass a timezone-aware ``start``); ``value``
    is the marginal $/kWh for that hour. Feed this to an evcc ``custom`` grid tariff via its
    ``http`` source (e.g. through the Home Assistant sensor's ``forecast`` attribute).
    """
    cache: dict[date, Any] = {}
    out: list[dict[str, Any]] = []
    for i in range(hours):
        ts = start + timedelta(hours=i)
        out.append(
            {
                "start": ts.isoformat(),
                "end": (ts + timedelta(hours=1)).isoformat(),
                "value": _hourly_rate(tariff, ts, cache),
            }
        )
    return out


def emhass_cost_forecast(tariff: Tariff, start: datetime, hours: int) -> list[float]:
    """Hourly marginal prices as an EMHASS ``load_cost_forecast`` list — current period first."""
    cache: dict[date, Any] = {}
    return [_hourly_rate(tariff, start + timedelta(hours=i), cache) for i in range(hours)]
