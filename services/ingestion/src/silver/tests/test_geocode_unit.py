"""Unit tests for the geocoding helpers in `silver.transformer` — the Nominatim
client + query composer.

No network and no DB: the HTTP call is served by an `httpx.MockTransport`
(injected by patching `httpx.Client` so the client `_NominatimGeocoder` builds
internally uses it). Covers query composition, result parsing, the malformed /
empty-result paths, and the pure rate-limit math.
"""

from __future__ import annotations

import httpx

from silver import transformer as geocode

# ---------------------------------------------------------------------------
# _compose_query — pure
# ---------------------------------------------------------------------------


def test_compose_query_full_address_anchors_berlin_germany():
    q = geocode._compose_query("Manteuffelstr. 42", "10999", "Kreuzberg", "Berlin")
    # Berlin already present (city) → not appended twice; Germany anchored once.
    assert q == "Manteuffelstr. 42, 10999, Kreuzberg, Berlin, Germany"


def test_compose_query_postcode_only_yields_area_centroid_query():
    q = geocode._compose_query(None, "10115", "Mitte", None)
    assert q == "10115, Mitte, Berlin, Germany"


def test_compose_query_appends_berlin_when_absent():
    q = geocode._compose_query("Somestr. 1", None, None, None)
    assert q == "Somestr. 1, Berlin, Germany"


def test_compose_query_dedupes_case_insensitively_and_collapses_whitespace():
    q = geocode._compose_query("  Foostr.  3 ", "10115", "10115", "Berlin")
    assert q == "Foostr. 3, 10115, Berlin, Germany"


def test_compose_query_returns_none_when_no_address_parts():
    assert geocode._compose_query(None, None, None, None) is None
    assert geocode._compose_query("", "  ", None, "") is None


# ---------------------------------------------------------------------------
# _seconds_to_wait — pure rate-limit math
# ---------------------------------------------------------------------------


def test_seconds_to_wait():
    assert geocode._seconds_to_wait(None, 100.0, 1.0) == 0.0  # no prior call
    assert geocode._seconds_to_wait(100.0, 100.5, 1.0) == 0.5  # 0.5s elapsed → wait 0.5
    assert geocode._seconds_to_wait(100.0, 102.0, 1.0) == 0.0  # enough elapsed
    assert geocode._seconds_to_wait(100.0, 100.0, 0.0) == 0.0  # disabled


# ---------------------------------------------------------------------------
# NominatimGeocoder.geocode — against a MockTransport
# ---------------------------------------------------------------------------


def _geocoder(monkeypatch, handler) -> geocode.NominatimGeocoder:
    """Build a geocoder whose internal httpx.Client routes through `handler`."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(geocode.httpx, "Client", fake_client)
    # rate_limit_s=0 → no sleeps in tests.
    return geocode._NominatimGeocoder(
        base_url="https://nominatim.test", user_agent="test/1.0", rate_limit_s=0.0
    )


def test_geocode_returns_lat_lon_from_top_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"lat": "52.5200", "lon": "13.4050"}])

    with _geocoder(monkeypatch, handler) as g:
        assert g.geocode("Alexanderplatz, Berlin, Germany") == (52.52, 13.405)


def test_geocode_returns_none_on_empty_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with _geocoder(monkeypatch, handler) as g:
        assert g.geocode("Nowhere, Berlin, Germany") is None


def test_geocode_returns_none_on_malformed_row(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"display_name": "no coords here"}])

    with _geocoder(monkeypatch, handler) as g:
        assert g.geocode("Weird, Berlin, Germany") is None


def test_geocode_sends_berlin_bounded_viewbox(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[{"lat": "52.5", "lon": "13.4"}])

    with _geocoder(monkeypatch, handler) as g:
        g.geocode("Somestr. 1, Berlin, Germany")

    assert seen["bounded"] == "1"
    assert seen["countrycodes"] == "de"
    assert seen["viewbox"] == geocode._BERLIN_VIEWBOX
