"""Nominatim-backed geocoder with rate limiting and persistent caching.

Data-sovereignty note: this module sends *location strings* (not document text)
to the configured Nominatim endpoint. It is the only component in the pipeline
that makes outbound calls for investigator-facing metadata. Disable via
`GEOCODING_ENABLED=0` in the environment if your engagement prohibits it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from operation_lens_v2.config import settings
from operation_lens_v2.ingestion import duck_store

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str
    provider: str


class GeocoderDisabled(RuntimeError):
    pass


_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.IGNORECASE)


def _prepare_query(raw: str) -> str:
    """Light cleanups so Nominatim has the best shot at matching surface forms."""
    q = raw.strip()
    q = re.sub(r"\s+", " ", q)
    # Insert the space inside a concatenated UK postcode (CR0 2AB style).
    match = _POSTCODE_RE.search(q)
    if match:
        q = (
            q[: match.start()]
            + f"{match.group(1).upper()} {match.group(2).upper()}"
            + q[match.end() :]
        )
    return q


class NominatimGeocoder:
    """Async client with per-process serialisation to respect OSM usage policy."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        user_agent: str | None = None,
        country_bias: str | None = None,
        min_interval: float | None = None,
    ) -> None:
        self._base_url = (base_url or settings.nominatim_base_url).rstrip("/")
        self._user_agent = user_agent or settings.nominatim_user_agent
        self._country_bias = (country_bias or settings.nominatim_country_bias or "").strip().lower()
        self._min_interval = max(0.1, min_interval or settings.nominatim_min_interval)
        self._lock = asyncio.Lock()
        self._last_call_at = 0.0

    async def lookup(self, query: str) -> GeocodeResult | None:
        if not settings.geocoding_enabled:
            raise GeocoderDisabled("Geocoding is disabled (GEOCODING_ENABLED=0)")
        cleaned = _prepare_query(query)
        if not cleaned:
            return None
        params: dict[str, Any] = {"q": cleaned, "format": "jsonv2", "limit": 1}
        if self._country_bias:
            params["countrycodes"] = self._country_bias
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        async with self._lock:
            await self._sleep_for_rate_limit()
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.get(
                        f"{self._base_url}/search", params=params, headers=headers
                    )
            finally:
                self._last_call_at = asyncio.get_event_loop().time()
        if resp.status_code != 200:
            logger.warning("Nominatim lookup failed status=%s query=%r", resp.status_code, cleaned)
            return None
        payload = resp.json()
        if not payload:
            return None
        top = payload[0]
        try:
            lat = float(top["lat"])
            lon = float(top["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        return GeocodeResult(
            latitude=lat,
            longitude=lon,
            display_name=top.get("display_name") or cleaned,
            provider="nominatim",
        )

    async def _sleep_for_rate_limit(self) -> None:
        now = asyncio.get_event_loop().time()
        delta = now - self._last_call_at
        if delta < self._min_interval:
            await asyncio.sleep(self._min_interval - delta)


async def geocode_entity(
    con,
    *,
    entity_id: str,
    canonical_name: str,
    geocoder: NominatimGeocoder | None = None,
    force: bool = False,
) -> dict[str, object] | None:
    """Geocode a single entity and persist the result. Returns cached hit if present."""
    existing = duck_store.get_entity_geocode(con, entity_id)
    if existing and not force:
        return existing
    coder = geocoder or NominatimGeocoder()
    try:
        result = await coder.lookup(canonical_name)
    except GeocoderDisabled:
        raise
    except Exception as exc:
        logger.warning("Geocoder error for %s: %s", canonical_name, exc)
        return None
    if not result:
        return None
    duck_store.set_entity_geocode(
        con,
        entity_id=entity_id,
        latitude=result.latitude,
        longitude=result.longitude,
        provider=result.provider,
        display_name=result.display_name,
    )
    return {
        "latitude": result.latitude,
        "longitude": result.longitude,
        "provider": result.provider,
        "display_name": result.display_name,
        "geocoded_at": None,
    }
