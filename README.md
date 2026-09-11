# CareCo vs Complete Care Shop Price Watch

CareCo is the master catalogue. The application crawls CareCo first, then searches Complete Care Shop only for equivalent products. Products are never added from Complete Care Shop unless they match a CareCo product.

## Workflow
1. Scrape CareCo catalogue and current prices.
2. Scrape Complete Care Shop catalogue and current prices.
3. Match Complete Care products to CareCo products using SKU first, then exact/strong product-name matching with brand checks.
4. Highlight which retailer is cheaper.
5. Refresh prices on demand.
6. Export the comparison as CSV.

Unmatched CareCo products remain visible as No match.
