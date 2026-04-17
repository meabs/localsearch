"""Geocode every LOCATION entity that doesn't already have coordinates.

Respects Nominatim's 1 req/sec usage policy via the shared NominatimGeocoder.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store
from operation_lens_v2.ingestion.duck_store import connect
from operation_lens_v2.services.geocoder import (
    GeocoderDisabled,
    NominatimGeocoder,
    geocode_entity,
)

logger = logging.getLogger(__name__)


async def run(force: bool, limit: int | None) -> None:
    con = connect(settings.duckdb_path)
    duck_store._ensure_geocode_columns(con)  # noqa: SLF001 — safe idempotent migration
    sql = """
        SELECT entity_id, canonical_name, latitude
        FROM entities
        WHERE entity_type = 'LOCATION'
        ORDER BY mention_count DESC, canonical_name
    """
    rows = con.execute(sql).fetchall()
    if limit:
        rows = rows[:limit]

    geocoder = NominatimGeocoder()
    hits = 0
    misses = 0
    cached = 0
    for entity_id, name, lat in rows:
        if lat is not None and not force:
            cached += 1
            continue
        try:
            result = await geocode_entity(
                con,
                entity_id=entity_id,
                canonical_name=name,
                geocoder=geocoder,
                force=force,
            )
        except GeocoderDisabled:
            print("Geocoding disabled via settings; aborting.")
            return
        if result:
            hits += 1
            print(f"  OK   {name} -> ({result['latitude']:.4f}, {result['longitude']:.4f})")
        else:
            misses += 1
            print(f"  MISS {name}")
    print(
        f"\nDone. resolved={hits} miss={misses} already_cached={cached}"
        f" total_locations={len(rows)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-geocode even if cached")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of lookups")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(force=args.force, limit=args.limit))


if __name__ == "__main__":
    main()
