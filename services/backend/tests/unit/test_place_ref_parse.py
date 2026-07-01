"""Unit tests for `search.service._parse_place_ref`.

The `place_ref` token is opaque to the backend — it only parses the FORMAT
(`'<kind>:<src_id>'`, split on the FIRST ':'). The `src_id` is kept as TEXT
(the gazetteer view emits text src_ids; transit stop_ids are themselves
colon-laden, e.g. `de:11000:900100003`). Because the LLM passes these tokens
through, a malformed / hallucinated one must fail CLOSED (return None → the
search drops the filter) rather than reaching Postgres and raising. These
tests pin that contract. No DB, no LLM.
"""

from __future__ import annotations

from flat_chat.search.service import _parse_place_ref


class TestParsePlaceRef:
    def test_valid_token_parses_to_kind_and_text_id(self):
        assert _parse_place_ref("park:42") == ("park", "42")
        assert _parse_place_ref("landmark:1") == ("landmark", "1")
        assert _parse_place_ref("water:999") == ("water", "999")

    def test_colon_laden_transit_id_keeps_full_remainder(self):
        # The FIRST ':' splits kind from id; the colon-laden GTFS stop_id is
        # kept whole as the src_id.
        assert _parse_place_ref("transit_stop:de:11000:900100003") == (
            "transit_stop",
            "de:11000:900100003",
        )
        assert _parse_place_ref("kind:a:b") == ("kind", "a:b")

    def test_non_numeric_id_is_valid_text(self):
        # src_id is text now — a non-numeric id is a legitimate value, not None.
        assert _parse_place_ref("park:abc") == ("park", "abc")

    def test_no_colon_returns_none(self):
        assert _parse_place_ref("park42") is None

    def test_empty_kind_returns_none(self):
        assert _parse_place_ref(":42") is None

    def test_empty_id_returns_none(self):
        assert _parse_place_ref("park:") is None

    def test_empty_string_returns_none(self):
        assert _parse_place_ref("") is None

    def test_garbage_without_colon_returns_none(self):
        assert _parse_place_ref("not a real token") is None
