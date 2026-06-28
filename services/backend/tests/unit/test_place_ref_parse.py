"""Unit tests for `search.service._parse_place_ref`.

The `place_ref` token is opaque to the backend — it only parses the FORMAT
(`'<kind>:<src_id>'`). Because the LLM passes these tokens through, a
malformed / hallucinated one must fail CLOSED (return None → the search
drops the filter) rather than reaching Postgres and raising. These tests
pin that contract. No DB, no LLM.
"""

from __future__ import annotations

from flat_chat.search.service import _parse_place_ref


class TestParsePlaceRef:
    def test_valid_token_parses_to_kind_and_int_id(self):
        assert _parse_place_ref("park:42") == ("park", 42)
        assert _parse_place_ref("landmark:1") == ("landmark", 1)
        assert _parse_place_ref("water:999") == ("water", 999)

    def test_id_with_extra_colon_keeps_only_first_split(self):
        # partition() splits on the FIRST ':'. "a:b" as id is not an int → None.
        assert _parse_place_ref("kind:a:b") is None

    def test_no_colon_returns_none(self):
        assert _parse_place_ref("park42") is None

    def test_empty_kind_returns_none(self):
        assert _parse_place_ref(":42") is None

    def test_empty_id_returns_none(self):
        assert _parse_place_ref("park:") is None

    def test_non_integer_id_returns_none(self):
        assert _parse_place_ref("park:abc") is None

    def test_empty_string_returns_none(self):
        assert _parse_place_ref("") is None

    def test_garbage_returns_none(self):
        assert _parse_place_ref("not a real token") is None
