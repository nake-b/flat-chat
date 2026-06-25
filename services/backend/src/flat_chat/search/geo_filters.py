"""Geo-context filter shapes — search input contract.

These Pydantic models are surfaced as nested arg objects in the
`search_apartments` agent tool. They describe WHAT the user wants
("near U-Bahn", "near a school", "improving area") in structured terms;
`SearchService` translates them into B-tree predicates against gold's
denormalised columns.

Used by:
  - `search.schemas.SearchParams` — bundled into the full tool-arg surface
  - `search.service.SearchService` — translates each filter into SQL

The per-listing detail shapes (NearestTransitStop, NearestSchool, …) that
used to live here have moved to `listings.context`. Filter inputs stay
here; detail outputs are a listings-domain concern.

Threshold doc: `agent-compound-docs/decisions/geo-context-thresholds.md`.
"""

from typing import Literal

from pydantic import BaseModel

from flat_chat.listings.types import GtfsMode, NearSpec


class TransitFilter(BaseModel):
    """Filter listings by proximity to a transit stop.

    Use `modes` to require a specific service type (`["u_bahn"]` = U-Bahn
    nearby; `["u_bahn", "s_bahn"]` = either nearby — OR semantics). Use
    `lines` to require a specific line name (`["U8"]` = a stop served by
    U8). Use `stop_name` to match by name fragment (`"Wittenau"` matches
    "S+U Wittenau"). All three can combine.
    """

    distance: NearSpec = "near"
    modes: list[GtfsMode] | None = None
    lines: list[str] | None = None
    stop_name: str | None = None


class SchoolFilter(BaseModel):
    """Filter listings by proximity to a school.

    `school_type` matches against the Berlin Schulverzeichnis category
    (e.g. "Grundschule", "Gymnasium", "ISS", "Berufsschule"). Left as
    free-text since the source vocabulary is open.

    Note: post-refactor the school filter is satisfied by checking whether
    the listing has a non-null `school_catchment` blob in gold (the
    listing falls inside a primary-school catchment). For a stricter
    proximity filter we'd add an indexed `nearest_school_m` column to
    gold — deferred until needed.
    """

    distance: NearSpec = "near"
    school_type: str | None = None


class HospitalFilter(BaseModel):
    """Filter listings by proximity to a hospital.

    `tier` defaults to `"plan_hospital"` for the filter — when the user
    says "near a hospital" they usually mean emergency-care reachability,
    which is the Krankenhausplan network. The detail-enrichment path
    uses `"any"` so the user sees specialty clinics too.
    """

    distance: NearSpec = "near"
    tier: Literal["plan_hospital", "any"] = "plan_hospital"


class LandmarkFilter(BaseModel):
    """Filter listings by proximity to a named landmark.

    `name` is a substring match against ALKIS building names (`buildings.name`).
    The filter matches listings whose `listings.location` is within `distance`
    meters of any matching ALKIS building footprint (`buildings.geom`).
    """

    name: str
    distance: NearSpec = "near"


class NamedGeoContextFilter(BaseModel):
    """Generic "near X by name" filter against gold's JSONB nearest lists.

    Use this when the user names a specific place and you want apartments
    within a distance band of that named feature.

    Notes:
      - For most kinds this is NOT a spatial join at query time. It matches
        against the precomputed Top-K nearest blobs in `listings_geo_context`.
      - `kind="landmark"` is special: it performs a true radius search against
        ALKIS building footprints (`buildings`) via ST_DWithin.
      - `name` is a case-insensitive substring match.
      - `distance` uses the same NearSpec ladder as the other geo filters.
    """

    kind: Literal[
        "landmark",  # ALKIS named buildings
        "school",
        "kita",
        "park",  # Grünflächen
        "playground",
        "water",
        "hospital",
        "transit_stop",
    ]
    name: str
    distance: NearSpec = "near"
