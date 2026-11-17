# Product catalogue provenance and quality gate

The product catalogue is the identity backbone for trend research. A typo or ambiguous alias can attach observations to the wrong shoe or bag, so product identity must be reviewed before query generation or scoring.

## Prepare and import the product master

Copy the blank template and fill one row per real product candidate:

```powershell
Copy-Item configs/products_template.csv configs/products.csv
python -m fashion_radar.cli --db data/registry.sqlite import-products --file configs/products.csv
```

Use the exact manufacturer model name and style code where available. Separate aliases with semicolons; do not merge different colorways or generations into one row unless the manufacturer treats them as the same product identity. Use `identity_confidence` conservatively: 1.0 only when the exact model and style identity are clear, lower values when a model/colorway match is uncertain.

## Attach product references

Copy `configs/product_evidence_template.csv` and add at least one reference per product:

```powershell
Copy-Item configs/product_evidence_template.csv configs/product_evidence.csv
python scripts/import_product_evidence.py --db data/registry.sqlite --file configs/product_evidence.csv
```

Supported evidence types:
- `official_product_page`: brand product page or official product catalogue.
- `official_release_announcement`: brand release/news announcement.
- `authorized_retailer`: product page from an authorized retailer.
- `marketplace_listing`: marketplace listing; treat seller claims cautiously.
- `editorial_reference`: reputable editorial/release reference.

Use `is_primary=true` for the strongest source used to establish model name, style code and colorway. The importer rejects non-HTTPS and placeholder `.invalid` URLs, validates product IDs, and keeps the original URL and capture timestamp. It does not automatically authenticate a source or decide whether a retailer is authorized; a human must review that.

## Run the catalogue audit

```powershell
python scripts/audit_product_catalog.py --db data/registry.sqlite --out reports/product_catalog/audit.json
```

The default identity-confidence threshold is 0.80. Blockers include:
- no product records;
- product without any evidence reference;
- confidence below threshold;
- duplicate normalized brand/canonical identity;
- duplicate brand/style code;
- an alias that resolves to more than one product;
- unsupported category/subcategory or audience tag.

Warnings include missing style code, missing aliases, no primary evidence, and incomplete active market mappings. Market mappings are warnings because baseline query plans can still be generated for all three markets.

Exit code 0 means no blockers; exit code 1 means the catalogue is not ready; exit code 2 means the audit could not be run. A ready result means the catalogue is internally consistent and has recorded provenance references. It does not certify the accuracy of source pages or prove demand.

## Recommended order

1. Import a curated product list with canonical names, aliases, style codes and confidence.
2. Attach official/authorized evidence references.
3. Import product-market mappings and local terms.
4. Run the product catalogue audit and resolve all blockers.
5. Generate the market research query plan.
6. Collect and import real observations.
7. Run the source coverage audit before scoring.
