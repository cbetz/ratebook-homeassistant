<img src="icon.png" width="72" align="right" alt="Ratebook">

# Ratebook for Home Assistant

A Home Assistant custom integration that turns a US electricity tariff into live
electricity-price sensors and a cheapest-charge-window sensor — the price signal that EV
chargers, [evcc](https://evcc.io), [EMHASS](https://emhass.readthedocs.io), and HA automations
need to answer *"what does a kWh cost right now, and when should I charge?"*

It is a thin shell over the deterministic [Ratebook rate engine](https://github.com/cbetz/ratebook).
The engine and adapter are **vendored into this integration**, so it installs with **no PyPI or
network dependency**.

> This repository is the **HACS distribution mirror** of the integration. The source of truth —
> engine, tests, and the rest of the project — lives in the monorepo at
> **[cbetz/ratebook](https://github.com/cbetz/ratebook)** (`packages/ratebook-homeassistant`).

## Install

### HACS (custom repository)
1. HACS → ⋮ → **Custom repositories** → add `https://github.com/cbetz/ratebook-homeassistant`,
   category **Integration**.
2. Install **Ratebook**, restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → **Ratebook**.

### Manual
Copy `custom_components/ratebook` into your HA `config/custom_components/` directory, restart,
then add the integration.

## Entities

- **`sensor.ratebook_electricity_price`** — current marginal price ($/kWh). Attributes `today` /
  `tomorrow` carry the full hourly schedule (the shape evcc and price-aware automations consume).
- **`sensor.ratebook_cheapest_charge_window`** — start time of the cheapest contiguous charge
  block in the next 24 hours. Attributes: `end`, `avg_rate`, `hours`.

## Configuration

Add via the UI, pick a bundled example tariff (generic time-of-use or flat residential) or paste
a Ratebook tariff JSON, then set the charge-window length and currency.

> **Status: v0.** Prices are the tariff's energy marginal price (the time-of-use signal); demand
> charges and tier-vs-baseline position are not modeled — see the [engine docs](https://github.com/cbetz/ratebook).

## License

[Apache-2.0](LICENSE). Issues and contributions: please use the
[monorepo](https://github.com/cbetz/ratebook/issues).
