"""Tests for ``BerlinGdiWfsClient.fetch_layer`` pagination.

The Berlin GDI WFS 2.0 servers can cap a single GetFeature response. The
client paginates via ``count`` / ``startIndex`` and terminates when a page
returns fewer features than ``PAGE_SIZE``. These tests fake the HTTP layer
with a configurable page sequence and assert the request mechanics +
result concatenation.

The client issues requests through a retrying ``requests.Session`` (mounted
with a backoff adapter), so the tests mock the *instance's* ``_session.get``
rather than module-level ``requests.get`` — otherwise the call escapes the
mock and hits the real network.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from geo_context.extract.wfs import BerlinGdiWfsClient


def _feature(idx: int) -> dict:
    """Minimal valid GeoJSON Point feature; coords are nonsense but parseable."""
    return {
        "type": "Feature",
        "properties": {"id": idx, "label": f"f{idx}"},
        "geometry": {"type": "Point", "coordinates": [13.4, 52.5]},
    }


def _page(n: int, start: int = 0) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [_feature(start + i) for i in range(n)],
    }


def _fake_response(json_payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = json_payload
    resp.raise_for_status = MagicMock()
    # `resp.encoding = "utf-8"` is an attribute assignment, not a call —
    # MagicMock accepts arbitrary attribute writes silently.
    return resp


def test_fetch_layer_paginates_until_short_page() -> None:
    client = BerlinGdiWfsClient()
    # Shrink page size so we don't have to mock 10k features per page.
    client.PAGE_SIZE = 3
    # Two full pages of 3, then a short page of 1 → loop terminates.
    pages = [_page(3, start=0), _page(3, start=3), _page(1, start=6)]
    mock_get = MagicMock(side_effect=[_fake_response(p) for p in pages])
    client._session.get = mock_get
    gdf = client.fetch_layer("dataset_x", "layer_y")

    assert len(gdf) == 7
    # Three HTTP calls with correctly stepping startIndex.
    assert mock_get.call_count == 3
    calls = mock_get.call_args_list
    start_indices = [int(c.kwargs["params"]["startIndex"]) for c in calls]
    counts = [int(c.kwargs["params"]["count"]) for c in calls]
    assert start_indices == [0, 3, 6]
    assert counts == [3, 3, 3]


def test_fetch_layer_terminates_on_empty_first_page() -> None:
    client = BerlinGdiWfsClient()
    client.PAGE_SIZE = 5
    mock_get = MagicMock(return_value=_fake_response(_page(0)))
    client._session.get = mock_get
    gdf = client.fetch_layer("dataset_x", "empty_layer")

    assert gdf.empty
    # One call, then the loop saw zero features and broke.
    assert mock_get.call_count == 1


def test_fetch_layer_raises_when_exceeding_max_features() -> None:
    client = BerlinGdiWfsClient()
    client.PAGE_SIZE = 5
    client.MAX_FEATURES = 10  # second page would push us *to* the cap → must raise
    pages = [_page(5, start=0), _page(5, start=5)]
    mock_get = MagicMock(side_effect=[_fake_response(p) for p in pages])
    client._session.get = mock_get
    with pytest.raises(RuntimeError, match="MAX_FEATURES"):
        client.fetch_layer("dataset_x", "huge_layer")


def test_fetch_layer_terminates_when_exact_multiple_of_page_size() -> None:
    # Page size 3, two full pages of 3, then an empty page (server has nothing
    # more to give but reports `numberReturned = 0`). Loop must exit on the
    # zero-feature page, not loop forever.
    client = BerlinGdiWfsClient()
    client.PAGE_SIZE = 3
    pages = [_page(3, start=0), _page(3, start=3), _page(0, start=6)]
    mock_get = MagicMock(side_effect=[_fake_response(p) for p in pages])
    client._session.get = mock_get
    gdf = client.fetch_layer("dataset_x", "exact_multiple_layer")

    assert len(gdf) == 6
    assert mock_get.call_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
