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

from flat_chat.listings.types import GtfsMode, NearSpec, WaterKind


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
    """Filter listings by proximity to (or catchment of) a school.

    Two intents this filter expresses:

    - **Proximity** (default): "near a school within X meters", optionally
      filtered by `school_type`. `school_type` matches against the Berlin
      Schulverzeichnis category (e.g. "Grundschule", "Gymnasium", "ISS",
      "Berufsschule") via case-insensitive substring; the vocabulary is
      open so we don't enumerate it.
    - **Catchment membership**: set `requires_catchment=True` for the
      legal-attendance question ("which primary school is this kid
      assigned to?"). Berlin tiles primary catchments across the whole
      city, so this filter is usually combined with proximity for
      meaningful narrowing.

    Both checks AND together when both are requested.
    """

    distance: NearSpec = "near"
    school_type: str | None = None
    requires_catchment: bool = False


class HospitalFilter(BaseModel):
    """Filter listings by proximity to a hospital.

    `tier` defaults to `"plan_hospital"` for the filter — when the user
    says "near a hospital" they usually mean emergency-care reachability,
    which is the Krankenhausplan network. The detail-enrichment path
    uses `"any"` so the user sees specialty clinics too.
    """

    distance: NearSpec = "near"
    tier: Literal["plan_hospital", "any"] = "plan_hospital"


class KitaFilter(BaseModel):
    """Filter listings by proximity to a daycare (Kita).

    A Kita has no sub-type to filter on (unlike schools, where
    `SchoolFilter.school_type` selects Grundschule / Gymnasium / …), so
    this is purely a distance filter. "near a kita" → `{"distance": "near"}`.
    """

    distance: NearSpec = "near"


class WaterFilter(BaseModel):
    """Filter listings by proximity to a water body, optionally by kind.

    `kinds` narrows to specific water types — `["lake"]` = standing water
    (Seen/Teiche), `["river"]` = flowing water (rivers, canals, the Spree),
    `["harbor"]` = Hafen. Omit `kinds` for "near ANY water". Multiple kinds
    combine with OR ("near a lake or river"). The Berlin GDI source has no
    distinct "canal" class — canals are flowing water, so map them to
    `"river"`. `kinds` resolve to raw German `water_kind` values via
    `listings.labels.WATER_KIND_TO_RAW`.
    """

    distance: NearSpec = "near"
    kinds: list[WaterKind] | None = None
