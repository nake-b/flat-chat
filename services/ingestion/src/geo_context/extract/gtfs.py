"""VBB GTFS client.

Downloads the VBB feed (a ~50MB zip of CSVs) in memory and returns only
the tables we actually use. The full feed has ~13 files; loading just
the 5 we need saves ~30% of parse time and memory.
"""

from __future__ import annotations

import io
import logging
import zipfile

import pandas as pd
import requests

logger = logging.getLogger(__name__)


# The minimum set required to derive transit_stops + transit_routes
# + transit_route_shapes. Anything else in the feed is ignored.
_REQUIRED_TABLES = ("stops", "routes", "trips", "stop_times", "shapes")


class VbbGtfsClient:
    DEFAULT_URL = "https://www.vbb.de/vbbgtfs"

    def __init__(self, http_timeout_s: float = 120.0) -> None:
        self.http_timeout_s = http_timeout_s

    def fetch_feed(
        self,
        url: str = DEFAULT_URL,
    ) -> dict[str, pd.DataFrame]:
        """Download the feed zip into memory and parse the required tables.

        Returns:
            {"stops": df, "routes": df, "trips": df, "stop_times": df, "shapes": df}
        Raises:
            requests.HTTPError on download failure.
            KeyError if a required table is missing from the feed.
        """
        logger.info("gtfs: downloading feed %s", url)
        resp = requests.get(
            url,
            stream=True,
            allow_redirects=True,
            timeout=self.http_timeout_s,
        )
        resp.raise_for_status()
        size_mb = len(resp.content) / (1024 * 1024)
        logger.info("gtfs: downloaded %.1f MB", size_mb)

        tables: dict[str, pd.DataFrame] = {}
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = {n.replace(".txt", ""): n for n in zf.namelist()}
            for required in _REQUIRED_TABLES:
                if required not in names:
                    raise KeyError(
                        f"gtfs feed missing required table: {required}.txt"
                    )
                with zf.open(names[required]) as f:
                    df = pd.read_csv(f, low_memory=False)
                logger.info("gtfs: loaded %s (%d rows)", required, len(df))
                tables[required] = df

        return tables
