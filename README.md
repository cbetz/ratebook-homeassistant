<img src="icon.png" width="72" align="right" alt="Ratebook">

# Ratebook for Home Assistant

**See what electricity actually costs you, hour by hour — and when to charge.** Real US
utility tariffs (time-of-use, tiered, seasonal, holidays) as live Home Assistant sensors:
a `$/kWh` price entity for the **Energy Dashboard** and a **cheapest-charge-window** sensor
for EVs and batteries. 100% local — no cloud, no API key, no account.

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cbetz&repository=ratebook-homeassistant&category=integration)

<!-- HERO: Energy Dashboard cost-tracking screenshot + config-flow GIF land here (captured from a live HA install). -->

## Why

EU installs get dynamic day-ahead prices (Nordpool, Octopus…). US rates hide in utility PDF
rate sheets — time-of-use windows, seasons, baseline tiers, holiday rules. Ratebook turns
those PDFs into data and prices every hour of your day with a deterministic, engine-tested
rate engine, so your Energy Dashboard tracks dollars that match your bill.

## Supported utilities

30 plans across 16 utilities, **every one audited against the utility's current rate sheet
(July 2026)** with sources and a `last_verified` date in the tariff file. Pick your plan in
the config flow — no YAML, no JSON:

| Utility (state) | Plans |
| --- | --- |
| PG&E (CA) | E-1 Tiered · E-TOU-C 4-9pm · **EV2-A Home Charging** |
| SCE (CA) | Domestic D Tiered · TOU-D-4-9PM · **TOU-D-PRIME (EV)** |
| SDG&E (CA) | TOU-DR1 · **EV-TOU-5** |
| Con Edison (NY) | SC-1 Residential (NYC) · SC-1 Rate III Time-of-Use |
| PECO (PA) | Rate R · Rate R Time-of-Use |
| Eversource (CT) | Rate 1 · Rate 7 Time-of-Day |
| National Grid (MA) | R-1 Residential |
| PSE&G (NJ) | RS · RS-TOU-3P |
| FPL (FL) | RS-1 · RTR-1 Time-of-Use |
| Duke Energy Carolinas (NC) | RS Residential |
| Georgia Power (GA) | R-31 · TOU-OA-14 Overnight Advantage |
| Dominion Energy (VA) | Schedule 1 · Schedule 1T Time-of-Use |
| DTE (MI) | D1 · D1.2 Time of Day |
| APS (AZ) | R-TOU-E 4-7pm |
| SRP (AZ) | E-23 Basic · E-26 Time-of-Use |
| Xcel Energy (CO) | RE-TOU |

TOU plans whose rate sheets define holiday off-peak pricing carry those **holiday rules**
(on PECO's TOU, Labor Day afternoon really is off-peak), and tiered plans price at
**your tier**. How big are the swings? On SDG&E's EV-TOU-5, a peak kWh
costs **6.2×** the overnight rate ($0.79 vs $0.13) — charging a 40 kWh EV in the wrong window
is ~$27. Con Edison's SC-1 Rate III hits **$1.18/kWh** on summer weekday afternoons vs $0.13
overnight.

**Utility not listed?** [Request it](https://github.com/cbetz/ratebook/issues/new?template=request-a-utility.yml)
— a rate-sheet link is all that's needed, and new tariffs ship in days, not quarters. Or paste
your own [tariff JSON](https://github.com/cbetz/ratebook/blob/main/docs/AUTHORING_TARIFFS.md)
into the config flow.

## Install

**HACS:** click the badge above (or HACS → ⋮ → *Custom repositories* → add
`https://github.com/cbetz/ratebook-homeassistant`, category *Integration*), install
**Ratebook**, restart. *(Submitted to the HACS default store —
[hacs/default#8837](https://github.com/hacs/default/pull/8837) — after which the custom-repo
step goes away.)*

**Manual:** copy `custom_components/ratebook` into your `config/custom_components/`, restart.

Then: **Settings → Devices & Services → Add Integration → Ratebook**, pick your utility plan,
and (tiered plans) the tier your bill usually lands in.

## Energy Dashboard: track real dollars

1. **Settings → Dashboards → Energy**.
2. Under **Electricity grid → Grid consumption**, add your consumption sensor (from your
   utility meter, Emporia/Sense/Shelly CT clamps, or a smart-meter integration).
3. For **"Use an entity with current price"**, pick **`sensor.ratebook_electricity_price`**.

The dashboard now accrues cost per hour at your tariff's real marginal rate — peak hours cost
more, off-peak less, exactly as your bill will. (Ratebook supplies the *price*; the
consumption sensor comes from whatever measures your usage.)

## Entities

- **`sensor.ratebook_electricity_price`** — current marginal price (`USD/kWh`). Attributes:
  - `raw_today` / `raw_tomorrow` — hourly `[{start, end, value}]`, the **Nordpool-compatible
    shape** existing charging automations, blueprints, and ApexCharts configs consume.
    `tomorrow_valid` is always `true`: unlike day-ahead markets, tomorrow's tariff prices are
    known all day.
  - `forecast` — 48h `[{start, end, value}]` in **evcc**'s custom-tariff shape
    ([recipe](docs/integrations/evcc.md)); also works for EMHASS
    ([recipe](docs/integrations/emhass.md)).
  - `today` / `tomorrow` — hourly `[{start, price}]` (legacy shape, kept for compatibility).
  - `today_is_holiday` / `tomorrow_is_holiday` — whether the tariff's holiday rule prices the
    day off-peak.
  - `tier` — the 1-based tier prices are computed at (tiered plans).
- **`sensor.ratebook_cheapest_charge_window`** — start of the cheapest upcoming contiguous
  charge block, searched from now through the end of tomorrow (timestamp). Attributes:
  `end`, `avg_rate`, `hours`.

A copy-paste dashboard (gauge, charge-window card, price history, optional ApexCharts hourly
curve) ships in [`examples/dashboard.yaml`](examples/dashboard.yaml).

## How it compares

| | Ratebook | [ha-openei](https://github.com/firstof9/ha-openei) | [MIDAS](https://community.home-assistant.io/t/766361) | ComEd (core) | Emporia app |
| --- | --- | --- | --- | --- | --- |
| Coverage | Curated bundled plans (growing weekly) + any custom tariff JSON | Whole NREL URDB | California RINs | ComEd RTP only | URDB picker |
| Data source | Verified against current rate sheets, provenance per tariff | URDB as-is (can lag rate changes) | CPUC MIDAS API | Live API | URDB |
| Needs API key / cloud | **No** | Yes (free key) | Yes (account) | No | Hardware + app |
| Tiered marginal price | **Yes (pick your tier)** | Partial | n/a | n/a | n/a |
| Holiday TOU rules | **Yes** | No | n/a | n/a | No |
| Cheapest-charge-window sensor | **Yes** | No | No | No | In-app scheduling |

If your utility is in URDB and you're happy with URDB freshness, ha-openei is a fine choice —
Ratebook exists for when the numbers have to match the bill.

## Honest limitations (v0)

- Prices are the tariff's **energy marginal price** — the time-of-use signal. Demand charges
  aren't modeled; on tiered plans the price is computed at the tier you pick in the config
  (not tracked against your cumulative monthly usage).
- Some deregulated-state tariffs are delivery + default-supply; if you buy generation from a
  third-party supplier, your all-in price differs. Every bundled tariff lists its sources and
  a `last_verified` date — check one bill against it, and
  [file a correction](https://github.com/cbetz/ratebook/issues/new?template=tariff-correction.yml)
  if anything is off.

## Development

This repository is the **HACS distribution mirror** — a generated copy of
[`cbetz/ratebook`](https://github.com/cbetz/ratebook) (`packages/ratebook-homeassistant`),
where the engine, dataset, tests, and issue tracker live. Please file issues and PRs there.

## License

[Apache-2.0](LICENSE). Tariff data: [CC0-1.0](https://github.com/cbetz/ratebook/blob/main/LICENSE-DATA).
