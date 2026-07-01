"""Drift guard: `SessionStateResponse` must mirror `SessionState`.

`GET /api/conversations/{id}/state` returns `SessionState.model_dump(mode="json")`
but declares `response_model=SessionStateResponse` so the OpenAPI schema reflects
the *columnar* wire shape of `result_markers` (which `SessionState`'s field
serializer produces) instead of lying with `array<Marker>`.

That accuracy only holds if the two models stay in sync. These tests fail loudly
the moment a field is added to one and not the other, or the columnar marker
shape diverges — so the parallel schema can't silently rot.
"""

from __future__ import annotations

from flat_chat.chat.schemas import ColumnarMarkers, SessionStateResponse
from flat_chat.chat.session_state import SessionState
from flat_chat.listings.context import ListingCard, Marker


def test_top_level_fields_match():
    """Every SessionState field appears on the response model and vice versa."""
    dumped = set(SessionState().model_dump(mode="json").keys())
    declared = set(SessionStateResponse.model_fields.keys())
    assert dumped == declared, (
        "SessionState and SessionStateResponse drifted: "
        f"only in dump={dumped - declared}, only in response={declared - dumped}"
    )


def test_columnar_marker_columns_match():
    """The serialized result_markers keys match ColumnarMarkers' fields."""
    columnar = SessionState().model_dump(mode="json")["result_markers"]
    assert isinstance(columnar, dict)
    assert set(columnar.keys()) == set(ColumnarMarkers.model_fields.keys())


def test_dumped_state_validates_against_response_model():
    """A full SessionState dump round-trips through the response model.

    This is what FastAPI does internally with `response_model=` — if the
    columnar dump didn't validate, the endpoint would 500 at serialization.
    """
    state = SessionState()
    dumped = state.model_dump(mode="json")
    # Should not raise — proves the dict the endpoint returns is a valid
    # SessionStateResponse (the contract FastAPI enforces on the way out).
    SessionStateResponse.model_validate(dumped)


def test_populated_state_validates_and_preserves_columnar_markers():
    """A non-empty state (markers + a card) round-trips through the response model.

    The empty case can't exercise the columnar serializer with real data; this
    covers the path FastAPI actually serializes for a live conversation, so we
    don't depend on the DB-gated integration tier to catch a shape regression.
    """
    state = SessionState(
        total_results=2,
        result_markers=[
            Marker(id="a", lat=52.5, lng=13.4, lens_value=1200.0),
            Marker(id="b", lat=52.49, lng=13.41, lens_value=None),
        ],
        preview_cards=[ListingCard(id="a", district="Kreuzberg", rooms=2.0)],
        active_id="a",
    )
    dumped = state.model_dump(mode="json")

    resp = SessionStateResponse.model_validate(dumped)
    # Columnar columns are positional and preserved (incl. a null value).
    assert resp.result_markers.ids == ["a", "b"]
    assert resp.result_markers.values == [1200.0, None]
    assert resp.total_results == 2
    assert resp.active_id == "a"
    assert resp.preview_cards[0].district == "Kreuzberg"
