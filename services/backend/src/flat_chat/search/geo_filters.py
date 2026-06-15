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

from flat_chat.listings.types import GtfsMode, MssDynamics, MssStatus, NearSpec


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


class MssFilter(BaseModel):
    """Filter listings by neighbourhood socioeconomic character (Sozialmonitoring).

    `status_min` is a *minimum* status floor — `"mixed"` matches mixed
    AND affluent areas. `dynamics` is exact — `"improving"` only matches
    areas trending up faster than Berlin overall.

    These are neighbourhood-character labels, NOT a desirability score.
    A renter seeking "up-and-coming" wants `status_min="disadvantaged"`
    + `dynamics="improving"` (the classic gentrification signature).
    """

    status_min: MssStatus = "lower-income"
    dynamics: MssDynamics | None = None


# Used by `SearchService` to translate `mss.status_min` into a SQL
# threshold — higher value = more affluent. Co-located here because it's
# inherent to the MSS status enum (not a tweakable threshold).
MSS_STATUS_RANK: dict[MssStatus, int] = {
    "disadvantaged": 0,
    "lower-income": 1,
    "mixed": 2,
    "affluent": 3,
}
