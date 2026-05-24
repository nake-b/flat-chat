"""Frontend-facing mirror of the active result set.

`ResultSet` (in `state.py`) owns LLM-facing prose/CSV/detail formatting; the
agent reads from it. `UiState` is the parallel projection the React frontend
reads via AG-UI shared state — typed apartments with lat/lng, the currently
expanded card id, and a rolling tool-call log used for inline pills.

The two projections share source data (the DataFrame from
`SearchService.search()`) but never collapse into one: the LLM never sees
`UiState`, the UI never sees `ResultSet`.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, Field


class UiApartment(BaseModel):
    """A single listing as the frontend renders it on the map and in cards."""

    id: str
    lat: float | None = None
    lng: float | None = None
    price_warm_eur: float | None = None
    rooms: float | None = None
    area_sqm: float | None = None
    district: str | None = None
    title: str | None = None
    address: str | None = None
    source_url: str | None = None
    image_url: str | None = None

    @classmethod
    def from_dataframe_row(cls, row: pd.Series) -> UiApartment:
        """Project a single search-result row into the UI shape."""
        return cls(
            id=str(row["id"]),
            lat=_opt_float(row.get("latitude")),
            lng=_opt_float(row.get("longitude")),
            price_warm_eur=_opt_float(row.get("price_warm_eur")),
            rooms=_opt_float(row.get("rooms")),
            area_sqm=_opt_float(row.get("area_sqm")),
            district=_opt_str(row.get("district")),
            title=_opt_str(row.get("title")),
            address=_opt_str(row.get("address")),
            source_url=_opt_str(row.get("source_url")),
            image_url=None,  # populated from raw.images JSONB later; deferred
        )


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

    tool_logs: list[str] = Field(default_factory=list)
    """Rolling lifecycle entries for inline tool-call pills ("Searching …")."""


def _opt_float(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _opt_str(val: object) -> str | None:
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    text = str(val)
    return text or None
