"""Berlin GDI WFS client.

Hits `https://gdi.berlin.de/services/wfs/<dataset>` for GetCapabilities
(layer discovery) and GetFeature (data extraction). Returns GeoDataFrames
in the source CRS (EPSG:25833) — CRS reprojection happens in transform/.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import geopandas as gpd
import requests

logger = logging.getLogger(__name__)


_NS_WFS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "ows": "http://www.opengis.net/ows/1.1",
}


@dataclass(frozen=True)
class LayerInfo:
    name: str
    abstract: str | None


class BerlinGdiWfsClient:
    BASE_URL = "https://gdi.berlin.de/services/wfs/"

    def __init__(
        self,
        base_url: str = BASE_URL,
        http_timeout_s: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.http_timeout_s = http_timeout_s

    def _endpoint(self, dataset: str) -> str:
        return f"{self.base_url}{dataset}"

    def get_capabilities(self, dataset: str) -> list[LayerInfo]:
        """List every layer published under `dataset`."""
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetCapabilities",
        }
        resp = requests.get(
            self._endpoint(dataset),
            params=params,
            timeout=self.http_timeout_s,
        )
        resp.raise_for_status()
        resp.encoding = "utf-8"

        root = ET.fromstring(resp.text)
        layers: list[LayerInfo] = []
        for ft in root.findall(".//wfs:FeatureType", _NS_WFS):
            name_tag = ft.find("wfs:Name", _NS_WFS)
            abs_tag = ft.find("wfs:Abstract", _NS_WFS)
            if name_tag is None or name_tag.text is None:
                continue
            layers.append(
                LayerInfo(
                    name=name_tag.text,
                    abstract=abs_tag.text if abs_tag is not None else None,
                )
            )
        logger.info("wfs %s: discovered %d layers", dataset, len(layers))
        return layers

    # WFS 2.0 servers commonly cap responses around 10k features per request.
    # Berlin GDI doesn't publish its limit; this page size + the explicit
    # pagination loop below avoid silent truncation regardless.
    PAGE_SIZE: int = 10_000
    # Refuse to keep paginating past this in a single layer fetch — a
    # runaway query should fail loudly, not eat memory.
    MAX_FEATURES: int = 200_000

    def fetch_layer(
        self,
        dataset: str,
        layer: str,
        *,
        src_crs: int = 25833,
    ) -> gpd.GeoDataFrame:
        """GetFeature → GeoJSON → GeoDataFrame (still in src_crs).

        Paginates via ``count`` / ``startIndex`` so layers larger than the
        server's response cap come back whole. The loop terminates when a
        page returns fewer features than ``PAGE_SIZE`` (covers servers that
        don't report ``numberMatched`` reliably).
        """
        base_params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": layer,
            "outputFormat": "application/json",
        }
        logger.info("wfs %s/%s: fetching", dataset, layer)

        all_features: list[dict] = []
        start_index = 0
        while True:
            params = {
                **base_params,
                "count": str(self.PAGE_SIZE),
                "startIndex": str(start_index),
            }
            resp = requests.get(
                self._endpoint(dataset),
                params=params,
                timeout=self.http_timeout_s,
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"

            data = resp.json()
            features = data.get("features") or []
            page_n = len(features)
            logger.info(
                "wfs %s/%s: page startIndex=%d returned %d features",
                dataset,
                layer,
                start_index,
                page_n,
            )
            if page_n == 0:
                break
            all_features.extend(features)
            if len(all_features) > self.MAX_FEATURES:
                raise RuntimeError(
                    f"wfs {dataset}/{layer}: exceeded MAX_FEATURES "
                    f"({self.MAX_FEATURES}) — refusing to keep paginating"
                )
            if page_n < self.PAGE_SIZE:
                break
            start_index += page_n

        if not all_features:
            logger.warning("wfs %s/%s: zero features returned", dataset, layer)
            return gpd.GeoDataFrame(geometry=[], crs=f"EPSG:{src_crs}")

        gdf = gpd.GeoDataFrame.from_features(all_features, crs=f"EPSG:{src_crs}")
        logger.info("wfs %s/%s: %d features total", dataset, layer, len(gdf))
        return gdf
