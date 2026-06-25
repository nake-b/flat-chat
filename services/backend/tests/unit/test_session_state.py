"""Unit tests for `SessionState` wire serialization (review hole #11).

`result_markers` is a `list[Marker]` in memory but ships columnar
(`{ids,lats,lngs,prices}`) on the wire. The `@field_serializer` /
`@field_validator` pair MUST be symmetric: every turn the AG-UI envelope
echoes the frontend's state back and `chat/service.py:_extract_incoming_state`
runs `SessionState.model_validate(raw)` on it. If the validator can't decode
the columnar shape, validation fails, the `try/except` drops ALL incoming
state, and the frontend's `active_id` write-back is silently lost.

No DB, no LLM.
"""

from __future__ import annotations

from flat_chat.chat.session_state import SessionState
from flat_chat.listings.context import Marker


def _state_with_markers(n: int) -> SessionState:
    state = SessionState()
    state.result_markers = [
        Marker(
            id=f"id-{i}",
            lat=52.5 + i / 1000,
            lng=13.4 + i / 1000,
            price_warm_eur=1000.0 + i,
        )
        for i in range(n)
    ]
    state.total_results = n
    return state


def test_markers_serialize_to_columnar_wire_shape():
    state = _state_with_markers(3)
    dumped = state.model_dump()
    markers = dumped["result_markers"]
    # Columnar dict, not an array of objects.
    assert set(markers) == {"ids", "lats", "lngs", "prices"}
    assert markers["ids"] == ["id-0", "id-1", "id-2"]
    assert len(markers["lats"]) == 3
    # Coords rounded to 5 dp at the wire boundary.
    assert all(round(v, 5) == v for v in markers["lats"])


def test_serializer_validator_round_trip():
    # model_validate(model_dump(state)) == state — the symmetry contract.
    state = _state_with_markers(4)
    restored = SessionState.model_validate(state.model_dump())
    assert [m.id for m in restored.result_markers] == [
        m.id for m in state.result_markers
    ]
    assert restored.result_markers[0].lat == state.result_markers[0].lat
    assert restored.total_results == state.total_results


def test_validator_decodes_columnar_envelope_and_preserves_active_id():
    # Simulates the AG-UI envelope the frontend echoes back: result_markers in
    # the columnar WIRE shape + an active_id write-back. Validation must
    # succeed (not raise) and keep active_id — guards the _extract_incoming_state
    # clobber path (review hole #11).
    envelope = {
        "result_markers": {
            "ids": ["a", "b"],
            "lats": [52.5, 52.6],
            "lngs": [13.4, 13.5],
            "prices": [1200, None],
        },
        "active_id": "b",
    }
    state = SessionState.model_validate(envelope)
    assert [m.id for m in state.result_markers] == ["a", "b"]
    assert state.result_markers[1].price_warm_eur is None
    assert state.active_id == "b"


def test_validator_accepts_plain_list_of_markers():
    # In-process construction / tests pass a plain list — must pass through.
    state = SessionState.model_validate(
        {"result_markers": [{"id": "x", "lat": 52.5, "lng": 13.4}]}
    )
    assert state.result_markers[0].id == "x"


def test_empty_markers_round_trip():
    state = SessionState()
    restored = SessionState.model_validate(state.model_dump())
    assert restored.result_markers == []
