from __future__ import annotations

from fastapi import APIRouter

from operation_lens_v2.ingestion.entity_schema import list_domain_packs

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/domain-packs")
def domain_packs() -> dict[str, object]:
    return {"domain_packs": list_domain_packs()}
