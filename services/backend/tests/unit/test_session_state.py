"""Unit tests for `SessionState` wire serialization (review hole #11).

`result_markers` is a `list[Marker]` in memory but ships columnar
(`{ids,lats,lngs,values}`) on the wire. The `@field_serializer` /
`@field_validator` pair MUST be symmetric: every turn the AG-UI envelope
echoes the frontend's state back and `chat/service.py:_extract_incoming_state`
runs `SessionState.model_validate(raw)` on it. If the validator can't decode
the columnar shape, validation fails, the `try/except` drops ALL incoming
state, and the frontend's `active_id` write-back is silently lost.

The `values` column carries `Marker.lens_value` — the single active
visualization scalar (warm rent by default, commute minutes under a travel
lens). The legacy `prices` key is still accepted on decode for snapshots
persisted before the lens generalization.

No DB, no LLM.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flat_chat.chat.session_state import SessionState
from flat_chat.listings.context import Marker
from flat_chat.search.schemas import DistrictCount, NumericFacet, ResultFacets


def _state_with_markers(n: int) -> SessionState:
    state = SessionState()
    state.result_markers = [
        Marker(
            id=f"id-{i}",
            lat=52.5 + i / 1000,
            lng=13.4 + i / 1000,
            lens_value=1000.0 + i,
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
    assert set(markers) == {"ids", "lats", "lngs", "values"}
    assert markers["ids"] == ["id-0", "id-1", "id-2"]
    assert markers["values"] == [1000.0, 1001.0, 1002.0]
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
    assert restored.result_markers[0].lens_value == 1000.0
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
            "values": [1200, None],
        },
        "active_id": "b",
    }
    state = SessionState.model_validate(envelope)
    assert [m.id for m in state.result_markers] == ["a", "b"]
    assert state.result_markers[0].lens_value == 1200
    assert state.result_markers[1].lens_value is None
    assert state.active_id == "b"


def test_validator_accepts_legacy_prices_key():
    # Snapshots persisted before the lens generalization used the `prices`
    # key; decode must still accept it and map it onto lens_value.
    envelope = {
        "result_markers": {
            "ids": ["a", "b"],
            "lats": [52.5, 52.6],
            "lngs": [13.4, 13.5],
            "prices": [1200, None],
        },
    }
    state = SessionState.model_validate(envelope)
    assert state.result_markers[0].lens_value == 1200
    assert state.result_markers[1].lens_value is None


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


def test_validator_raises_on_mismatched_column_lengths():
    # A corrupt/partial columnar payload must RAISE rather than silently
    # truncate to the shortest column (which would drop or misalign markers).
    # `_extract_incoming_state` catches this and keeps the server state.
    envelope = {
        "result_markers": {
            "ids": ["a", "b", "c"],
            "lats": [52.5, 52.6],  # short by one
            "lngs": [13.4, 13.5, 13.6],
            "values": [1, 2, 3],
        }
    }
    with pytest.raises(ValidationError):
        SessionState.model_validate(envelope)


def test_validator_raises_on_short_values_column():
    # `values` may be ABSENT (defaults to all-None), but a PRESENT-but-short
    # values column is a corrupt payload and must raise.
    envelope = {
        "result_markers": {
            "ids": ["a", "b"],
            "lats": [52.5, 52.6],
            "lngs": [13.4, 13.5],
            "values": [1200],  # present but short
        }
    }
    with pytest.raises(ValidationError):
        SessionState.model_validate(envelope)


def test_validator_allows_absent_values_column():
    # Old/empty envelopes legitimately omit the value column — all-None.
    envelope = {
        "result_markers": {
            "ids": ["a", "b"],
            "lats": [52.5, 52.6],
            "lngs": [13.4, 13.5],
        }
    }
    state = SessionState.model_validate(envelope)
    assert [m.id for m in state.result_markers] == ["a", "b"]
    assert all(m.lens_value is None for m in state.result_markers)


def test_facets_round_trip():
    # `facets` is a plain nested model (no custom serializer like markers): it
    # must survive model_dump → model_validate so the AG-UI envelope echo
    # doesn't drop it on the way back in.
    state = SessionState(
        total_results=33,
        facets=ResultFacets(
            price_warm_eur=NumericFacet(min=620.0, median=1180.0, max=1950.0),
            area_sqm=NumericFacet(min=28.0, median=64.0, max=112.0),
            districts=[
                DistrictCount(district="Prenzlauer Berg", count=21),
                DistrictCount(district="Wedding", count=9),
            ],
        ),
    )
    restored = SessionState.model_validate(state.model_dump())
    assert restored.facets is not None
    assert restored.facets.price_warm_eur.max == 1950.0
    assert restored.facets.area_sqm.median == 64.0
    assert [d.district for d in restored.facets.districts] == [
        "Prenzlauer Berg",
        "Wedding",
    ]
    assert restored.facets.districts[0].count == 21


def test_facets_defaults_to_none():
    # No search yet → no facets; must round-trip as None (omitted block).
    assert SessionState().facets is None
    restored = SessionState.model_validate(SessionState().model_dump())
    assert restored.facets is None
