"""SessionState — canonical in-memory representation of the active conversation.

One per conversation thread. Lives in `ChatSession.state` and gets mirrored
to the frontend over the AG-UI stream as full `STATE_SNAPSHOT` events — NOT
JSON-Patch deltas; tools emit a fresh snapshot per mutation (see
`chat/service.py:_return_with_state`). The same object serves three readers:
  - The LLM (via `chat/llm_context.py:build_dynamic_state_prompt` — emits
    `<current_state>` + `<user_focus>` XML each turn)
  - The frontend (renders markers/cards/detail panel from these fields)
  - The agent's tools (`open_listing` / `get_result_page` resolve 1-based
    indices against `result_markers`)

Three tiers, co-located:
  - `result_markers` — tier-1, EVERY match (≤ MARKER_CAP), thin
    {id,lat,lng,lens_value}. The map plots these; the count is its length.
  - `preview_cards` — tier-2, the top-N full cards kept hot for the LLM and
    the card strip's first paint. The rest hydrate on demand by id via
    `GET /api/listings?ids=…&view=card`.
  - `active_listing_detail` — tier-3, the open listing.

Wire compaction: `result_markers` serializes to a columnar dict
`{ids,lats,lngs,values}` (~200 KB at 5k vs ~425 KB array-of-objects) via
`@field_serializer`, and decodes back via the paired `@field_validator`. The
pair MUST be symmetric: the AG-UI envelope echoes this state back every turn
and `chat/service.py:_extract_incoming_state` calls `model_validate` on it —
without the validator, validation of the columnar shape fails and all
incoming state (incl. the frontend's `active_id` write-back) is dropped.

Architecture-decision doc: `agent-compound-docs/decisions/session-state-design.md`
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import (
    BaseModel,
    Field,
    computed_field,
    field_serializer,
    field_validator,
)

from flat_chat.listings.context import (
    ListingCard,
    ListingDetail,
    Marker,
)
from flat_chat.listings.lenses import ActiveLens, MarkerLens, marker_lens_for
from flat_chat.listings.overlays import MapOverlay
from flat_chat.search.schemas import ResultFacets, SearchParams, SearchResult


class SessionState(BaseModel):
    """Shared state mirrored backend (truth) → frontend (read) as full
    STATE_SNAPSHOTs. Write-back: on card click the frontend sets `active_id`
    (and HTTP-fetches the detail into `active_listing_detail`) so the agent's
    next turn already has both.
    """

    # The applied search (the question)
    search_params: SearchParams | None = None
    """The filters the LLM used for the active result set. Co-located with the
    answer so count/order/filter prose comes from one place."""

    total_results: int = 0
    """Total listings matching the active search. Equals `len(result_markers)`
    unless the search hit MARKER_CAP, in which case it's a real COUNT(*)."""

    # Tier-1: EVERY match as a thin marker (the canonical answer + map source).
    result_markers: list[Marker] = Field(default_factory=list)
    """Every match, ≤ MARKER_CAP. In-memory a plain list — index it as
    `[m.id for m in result_markers]` (there is NO `.ids` attribute; that name
    exists only in the columnar wire form). Serialized columnar (below)."""

    # Tier-2: top-N full cards kept hot (LLM context + card-strip first paint).
    preview_cards: list[ListingCard] = Field(default_factory=list)
    """The first PREVIEW_N cards. The rest hydrate on demand by id."""

    facets: ResultFacets | None = None
    """Aggregate stats (price/area ranges, neighbourhood counts) over the WHOLE
    filtered set — lets the agent ground whole-set summaries instead of
    extrapolating from `preview_cards`. Plain nested model: serializes/decodes
    via default Pydantic (no custom serializer, unlike `result_markers`)."""

    # The active interaction (the focus)
    active_id: str | None = None
    """The id of the card currently expanded into detail view, if any."""

    active_listing_detail: ListingDetail | None = None
    """Full tier-3 detail for `active_id`. Set by `open_listing` or the
    frontend's HTTP fetch on card click; cleared on next search. The agent
    reads it in the `<user_focus>` block to answer follow-ups without a tool
    call."""

    # Map overlays — geometries drawn alongside the markers (the Spree, U-Bahn
    # lines, a Bezirk, the inside-the-ring zone). Bidirectional shared state:
    # the agent adds/replaces overlays (content is agent-owned); the frontend
    # may only REMOVE them when the user dismisses one (handled in
    # `service.py:merge_incoming_state`). Surfaced to the agent each turn via
    # `build_dynamic_state_prompt` so it never blindly re-draws a dismissed one.
    map_overlays: list[MapOverlay] = Field(default_factory=list)
    """Geometries currently drawn on the map. GeoJSON rides as-is (no columnar
    packing); kept small via server-side simplification at resolution time."""

    # Active map lens, if any. Selection-slice input re-applied by the shared
    # lens derivation so the lens survives a follow-up `search_apartments` (which
    # otherwise rebuilds markers from SQL only). None = default price pins. This
    # is the SINGLE stored source of truth for the lens — `marker_lens` below is
    # computed from it, and the anchor overlay carries its own `origin="lens"`.
    active_lens: ActiveLens | None = None
    """The active lens input — a `TravelTimeLens` or `DistanceLens` (discriminated
    on `kind`). Set by an `apply_*_lens` tool; consumed by the matching provider
    (`RoutingService.resolve` / `DistanceService.resolve`)."""

    def apply_search_result(self, result: SearchResult) -> None:
        """Load a fresh `SearchService.search()` result into the snapshot
        (markers / preview / total / facets).

        Does NOT touch `search_params` — the caller owns the query (a lens
        re-derivation reuses the existing params; a new search sets them). Shared
        by `search_apartments` and the lens layer's `_refresh_result_set` so the
        four-field assignment lives in one place. Unpacks positionally (a
        `SearchResult` is a NamedTuple) so a plain 4-tuple works too."""
        markers, preview, total, facets = result
        self.result_markers = markers
        self.preview_cards = preview
        self.total_results = total
        self.facets = facets

    @computed_field  # type: ignore[prop-decorator]
    @property
    def marker_lens(self) -> MarkerLens:
        """What `result_markers[*].lens_value` means right now — the frontend
        descriptor (`key` → ramp, `label` → legend). DERIVED from `active_lens`
        (single source of truth, can't drift). Serialized to the frontend like a
        field; ignored on input (the frontend can't set it). Default `price_warm`
        when no lens is active."""
        return marker_lens_for(self.active_lens)

    @field_serializer("result_markers")
    def _serialize_markers(self, markers: list[Marker]) -> dict[str, list]:
        """Columnar wire form: drop repeated keys, round coords to ~1 m.

        ~200 KB at 5k markers vs ~425 KB array-of-objects. Applies on
        `model_dump()` (how `_return_with_state` builds the snapshot), so the
        compaction is automatic and lives only at the wire boundary."""
        return {
            "ids": [m.id for m in markers],
            "lats": [round(m.lat, 5) for m in markers],
            "lngs": [round(m.lng, 5) for m in markers],
            # The single active-lens scalar (warm rent by default, commute
            # minutes under a travel lens). `marker_lens` names it.
            "values": [m.lens_value for m in markers],
        }

    @field_validator("result_markers", mode="before")
    @classmethod
    def _decode_markers(cls, value: object) -> object:
        """Decode the columnar wire form back to a list of Marker dicts.

        Symmetric with `_serialize_markers` so `model_validate(model_dump(s))
        == s`. The frontend (CopilotKit) stores the wire shape and echoes it
        back in the AG-UI envelope; this runs every turn. A plain list passes
        through unchanged (in-process construction / tests).

        A length-mismatched payload RAISES (rather than silently truncating to
        the shortest column with `zip`): the columns are positional, so a
        mismatch means the wire form is corrupt and decoding it would drop or
        misalign markers. `chat/service.py:_extract_incoming_state` catches the
        error and falls back to the authoritative persisted server state — the
        documented "a malformed frontend push must not clobber server state"
        behaviour."""
        if isinstance(value, dict):
            columns = cast(dict[str, list[Any] | None], value)
            ids = columns.get("ids") or []
            n = len(ids)
            lats = columns.get("lats") or []
            lngs = columns.get("lngs") or []
            # The lens-value column is legitimately absent on old/empty
            # envelopes; default it to all-None. Accept the legacy "prices" key
            # so snapshots persisted before the lens generalization still
            # decode. Any present column, though, must match `ids`.
            values = columns.get("values")
            if values is None:
                values = columns.get("prices")
            if values is None:
                values = [None] * n
            if len(lats) != n or len(lngs) != n or len(values) != n:
                raise ValueError(
                    "columnar result_markers has mismatched column lengths "
                    f"(ids={n}, lats={len(lats)}, lngs={len(lngs)}, "
                    f"values={len(values)})"
                )
            return [
                {"id": i, "lat": la, "lng": lo, "lens_value": v}
                for i, la, lo, v in zip(ids, lats, lngs, values, strict=True)
            ]
        return value
