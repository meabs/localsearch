# Exports

Case briefing export is designed for readable handoff, not raw database dump output.

## Endpoint

`POST /cases/{case_ref}/export?format=md|html|pdf`

## Output location

Exports are saved under:

`data/exports/<case_ref>/`

## Sections

- case title
- executive summary
- key entities
- key relationships
- timeline summary
- notable locations
- analyst notes
- appendix: cited evidence

## Recommended use

- export Markdown for source-controlled analyst notes
- export HTML for easy local sharing or browser review
- export PDF when you need a static handoff artifact
