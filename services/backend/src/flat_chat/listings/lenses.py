"""Map-visualization *lens* vocabulary — the shared kernel for the lens layer.

A "lens" colours every `Marker` on the map by a single scalar (`Marker.lens_value`)
— travel time to a place, straight-line distance to a place, … one active at a
time. This module owns the leaf types both `search/`/`routing/` (which *produce*
lens values) and `chat/` (which *applies* them) import, so it sits in `listings/`
(the leaf layer) alongside `overlays.py`.

Two co-located concerns, same semantics/appearance split as `MapOverlay`:

  - `MarkerLens` — the thin *descriptor* mirrored to the frontend: `key` names
    the active scalar (the frontend keys its colour ramp / number format off it
    in `state/lensStyles.ts`); `label` is the human caption for the legend.
    The backend sets semantics only; the frontend owns appearance.

  - `ActiveLens` — the richer *input* the backend keeps to re-derive the lens
    after a follow-up `search_apartments` (which rebuilds markers from SQL and
    would otherwise drop the lens). A discriminated union over `kind`:
      - `TravelTimeLens` — anchor + mode + optional minutes cutoff (routing).
      - `DistanceLens`   — anchor + optional km cutoff (geometry, no routing).

Adding a lens is: a new union member here + a provider that returns
`{marker_id: value}` + one `state/lensStyles.ts` entry. See
`agent-compound-docs/decisions/lens-layer.md`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from flat_chat.listings.context import Marker


class MarkerLens(BaseModel):
    """Names the single scalar every `Marker.lens_value` currently carries — the
    active map visualization lens. Lives once in `SessionState`, not per marker.
    The backend sets SEMANTICS (`key` + human `label`); the frontend owns
    APPEARANCE (colour ramp / domain / number format) keyed off `key` in
    `state/lensStyles.ts` — same split as `MapOverlay`.

    Default `price_warm` → the frontend renders the plain pin (no heatmap);
    `commute_min` → a travel-time ramp; `distance_m` → a distance ramp. Adding a
    future lens is one registry entry + the backend populating that scalar."""

    key: str = "price_warm"
    label: str | None = None


class _LensBase(BaseModel):
    """Fields shared by every active-lens variant: the resolved anchor (a place
    the user cares about) plus the opaque `place_ref` it came from.

    `anchor_label` is the human name ("TU Berlin") used for the lens label and
    the anchor overlay; `anchor_lat`/`anchor_lng` are the resolved coordinates.
    `near_place_ref` is carried so the lens can be RE-APPLIED after a refinement:
    the distance provider re-resolves the place's exact geometry from it (a line
    or polygon, not just the centroid); the travel provider uses the coordinates
    directly and ignores it."""

    anchor_label: str
    anchor_lat: float
    anchor_lng: float
    near_place_ref: str | None = None


class TravelTimeLens(_LensBase):
    """The active commute lens — travel time from the anchor, via a routing
    engine (OSRM car / MOTIS transit). Consumed by `RoutingService.resolve`.

    `max_minutes` set → hard filter (drop listings over the limit); None →
    annotate + colour only. `schedule_as_of` / `schedule_stale` describe the
    TRANSIT timetable the result was computed against — the routing layer stamps
    them when a lapsed MOTIS feed forces the departure to be clamped, so the UI
    can say "schedule as of <date>". Both stay defaulted for car mode."""

    kind: Literal["travel_time"] = "travel_time"
    mode: Literal["transit", "car"] = "transit"
    max_minutes: int | None = None
    schedule_as_of: str | None = None
    schedule_stale: bool = False


class DistanceLens(_LensBase):
    """The active distance lens — straight-line (bird's-eye) distance from the
    anchor's exact geometry, computed with PostGIS `ST_Distance` (no routing
    engine). Consumed by `DistanceService.resolve`.

    `max_km` set → hard filter (drop listings farther than the limit); None →
    annotate + colour only. Values annotated onto markers are METRES (the
    frontend formats km); the cutoff is expressed in km for natural agent args."""

    kind: Literal["distance"] = "distance"
    max_km: float | None = None


ActiveLens = Annotated[TravelTimeLens | DistanceLens, Field(discriminator="kind")]
"""The one active lens input, if any. Discriminated on `kind` so it round-trips
through the AG-UI envelope (`model_validate`) and persisted session state."""


class LensValueProvider(Protocol):
    """A source of per-marker lens values. Both `RoutingService` (travel time via
    OSRM/MOTIS) and `DistanceService` (straight-line via PostGIS `ST_Distance`)
    implement it, so the lens layer (`chat/tools/lenses.py`) dispatches on
    `ActiveLens.kind` and treats them interchangeably — the abstraction is
    demonstrably not coupled to travel time.

    `resolve` returns `{marker_id: value}` in the provider's own units (minutes for
    travel, metres for distance). Markers with no value (unreachable / unrouted /
    no geometry) are simply absent from the dict."""

    async def resolve(
        self, markers: list[Marker], lens: ActiveLens
    ) -> dict[str, float]: ...


def marker_lens_for(lens: ActiveLens | None) -> MarkerLens:
    """The frontend descriptor for an active lens — a PURE function of it, so it's
    the single source of truth for `SessionState.marker_lens` (a `@computed_field`).

    `key` selects the ramp in `state/lensStyles.ts`; `label` is the legend caption.
    No active lens → the default `price_warm` (plain pins, no heatmap)."""
    if lens is None:
        return MarkerLens()
    if lens.kind == "travel_time":
        how = "car" if lens.mode == "car" else "public transport"
        return MarkerLens(
            key="commute_min", label=f"Minutes by {how} to {lens.anchor_label}"
        )
    return MarkerLens(key="distance_m", label=f"Kilometres to {lens.anchor_label}")
