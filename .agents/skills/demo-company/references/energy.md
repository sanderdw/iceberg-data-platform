# Energy blueprint: Voltara Energy

Voltara Energy is a fictional regional grid operator and energy supplier. It plans network capacity, maintains substations and cables, restores outages, trades energy, serves households with smart meters and runs solar and wind parks.

Teams are used in this order: a class of up to 4 people gets only `grid-planning`, and 21 or more get all six.

```yaml
company: Voltara Energy
email_domain: voltara.example
teams:
  - name: grid-planning
    description: Forecasts demand and plans grid capacity for neighbourhoods and industrial parks.
    databases:
      - name: grid-capacity
        environment: production
        description: Transformer and feeder capacity per neighbourhood, refreshed nightly.
      - name: grid-capacity-scenarios
        environment: development
        description: Growth scenarios for heat pumps, EV charging and rooftop solar.
  - name: asset-management
    description: Keeps substations, cables and transformers healthy with inspections and maintenance plans.
    databases:
      - name: grid-assets
        environment: production
        description: Register of substations, cables and transformers with condition scores.
      - name: maintenance-forecasts
        environment: development
        description: Failure-risk models that prioritise inspections and replacements.
  - name: outage-response
    description: Detects, dispatches and restores power outages and reports on reliability.
    databases:
      - name: outage-events
        environment: production
        description: Outage tickets with affected connections, cause and restoration times.
      - name: outage-analytics
        environment: development
        description: Reliability metrics such as SAIDI and SAIFI per region and cause.
  - name: energy-trading
    description: Buys and sells energy on the day-ahead and intraday markets to balance supply and demand.
    databases:
      - name: market-prices
        environment: production
        description: Day-ahead and intraday prices per quarter hour.
      - name: trading-models
        environment: development
        description: Price and load forecasts used to plan trading positions.
  - name: customer-insights
    description: Understands household and business customers through smart-meter data and contracts.
    databases:
      - name: smart-meter-readings
        environment: production
        description: Quarter-hourly consumption and feed-in per connection, pseudonymised.
      - name: energy-advice
        environment: development
        description: Savings advice models based on usage profiles and home characteristics.
  - name: renewables
    description: Operates the company's solar and wind parks and forecasts their production.
    databases:
      - name: park-production
        environment: production
        description: Generation per solar and wind park with availability and curtailment.
      - name: weather-forecasts
        environment: development
        description: Wind and irradiance forecasts that drive production planning.
```
