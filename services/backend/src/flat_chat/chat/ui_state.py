"""Frontend-facing mirror of the active result set.

`LlmResultSetView` (in `chat/llm_context.py`) owns LLM-facing prose/CSV/detail
formatting; the agent reads from it. `UiState` is the parallel projection the
React frontend reads via AG-UI shared state — typed apartments with lat/lng
and the currently expanded card id.

The two projections share source data (the DataFrame from
`SearchService.search()`) but never collapse into one: the LLM never sees
`UiState`, the UI never sees `LlmResultSetView`.

Status-pill copy is NOT mirrored here. The frontend derives lifecycle labels
directly from AG-UI tool-call events via a tool-name → label registry
(`services/frontend/src/state/toolStatus.ts`). Keeping that string-building
on the frontend means adding a new tool is one registry line, with zero
backend churn — and tools stay pure data mutators.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pandas as pd
from pydantic import BaseModel, Field

from flat_chat.search.buckets import DensityLabel, NoiseLabel
from flat_chat.search.geo_filters import ListingContext, MssDynamics, MssStatus


class UiApartment(BaseModel):
    """A single listing as the frontend renders it on the map and in cards."""

    id: str
    lat: float | None = None
    lng: float | None = None
    # Money — full breakdown so the detail panel can show what the user actually pays
    price_warm_eur: float | None = None
    price_cold_eur: float | None = None
    nebenkosten_eur: float | None = None
    kaution_eur: float | None = None
    # Size
    rooms: float | None = None
    bedrooms: int | None = None
    area_sqm: float | None = None
    # Building / availability
    floor: int | None = None
    floors_total: int | None = None
    available_from: str | None = None  # ISO date string (datetime → isoformat)
    listing_type: str | None = None
    # Location
    district: str | None = None
    title: str | None = None
    address: str | None = None
    # Amenities (most-asked subset surfaced as chips; rest stays in raw)
    wbs_required: bool | None = None
    is_furnished: bool | None = None
    has_balcony: bool | None = None
    has_kitchen: bool | None = None
    has_elevator: bool | None = None
    has_garden: bool | None = None
    # Energy — shown in the detail panel when present
    heating: str | None = None
    energy_consumption_kwh: float | None = None
    # Listing source signal — "private" / "agency" / "commercial"
    lister_type: str | None = None
    # Outbound link
    source_url: str | None = None
    # Image plumbing deferred — populated from raw.images JSONB in a later change.
    image_url: str | None = None
    # Geo-context chips — populated by GeoContextService.apply_chips() via
    # LATERAL joins during search. None when no nearby data or no location.
    # All English labels — German source labels never leak past the service
    # boundary (MSS_*_DE_TO_EN handles the translation in service.py).
    nearest_transit_line: str | None = None
    walk_min_to_transit: int | None = None
    nearest_park_name: str | None = None
    nearest_park_m: int | None = None
    # Narrowed to the canonical Literal types from `search/buckets.py` +
    # `search/geo_filters.py` — the same producers (`bucket_noise`,
    # `bucket_density`, `MSS_*_DE_TO_EN`) only ever emit values in the set.
    # Pydantic surfaces any future drift at the boundary instead of letting
    # the frontend's narrower TS types silently lose a switch-case branch.
    noise_label: NoiseLabel | None = None
    density_label: DensityLabel | None = None
    mss_status_label: MssStatus | None = None
    mss_dynamics_label: MssDynamics | None = None

    @classmethod
    def from_dataframe_row(cls, row: pd.Series) -> UiApartment:
        """Project a single search-result row into the UI shape.

        Driven by `_PROJECTORS` below — one tuple row per field. Adding a
        new field is one row, not three lines of repetitive `_opt_X(row.get(...))`.
        """
        kwargs: dict[str, object] = {
            field: caster(row.get(col)) for field, col, caster in _PROJECTORS
        }
        kwargs["id"] = str(row["id"])
        kwargs["image_url"] = None  # Plumbed from raw.images JSONB later.
        return cls(**kwargs)


class UiState(BaseModel):
    """Shared state mirrored between backend (truth) and frontend (read).

    AG-UI streams JSON Patch deltas of this object to the frontend on every
    mutation by an agent tool. The frontend's CopilotKit store applies the
    patches; `useCoAgent<UiState>()` exposes the result to React components.
    Write-back: when the user clicks a card, `setState({active_id})` flows
    back so the agent's next turn knows what the user is looking at.
    """

    results: list[UiApartment] = Field(default_factory=list)
    """Apartments currently displayed on the map and in the card strip."""

    active_id: str | None = None
    """The id of the card currently expanded into detail view, if any."""

    active_listing_context: ListingContext | None = None
    """Full geo-context blob for the active listing — populated when the
    agent calls `get_listing_details(id)`. Cleared on next search."""


def _opt_float(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    try:
        return float(val)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None


def _opt_int(val: object) -> int | None:
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    try:
        return int(val)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None


def _opt_str(val: object) -> str | None:
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    text = str(val)
    return text or None


def _opt_bool(val: object) -> bool | None:
    # SQLAlchemy returns Python bools directly; pandas may surface them as
    # objects. None / NaN both map to None so the chip layer can use strict
    # `=== true` to render only confirmed amenities.
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    if isinstance(val, bool):
        return val
    return bool(val)


def _opt_iso(val: object) -> str | None:
    # available_from is a TIMESTAMP in the new schema. Pandas surfaces it as
    # a pd.Timestamp; SQLAlchemy may also hand back a datetime. Normalize to
    # an ISO string so the frontend can format with Intl.DateTimeFormat.
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    if isinstance(val, pd.Timestamp):
        if pd.isna(val):
            return None
        return val.isoformat()
    if isinstance(val, datetime):
        return val.isoformat()
    text = str(val)
    return text or None


# Single source of truth for the DataFrame-row → UiApartment projection.
# Tuple rows of (field_name, df_column_name, caster). `id` and `image_url`
# are special-cased in `from_dataframe_row` (one required, one deferred).
_PROJECTORS: tuple[tuple[str, str, Callable[[object], object]], ...] = (
    ("lat", "latitude", _opt_float),
    ("lng", "longitude", _opt_float),
    ("price_warm_eur", "price_warm_eur", _opt_float),
    ("price_cold_eur", "price_cold_eur", _opt_float),
    ("nebenkosten_eur", "nebenkosten_eur", _opt_float),
    ("kaution_eur", "kaution_eur", _opt_float),
    ("rooms", "rooms", _opt_float),
    ("bedrooms", "bedrooms", _opt_int),
    ("area_sqm", "area_sqm", _opt_float),
    ("floor", "floor", _opt_int),
    ("floors_total", "floors_total", _opt_int),
    ("available_from", "available_from", _opt_iso),
    ("listing_type", "listing_type", _opt_str),
    ("district", "district", _opt_str),
    ("title", "title", _opt_str),
    ("address", "address", _opt_str),
    ("wbs_required", "wbs_required", _opt_bool),
    ("is_furnished", "is_furnished", _opt_bool),
    ("has_balcony", "has_balcony", _opt_bool),
    ("has_kitchen", "has_kitchen", _opt_bool),
    ("has_elevator", "has_elevator", _opt_bool),
    ("has_garden", "has_garden", _opt_bool),
    ("heating", "heating", _opt_str),
    ("energy_consumption_kwh", "energy_consumption_kwh", _opt_float),
    ("lister_type", "lister_type", _opt_str),
    ("source_url", "source_url", _opt_str),
    ("nearest_transit_line", "nearest_transit_line", _opt_str),
    ("walk_min_to_transit", "walk_min_to_transit", _opt_int),
    ("nearest_park_name", "nearest_park_name", _opt_str),
    ("nearest_park_m", "nearest_park_m", _opt_int),
    ("noise_label", "noise_label", _opt_str),
    ("density_label", "density_label", _opt_str),
    ("mss_status_label", "mss_status_label", _opt_str),
    ("mss_dynamics_label", "mss_dynamics_label", _opt_str),
)
