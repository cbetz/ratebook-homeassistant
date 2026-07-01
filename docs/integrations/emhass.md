# Using Ratebook as an EMHASS load-cost forecast

[EMHASS](https://emhass.readthedocs.io) optimizes home energy (battery, deferrable loads, EV)
against a price forecast it calls `load_cost_forecast`. EMHASS accepts a custom forecast at
runtime via the `list` method — a list of $/kWh values, **current period first**, as long as
the optimization's `prediction_horizon`.

Ratebook produces exactly that. The Home Assistant integration's
`sensor.ratebook_electricity_price` carries a `forecast` attribute (`[{start, end, value}]`,
hourly, starting at the current hour), so the values are already in EMHASS order.

## Recipe: feed the Ratebook forecast into an EMHASS optimization

If you run the EMHASS Home Assistant add-on, build the `load_cost_forecast` from the sensor in
a `rest_command` (or in the automation that triggers the optimization). The template maps the
forecast's `value` field to the list EMHASS expects:

```yaml
# configuration.yaml
rest_command:
  emhass_dayahead:
    url: http://localhost:5000/action/dayahead-optim
    method: POST
    content_type: application/json
    payload: >
      {
        "load_cost_forecast":
          {{ state_attr('sensor.ratebook_electricity_price', 'forecast')
             | map(attribute='value') | list | tojson }},
        "prod_price_forecast":
          {{ state_attr('sensor.ratebook_electricity_price', 'forecast')
             | map(attribute='value') | list | tojson }}
      }
```

`prediction_horizon` defaults from your EMHASS config; trim the list if you pass a shorter
horizon (EMHASS requires the list length to match). Because the forecast starts at the current
hour, no reordering is needed — it satisfies EMHASS's "current time period first" rule.

Calling EMHASS directly (Python / REST) works the same way — pass `load_cost_forecast` as a
list of hourly $/kWh values. `ratebook_ha.pricing.emhass_cost_forecast(tariff, start, hours)`
generates the list programmatically if you're not going through Home Assistant.

## Notes

- v0 prices are the tariff's energy marginal price (time-of-use signal). Demand charges and
  tier-vs-baseline position aren't modeled — exact for pure time-of-use plans.
- For an export/feed-in price (`prod_price_forecast`), v0 reuses the consumption price as a
  placeholder; net-metering sell rates are carried in the schema but not yet priced.
