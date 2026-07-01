"""The Ratebook rate engine: pure, deterministic bill computation.

Public surface:

- :func:`estimate_bill` — one billing window → :class:`BillResult`.
- :func:`estimate_annual` — an 8,760-hour load over a calendar year (12 monthly windows).
- :func:`supported` — whether the engine can price a tariff, without computing.

The single accounting abstraction is the :class:`BillingWindow`: a start date plus an
explicit integer day count, over which **tiers reset exactly once**. A real utility bill is
one window; PySAM cross-validation is twelve calendar-month windows. There is no separate
"calendar vs billing" branch — only the window-list generator differs.

"Unknown" is a first-class answer: an unpriceable-but-well-formed tariff/usage combination
returns ``BillResult(ok=False, refusal=...)`` with ``total is None`` — never a wrong number.
Malformed tariffs raise at construction; caller/usage bugs raise ``ValueError``.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from functools import cache
from math import ceil
from typing import Any

from .money import ZERO, decimal_to_json, to_decimal
from .schema import (
    COMPUTABLE_TIER_MAX_UNITS,
    REFUSING_UNSUPPORTED_KINDS,
    DayType,
    EnergyPeriod,
    FixedChargeUnit,
    Holiday,
    HolidayObservance,
    HolidayPolicy,
    MinChargeUnit,
    Tariff,
    TierMaxUnit,
    UnsupportedKind,
)


class RefusalReason(StrEnum):
    DEMAND_CHARGE = "demand_charge"
    RIDER = "rider"
    UNMODELABLE = "unmodelable"
    DEMAND_NORMALIZED_TIER_MAX = "demand_normalized_tier_max"
    AGGREGATE_USAGE_MULTI_PERIOD = "aggregate_usage_multi_period"
    ANNUAL_MIN_SINGLE_WINDOW = "annual_min_single_window"


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: RefusalReason
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"reason": self.reason.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class BillingWindow:
    """A billing period: a start date and an explicit day count. Tiers reset once per window.

    ``days`` is required and never inferred — ``$/day`` fixed/min charges and ``kWh daily``
    tier maxes depend on it. Use :meth:`from_dates` with an **exclusive** end date.
    """

    start: date
    days: int

    def __post_init__(self) -> None:
        if self.days <= 0:
            raise ValueError(f"window days must be positive, got {self.days}")

    @classmethod
    def from_dates(cls, start: date, end_exclusive: date) -> BillingWindow:
        days = (end_exclusive - start).days
        if days <= 0:
            raise ValueError(f"end {end_exclusive} must be after start {start}")
        return cls(start=start, days=days)

    @property
    def hours(self) -> int:
        return self.days * 24

    def iter_days(self):
        for offset in range(self.days):
            yield self.start + timedelta(days=offset)

    def to_json(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "days": self.days}


@dataclass(frozen=True, slots=True)
class Usage:
    """Consumption for a window: EITHER an hour-aligned load OR a single aggregate total.

    ``hourly_kwh`` (length must equal the window's hours) supports any tariff. ``total_kwh``
    (a pasted bill number) is sufficient only when the window resolves to a single effective
    period — flat, tiered-non-TOU, or seasonal landing in one season. Exactly one is set.

    Usage is consumption only (kWh ≥ 0, no export); the engine never applies sell rates.
    """

    hourly_kwh: tuple[Decimal, ...] | None = None
    total_kwh: Decimal | None = None

    def __post_init__(self) -> None:
        if (self.hourly_kwh is None) == (self.total_kwh is None):
            raise ValueError("Usage requires exactly one of hourly_kwh or total_kwh")

    @classmethod
    def hourly(cls, values) -> Usage:
        return cls(hourly_kwh=tuple(to_decimal(v) for v in values))

    @classmethod
    def aggregate(cls, total) -> Usage:
        return cls(total_kwh=to_decimal(total))


@dataclass(frozen=True, slots=True)
class LineItem:
    period: int
    tier: int
    kwh: Decimal
    rate: Decimal  # effective rate actually charged (rate + adj)
    subtotal: Decimal
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "tier": self.tier,
            "kwh": decimal_to_json(self.kwh),
            "rate": decimal_to_json(self.rate),
            "subtotal": decimal_to_json(self.subtotal),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class BillResult:
    ok: bool
    total: Decimal | None
    energy_charge: Decimal = ZERO
    fixed_charge: Decimal = ZERO
    min_charge_floor_applied: bool = False
    line_items: tuple[LineItem, ...] = ()
    window: BillingWindow | None = None
    warnings: tuple[str, ...] = ()
    refusal: Refusal | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "total": decimal_to_json(self.total),
            "energy_charge": decimal_to_json(self.energy_charge),
            "fixed_charge": decimal_to_json(self.fixed_charge),
            "min_charge_floor_applied": self.min_charge_floor_applied,
            "line_items": [li.to_json() for li in self.line_items],
            "window": self.window.to_json() if self.window else None,
            "warnings": list(self.warnings),
            "refusal": self.refusal.to_json() if self.refusal else None,
        }


@dataclass(frozen=True, slots=True)
class AnnualResult:
    ok: bool
    total: Decimal | None
    energy_charge: Decimal = ZERO
    fixed_charge: Decimal = ZERO
    windows: tuple[BillResult, ...] = ()
    warnings: tuple[str, ...] = ()
    refusal: Refusal | None = None


@dataclass(frozen=True, slots=True)
class SupportReport:
    fully_supported: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------------------
# Support analysis (no computation)
# --------------------------------------------------------------------------------------
def supported(tariff: Tariff, *, single_window: bool = True) -> SupportReport:
    """What the engine can/can't price, without running a computation.

    With ``single_window`` (the bill-match path), a ``$/year`` minimum is reported as
    unsupported because it cannot be allocated to one window; ``estimate_annual`` handles it,
    so pass ``single_window=False`` to reflect the annual path.
    """
    reasons: list[str] = []
    for feat in tariff.unsupported:
        if feat.kind in REFUSING_UNSUPPORTED_KINDS:
            reasons.append(f"{feat.kind.value}: {feat.detail}".rstrip(": "))
    for p, period in enumerate(tariff.energy.periods):
        for t, tier in enumerate(period.tiers):
            if tier.max_unit not in COMPUTABLE_TIER_MAX_UNITS:
                reasons.append(f"demand_normalized_tier_max at period {p} tier {t}")
    mc = tariff.min_charge
    if single_window and mc is not None and mc.unit is MinChargeUnit.PER_YEAR:
        reasons.append("annual_min_single_window (use estimate_annual)")
    return SupportReport(fully_supported=not reasons, reasons=tuple(reasons))


# --------------------------------------------------------------------------------------
# Holidays
# --------------------------------------------------------------------------------------
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th (1-based) given weekday (Mon=0) of a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


_HOLIDAY_RULE = {
    Holiday.NEW_YEARS_DAY: lambda y: date(y, 1, 1),
    Holiday.MLK_DAY: lambda y: _nth_weekday(y, 1, 0, 3),
    Holiday.WASHINGTONS_BIRTHDAY: lambda y: _nth_weekday(y, 2, 0, 3),
    Holiday.MEMORIAL_DAY: lambda y: _last_weekday(y, 5, 0),
    Holiday.JUNETEENTH: lambda y: date(y, 6, 19),
    Holiday.INDEPENDENCE_DAY: lambda y: date(y, 7, 4),
    Holiday.LABOR_DAY: lambda y: _nth_weekday(y, 9, 0, 1),
    Holiday.COLUMBUS_DAY: lambda y: _nth_weekday(y, 10, 0, 2),
    Holiday.VETERANS_DAY: lambda y: date(y, 11, 11),
    Holiday.THANKSGIVING: lambda y: _nth_weekday(y, 11, 3, 4),
    Holiday.DAY_AFTER_THANKSGIVING: lambda y: _nth_weekday(y, 11, 3, 4) + timedelta(days=1),
    Holiday.CHRISTMAS: lambda y: date(y, 12, 25),
}


@cache
def holiday_dates(
    year: int,
    holidays: tuple[Holiday, ...],
    observance: HolidayObservance = HolidayObservance.SUNDAY_TO_MONDAY,
) -> frozenset[date]:
    """The calendar dates the named ``holidays`` land on in ``year``.

    With ``sunday_to_monday`` observance a Sunday holiday also marks the following Monday
    (the prevailing utility rule); the Sunday itself already prices as a weekend day.
    """
    out: set[date] = set()
    for h in holidays:
        d = _HOLIDAY_RULE[h](year)
        out.add(d)
        if observance is HolidayObservance.SUNDAY_TO_MONDAY and d.weekday() == 6:
            out.add(d + timedelta(days=1))
    return frozenset(out)


# --------------------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------------------
def _day_type(tariff: Tariff, day: date) -> DayType:
    sched = tariff.schedule
    if (
        sched.holiday_policy is HolidayPolicy.AS_WEEKEND
        and sched.holidays
        and day in holiday_dates(day.year, sched.holidays, sched.holiday_observance)
    ):
        return DayType.WEEKEND
    # ``as_weekday`` and ``unknown`` price every day by its real weekday/weekend.
    return DayType.WEEKEND if day.weekday() >= 5 else DayType.WEEKDAY


def _period_at(tariff: Tariff, day: date, hour: int) -> int:
    return tariff.schedule.period_at(_day_type(tariff, day), day.month, hour)


def _period_active_days(tariff: Tariff, window: BillingWindow) -> dict[int, int]:
    """Per period, the number of distinct days in the window on which it appears.

    This is the correct multiplier for ``kWh daily`` tier caps: a daily allowance accrues
    only on days the period is actually in effect (a weekday-only period accrues on ~22 days
    of a month, a summer period on 0 days of a winter window), matching PySAM. Keys are the
    set of periods the window touches.
    """
    days_seen: dict[int, set[date]] = {}
    for day in window.iter_days():
        for hour in range(24):
            days_seen.setdefault(_period_at(tariff, day, hour), set()).add(day)
    return {p: len(ds) for p, ds in days_seen.items()}


_UNSUPPORTED_REASON = {
    UnsupportedKind.DEMAND_CHARGE: RefusalReason.DEMAND_CHARGE,
    UnsupportedKind.TOU_DEMAND: RefusalReason.DEMAND_CHARGE,
    UnsupportedKind.FLAT_DEMAND: RefusalReason.DEMAND_CHARGE,
    UnsupportedKind.COINCIDENT_DEMAND: RefusalReason.DEMAND_CHARGE,
    UnsupportedKind.RIDER: RefusalReason.RIDER,
    UnsupportedKind.DEMAND_NORMALIZED_TIER_MAX: RefusalReason.DEMAND_NORMALIZED_TIER_MAX,
    UnsupportedKind.UNMODELABLE: RefusalReason.UNMODELABLE,
}


def _refuse_for_unsupported(tariff: Tariff, used_periods: set[int]) -> Refusal | None:
    for feat in tariff.unsupported:
        if feat.kind in REFUSING_UNSUPPORTED_KINDS:
            reason = _UNSUPPORTED_REASON.get(feat.kind, RefusalReason.UNMODELABLE)
            return Refusal(reason, feat.detail or feat.kind.value)
    for p in used_periods:
        for t, tier in enumerate(tariff.energy.periods[p].tiers):
            if tier.max_unit not in COMPUTABLE_TIER_MAX_UNITS:
                return Refusal(
                    RefusalReason.DEMAND_NORMALIZED_TIER_MAX,
                    f"period {p} tier {t} uses {tier.max_unit.value}",
                )
    return None


def _ladder_key(period: EnergyPeriod) -> tuple:
    """A value key for a period's tier ladder, for detecting identically-priced periods."""
    return tuple((t.effective_rate, t.max, t.max_unit) for t in period.tiers)


def _has_daily_tier(period: EnergyPeriod) -> bool:
    return any(t.max_unit is TierMaxUnit.KWH_DAILY for t in period.tiers)


def _warnings_for(tariff: Tariff) -> tuple[str, ...]:
    out: list[str] = []
    for feat in tariff.unsupported:
        if feat.kind in (UnsupportedKind.NET_METERING, UnsupportedKind.SELL_RATE):
            out.append(f"{feat.kind.value}_not_modeled")
    if tariff.metering in (tariff.metering.NET_METERING, tariff.metering.NET_BILLING):
        out.append("net_metering_not_modeled")
    if any(t.sell for p in tariff.energy.periods for t in p.tiers):
        out.append("sell_rate_not_modeled")
    if tariff.schedule.holiday_policy is HolidayPolicy.AS_WEEKEND and not tariff.schedule.holidays:
        # The rate sheet defines holiday treatment but the dates aren't enumerated yet, so
        # holidays still price on the regular weekday schedule.
        out.append("holidays_not_enumerated")
    # Stable, de-duplicated ordering for deterministic output.
    return tuple(dict.fromkeys(out))


def _price_tiers(
    period_kwh: Decimal, tariff: Tariff, period: int, active_days: int
) -> tuple[list[LineItem], Decimal, bool]:
    """Apply one period's independent tier ladder to its accumulated kWh.

    ``active_days`` scales ``kWh daily`` tier caps (the days this period was in effect).
    Returns ``(line_items, charge, exceeded_final_max)``; the last flag is set when usage
    runs past a finite final-tier max — the final tier is treated as open (URDB/PySAM
    convention), so this is surfaced as a warning rather than silently extrapolated.
    """
    items: list[LineItem] = []
    charge = ZERO
    remaining = period_kwh
    prior_max = ZERO
    exceeded_final = False
    tiers = tariff.energy.periods[period].tiers

    def boundary_of(tier) -> Decimal:
        if tier.max_unit is TierMaxUnit.KWH_DAILY:
            return tier.max * Decimal(active_days)
        return tier.max

    for t, tier in enumerate(tiers):
        if remaining <= 0:
            break
        is_final = t == len(tiers) - 1
        if tier.max is None:
            slice_kwh = remaining
        elif is_final:
            # The final tier is open; a finite max on it is informational (flag, don't cap).
            if prior_max + remaining > boundary_of(tier):
                exceeded_final = True
            slice_kwh = remaining
        else:
            cap = boundary_of(tier) - prior_max
            if cap < ZERO:  # defensive: uniform-unit construction guard should prevent this
                cap = ZERO
            slice_kwh = remaining if remaining < cap else cap
            prior_max = boundary_of(tier)
        eff = tier.effective_rate
        subtotal = slice_kwh * eff
        items.append(LineItem(period, t, slice_kwh, eff, subtotal))
        charge += subtotal
        remaining -= slice_kwh
    return items, charge, exceeded_final


def _price_window(
    tariff: Tariff, usage: Usage, window: BillingWindow, *, is_annual: bool
) -> BillResult:
    # Caller-input validation comes first: a wrong-length hourly array is a programmer bug
    # and must raise regardless of whether the tariff is otherwise refusable.
    if usage.hourly_kwh is not None and len(usage.hourly_kwh) != window.hours:
        raise ValueError(
            f"hourly_kwh has {len(usage.hourly_kwh)} values, window needs {window.hours}"
        )

    active_days = _period_active_days(tariff, window)
    used_periods = set(active_days)

    refusal = _refuse_for_unsupported(tariff, used_periods)
    if refusal is not None:
        return BillResult(ok=False, total=None, window=window, refusal=refusal)

    if (
        not is_annual
        and tariff.min_charge is not None
        and tariff.min_charge.unit is MinChargeUnit.PER_YEAR
    ):
        return BillResult(
            ok=False,
            total=None,
            window=window,
            refusal=Refusal(
                RefusalReason.ANNUAL_MIN_SINGLE_WINDOW,
                "$/year minimum cannot be allocated to one window; use estimate_annual",
            ),
        )

    # Step 1: accumulate kWh per period.
    period_kwh: dict[int, Decimal] = {p: ZERO for p in range(len(tariff.energy.periods))}
    if usage.hourly_kwh is not None:
        i = 0
        for day in window.iter_days():
            for hour in range(24):
                period_kwh[_period_at(tariff, day, hour)] += usage.hourly_kwh[i]
                i += 1
    else:
        assert usage.total_kwh is not None
        used = sorted(used_periods)
        distinct_ladders = {_ladder_key(tariff.energy.periods[p]) for p in used}
        touches_daily = any(_has_daily_tier(tariff.energy.periods[p]) for p in used)
        # An aggregate total is sufficient when the window resolves to a single period, or to
        # several periods that price identically (e.g. a TOU plan whose peak == off-peak) —
        # except identical kWh-daily ladders, whose caps depend on each period's active days.
        if len(used) == 1 or (len(distinct_ladders) == 1 and not touches_daily):
            period_kwh[used[0]] = usage.total_kwh
        else:
            return BillResult(
                ok=False,
                total=None,
                window=window,
                refusal=Refusal(
                    RefusalReason.AGGREGATE_USAGE_MULTI_PERIOD,
                    f"window touches {len(distinct_ladders)} distinct price periods; "
                    "supply hourly load",
                ),
            )

    # Step 2: tiers per period.
    line_items: list[LineItem] = []
    energy_charge = ZERO
    extra_warnings: list[str] = []
    for p in sorted(period_kwh):
        if period_kwh[p] <= 0:
            continue
        items, charge, exceeded_final = _price_tiers(
            period_kwh[p], tariff, p, active_days.get(p, window.days)
        )
        line_items.extend(items)
        energy_charge += charge
        if exceeded_final:
            extra_warnings.append("usage_exceeds_final_tier_max")

    # Step 3: fixed charges.
    fixed_charge = ZERO
    for fc in tariff.fixed_charges:
        amount = (
            fc.amount if fc.unit is FixedChargeUnit.PER_MONTH else fc.amount * Decimal(window.days)
        )
        fixed_charge += amount
        line_items.append(LineItem(-1, -1, ZERO, ZERO, amount, note=f"fixed {fc.unit.value}"))

    subtotal = energy_charge + fixed_charge

    # Step 4: minimum charge floor ($/year handled only in estimate_annual).
    floor_applied = False
    total = subtotal
    mc = tariff.min_charge
    if mc is not None and mc.unit is not MinChargeUnit.PER_YEAR:
        floor = (
            mc.amount if mc.unit is MinChargeUnit.PER_MONTH else mc.amount * Decimal(window.days)
        )
        if floor > subtotal:
            total = floor
            floor_applied = True
            line_items.append(
                LineItem(-1, -1, ZERO, ZERO, floor - subtotal, note="min charge floor")
            )

    return BillResult(
        ok=True,
        total=total,
        energy_charge=energy_charge,
        fixed_charge=fixed_charge,
        min_charge_floor_applied=floor_applied,
        line_items=tuple(line_items),
        window=window,
        warnings=tuple(dict.fromkeys((*_warnings_for(tariff), *extra_warnings))),
    )


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------
def estimate_bill(tariff: Tariff, usage: Usage, window: BillingWindow) -> BillResult:
    """Price one billing window. Tiers reset once over the window."""
    return _price_window(tariff, usage, window, is_annual=False)


def estimate_annual(tariff: Tariff, hourly_kwh_8760, year: int) -> AnnualResult:
    """Price a full calendar year as twelve calendar-month windows (PySAM-parity mode).

    ``hourly_kwh_8760`` is an hour-aligned load starting at Jan 1 00:00 of ``year`` (8,760
    values, or 8,784 in a leap year). Tiers reset each calendar month. A ``$/year`` minimum
    is applied once against the annual sum.
    """
    load = tuple(to_decimal(v) for v in hourly_kwh_8760)
    expected = 8784 if calendar.isleap(year) else 8760
    if len(load) != expected:
        raise ValueError(f"hourly load has {len(load)} values, year {year} needs {expected}")

    windows: list[BillResult] = []
    energy_total = ZERO
    fixed_total = ZERO
    grand_total = ZERO
    all_warnings: list[str] = []
    offset = 0
    for month in range(1, 13):
        days = calendar.monthrange(year, month)[1]
        window = BillingWindow(date(year, month, 1), days)
        slice_load = load[offset : offset + days * 24]
        offset += days * 24
        result = _price_window(tariff, Usage(hourly_kwh=slice_load), window, is_annual=True)
        if not result.ok:
            return AnnualResult(ok=False, total=None, refusal=result.refusal)
        windows.append(result)
        energy_total += result.energy_charge
        fixed_total += result.fixed_charge
        grand_total += result.total or ZERO
        all_warnings.extend(result.warnings)

    mc = tariff.min_charge
    if mc is not None and mc.unit is MinChargeUnit.PER_YEAR and mc.amount > grand_total:
        grand_total = mc.amount

    return AnnualResult(
        ok=True,
        total=grand_total,
        energy_charge=energy_total,
        fixed_charge=fixed_total,
        windows=tuple(windows),
        warnings=tuple(dict.fromkeys(all_warnings)),
    )


# --------------------------------------------------------------------------------------
# Charge-window optimization (the "when should I charge?" half of the thesis)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ChargeWindow:
    start: datetime
    hours: int
    avg_rate: Decimal  # average marginal $/kWh over the block
    hourly_rates: tuple[Decimal, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "hours": self.hours,
            "avg_rate": decimal_to_json(self.avg_rate),
            "hourly_rates": [decimal_to_json(r) for r in self.hourly_rates],
        }


def period_at(tariff: Tariff, day: date, hour: int) -> int:
    """Public: the energy period index a tariff assigns to ``(day, hour)``."""
    return _period_at(tariff, day, hour)


def hourly_marginal_prices(
    tariff: Tariff, window: BillingWindow, *, tier: int = 0
) -> tuple[Decimal, ...]:
    """Per-hour marginal energy price ($/kWh) over the window — the TOU price signal.

    Returns the effective rate of the period each hour maps to, at the given tier index
    (clamped to the period's tier count). v0 uses an energy-only marginal price; demand charges
    and tier position relative to the customer's baseline usage are not modeled, so this is the
    time-of-use signal for *when* to charge, not an exact incremental cost.
    """
    prices: list[Decimal] = []
    for day in window.iter_days():
        for hour in range(24):
            tiers = tariff.energy.periods[_period_at(tariff, day, hour)].tiers
            prices.append(tiers[max(0, min(tier, len(tiers) - 1))].effective_rate)
    return tuple(prices)


def cheapest_charge_window(
    tariff: Tariff,
    window: BillingWindow,
    charge_hours: int,
    *,
    tier: int = 0,
    not_before: datetime | None = None,
) -> ChargeWindow:
    """Find the cheapest contiguous ``charge_hours``-long block in the window to add load.

    Minimizing the block's average marginal price minimizes the cost of charging a fixed amount
    of energy spread uniformly over the block. Ties resolve to the earliest start.

    When ``not_before`` (a naive local datetime) is given, only blocks starting at or after it —
    rounded up to the next whole hour — are considered, so a "when should I charge next?" caller
    never gets a window that has already passed. If no such block fits in the window, the latest
    block that does is returned.
    """
    prices = hourly_marginal_prices(tariff, window, tier=tier)
    if charge_hours <= 0 or charge_hours > len(prices):
        raise ValueError(
            f"charge_hours {charge_hours} out of range for a {len(prices)}-hour window"
        )
    window_start = datetime.combine(window.start, time())
    last_start = len(prices) - charge_hours
    first_start = 0
    if not_before is not None:
        offset_hours = (not_before.replace(tzinfo=None) - window_start).total_seconds() / 3600
        first_start = min(max(0, ceil(offset_hours)), last_start)
    best_start, best_sum = first_start, None
    for i in range(first_start, last_start + 1):
        block_sum = sum(prices[i : i + charge_hours], ZERO)
        if best_sum is None or block_sum < best_sum:
            best_sum, best_start = block_sum, i
    start_dt = window_start + timedelta(hours=best_start)
    return ChargeWindow(
        start=start_dt,
        hours=charge_hours,
        avg_rate=best_sum / Decimal(charge_hours),
        hourly_rates=prices[best_start : best_start + charge_hours],
    )
