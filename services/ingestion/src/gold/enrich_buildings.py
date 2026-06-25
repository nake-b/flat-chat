"""Enrichment for named buildings (landmarks) from ALKIS.

This module provides a single bulk UPSERT (SET) that writes nearest
named building and a top-3 detail blob to `listings_geo_context`.
Only buildings with a `name` (the transformed `nam` field) are
considered — this preserves the user's intent to surface landmarks.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def enrich_buildings(conn: Connection) -> int:
    """Nearest named building (chip) + top 3 (detail blob)."""
    result = conn.execute(
        text(
            """
            UPDATE listings_geo_context lgc
            SET nearest_landmark_m = nearest.distance_m,
                landmarks_top3      = top3.blob,
                enriched_at         = now()
            FROM listings l
            LEFT JOIN LATERAL (
                SELECT b.name,
                       b.description,
                       ST_Distance(b.geom::geography, l.location::geography)::int AS distance_m
                FROM buildings b
                WHERE l.location IS NOT NULL
                  AND b.name IS NOT NULL
                ORDER BY b.geom <-> l.location
                LIMIT 1
            ) nearest ON true
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'name', t.name,
                        'description', t.description,
                        'distance_m', t.distance_m
                    )
                    ORDER BY t.distance_m
                ) AS blob
                FROM (
                    SELECT b.name, b.description,
                           ST_Distance(b.geom::geography, l.location::geography)::int AS distance_m
                    FROM buildings b
                    WHERE l.location IS NOT NULL
                      AND b.name IS NOT NULL
                    ORDER BY b.geom <-> l.location
                    LIMIT 3
                ) t
            ) top3 ON true
            WHERE lgc.listing_id = l.id
            """
        )
    )
    return result.rowcount or 0
