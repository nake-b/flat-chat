"""Map-overlay vocabulary — a geometry the agent draws on the map (the Spree, a
U-Bahn line, a park/lake polygon, a Bezirk, the inside-the-ring zone).

Lives in the leaf `listings` layer so both `search/` (the resolvers that build
overlays — `PlaceService`, `TransitOverlayService`) and `chat/` (SessionState +
tools) can import it without breaking the import-direction rule
(`chat → search → listings`). A symbol both `search/` and `chat/` import must
live at or below `search/`; this is that home.

The backend sets only SEMANTICS (`kind` / `label` / `geojson` / `points`);
APPEARANCE (colors, opacity, line vs fill, station dots) is the frontend's job,
keyed off `kind` + the geojson geometry type in
`services/frontend/src/state/overlayStyles.ts`. No `style_hint` — that would be
a second, drifting source of truth. See agent-compound-docs/decisions/
map-overlays.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

OverlayKind = Literal["place", "transit_line", "bezirk", "ring", "parks"]
# "search" = redrawn each search (tied to a filter); "pinned" = user/agent
# explicit, sticky; "lens" = the anchor a lens drew (removed when the lens is
# cleared / its anchor changes, so lens cleanup never touches user pins).
OverlayOrigin = Literal["search", "pinned", "lens"]

# Geometry simplification applied when resolving an overlay to GeoJSON (shared
# by every resolver — PlaceService, TransitOverlayService). Douglas-Peucker
# tolerance in degrees (~0.00005° ≈ 5 m at Berlin's latitude) drops redundant
# vertices on long lines/polygons (the Spree, a Bezirk) while preserving shape;
# `OVERLAY_COORD_DIGITS=5` rounds coordinates to ~1 m. Both keep the GeoJSON
# that rides the AG-UI state snapshot small. Use with
# `ST_SimplifyPreserveTopology` (never breaks rings; a no-op for points).
OVERLAY_SIMPLIFY_TOLERANCE = 0.00005
OVERLAY_COORD_DIGITS = 5

# When a named place is fragmented into many identically-named footprints (e.g.
# a university campus stored as one ALKIS building per row, all named
# "Technische Universität Berlin"), drawing one row looks arbitrary. The overlay
# resolver unions every SAME-kind, SAME-name footprint within this radius of the
# resolved hit into one shape — the *local* cluster only, so a distant same-name
# cluster elsewhere in the city is excluded. Exact-name (not fuzzy) is
# deliberate: within a campus radius sit unrelated neighbours ("UdK Berlin",
# theatres, an embassy) that a similarity floor would wrongly swallow. A
# unique-named place unions to itself (no-op). Metres.
OVERLAY_CLUSTER_RADIUS_M = 500

# A seed alias is a representative POINT ("TU Berlin", "Görli", "Kotti") that
# sits ON its real target. When an overlay resolves to such a point, we snap to
# the nearest footprint (polygon/line, any kind) within this radius and draw
# that — "TU Berlin" → the Hauptgebäude building it marks, "Görli" → the
# Görlitzer Park polygon. The building/park names don't match the alias, so this
# proximity snap (not name matching) is what actually finds the target. Falls
# back to the point itself if nothing solid is within range. Metres.
OVERLAY_SNAP_RADIUS_M = 150


class OverlayPoint(BaseModel):
    """A labelled point that decorates an overlay — e.g. a transit line's
    stations. Semantics only (name + position); the frontend decides how to
    draw it (dot + pulse), same division of labour as `MapOverlay` itself.
    Coordinates are rounded to `OVERLAY_COORD_DIGITS` to keep the state snapshot
    small (a 24-stop line is ~24 points)."""

    label: str
    lon: float
    lat: float


class MapOverlay(BaseModel):
    """One geometry drawn on the map, mirrored to the frontend via SessionState.

    `id` is stable per logical overlay (e.g. `"place:park:42"`,
    `"transit_line:U7"`) so re-drawing replaces rather than duplicates, and the
    frontend can dismiss by id. `origin` drives the clear policy:
      - `"search"` overlays are derived from the active search's spatial anchors
        (`near_place_ref` / `transit.lines`) and are REPLACED on the next search.
      - `"pinned"` overlays come from an explicit `show_on_map` (or proactive
        agent draw) and PERSIST across searches until removed/dismissed.
    `geojson` is a GeoJSON geometry or Feature — source-agnostic (a
    `named_places` shape or a transit route shape look identical here).
    `points` are optional decorations on the geometry — currently the served
    stations of a transit line (the frontend draws them as dots + line badges);
    empty for everything else.
    """

    id: str
    kind: OverlayKind
    label: str
    geojson: dict
    origin: OverlayOrigin = "search"
    points: list[OverlayPoint] = []
