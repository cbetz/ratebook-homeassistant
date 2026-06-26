"""Pure validation helpers, shared by the engine and the extraction grader.

These encode the §6 grader checklist from ``docs/URDB_NOTES.md`` (tier partition, schedule
shape, every-referenced-period-has-a-rate, 8760-hour coverage, semantic value ranges). The
engine consumes them as pre-checks/warnings; the extraction grader will consume the same
functions for arithmetic-consistency grading, so the two can never disagree about what
"valid" means.

A :class:`Tariff` that is *malformed* already raises ``ValueError`` at construction (bad
shape, non-monotonic tiers, out-of-range period refs). These helpers cover the softer,
grader-facing checks that should report rather than crash, plus a couple that re-derive
construction guarantees so the grader can run on raw dict input independently.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from .schema import (
    COMPUTABLE_TIER_MAX_UNITS,
    DayType,
    EnergyPeriod,
    Schedule,
    Tariff,
)

Severity = Literal["error", "warn"]


@dataclass(frozen=True, slots=True)
class Issue:
    severity: Severity
    code: str
    message: str
    path: str = ""


def validate_tier_partition(period: EnergyPeriod, path: str = "") -> list[Issue]:
    """Tier maxes strictly increase, only the final tier is open, no non-positive maxes."""
    issues: list[Issue] = []
    prev: Decimal | None = None
    for i, tier in enumerate(period.tiers):
        is_final = i == len(period.tiers) - 1
        tpath = f"{path}.tiers[{i}]"
        if tier.max is None:
            if not is_final:
                issues.append(Issue("error", "open_nonfinal_tier", "non-final tier is open", tpath))
            continue
        if tier.max <= 0:
            issues.append(
                Issue("error", "nonpositive_tier_max", f"tier max {tier.max} <= 0", tpath)
            )
        if prev is not None and tier.max <= prev:
            issues.append(
                Issue(
                    "error", "nonmonotonic_tier_max", f"tier max {tier.max} <= prior {prev}", tpath
                )
            )
        prev = tier.max
    return issues


def validate_schedule_shape(schedule: Schedule, n_periods: int | None = None) -> list[Issue]:
    """Each matrix is exactly 12x24; every cell is a non-negative int in range."""
    issues: list[Issue] = []
    for name, matrix in (("weekday", schedule.weekday), ("weekend", schedule.weekend)):
        if len(matrix) != 12:
            issues.append(
                Issue("error", "schedule_rows", f"{name} has {len(matrix)} rows, want 12", name)
            )
            continue
        for m, row in enumerate(matrix):
            if len(row) != 24:
                issues.append(
                    Issue(
                        "error",
                        "schedule_cols",
                        f"{name}[{m}] has {len(row)} cols, want 24",
                        f"{name}[{m}]",
                    )
                )
                continue
            for h, cell in enumerate(row):
                if cell < 0 or (n_periods is not None and cell >= n_periods):
                    issues.append(
                        Issue(
                            "error",
                            "schedule_period_oob",
                            f"{name}[{m}][{h}] = {cell} out of range",
                            f"{name}[{m}][{h}]",
                        )
                    )
    return issues


def validate_period_coverage(tariff: Tariff) -> list[Issue]:
    """Every period the schedule references has at least one tier with a rate.

    This is the ≥15-dangling-reference check from URDB_NOTES §6: a schedule cell pointing at a
    period index that carries no priceable rate would silently misbill.
    """
    issues: list[Issue] = []
    n_periods = len(tariff.energy.periods)
    for ref in sorted(tariff.schedule.referenced_periods()):
        if ref < 0 or ref >= n_periods:
            issues.append(
                Issue(
                    "error",
                    "dangling_period",
                    f"schedule references missing period {ref}",
                    f"period[{ref}]",
                )
            )
            continue
        period = tariff.energy.periods[ref]
        if not period.tiers:
            issues.append(
                Issue(
                    "error",
                    "empty_period",
                    f"referenced period {ref} has no tiers",
                    f"period[{ref}]",
                )
            )
        elif all(t.effective_rate == Decimal(0) for t in period.tiers):
            # A schedule-referenced period that prices every kWh at $0 bills real consumption
            # at zero — usually an incomplete extraction (delivery/supply split), so flag for
            # HITL rather than silently shipping a free-electricity bill.
            issues.append(
                Issue(
                    "warn",
                    "zero_rate_period",
                    f"referenced period {ref} prices all energy at $0.00",
                    f"period[{ref}]",
                )
            )
    return issues


def validate_8760_coverage(schedule: Schedule, year: int = 2025) -> list[Issue]:
    """Every hour of ``year`` maps to a defined schedule cell (full 8,760/8,784 coverage)."""
    issues: list[Issue] = []
    hours = 0
    day = date(year, 1, 1)
    end = date(year + 1, 1, 1)
    while day < end:
        day_type = DayType.WEEKEND if day.weekday() >= 5 else DayType.WEEKDAY
        matrix = schedule.weekday if day_type is DayType.WEEKDAY else schedule.weekend
        row = matrix[day.month - 1]
        if len(row) != 24:
            issues.append(Issue("error", "coverage_gap", f"{day} month row not 24-wide", str(day)))
        hours += 24
        day += timedelta(days=1)
    expected = 8784 if calendar.isleap(year) else 8760
    if hours != expected:
        issues.append(
            Issue("error", "coverage_count", f"covered {hours} hours, want {expected}", "")
        )
    return issues


def validate_value_ranges(tariff: Tariff) -> list[Issue]:
    """Semantic range guards (numerics parse clean but run wild, per URDB_NOTES §6).

    Residential energy rate expected in [-1, 5] $/kWh; monthly fixed charge in [0, 500].
    These are warnings — negatives and outliers exist (water-heater credits, sell rates) and
    feed the HITL review queue rather than hard-failing.
    """
    issues: list[Issue] = []
    is_resi = tariff.identity.sector.value == "residential"
    for p, period in enumerate(tariff.energy.periods):
        for t, tier in enumerate(period.tiers):
            eff = tier.effective_rate
            if is_resi and (eff < Decimal(-1) or eff > Decimal(5)):
                issues.append(
                    Issue(
                        "warn",
                        "rate_out_of_range",
                        f"effective rate {eff} $/kWh",
                        f"period[{p}].tiers[{t}]",
                    )
                )
            if tier.max_unit not in COMPUTABLE_TIER_MAX_UNITS:
                issues.append(
                    Issue(
                        "warn",
                        "demand_normalized_max",
                        f"tier max unit {tier.max_unit.value} not priceable in v0",
                        f"period[{p}].tiers[{t}]",
                    )
                )
    for c, charge in enumerate(tariff.fixed_charges):
        if charge.unit.value == "$/month" and (
            charge.amount < Decimal(0) or charge.amount > Decimal(500)
        ):
            issues.append(
                Issue(
                    "warn",
                    "fixed_out_of_range",
                    f"fixed charge {charge.amount} $/month",
                    f"fixed_charges[{c}]",
                )
            )
    return issues


def validate_tariff(tariff: Tariff) -> list[Issue]:
    """Run all grader checks and return the combined issue list."""
    issues: list[Issue] = []
    for p, period in enumerate(tariff.energy.periods):
        issues.extend(validate_tier_partition(period, path=f"period[{p}]"))
    issues.extend(validate_schedule_shape(tariff.schedule, len(tariff.energy.periods)))
    issues.extend(validate_period_coverage(tariff))
    issues.extend(validate_value_ranges(tariff))
    return issues
