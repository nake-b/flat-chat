"""Unit tests for the OSM landmark extractor's pure helpers.

No network: these exercise the tag→category mapping and the native-geometry
builder (the part that must NOT centroid-flatten a bridge way or a stadium
polygon). The Overpass HTTP call itself is covered by the retry config; this
file guards the parsing contract.
"""

from __future__ import annotations

from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from geo_context.extract import osm


def test_category_for_first_match_wins() -> None:
    assert osm._category_for({"historic": "monument"}) == "monument"
    assert osm._category_for({"man_made": "bridge"}) == "bridge"
    assert osm._category_for({"leisure": "stadium"}) == "stadium"


def test_category_for_returns_none_when_no_selector_matches() -> None:
    assert osm._category_for({"amenity": "cafe"}) is None
    assert osm._category_for({}) is None


def test_geometry_for_node_is_point() -> None:
    geom = osm._geometry_for({"type": "node", "lon": 13.4, "lat": 52.5})
    assert isinstance(geom, Point)
    assert (geom.x, geom.y) == (13.4, 52.5)


def test_geometry_for_open_way_is_linestring() -> None:
    """A bridge way stays a line — no centroid flattening."""
    element = {
        "type": "way",
        "geometry": [
            {"lon": 13.40, "lat": 52.50},
            {"lon": 13.41, "lat": 52.51},
            {"lon": 13.42, "lat": 52.50},
        ],
    }
    geom = osm._geometry_for(element)
    assert isinstance(geom, LineString)
    assert len(geom.coords) == 3


def test_geometry_for_closed_way_is_polygon() -> None:
    """A closed way (stadium bowl outline) becomes a polygon."""
    ring = [
        {"lon": 13.40, "lat": 52.50},
        {"lon": 13.41, "lat": 52.50},
        {"lon": 13.41, "lat": 52.51},
        {"lon": 13.40, "lat": 52.50},
    ]
    geom = osm._geometry_for({"type": "way", "geometry": ring})
    assert isinstance(geom, Polygon)


def test_geometry_for_relation_outer_rings_is_multipolygon() -> None:
    member = {
        "type": "way",
        "role": "outer",
        "geometry": [
            {"lon": 13.40, "lat": 52.50},
            {"lon": 13.41, "lat": 52.50},
            {"lon": 13.41, "lat": 52.51},
            {"lon": 13.40, "lat": 52.50},
        ],
    }
    geom = osm._geometry_for({"type": "relation", "members": [member]})
    assert isinstance(geom, MultiPolygon)


def test_geometry_for_degenerate_inputs_return_none() -> None:
    assert osm._geometry_for({"type": "node"}) is None
    one_point = {"type": "way", "geometry": [{"lon": 1, "lat": 2}]}
    assert osm._geometry_for(one_point) is None
    assert osm._geometry_for({"type": "relation", "members": []}) is None


def test_build_query_scopes_to_berlin_and_unions_all_selectors() -> None:
    query = osm._build_query()
    assert f"area({osm._BERLIN_AREA_ID})->.berlin" in query
    # One node/way/relation clause per selector.
    assert query.count("(area.berlin)") == len(osm._TAG_CATEGORIES) * 3
    assert "out tags geom;" in query
