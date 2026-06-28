"""Silver dispatcher: routes bronze rows to per-source transformers and upserts."""

from __future__ import annotations

import logging
import os
import time

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from db import get_table

from .sources import housinganywhere, kleinanzeigen, wg_gesucht, wohninberlin
from .sources.common import (
    BERLIN_LAT_MAX,
    BERLIN_LAT_MIN,
    BERLIN_LON_MAX,
    BERLIN_LON_MIN,
    clean_berlin_coords,
)
from .upsert import conflict_update_set

logger = logging.getLogger(__name__)

_TRANSFORMERS = {
    "wg-gesucht": wg_gesucht.to_listing_row,
    "kleinanzeigen": kleinanzeigen.to_listing_row,
    "housinganywhere": housinganywhere.to_listing_row,
    "wohninberlin": wohninberlin.to_listing_row,
}


def transform(session: Session) -> int:
    """Read all bronze rows, route by source, upsert into silver.

    Returns the number of rows upserted.
    """
    raw_listings = get_table("raw_listings")
    listings = get_table("listings")

    rows = session.execute(select(raw_listings)).mappings().all()

    count = 0
    skipped: dict[str, int] = {}
    for raw in rows:
        source = raw["source_name"]
        fn = _TRANSFORMERS.get(source)
        if fn is None:
            skipped[source] = skipped.get(source, 0) + 1
            continue

        values = fn(dict(raw))
        values["raw_listing_id"] = raw["id"]
        values["source_name"] = source
        values["external_id"] = raw["external_id"]
        values["scraped_at"] = raw["scraped_at"]

        # Keep the PostGIS `location` Point in sync with `latitude`/`longitude`
        # on every write. Migration 0002 did a one-shot historical backfill,
        # but new listings without this would skip the gold layer entirely
        # (gold queries `location`, not lat/lng). The expression evaluates
        # at INSERT time so it captures whatever lat/lng the transformer set.
        lat = values.get("latitude")
        lon = values.get("longitude")
        if lat is not None and lon is not None:
            values["location"] = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)

        stmt = pg_insert(listings).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_listing_source_external",
            set_=conflict_update_set(values),
        )
        session.execute(stmt)
        count += 1

    session.commit()

    if skipped:
        for src, n in skipped.items():
            logger.warning("skipped %d rows from unknown source: %r", n, src)

    # Backfill coordinates for any listing that landed without a point (e.g.
    # wohninberlin, whose cards expose no lat/lng). Part of the transform, not a
    # separate step: the upserts are committed above, so this pass picks up every
    # `location IS NULL` row and geocodes its address. Best-effort — a geocoder
    # outage warns but never fails silver (gold still runs on listings that have
    # a point).
    try:
        # 1. Drop invalid stored points (out-of-Berlin / 0,0) so they re-geocode.
        # 2. Cheaply distribute any valid `location` geometry into the lat/lon
        #    scalars the search layer filters on (no API call).
        # 3. Geocode the rows that still have no point at all.
        reset = _reset_invalid_location(session)
        if reset:
            logger.info("Silver: reset %d invalid stored locations", reset)
        synced = _fill_latlon_from_location(session)
        if synced:
            logger.info(
                "Silver: filled lat/lon from existing location for %d listings",
                synced,
            )
        geocoded, failed, no_addr = _geocode_missing(session)
        logger.info(
            "Silver: geocoded %d listings (%d failed, %d had no address)",
            geocoded,
            failed,
            no_addr,
        )
    except Exception as exc:
        logger.warning(
            "Silver: geocoding pass skipped: %s. "
            "Coordinate-less listings stay invisible until the next run.",
            exc,
        )

    return count


# One window-function DELETE collapses duplicate flats — same title + address,
# any source — down to a single survivor. Companies repost the same apartment
# with a fresh `external_id` (often just a new price), so the UPSERT key
# `(source_name, external_id)` lets each repost in as its own row; this is the
# second pass that removes them. Run AFTER `transform` and BEFORE gold so deleted
# rows never get enriched. It must run every silver.run, not just once: bronze
# `raw_listings` rows survive a silver delete (FK is ON DELETE SET NULL), and
# `transform` reprocesses all of bronze, so a one-off cleanup would be re-undone
# on the next run.
#
# Survivor = the row that still carries coordinates (so it stays on the map / in
# search), then newest `scraped_at`; `ingested_at`/`id` only break ties for
# determinism. Only rows with a non-blank title AND address participate — NULL or
# empty values must never collapse together.
_DEDUP_SQL = text(
    """
    DELETE FROM listings
    WHERE id IN (
        SELECT id FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY btrim(title), btrim(address)
                       ORDER BY (location IS NOT NULL) DESC,
                                scraped_at DESC, ingested_at DESC, id DESC
                   ) AS rn
            FROM listings
            WHERE title IS NOT NULL AND btrim(title) <> ''
              AND address IS NOT NULL AND btrim(address) <> ''
        ) ranked
        WHERE rn > 1
    )
    """
)


def deduplicate(session: Session) -> int:
    """Delete duplicate listings (same title + address, any source), keeping one.

    Returns the number of rows deleted. Idempotent — a second call deletes 0.
    """
    result = session.execute(_DEDUP_SQL)
    session.commit()
    return result.rowcount


# ---------------------------------------------------------------------------
# Geocoding fallback — fill coordinates for listings that arrived without a
# point, by geocoding their address through an open API (Nominatim by default).
# Lives here, in the transform module, and runs as the tail of `transform()`.
# Configure via env (all optional): NOMINATIM_BASE_URL, GEOCODER_USER_AGENT,
# GEOCODER_RATE_LIMIT_S. Point the base URL at a self-hosted Nominatim / Photon
# instance to lift the public ~1 req/s limit for bulk backfills.
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "https://nominatim.openstreetmap.org"
_DEFAULT_USER_AGENT = "flat-chat-geocoder/1.0 (+https://github.com/nake-b/flat-chat)"
_DEFAULT_RATE_LIMIT_S = 1.0

# Nominatim viewbox is "lon_min,lat_min,lon_max,lat_max"; with bounded=1 it
# restricts results to this box. Reuse the same Berlin envelope that
# clean_berlin_coords validates against so the bias and the validation agree.
_BERLIN_VIEWBOX = f"{BERLIN_LON_MIN},{BERLIN_LAT_MIN},{BERLIN_LON_MAX},{BERLIN_LAT_MAX}"

# Transient statuses worth retrying: rate-limit + the 5xx family. A 4xx like 400
# (bad query) is NOT retryable and must surface. Mirrors platinum/embed.py.
_RETRYABLE_STATUS = {429, 502, 503, 504}
_MAX_ATTEMPTS = 5


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def _retry_wait(retry_state) -> float:
    """Honor a numeric ``Retry-After`` header when present; else exponential
    backoff with a 30 s ceiling."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass  # HTTP-date form — fall through to backoff
    return wait_exponential(multiplier=1, max=30)(retry_state)


def _seconds_to_wait(
    last_request_at: float | None, now: float, rate_limit_s: float
) -> float:
    """Pure rate-limit math: seconds to sleep before the next request so calls
    are spaced by at least ``rate_limit_s``. Zero if no prior call or disabled."""
    if rate_limit_s <= 0 or last_request_at is None:
        return 0.0
    return max(0.0, rate_limit_s - (now - last_request_at))


def _compose_query(
    address: str | None,
    postal_code: str | None,
    district: str | None,
    city: str | None,
) -> str | None:
    """Build a free-form geocoder query from a listing's address parts.

    Joins the non-empty parts (whitespace-collapsed, case-insensitively
    de-duplicated), anchors to Berlin + Germany, and returns ``None`` when there
    is nothing usable. A postcode/district-only listing yields an area-centroid
    query — approximate, but better than staying invisible.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (address, postal_code, district, city):
        if not raw:
            continue
        part = " ".join(raw.split())
        key = part.lower()
        if not part or key in seen:
            continue
        seen.add(key)
        parts.append(part)
    if not parts:
        return None
    if not any("berlin" in p.lower() for p in parts):
        parts.append("Berlin")
    parts.append("Germany")
    return ", ".join(parts)


class _NominatimGeocoder:
    """Thin connection-reusing client for a Nominatim-compatible geocoder.

    Self-throttles to ``rate_limit_s`` between requests (the public instance
    allows ~1 req/s) and sends an identifying ``User-Agent`` (its policy rejects
    generic agents). Use as a context manager. Swap the base URL via
    ``NOMINATIM_BASE_URL`` to target a self-hosted / Photon instance.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        user_agent: str | None = None,
        rate_limit_s: float | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("NOMINATIM_BASE_URL", _DEFAULT_BASE_URL)
        ).rstrip("/")
        ua = user_agent or os.environ.get("GEOCODER_USER_AGENT", _DEFAULT_USER_AGENT)
        if rate_limit_s is None:
            rate_limit_s = float(
                os.environ.get("GEOCODER_RATE_LIMIT_S", _DEFAULT_RATE_LIMIT_S)
            )
        self.rate_limit_s = rate_limit_s
        self._client = httpx.Client(headers={"User-Agent": ua}, timeout=timeout)
        self._last_request_at: float | None = None

    def _throttle(self) -> None:
        wait = _seconds_to_wait(
            self._last_request_at, time.monotonic(), self.rate_limit_s
        )
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=_retry_wait,
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        reraise=True,
    )
    def _get(self, query: str) -> list[dict]:
        response = self._client.get(
            f"{self.base_url}/search",
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "de",
                "viewbox": _BERLIN_VIEWBOX,
                "bounded": 1,
                "addressdetails": 0,
            },
        )
        response.raise_for_status()
        return response.json()

    def geocode(self, query: str) -> tuple[float, float] | None:
        """Geocode one query. Returns ``(lat, lon)`` from the top result, or
        ``None`` when nothing is found / the row is malformed."""
        self._throttle()
        results = self._get(query)
        if not results:
            return None
        top = results[0]
        try:
            return float(top["lat"]), float(top["lon"])
        except KeyError, TypeError, ValueError:
            logger.warning("geocoder returned a malformed row: %r", top)
            return None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> _NominatimGeocoder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


_MISSING_COORDS_SQL = text(
    """
    SELECT id::text, address, postal_code, district, city
    FROM listings
    WHERE location IS NULL
    ORDER BY scraped_at DESC NULLS LAST
    """
)

_SET_COORDS_SQL = text(
    """
    UPDATE listings
    SET latitude = :lat,
        longitude = :lon,
        location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
        updated_at = now()
    WHERE id = :id
    """
)

# Clear a stored `location` (and its scalars) when the geometry falls outside
# Berlin — most often a 0/0 null-island point set out-of-band. Such a row is
# stuck otherwise: the bbox-guarded lat/lon backfill won't propagate it, and the
# API geocode pass skips it (it keys on `location IS NULL`). Resetting it to NULL
# lets the geocode pass re-derive a real point from the address. Runs first.
_RESET_INVALID_LOCATION_SQL = text(
    """
    UPDATE listings
    SET location = NULL, latitude = NULL, longitude = NULL, updated_at = now()
    WHERE location IS NOT NULL
      AND NOT (
        ST_Y(location) BETWEEN :lat_min AND :lat_max
        AND ST_X(location) BETWEEN :lon_min AND :lon_max
      )
    """
)


def _reset_invalid_location(session: Session) -> int:
    """NULL out `location`/`latitude`/`longitude` for rows whose stored point is
    outside Berlin (incl. 0/0), so the geocode pass re-derives them from the
    address. Returns rows reset. Idempotent: a valid point no longer matches."""
    result = session.execute(
        _RESET_INVALID_LOCATION_SQL,
        {
            "lat_min": BERLIN_LAT_MIN,
            "lat_max": BERLIN_LAT_MAX,
            "lon_min": BERLIN_LON_MIN,
            "lon_max": BERLIN_LON_MAX,
        },
    )
    session.commit()
    return result.rowcount


# Distribute an existing `location` geometry back into the `latitude`/`longitude`
# columns when those are NULL. Normally all three are written together, but a row
# can carry a `location` without scalar coords (e.g. a point set out-of-band);
# the backend's search filters on `latitude`/`longitude IS NOT NULL`, so such a
# row is enriched by gold yet still invisible on the map until its scalars are
# filled. Guard with the Berlin bbox so only valid points propagate (a bad
# geometry leaves the scalars NULL → stays correctly hidden). Pure SQL, no API.
_FILL_LATLON_SQL = text(
    """
    UPDATE listings
    SET latitude = ST_Y(location),
        longitude = ST_X(location),
        updated_at = now()
    WHERE location IS NOT NULL
      AND (latitude IS NULL OR longitude IS NULL)
      AND ST_Y(location) BETWEEN :lat_min AND :lat_max
      AND ST_X(location) BETWEEN :lon_min AND :lon_max
    """
)


def _fill_latlon_from_location(session: Session) -> int:
    """Backfill `latitude`/`longitude` from the stored `location` geometry for
    rows that have a Berlin point but NULL scalar coords. Returns rows updated.
    Idempotent: a row with both scalars set no longer matches."""
    result = session.execute(
        _FILL_LATLON_SQL,
        {
            "lat_min": BERLIN_LAT_MIN,
            "lat_max": BERLIN_LAT_MAX,
            "lon_min": BERLIN_LON_MIN,
            "lon_max": BERLIN_LON_MAX,
        },
    )
    session.commit()
    return result.rowcount


def _geocode_missing(
    session: Session,
    geocoder: _NominatimGeocoder | None = None,
    *,
    limit: int | None = None,
) -> tuple[int, int, int]:
    """Geocode every listing with no ``location`` and write the point back.

    Returns ``(geocoded, failed, skipped)``:
      - geocoded: a Berlin-valid point was found and written
      - failed:   the geocoder found nothing, or a point outside Berlin
      - skipped:  the listing has no usable address to geocode from

    Idempotent: only ``location IS NULL`` rows are considered, so re-runs touch
    only real gaps. Commits per row (resumable at ~1 req/s). ``geocoder`` is
    injectable so tests pass a stub and never hit the network.
    """
    rows = session.execute(_MISSING_COORDS_SQL).all()
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        return 0, 0, 0

    owns_geocoder = geocoder is None
    geocoder = geocoder or _NominatimGeocoder()
    geocoded = failed = skipped = 0
    try:
        for listing_id, address, postal_code, district, city in rows:
            query = _compose_query(address, postal_code, district, city)
            if query is None:
                skipped += 1
                continue
            result = geocoder.geocode(query)
            if result is None:
                failed += 1
                logger.info("no geocode result for %s (%r)", listing_id, query)
                continue
            lat, lon = clean_berlin_coords(result[0], result[1])
            if lat is None or lon is None:
                failed += 1
                logger.info(
                    "geocode outside Berlin for %s (%r) -> %s",
                    listing_id,
                    query,
                    result,
                )
                continue
            session.execute(_SET_COORDS_SQL, {"id": listing_id, "lat": lat, "lon": lon})
            session.commit()
            geocoded += 1
            logger.info("geocoded %s -> (%.5f, %.5f)", listing_id, lat, lon)
    finally:
        if owns_geocoder:
            geocoder.close()

    return geocoded, failed, skipped
