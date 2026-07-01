"""Ratebook tariff schema v0 — frozen, JSON-round-tripping value objects.

This is the data model the rate engine consumes, the data plant produces, and the future
TypeScript port mirrors via shared JSON test vectors. It deliberately uses only the standard
library: frozen ``@dataclass(slots=True)`` value objects (immutable, hashable) plus
``StrEnum`` closed vocabularies, with hand-written ``to_json``/``from_json``. No pydantic
(runtime dependency + coercion magic), no bare ``TypedDict`` (no immutability, no
construction-time validation).

The priced core mirrors PySAM ``utilityrate5``'s ``ur_ec_tou_mat`` one-for-one so
cross-validation is apples-to-apples: a tariff is a list of periods, each a list of tiers,
indexed by two 12x24 weekday/weekend schedule matrices. Structures the v0 engine cannot
price (demand, net metering, riders) are *carried* as :class:`UnsupportedFeature` markers,
never silently dropped.

Malformed structure (bad schedule shape, non-monotonic tiers, out-of-range period
references) raises ``ValueError`` at construction. Well-formed-but-unpriceable structures are
the engine's compute-time concern, not the schema's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .money import (
    date_from_json,
    date_to_json,
    decimal_to_json,
    opt_decimal,
    to_decimal,
)

SCHEMA_VERSION = 0


# --------------------------------------------------------------------------------------
# Closed vocabularies
# --------------------------------------------------------------------------------------
class FixedChargeUnit(StrEnum):
    PER_MONTH = "$/month"
    PER_DAY = "$/day"


class MinChargeUnit(StrEnum):
    PER_MONTH = "$/month"
    PER_DAY = "$/day"
    PER_YEAR = "$/year"


class TierMaxUnit(StrEnum):
    KWH = "kWh"
    KWH_DAILY = "kWh daily"
    KWH_PER_KW = "kWh/kW"
    KWH_PER_KVA = "kWh/kVA"
    KWH_PER_HP = "kWh/hp"
    KWH_PER_KW_DAILY = "kWh/kW daily"


#: Tier-max units the v0 engine can price directly. The rest are demand-normalized and need
#: a demand value the engine does not model — they produce a compute-time refusal.
COMPUTABLE_TIER_MAX_UNITS = frozenset({TierMaxUnit.KWH, TierMaxUnit.KWH_DAILY})


class DayType(StrEnum):
    WEEKDAY = "weekday"
    WEEKEND = "weekend"


class HolidayPolicy(StrEnum):
    #: URDB carries no holiday dimension; ``unknown`` treats every day by its real
    #: weekday/weekend. ``as_weekend`` prices the tariff's enumerated ``holidays`` on the
    #: weekend schedule (the common US TOU rule: "holidays are off-peak"). ``as_weekday``
    #: is the explicit no-op — the rate sheet says holidays get no special treatment.
    UNKNOWN = "unknown"
    AS_WEEKEND = "as_weekend"
    AS_WEEKDAY = "as_weekday"


class Holiday(StrEnum):
    """Named US holidays a rate sheet can reference — computed per-year by the engine.

    A closed vocabulary rather than raw dates so a tariff stays valid every year without
    data churn (Memorial Day, Thanksgiving, etc. move; the *rule* doesn't).
    """

    NEW_YEARS_DAY = "new_years_day"
    MLK_DAY = "mlk_day"
    WASHINGTONS_BIRTHDAY = "washingtons_birthday"
    MEMORIAL_DAY = "memorial_day"
    JUNETEENTH = "juneteenth"
    INDEPENDENCE_DAY = "independence_day"
    LABOR_DAY = "labor_day"
    COLUMBUS_DAY = "columbus_day"
    VETERANS_DAY = "veterans_day"
    THANKSGIVING = "thanksgiving"
    DAY_AFTER_THANKSGIVING = "day_after_thanksgiving"
    CHRISTMAS = "christmas"


class HolidayObservance(StrEnum):
    #: ``sunday_to_monday`` — a holiday falling on Sunday is also observed the following
    #: Monday (the prevailing utility rule, e.g. PG&E/SCE: "the dates on which the holidays
    #: are legally observed"). ``actual_day`` — only the calendar date itself.
    SUNDAY_TO_MONDAY = "sunday_to_monday"
    ACTUAL_DAY = "actual_day"


class Sector(StrEnum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    INDUSTRIAL = "industrial"
    LIGHTING = "lighting"
    UNKNOWN = "unknown"


class MeteringOption(StrEnum):
    NONE = "none"
    NET_METERING = "net_metering"
    NET_BILLING = "net_billing"
    BUY_ALL_SELL_ALL = "buy_all_sell_all"
    UNKNOWN = "unknown"


class TariffType(StrEnum):
    BUNDLED = "bundled"
    DELIVERY_ONLY = "delivery_only"
    SUPPLY_ONLY = "supply_only"
    UNKNOWN = "unknown"


class SourceType(StrEnum):
    PDF_URL = "pdf_url"
    HTML_URL = "html_url"
    DYNAMIC_ENDPOINT = "dynamic_endpoint"
    ARCHIVAL_CITATION = "archival_citation"
    UNKNOWN = "unknown"


class UnsupportedKind(StrEnum):
    DEMAND_CHARGE = "demand_charge"
    TOU_DEMAND = "tou_demand"
    FLAT_DEMAND = "flat_demand"
    COINCIDENT_DEMAND = "coincident_demand"
    SELL_RATE = "sell_rate"
    NET_METERING = "net_metering"
    RIDER = "rider"
    DEMAND_NORMALIZED_TIER_MAX = "demand_normalized_tier_max"
    UNMODELABLE = "unmodelable"


#: Unsupported kinds that change the *consumption* bill, so their presence forces a refusal.
#: Sell-rate / net-metering only affect *export*, which v0 usage never expresses, so those
#: are warnings rather than refusals (see engine.estimate_bill).
REFUSING_UNSUPPORTED_KINDS = frozenset(
    {
        UnsupportedKind.DEMAND_CHARGE,
        UnsupportedKind.TOU_DEMAND,
        UnsupportedKind.FLAT_DEMAND,
        UnsupportedKind.COINCIDENT_DEMAND,
        UnsupportedKind.RIDER,
        UnsupportedKind.DEMAND_NORMALIZED_TIER_MAX,
        UnsupportedKind.UNMODELABLE,
    }
)


# --------------------------------------------------------------------------------------
# Priced core
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class EnergyTier:
    """One consumption tier within a period.

    ``rate`` and ``adj`` are summed into the effective $/kWh actually charged. ``max`` is the
    upper kWh boundary in ``max_unit``; ``None`` marks the open final tier. ``sell`` (export
    credit) is carried for provenance but not priced in v0.
    """

    rate: Decimal
    adj: Decimal = Decimal(0)
    max: Decimal | None = None
    max_unit: TierMaxUnit = TierMaxUnit.KWH
    sell: Decimal | None = None

    @property
    def effective_rate(self) -> Decimal:
        return self.rate + self.adj

    def to_json(self) -> dict[str, Any]:
        return {
            "rate": decimal_to_json(self.rate),
            "adj": decimal_to_json(self.adj),
            "max": decimal_to_json(self.max),
            "max_unit": self.max_unit.value,
            "sell": decimal_to_json(self.sell),
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> EnergyTier:
        return cls(
            rate=to_decimal(d["rate"]),
            adj=to_decimal(d.get("adj", 0)),
            max=opt_decimal(d.get("max")),
            max_unit=TierMaxUnit(d.get("max_unit", TierMaxUnit.KWH.value)),
            sell=opt_decimal(d.get("sell")),
        )


@dataclass(frozen=True, slots=True)
class EnergyPeriod:
    """A period's independent tier ladder. Tiers never pool across periods."""

    tiers: tuple[EnergyTier, ...]

    def __post_init__(self) -> None:
        if not self.tiers:
            raise ValueError("EnergyPeriod must have at least one tier")
        # Non-final tiers must carry a strictly increasing positive max; only the final tier
        # may be open. (The 3000->200 inversions and 150=150 ties in the corpus fail here.)
        prev: Decimal | None = None
        nonfinal_units: set[TierMaxUnit] = set()
        for i, tier in enumerate(self.tiers):
            is_final = i == len(self.tiers) - 1
            if tier.max is None:
                if not is_final:
                    raise ValueError(f"non-final tier {i} has open max")
                continue
            if tier.max <= 0:
                raise ValueError(f"tier {i} has non-positive max {tier.max}")
            if prev is not None and tier.max <= prev:
                raise ValueError(
                    f"tier maxes must strictly increase: tier {i} max {tier.max} <= {prev}"
                )
            prev = tier.max
            if not is_final:
                nonfinal_units.add(tier.max_unit)
        # Mixing tier-max units among bounded tiers makes the partition ill-defined (a
        # "kWh daily" bound scales by day count while an absolute "kWh" bound does not, so
        # their ordering is not fixed). Reject rather than risk a negative tier slice.
        if len(nonfinal_units) > 1:
            mixed = sorted(u.value for u in nonfinal_units)
            raise ValueError(f"bounded tiers mix max units: {mixed}")

    def to_json(self) -> dict[str, Any]:
        return {"tiers": [t.to_json() for t in self.tiers]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> EnergyPeriod:
        return cls(tiers=tuple(EnergyTier.from_json(t) for t in d["tiers"]))


@dataclass(frozen=True, slots=True)
class EnergyRateStructure:
    periods: tuple[EnergyPeriod, ...]

    def __post_init__(self) -> None:
        if not self.periods:
            raise ValueError("EnergyRateStructure must have at least one period")

    def to_json(self) -> dict[str, Any]:
        return {"periods": [p.to_json() for p in self.periods]}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> EnergyRateStructure:
        return cls(periods=tuple(EnergyPeriod.from_json(p) for p in d["periods"]))


def _validate_matrix(name: str, matrix: tuple[tuple[int, ...], ...]) -> None:
    if len(matrix) != 12:
        raise ValueError(f"{name} must have 12 month rows, got {len(matrix)}")
    for m, row in enumerate(matrix):
        if len(row) != 24:
            raise ValueError(f"{name} month {m} must have 24 hours, got {len(row)}")


@dataclass(frozen=True, slots=True)
class Schedule:
    """Two 12x24 integer matrices (month x hour) of period indices into the period list.

    Byte-identical to URDB's ``energyweekdayschedule`` / ``energyweekendschedule`` so import
    is a parse and export a dump, and identical to PySAM ``ur_ec_sched_weekday/weekend``
    modulo PySAM's 1-based period indexing.
    """

    weekday: tuple[tuple[int, ...], ...]
    weekend: tuple[tuple[int, ...], ...]
    holiday_policy: HolidayPolicy = HolidayPolicy.UNKNOWN
    #: The holidays the rate sheet names (meaningful only with ``as_weekend`` policy).
    holidays: tuple[Holiday, ...] = ()
    holiday_observance: HolidayObservance = HolidayObservance.SUNDAY_TO_MONDAY

    def __post_init__(self) -> None:
        _validate_matrix("weekday", self.weekday)
        _validate_matrix("weekend", self.weekend)
        if len(set(self.holidays)) != len(self.holidays):
            raise ValueError("schedule holidays must be unique")

    def period_at(self, day_type: DayType, month: int, hour: int) -> int:
        """``month`` is 1-12, ``hour`` is 0-23."""
        matrix = self.weekday if day_type is DayType.WEEKDAY else self.weekend
        return matrix[month - 1][hour]

    def referenced_periods(self) -> frozenset[int]:
        out: set[int] = set()
        for matrix in (self.weekday, self.weekend):
            for row in matrix:
                out.update(row)
        return frozenset(out)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "weekday": [list(r) for r in self.weekday],
            "weekend": [list(r) for r in self.weekend],
            "holiday_policy": self.holiday_policy.value,
        }
        # Emitted only when set, so pre-holiday tariff JSONs round-trip byte-identically.
        if self.holidays:
            out["holidays"] = [h.value for h in self.holidays]
        if self.holiday_observance is not HolidayObservance.SUNDAY_TO_MONDAY:
            out["holiday_observance"] = self.holiday_observance.value
        return out

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Schedule:
        return cls(
            weekday=tuple(tuple(int(h) for h in row) for row in d["weekday"]),
            weekend=tuple(tuple(int(h) for h in row) for row in d["weekend"]),
            holiday_policy=HolidayPolicy(d.get("holiday_policy", HolidayPolicy.UNKNOWN.value)),
            holidays=tuple(Holiday(h) for h in d.get("holidays", ())),
            holiday_observance=HolidayObservance(
                d.get("holiday_observance", HolidayObservance.SUNDAY_TO_MONDAY.value)
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedCharge:
    amount: Decimal
    unit: FixedChargeUnit = FixedChargeUnit.PER_MONTH

    def to_json(self) -> dict[str, Any]:
        return {"amount": decimal_to_json(self.amount), "unit": self.unit.value}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> FixedCharge:
        return cls(amount=to_decimal(d["amount"]), unit=FixedChargeUnit(d["unit"]))


@dataclass(frozen=True, slots=True)
class MinCharge:
    amount: Decimal
    unit: MinChargeUnit = MinChargeUnit.PER_MONTH

    def to_json(self) -> dict[str, Any]:
        return {"amount": decimal_to_json(self.amount), "unit": self.unit.value}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> MinCharge:
        return cls(amount=to_decimal(d["amount"]), unit=MinChargeUnit(d["unit"]))


@dataclass(frozen=True, slots=True)
class UnsupportedFeature:
    kind: UnsupportedKind
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "detail": self.detail}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> UnsupportedFeature:
        return cls(kind=UnsupportedKind(d["kind"]), detail=d.get("detail", ""))


# --------------------------------------------------------------------------------------
# Identity / provenance / effective range
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TariffIdentity:
    utility_id: str = ""
    eiaid: int | None = None
    jurisdiction: str | None = None
    plan_code: str = ""
    plan_name: str = ""
    sector: Sector = Sector.UNKNOWN
    is_default_plan: bool | None = None
    tariff_type: TariffType = TariffType.UNKNOWN

    def to_json(self) -> dict[str, Any]:
        return {
            "utility_id": self.utility_id,
            "eiaid": self.eiaid,
            "jurisdiction": self.jurisdiction,
            "plan_code": self.plan_code,
            "plan_name": self.plan_name,
            "sector": self.sector.value,
            "is_default_plan": self.is_default_plan,
            "tariff_type": self.tariff_type.value,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> TariffIdentity:
        return cls(
            utility_id=d.get("utility_id", ""),
            eiaid=d.get("eiaid"),
            jurisdiction=d.get("jurisdiction"),
            plan_code=d.get("plan_code", ""),
            plan_name=d.get("plan_name", ""),
            sector=Sector(d.get("sector", Sector.UNKNOWN.value)),
            is_default_plan=d.get("is_default_plan"),
            tariff_type=TariffType(d.get("tariff_type", TariffType.UNKNOWN.value)),
        )


@dataclass(frozen=True, slots=True)
class EffectiveRange:
    start: date | None = None
    end: date | None = None
    superseded_at: date | None = None
    scheduled_end: date | None = None

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError(f"effective range end {self.end} precedes start {self.start}")

    def to_json(self) -> dict[str, Any]:
        return {
            "start": date_to_json(self.start),
            "end": date_to_json(self.end),
            "superseded_at": date_to_json(self.superseded_at),
            "scheduled_end": date_to_json(self.scheduled_end),
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> EffectiveRange:
        return cls(
            start=date_from_json(d.get("start")),
            end=date_from_json(d.get("end")),
            superseded_at=date_from_json(d.get("superseded_at")),
            scheduled_end=date_from_json(d.get("scheduled_end")),
        )


@dataclass(frozen=True, slots=True)
class SourceDocument:
    url: str = ""
    role: str = ""
    source_type: SourceType = SourceType.UNKNOWN
    sha256: str | None = None
    fetched_at: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    fetch_method: str | None = None
    revision_token: str | None = None
    locator: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "role": self.role,
            "source_type": self.source_type.value,
            "sha256": self.sha256,
            "fetched_at": self.fetched_at,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "fetch_method": self.fetch_method,
            "revision_token": self.revision_token,
            "locator": self.locator,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> SourceDocument:
        return cls(
            url=d.get("url", ""),
            role=d.get("role", ""),
            source_type=SourceType(d.get("source_type", SourceType.UNKNOWN.value)),
            sha256=d.get("sha256"),
            fetched_at=d.get("fetched_at"),
            http_status=d.get("http_status"),
            content_type=d.get("content_type"),
            fetch_method=d.get("fetch_method"),
            revision_token=d.get("revision_token"),
            locator=d.get("locator"),
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    urdb_label: str | None = None
    urdb_latest_update: date | None = None
    last_verified: date | None = None
    confidence: Decimal | None = None
    snapshot_sha256: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "urdb_label": self.urdb_label,
            "urdb_latest_update": date_to_json(self.urdb_latest_update),
            "last_verified": date_to_json(self.last_verified),
            "confidence": decimal_to_json(self.confidence),
            "snapshot_sha256": self.snapshot_sha256,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Provenance:
        return cls(
            urdb_label=d.get("urdb_label"),
            urdb_latest_update=date_from_json(d.get("urdb_latest_update")),
            last_verified=date_from_json(d.get("last_verified")),
            confidence=opt_decimal(d.get("confidence")),
            snapshot_sha256=d.get("snapshot_sha256"),
        )


# --------------------------------------------------------------------------------------
# Top-level tariff
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Tariff:
    energy: EnergyRateStructure
    schedule: Schedule
    identity: TariffIdentity = field(default_factory=TariffIdentity)
    effective_range: EffectiveRange = field(default_factory=EffectiveRange)
    fixed_charges: tuple[FixedCharge, ...] = ()
    min_charge: MinCharge | None = None
    unsupported: tuple[UnsupportedFeature, ...] = ()
    metering: MeteringOption = MeteringOption.UNKNOWN
    source_documents: tuple[SourceDocument, ...] = ()
    provenance: Provenance = field(default_factory=Provenance)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        n_periods = len(self.energy.periods)
        for ref in self.schedule.referenced_periods():
            if ref < 0 or ref >= n_periods:
                raise ValueError(f"schedule references period {ref}, out of range [0, {n_periods})")

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": self.identity.to_json(),
            "effective_range": self.effective_range.to_json(),
            "energy": self.energy.to_json(),
            "schedule": self.schedule.to_json(),
            "fixed_charges": [c.to_json() for c in self.fixed_charges],
            "min_charge": self.min_charge.to_json() if self.min_charge else None,
            "unsupported": [u.to_json() for u in self.unsupported],
            "metering": self.metering.value,
            "source_documents": [s.to_json() for s in self.source_documents],
            "provenance": self.provenance.to_json(),
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Tariff:
        mc = d.get("min_charge")
        return cls(
            energy=EnergyRateStructure.from_json(d["energy"]),
            schedule=Schedule.from_json(d["schedule"]),
            identity=TariffIdentity.from_json(d.get("identity", {})),
            effective_range=EffectiveRange.from_json(d.get("effective_range", {})),
            fixed_charges=tuple(FixedCharge.from_json(c) for c in d.get("fixed_charges", [])),
            min_charge=MinCharge.from_json(mc) if mc else None,
            unsupported=tuple(UnsupportedFeature.from_json(u) for u in d.get("unsupported", [])),
            metering=MeteringOption(d.get("metering", MeteringOption.UNKNOWN.value)),
            source_documents=tuple(
                SourceDocument.from_json(s) for s in d.get("source_documents", [])
            ),
            provenance=Provenance.from_json(d.get("provenance", {})),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )
