"""Unit tests for `_safe_mss_status` / `_safe_mss_dynamics`.

Regression direction: the shadow-test run in June 2026 surfaced 3
listings whose `listings_geo_context.mss_status` carried the German
publisher sentinel ``"Planungsraum ohne Zuordnung"`` ("planning area
without assignment"). That string is not a real status label and isn't
in the silver translation map historically, so it slipped through
unmapped and the new `ListingCard` Pydantic model (typed
``MssStatus | None``) blew up trying to validate it.

The defensive coercion in `listings/projection.py` maps any unknown string
(anything not in the canonical Literal set) to None, so the row still
projects cleanly. These tests pin the contract.

The long-term fix in silver (`geo_context/transform/wfs.py` — German
sentinel now maps to None) is tested by behaviour: re-running silver +
gold would clear the values from gold. But until that re-run happens
the coercion here is what keeps search working.
"""

from __future__ import annotations

from flat_chat.listings.projection import _safe_mss_dynamics, _safe_mss_status


class TestSafeMssStatus:
    def test_canonical_labels_pass_through(self):
        for v in ("disadvantaged", "lower-income", "mixed", "affluent"):
            assert _safe_mss_status(v) == v

    def test_none_passes_through(self):
        assert _safe_mss_status(None) is None

    def test_german_sentinel_coerces_to_none(self):
        assert _safe_mss_status("Planungsraum ohne Zuordnung") is None

    def test_german_status_strings_coerce_to_none(self):
        # Old data could have these if silver translation didn't run.
        for v in ("sehr niedrig", "niedrig", "mittel", "hoch"):
            assert _safe_mss_status(v) is None

    def test_empty_string_coerces_to_none(self):
        assert _safe_mss_status("") is None

    def test_random_garbage_coerces_to_none(self):
        assert _safe_mss_status("not a label") is None


class TestSafeMssDynamics:
    def test_canonical_labels_pass_through(self):
        for v in ("slipping", "stable", "improving"):
            assert _safe_mss_dynamics(v) == v

    def test_none_passes_through(self):
        assert _safe_mss_dynamics(None) is None

    def test_german_dynamics_coerce_to_none(self):
        for v in ("positiv", "stabil", "negativ"):
            assert _safe_mss_dynamics(v) is None
