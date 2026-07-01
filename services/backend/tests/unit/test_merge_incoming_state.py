"""merge_incoming_state — the frontend↔backend ownership edit-site.

`chat/service.py:merge_incoming_state(persisted, incoming)` is the single place
the ownership rule lives: the persisted server state wins for agent-owned fields
(results, search_params, overlay *content*), while a fixed set of frontend-owned
fields may be layered from the incoming AG-UI envelope. Overlays are special —
the frontend may only **shrink** the set (dismissal), never inject geometry.

These tests lock that contract directly (no DB, no LLM):

  - `incoming is None`        → persisted wins untouched (parse failure / old client)
  - agent-owned fields        → never taken from the envelope
  - active_id / detail        → applied when present, ignored when None
  - overlays                  → intersect-by-id (dismissal sticks; additions ignored)
  - the result is a COPY      → mutating it can't corrupt the stored session

Regression guard for footgun #2 (see map-overlays.md): if a refactor breaks the
None-guard or flips the overlay intersect to a union, these fail.
"""

from __future__ import annotations

from flat_chat.chat.service import merge_incoming_state
from flat_chat.chat.session_state import SessionState
from flat_chat.listings.context import (
    ListingCard,
    ListingDetail,
    MarkerLens,
    TravelTimeFilter,
)
from flat_chat.listings.overlays import MapOverlay
from flat_chat.search.schemas import SearchParams


def _overlay(id_: str, *, origin: str = "search") -> MapOverlay:
    return MapOverlay(
        id=id_,
        kind="transit_line",
        label=id_.split(":")[-1],
        geojson={"type": "LineString"},
        origin=origin,
    )


def test_incoming_none_returns_persisted_untouched():
    persisted = SessionState(
        active_id="id-1",
        map_overlays=[_overlay("transit_line:U7")],
        total_results=42,
    )

    merged = merge_incoming_state(persisted, None)

    assert merged.active_id == "id-1"
    assert [o.id for o in merged.map_overlays] == ["transit_line:U7"]
    assert merged.total_results == 42
    # A copy, not the same object — so a later mutation can't leak into the store.
    assert merged is not persisted
    merged.total_results = 0
    assert persisted.total_results == 42


def test_active_id_and_detail_applied_when_present():
    persisted = SessionState(active_id="old", active_listing_detail=None)
    incoming = SessionState(
        active_id="new", active_listing_detail=ListingDetail(id="new")
    )

    merged = merge_incoming_state(persisted, incoming)

    assert merged.active_id == "new"
    assert merged.active_listing_detail is not None
    assert merged.active_listing_detail.id == "new"


def test_active_fields_not_clobbered_when_incoming_is_none():
    persisted = SessionState(
        active_id="keep", active_listing_detail=ListingDetail(id="keep")
    )
    incoming = SessionState(active_id=None, active_listing_detail=None)

    merged = merge_incoming_state(persisted, incoming)

    # None in the envelope means "no change", not "clear it".
    assert merged.active_id == "keep"
    assert merged.active_listing_detail is not None
    assert merged.active_listing_detail.id == "keep"


def test_agent_owned_fields_always_win_over_envelope():
    persisted = SessionState(
        search_params=SearchParams(rooms_min=2.0),
        preview_cards=[ListingCard(id="srv", title="server", lat=52.5, lng=13.4)],
        total_results=7,
    )
    # A malicious / stale envelope tries to overwrite agent-owned fields.
    incoming = SessionState(
        search_params=SearchParams(rooms_min=99.0),
        preview_cards=[ListingCard(id="evil", title="injected", lat=0.0, lng=0.0)],
        total_results=9999,
    )

    merged = merge_incoming_state(persisted, incoming)

    assert merged.search_params is not None
    assert merged.search_params.rooms_min == 2.0  # server value, not 99.0
    assert [c.id for c in merged.preview_cards] == ["srv"]
    assert merged.total_results == 7


def test_overlay_dismissal_shrinks_to_the_visible_set():
    persisted = SessionState(
        map_overlays=[
            _overlay("transit_line:U7"),
            _overlay("transit_line:U8"),
            _overlay("place:park:7"),
        ]
    )
    # Frontend dismissed U8 — its echo omits it.
    incoming = SessionState(
        map_overlays=[_overlay("transit_line:U7"), _overlay("place:park:7")]
    )

    merged = merge_incoming_state(persisted, incoming)

    assert {o.id for o in merged.map_overlays} == {
        "transit_line:U7",
        "place:park:7",
    }


def test_overlay_additions_in_envelope_are_ignored():
    persisted = SessionState(map_overlays=[_overlay("transit_line:U7")])
    # Frontend tries to inject a geometry the agent never drew.
    incoming = SessionState(
        map_overlays=[
            _overlay("transit_line:U7"),
            _overlay("place:injected:1"),
        ]
    )

    merged = merge_incoming_state(persisted, incoming)

    # Only the intersection survives — the injected overlay is dropped (content
    # is agent-owned; the frontend may only shrink the set).
    assert [o.id for o in merged.map_overlays] == ["transit_line:U7"]


def test_empty_incoming_overlays_clears_all():
    persisted = SessionState(
        map_overlays=[_overlay("transit_line:U7"), _overlay("place:park:7")]
    )
    incoming = SessionState(map_overlays=[])

    merged = merge_incoming_state(persisted, incoming)

    # User dismissed everything → nothing visible echoed back → all removed.
    assert merged.map_overlays == []


def test_merged_overlay_list_is_independent_of_persisted():
    persisted = SessionState(map_overlays=[_overlay("transit_line:U7")])
    incoming = SessionState(map_overlays=[_overlay("transit_line:U7")])

    merged = merge_incoming_state(persisted, incoming)
    merged.map_overlays.append(_overlay("place:park:7"))

    # Mutating the per-run list must not bleed into the stored session.
    assert [o.id for o in persisted.map_overlays] == ["transit_line:U7"]


# --- lens dismissal: the × on the lens legend -------------------------------
# Same shrink-only authority as overlays — the frontend may CLEAR the active
# lens but never SET one (setting stays agent-only, via apply_travel_time).


def _lensed() -> SessionState:
    return SessionState(
        travel_time_filter=TravelTimeFilter(
            anchor_label="TU Berlin",
            anchor_lat=52.5,
            anchor_lng=13.3,
            mode="transit",
            max_minutes=30,
        ),
        marker_lens=MarkerLens(key="commute_min", label="min to TU Berlin"),
    )


def test_frontend_clear_drops_persisted_lens():
    # Persisted had a lens; the incoming envelope dropped it → honour the clear.
    merged = merge_incoming_state(_lensed(), SessionState())
    assert merged.travel_time_filter is None
    assert merged.marker_lens.key == "price_warm"


def test_incoming_mirroring_lens_is_preserved():
    # The frontend still shows the lens → it stays.
    merged = merge_incoming_state(_lensed(), _lensed())
    assert merged.travel_time_filter is not None
    assert merged.marker_lens.key == "commute_min"


def test_frontend_cannot_set_a_lens():
    # Persisted has no lens; an incoming lens is ignored (setting is agent-only).
    merged = merge_incoming_state(SessionState(), _lensed())
    assert merged.travel_time_filter is None
    assert merged.marker_lens.key == "price_warm"


def test_none_incoming_keeps_persisted_lens():
    merged = merge_incoming_state(_lensed(), None)
    assert merged.travel_time_filter is not None
    assert merged.marker_lens.key == "commute_min"
