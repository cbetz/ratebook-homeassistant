# Ratebook

Live US electricity-price + cheapest-charge-window sensors for Home Assistant, over the
deterministic [Ratebook rate engine](https://github.com/cbetz/ratebook). Self-contained (engine
vendored — no extra dependencies).

- **`sensor.ratebook_electricity_price`** — current marginal $/kWh, with the hourly schedule as
  attributes (evcc-compatible).
- **`sensor.ratebook_cheapest_charge_window`** — when to charge: the cheapest contiguous block in
  the next 24 hours.

Configure in the UI with a bundled example tariff or your own pasted tariff JSON. v0 models the
time-of-use energy price; see the project for scope and roadmap.
