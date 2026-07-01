"""Ratebook rate engine: deterministic, pure-function US electricity tariff math.

Public surface:

- Schema (``ratebook.schema``): :class:`Tariff` and its parts — the data model shared with
  the data plant and the future TypeScript port via JSON test vectors.
- Engine (``ratebook.engine``): :func:`estimate_bill`, :func:`estimate_annual`,
  :func:`supported`, with :class:`Usage` / :class:`BillingWindow` inputs and
  :class:`BillResult` / :class:`Refusal` outputs.
- Validation (``ratebook.validate``): grader-shared :func:`validate_tariff` checks.
"""

from .engine import (
    AnnualResult,
    BillingWindow,
    BillResult,
    ChargeWindow,
    LineItem,
    Refusal,
    RefusalReason,
    SupportReport,
    Usage,
    cheapest_charge_window,
    estimate_annual,
    estimate_bill,
    holiday_dates,
    hourly_marginal_prices,
    period_at,
    supported,
)
from .schema import (
    EffectiveRange,
    EnergyPeriod,
    EnergyRateStructure,
    EnergyTier,
    FixedCharge,
    FixedChargeUnit,
    Holiday,
    HolidayObservance,
    HolidayPolicy,
    MeteringOption,
    MinCharge,
    MinChargeUnit,
    Provenance,
    Schedule,
    Sector,
    SourceDocument,
    Tariff,
    TariffIdentity,
    TariffType,
    TierMaxUnit,
    UnsupportedFeature,
    UnsupportedKind,
)
from .validate import Issue, validate_tariff

__version__ = "0.1.0"

__all__ = [
    "AnnualResult",
    "BillResult",
    "BillingWindow",
    "ChargeWindow",
    "EffectiveRange",
    "EnergyPeriod",
    "EnergyRateStructure",
    "EnergyTier",
    "FixedCharge",
    "FixedChargeUnit",
    "Holiday",
    "HolidayObservance",
    "HolidayPolicy",
    "Issue",
    "LineItem",
    "MeteringOption",
    "MinCharge",
    "MinChargeUnit",
    "Provenance",
    "Refusal",
    "RefusalReason",
    "Schedule",
    "Sector",
    "SourceDocument",
    "SupportReport",
    "Tariff",
    "TariffIdentity",
    "TariffType",
    "TierMaxUnit",
    "UnsupportedFeature",
    "UnsupportedKind",
    "Usage",
    "__version__",
    "cheapest_charge_window",
    "estimate_annual",
    "estimate_bill",
    "holiday_dates",
    "hourly_marginal_prices",
    "period_at",
    "supported",
    "validate_tariff",
]
