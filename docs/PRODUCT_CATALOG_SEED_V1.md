# Source-backed product catalogue seed v1

Captured/researched: 2026-11-18. This is a small bootstrap catalogue to validate product identity and the evidence-import workflow. It is not a complete trend ranking or proof of demand.

## Included candidates

| Product | Category | Identity reference | Research rationale |
|---|---|---|---|
| Nike Caitlin 1 Cave Stone (IH7423-101) | Basketball sneaker | Nike product page | Current Nike signature basketball model; the official page confirms colorway and style code. |
| Nike Kobe 9 EM Low Protro Philippines (IO6262-600) | Basketball sneaker | Nike SNKRS release page | Release dated 2026-11-18; distinctive Philippines/banig cultural story. |
| Coach Soft Empire Carryall Bag 40 (CFR36) | Tote/carryall | Coach product page | Official page confirms style number, leather, capacity and dimensions; Vogue includes it in AW26 bag trend coverage. |
| COS Trullo Oversized Flap Bag (1361103001) | Shoulder/top-handle bag | COS US product page | Official page confirms oversized folded silhouette and leather; British Vogue includes the model in AW26 bag coverage. |

## Import

From the repository root:

    fashion-radar --db data/registry.sqlite import-products --file configs/manual_research/products_seed_v1.csv

Then attach identity and editorial references:

    python scripts/import_product_evidence.py --db data/registry.sqlite --file configs/manual_research/product_evidence_seed_v1.csv

Audit the catalogue:

    python scripts/audit_product_catalog.py --db data/registry.sqlite --out reports/product_catalog/seed_v1_audit.json

Generate market-specific research tasks only after importing:

    python scripts/generate_research_plan.py --db data/registry.sqlite --out configs/manual_research/research_query_plan.csv

## Boundaries and known gaps

- This seed intentionally contains four products only. It does not imply market-wide representativeness.
- The Nike and Coach official URLs are US-facing. COS has a separate Canada page; market-level stock, price and distribution have not been fully checked. Do not assume a US product page proves availability in CA or GB.
- Prices are not copied into the product master. Market prices and stock are volatile and require dated market-specific evidence.
- Product identity evidence is not the same as trend evidence. Editorial trend coverage is contextual evidence, not a measured Pinterest, TikTok or Google Trends observation.
- No social engagement, search-index, sales or purchase-intent values are included. Those must be collected independently from their respective sources.
- Review the product names and evidence links in the browser before treating the records as commercially actionable. Product evidence references document provenance; the current audit does not authenticate page contents automatically.
