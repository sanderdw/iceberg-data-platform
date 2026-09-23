# Webshop blueprint: Kiosko Online

Kiosko Online is a fictional online shop for home and lifestyle products. It runs the storefront, ships orders from two warehouses, markets through search and social campaigns, handles customer service and returns, sets prices and manages payments.

Teams are used in this order: a class of up to 4 people gets only `storefront`, and 21 or more get all six.

```yaml
company: Kiosko Online
email_domain: kiosko.example
teams:
  - name: storefront
    description: Owns the product catalogue, search and the shopping experience on web and app.
    databases:
      - name: product-catalog
        environment: production
        description: Products, variants, prices and stock status shown in the shop.
      - name: search-experiments
        environment: development
        description: Search queries, rankings and A/B test results for the shop search.
  - name: order-fulfilment
    description: Picks, packs and ships customer orders from the warehouses.
    databases:
      - name: orders
        environment: production
        description: Orders and order lines with payment and fulfilment status.
      - name: shipment-tracking
        environment: development
        description: Carrier events and delivery times per parcel.
  - name: marketing-analytics
    description: Measures traffic, campaigns and conversion to steer the marketing budget.
    databases:
      - name: web-sessions
        environment: production
        description: Sessions and page views with traffic source and device.
      - name: campaign-attribution
        environment: development
        description: Attribution models that credit orders to campaigns and channels.
  - name: customer-service
    description: Answers customer questions and handles complaints, returns and refunds.
    databases:
      - name: support-tickets
        environment: production
        description: Contacts per channel with topic, handling time and satisfaction score.
      - name: returns-analysis
        environment: development
        description: Returned items with reasons, used to find products with quality issues.
  - name: pricing
    description: Sets prices and promotions based on demand, stock and competitor prices.
    databases:
      - name: price-history
        environment: production
        description: Every price and promotion per product with start and end time.
      - name: competitor-prices
        environment: development
        description: Scraped competitor prices matched to our products.
  - name: finance
    description: Reconciles payments, refunds and payouts and forecasts revenue.
    databases:
      - name: payments
        environment: production
        description: Payments, refunds and chargebacks per order and payment method.
      - name: revenue-forecasts
        environment: development
        description: Weekly revenue and margin forecasts per product category.
```
