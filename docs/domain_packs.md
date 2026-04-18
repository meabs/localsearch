# Domain Packs

Domain packs extend the base entity schema without forking the ingestion pipeline.

## Files

- `config/entity_schema.json`
- `config/domain_packs/investigations.json`
- `config/domain_packs/fraud_finance.json`
- `config/domain_packs/cyber_infra.json`
- `config/domain_packs/supply_chain.json`

## Resolution rules

1. Load the base schema.
2. Load the selected domain pack.
3. Apply deterministic deep merge behavior.
4. Apply optional case overrides.

## UI behavior

The selected pack drives:

- default query templates
- default dashboard widgets
- pack metadata shown in the case experience

## API

- `GET /config/domain-packs`
- `POST /cases/{case_ref}/domain-pack`
- `GET /cases/{case_ref}/resolved-schema`
