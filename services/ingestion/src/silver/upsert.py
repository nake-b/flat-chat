"""Pure helpers for the silver UPSERT — no DB/config imports, so they're
unit-testable without a database or environment."""

from __future__ import annotations

from typing import Any

# The conflict key — never refreshed on UPSERT (it's what we matched on).
_CONFLICT_KEYS = ("source_name", "external_id")

# Columns carrying a listing's geocoded point. A source may not expose
# coordinates on a re-transform — e.g. wohninberlin, whose points are geocoded
# out-of-band after the scrape and never live in bronze. Overwriting these with
# NULL on conflict would strip the listing's location on the next `silver.run`,
# and because the map card is built from the latitude/longitude columns
# (search/service.py) a coordinate-less listing is filtered out of search
# results entirely. So we never clobber a stored coordinate with NULL: refresh
# these columns only when the incoming row actually carries a point.
COORD_COLS = ("latitude", "longitude", "location")


def conflict_update_set(values: dict[str, Any]) -> dict[str, Any]:
    """Columns to refresh in `ON CONFLICT DO UPDATE`.

    Everything in `values` except the conflict key — and minus the coordinate
    columns when the incoming row has no point, so an existing point survives.
    A row that *does* carry coordinates still overwrites the stored ones.
    """
    skip = set(_CONFLICT_KEYS)
    if values.get("latitude") is None or values.get("longitude") is None:
        skip |= set(COORD_COLS)
    return {k: v for k, v in values.items() if k not in skip}
