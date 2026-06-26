"""Import a URDB v8 JSON record into a :class:`~ratebook.schema.Tariff`.

This is pure (``dict -> Tariff``), so it lives next to the schema and is shared by the data
plant and the PySAM validation harness. The matching un-flattener that turns a flat
``raw.urdb`` CSV row into this v8 JSON shape lives in ``ratebook_data.urdb`` (it is corpus
knowledge, not schema knowledge).

URDB v8 energy shape (the part this importer reads):
``energyratestructure``: ``[[{rate, adj, max, unit, sell}, ...tiers], ...periods]``;
``energyweekdayschedule`` / ``energyweekendschedule``: 12x24 int matrices;
plus scalar ``fixedchargefirstmeter`` / ``fixedchargeunits``, ``mincharge`` /
``minchargeunits``, ``dgrules``, and (presence-only here) the demand families.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .schema import (
    EffectiveRange,
    EnergyPeriod,
    EnergyRateStructure,
    EnergyTier,
    FixedCharge,
    FixedChargeUnit,
    MeteringOption,
    MinCharge,
    MinChargeUnit,
    Provenance,
    Schedule,
    Sector,
    Tariff,
    TariffIdentity,
    TierMaxUnit,
    UnsupportedFeature,
    UnsupportedKind,
)

_SECTOR = {
    "residential": Sector.RESIDENTIAL,
    "commercial": Sector.COMMERCIAL,
    "industrial": Sector.INDUSTRIAL,
    "lighting": Sector.LIGHTING,
}
_DG = {
    "net metering": MeteringOption.NET_METERING,
    "net billing": MeteringOption.NET_BILLING,
    "buy all sell all": MeteringOption.BUY_ALL_SELL_ALL,
}
_FIXED_UNIT = {"$/month": FixedChargeUnit.PER_MONTH, "$/day": FixedChargeUnit.PER_DAY}
_MIN_UNIT = {
    "$/month": MinChargeUnit.PER_MONTH,
    "$/day": MinChargeUnit.PER_DAY,
    "$/year": MinChargeUnit.PER_YEAR,
}


class UrdbImportError(ValueError):
    """Raised when a URDB record cannot be represented as a v0 Tariff."""


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _safe_date(value: Any) -> date | None:
    """Parse a URDB date with the URDB_NOTES §6 guards: reject 2-digit-year and pre-1990
    sentinels (epoch-zero, 1969/1970 "unknown" markers)."""
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    head = text.split(" ", 1)[0]
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text) if " " in text else datetime.fromisoformat(head)
    except ValueError:
        # Recover the 2-digit-year enddates (e.g. "24-12-30") that URDB_NOTES §6 flags: naive
        # parsing reads them as year 24 AD; %y maps 00-68 -> 2000s, 69-99 -> 1900s. Without
        # this an ended tariff would import as active (no enddate signal).
        for fmt in ("%Y-%m-%d", "%y-%m-%d"):
            try:
                parsed = datetime.strptime(head, fmt)
                break
            except ValueError:
                continue
    if parsed is None or parsed.year < 1990:  # drop epoch-zero / pre-1990 "unknown" sentinels
        return None
    return parsed.date()


def _tier_max_unit(unit: Any) -> TierMaxUnit:
    if not unit:
        return TierMaxUnit.KWH
    try:
        return TierMaxUnit(str(unit))
    except ValueError:
        return TierMaxUnit.KWH


def _structure_has_rate(structure: Any) -> bool:
    """True if a demand/flat-demand structure carries a real charge.

    A charge may live in ``rate`` OR ``adj`` (an adjustment-only demand charge is still a
    demand charge), so both count as presence — otherwise an adj-only demand structure would
    be silently dropped and the engine would report the tariff as fully supported.
    """
    if not isinstance(structure, list):
        return False
    for period in structure:
        for tier in period if isinstance(period, list) else []:
            if not isinstance(tier, dict):
                continue
            if _dec(tier.get("rate")) not in (None, Decimal(0)):
                return True
            if _dec(tier.get("adj")) not in (None, Decimal(0)):
                return True
    return False


def _energy_from_v8(structure: list) -> EnergyRateStructure:
    periods: list[EnergyPeriod] = []
    for p_idx, period in enumerate(structure):
        tiers: list[EnergyTier] = []
        for t_idx, tier in enumerate(period):
            rate = _dec(tier.get("rate"))
            if rate is None:
                # A tier with no rate is unpriceable; surface rather than guess a zero.
                raise UrdbImportError(f"period {p_idx} tier {t_idx} has no rate")
            tiers.append(
                EnergyTier(
                    rate=rate,
                    adj=_dec(tier.get("adj")) or Decimal(0),
                    max=_dec(tier.get("max")),
                    max_unit=_tier_max_unit(tier.get("unit")),
                    sell=_dec(tier.get("sell")),
                )
            )
        periods.append(EnergyPeriod(tiers=tuple(tiers)))
    return EnergyRateStructure(periods=tuple(periods))


def _schedule_matrix(raw: Any, name: str) -> tuple[tuple[int, ...], ...]:
    if not isinstance(raw, list) or len(raw) != 12:
        raise UrdbImportError(f"{name} is not a 12-row matrix")
    return tuple(tuple(int(h) for h in row) for row in raw)


def tariff_from_v8(v8: dict[str, Any]) -> Tariff:
    """Build a :class:`Tariff` from a URDB v8 JSON record.

    Raises :class:`UrdbImportError` for records that cannot be a v0 Tariff (missing energy
    structure, malformed schedule, unpriceable tier). Demand and flat-demand structures are
    *carried* as :class:`UnsupportedFeature` markers so the engine refuses rather than
    silently dropping them.
    """
    structure = v8.get("energyratestructure")
    if not isinstance(structure, list) or not structure:
        raise UrdbImportError("record has no energyratestructure")
    energy = _energy_from_v8(structure)

    schedule = Schedule(
        weekday=_schedule_matrix(v8.get("energyweekdayschedule"), "energyweekdayschedule"),
        weekend=_schedule_matrix(v8.get("energyweekendschedule"), "energyweekendschedule"),
    )

    fixed_charges: list[FixedCharge] = []
    fcm = _dec(v8.get("fixedchargefirstmeter"))
    if fcm is not None:
        unit = _FIXED_UNIT.get(
            str(v8.get("fixedchargeunits", "")).lower(), FixedChargeUnit.PER_MONTH
        )
        fixed_charges.append(FixedCharge(fcm, unit))

    min_charge = None
    mc = _dec(v8.get("mincharge"))
    if mc is not None:
        munit = _MIN_UNIT.get(str(v8.get("minchargeunits", "")).lower(), MinChargeUnit.PER_MONTH)
        min_charge = MinCharge(mc, munit)

    unsupported: list[UnsupportedFeature] = []
    if _structure_has_rate(v8.get("demandratestructure")):
        unsupported.append(
            UnsupportedFeature(UnsupportedKind.TOU_DEMAND, "demandratestructure present")
        )
    if _structure_has_rate(v8.get("flatdemandstructure")):
        unsupported.append(
            UnsupportedFeature(UnsupportedKind.FLAT_DEMAND, "flatdemandstructure present")
        )
    if _structure_has_rate(v8.get("coincidentratestructure")):
        unsupported.append(
            UnsupportedFeature(UnsupportedKind.COINCIDENT_DEMAND, "coincidentratestructure present")
        )

    dg = str(v8.get("dgrules", "")).strip().lower()
    metering = _DG.get(dg, MeteringOption.NONE if not dg else MeteringOption.UNKNOWN)

    eiaid_raw = v8.get("eiaid")
    eiaid = None
    if eiaid_raw not in (None, ""):
        try:
            eiaid = int(float(eiaid_raw))  # strip ".0" float artifacts (URDB_NOTES §4)
        except (ValueError, TypeError):
            eiaid = None

    identity = TariffIdentity(
        utility_id=str(v8.get("utility", "")),
        eiaid=eiaid,
        plan_code=str(v8.get("rateno", "") or ""),
        plan_name=str(v8.get("name", "")),
        sector=_SECTOR.get(str(v8.get("sector", "")).lower(), Sector.UNKNOWN),
    )
    effective = EffectiveRange(
        start=_safe_date(v8.get("startdate")), end=_safe_date(v8.get("enddate"))
    )
    provenance = Provenance(
        urdb_label=v8.get("label"),
        urdb_latest_update=_safe_date(v8.get("latest_update")),
    )

    return Tariff(
        energy=energy,
        schedule=schedule,
        identity=identity,
        effective_range=effective,
        fixed_charges=tuple(fixed_charges),
        min_charge=min_charge,
        unsupported=tuple(unsupported),
        metering=metering,
        provenance=provenance,
    )
