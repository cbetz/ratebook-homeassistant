# Using Ratebook as an evcc price source

[evcc](https://evcc.io) charges your EV when electricity is cheapest. It reads a price
forecast from a `tariffs.grid` source. Ratebook can supply that forecast for any US tariff —
including the ~half of a restructured-state bill (generation + transmission) that flat "we have
the rates" datasets miss.

The Ratebook Home Assistant integration already publishes an evcc-shaped forecast on
`sensor.ratebook_electricity_price` (the `forecast` attribute is a list of
`{start, end, value}` objects, the exact shape evcc's `custom` tariff consumes). So if you run
Home Assistant and evcc together, the integration is config-only.

## Recipe: evcc `custom` tariff → Home Assistant REST API

1. In Home Assistant, create a long-lived access token (Profile → Security).
2. In `evcc.yaml`, point a `custom` grid tariff at the sensor via the HTTP source, extracting
   the `forecast` attribute with `jq`:

```yaml
tariffs:
  currency: USD
  grid:
    type: custom
    forecast:
      source: http
      uri: http://homeassistant.local:8123/api/states/sensor.ratebook_electricity_price
      headers:
        - Authorization: Bearer YOUR_HA_LONG_LIVED_TOKEN
      jq: .attributes.forecast
```

evcc polls hourly and renders the forecast in its planner. `value` is $/kWh; set `currency`
to match. The `forecast` attribute covers the next 48 hours, recomputed every 5 minutes.

## Notes

- v0 prices are the tariff's **energy marginal price** (the time-of-use signal evcc needs to
  pick charge windows). Demand charges and tier-vs-baseline position aren't modeled — see the
  engine docs. For pure time-of-use plans this is exact.
- Non-Home-Assistant users can produce the same forecast by running the `ratebook` engine /
  `ratebook-mcp` server directly; the HA sensor is just a convenient bridge for HA setups.
