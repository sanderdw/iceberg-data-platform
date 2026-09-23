# Retail blueprint: Linden Market

Linden Market is a fictional chain of 40 neighbourhood grocery stores. It runs the stores and tills, plans assortments and prices, replenishes stock from a central distribution centre, runs a loyalty programme, sells online for pickup, and tracks store finances.

Teams are used in this order: a class of up to 4 people gets only `store-operations`, and 21 or more get all six.

```yaml
company: Linden Market
email_domain: lindenmarket.example
teams:
  - name: store-operations
    description: Runs the stores, tills and staff planning across all locations.
    databases:
      - name: pos-transactions
        environment: production
        description: Till receipts and receipt lines per store, refreshed every hour.
      - name: store-footfall
        environment: development
        description: Visitor counts per store and hour, used for staff planning.
  - name: merchandising
    description: Decides which products each store carries, where they go on the shelf and at what price.
    databases:
      - name: assortment-plans
        environment: production
        description: Planned assortment and shelf space per store format.
      - name: pricing-experiments
        environment: development
        description: Price tests and promotions with their effect on sales and margin.
  - name: supply-chain
    description: Replenishes the stores from the distribution centre and manages suppliers.
    databases:
      - name: inventory-levels
        environment: production
        description: Stock on hand per store and product, including the distribution centre.
      - name: demand-forecasts
        environment: development
        description: Daily sales forecasts per store and product for automatic replenishment.
  - name: loyalty-program
    description: Runs the loyalty card, personal offers and member communication.
    databases:
      - name: loyalty-members
        environment: production
        description: Members, their store visits and points balance, pseudonymised.
      - name: offer-performance
        environment: development
        description: Redemption and uplift of personal offers per member segment.
  - name: online-orders
    description: Runs online ordering with pickup in store and home delivery.
    databases:
      - name: pickup-orders
        environment: production
        description: Online orders with pickup slot, store and picking status.
      - name: basket-analysis
        environment: development
        description: Products bought together, used for online recommendations.
  - name: store-finance
    description: Tracks revenue, costs and margins per store and region.
    databases:
      - name: store-results
        environment: production
        description: Weekly revenue, costs and margin per store.
      - name: waste-reduction
        environment: development
        description: Written-off fresh products per store, used to reduce waste.
```
