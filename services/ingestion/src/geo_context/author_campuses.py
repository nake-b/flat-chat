"""Author the frozen curated university-campus geometries (the G5 step).

    python -m geo_context.author_campuses            # regenerate university_seed.yaml
    python -m geo_context.author_campuses --check     # report only, don't write

Reads the editorial selectors in ``campus_sources.yaml`` and, for each campus,
unions the real ``world.landmarks`` footprints whose name matches ``name_pattern``
AND whose centroid falls inside ``bbox``, then unions them into one
polygon. The result is written as WKT into ``university_seed.yaml`` — the frozen,
committed source of truth the loader (`_run_universities`) reads.

This "derive once from real footprints, then freeze" flow (rather than a live
union view) keeps the runtime geometry decoupled from the unstable
``landmarks.id`` serial and from per-ingest OSM/ALKIS noise: the editorial call
is only revisited when a human re-runs this script and reviews the diff. See
agent-compound-docs/decisions/named-place-search.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml
from sqlalchemy import text

from db import engine

logger = logging.getLogger(__name__)

SOURCES_PATH = Path(__file__).resolve().parent / "campus_sources.yaml"
OUT_PATH = Path(__file__).resolve().parent / "university_seed.yaml"
_DEFAULT_KIND = "university"

# Union the footprints whose CENTROID is inside the bbox and whose name matches
# the (case-insensitive) pattern into ONE MultiPolygon of the REAL building
# shapes — dissolving only genuinely-overlapping/adjacent parts, keeping
# distinct buildings distinct. Deliberately NOT a convex hull: a hull wraps a
# single fat polygon around everything, swallowing streets and non-campus
# buildings (and making ST_DWithin match listings that sit between buildings).
# ST_Multi normalises to a stable MultiPolygon type. `n` lets the caller
# sanity-check the selector actually matched buildings.
_CAMPUS_SQL = text(
    """
    SELECT
        COUNT(*) AS n,
        ST_AsText(
            ST_Multi(ST_Union(geom))
        ) AS wkt
    FROM world.landmarks
    WHERE name ~* :pattern
      AND ST_Intersects(
          ST_Centroid(geom),
          ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
      )
    """
)

# Optional MAIN-building anchor: the representative point a travel-time lens
# routes to (a routing engine needs ONE destination, so the campus centroid —
# the mean of a sprawling multi-building campus — is misleading). Resolve the
# building(s) matching `anchor_pattern` within the same bbox and take
# ST_PointOnSurface (guaranteed ON the building, unlike a centroid that could
# fall in a courtyard). Returns lat/lon floats frozen into the seed.
_ANCHOR_SQL = text(
    """
    SELECT
        COUNT(*) AS n,
        ST_Y(ST_PointOnSurface(ST_Union(geom))) AS lat,
        ST_X(ST_PointOnSurface(ST_Union(geom))) AS lon
    FROM world.landmarks
    WHERE name ~* :pattern
      AND ST_Intersects(
          ST_Centroid(geom),
          ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
      )
    """
)


def _author(campuses: list[dict]) -> list[dict]:
    """Resolve each campus selector to a frozen seed row. Skips (with a warning)
    any campus whose selector matched zero footprints."""
    seeds: list[dict] = []
    with engine.connect() as conn:
        for c in campuses:
            name = (c.get("name") or "").strip()
            bbox = c.get("bbox") or []
            pattern = (c.get("name_pattern") or "").strip()
            if not (name and pattern and len(bbox) == 4):
                logger.warning("campus %r: missing name/pattern/bbox — skipped", name)
                continue
            row = conn.execute(
                _CAMPUS_SQL,
                {
                    "pattern": pattern,
                    "min_lon": bbox[0],
                    "min_lat": bbox[1],
                    "max_lon": bbox[2],
                    "max_lat": bbox[3],
                },
            ).one()
            if row.n == 0 or row.wkt is None:
                logger.warning(
                    "campus %r: pattern∩bbox matched 0 footprints — skipped", name
                )
                continue
            seed = {
                "name": name,
                "description": c.get("description"),
                "kind": (c.get("kind") or _DEFAULT_KIND).strip(),
                "geometry": row.wkt,
            }

            # Optional main-building anchor.
            anchor_pattern = (c.get("anchor_pattern") or "").strip()
            anchor_note = ""
            if anchor_pattern:
                a = conn.execute(
                    _ANCHOR_SQL,
                    {
                        "pattern": anchor_pattern,
                        "min_lon": bbox[0],
                        "min_lat": bbox[1],
                        "max_lon": bbox[2],
                        "max_lat": bbox[3],
                    },
                ).one()
                if a.n and a.lat is not None:
                    seed["anchor"] = [round(a.lon, 6), round(a.lat, 6)]
                    anchor_note = f", anchor ← {a.n} bldg(s)"
                else:
                    logger.warning(
                        "campus %r: anchor_pattern matched 0 — centroid fallback",
                        name,
                    )

            logger.info("OK %s ← %d footprint(s)%s", name, row.n, anchor_note)
            seeds.append(seed)
    return seeds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geo_context.author_campuses")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report matches only; do not (over)write university_seed.yaml",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
    )

    raw = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8")) or {}
    campuses = raw.get("campuses") or []
    if not campuses:
        logger.error("no campuses in %s", SOURCES_PATH)
        return 1

    seeds = _author(campuses)
    if not seeds:
        logger.error("authored 0 campuses — nothing to write")
        return 1

    if args.check:
        logger.info("--check: %d campuses would be written (no write)", len(seeds))
        return 0

    header = (
        "# GENERATED by `python -m geo_context.author_campuses` — do not hand-edit.\n"
        "# Edit selectors in campus_sources.yaml and re-run; review the diff.\n"
        "# Frozen, footprint-derived campus geometries loaded into"
        " world.curated_places.\n"
    )
    body = yaml.safe_dump(
        {"seeds": seeds}, allow_unicode=True, sort_keys=False, width=100
    )
    OUT_PATH.write_text(header + body, encoding="utf-8")
    logger.info("wrote %d campuses → %s", len(seeds), OUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
