"""Berlin GDI WFS client.

Hits `https://gdi.berlin.de/services/wfs/<dataset>` for GetCapabilities
(layer discovery) and GetFeature (data extraction). Returns GeoDataFrames
in the source CRS (EPSG:25833) — CRS reprojection happens in transform/.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import requests
from requests.exceptions import HTTPError

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

    def _resolve_layer_name(self, dataset: str, layer: str) -> str:
        """Resolve the effective WFS typeName for a dataset/layer request.

        Some Berlin endpoints expose namespaced type names (e.g.
        ``foo:bar``), others only the local part (``bar``). We read
        capabilities once and pick the best available match so GetFeature
        stays robust across endpoint quirks.
        """
        try:
            names = [info.name for info in self.get_capabilities(dataset)]
        except Exception:
            logger.warning(
                "wfs %s/%s: capabilities lookup failed, using requested layer as-is",
                dataset,
                layer,
            )
            return layer

        if layer in names:
            return layer

        local = layer.split(":", 1)[-1]
        if local in names:
            logger.info(
                "wfs %s/%s: resolved typeName to local layer '%s'",
                dataset,
                layer,
                local,
            )
            return local

        namespaced_matches = [name for name in names if name.split(":", 1)[-1] == local]
        if len(namespaced_matches) == 1:
            resolved = namespaced_matches[0]
            logger.info(
                "wfs %s/%s: resolved typeName to namespaced layer '%s'",
                dataset,
                layer,
                resolved,
            )
            return resolved

        return layer

    def _first_page_fallback_request(
        self,
        dataset: str,
        *,
        requested_layer: str,
        resolved_layer: str,
    ) -> requests.Response | None:
        """Try tolerant first-page GetFeature variants for picky WFS servers."""
        local = requested_layer.split(":", 1)[-1]
        layer_candidates = [resolved_layer, requested_layer, local]
        seen_layers: set[str] = set()
        deduped_layers: list[str] = []
        for candidate in layer_candidates:
            if candidate and candidate not in seen_layers:
                deduped_layers.append(candidate)
                seen_layers.add(candidate)

        for version, type_key in (
            ("2.0.0", "typeNames"),
            ("2.0.0", "typeName"),
            ("1.1.0", "typeName"),
        ):
            for type_name in deduped_layers:
                params = {
                    "service": "WFS",
                    "version": version,
                    "request": "GetFeature",
                    type_key: type_name,
                    "outputFormat": "application/json",
                }
                resp = requests.get(
                    self._endpoint(dataset),
                    params=params,
                    timeout=self.http_timeout_s,
                )
                if resp.ok:
                    logger.warning(
                        "wfs %s/%s: using fallback GetFeature variant "
                        "(version=%s, %s=%s, unpaged)",
                        dataset,
                        requested_layer,
                        version,
                        type_key,
                        type_name,
                    )
                    return resp
        return None

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
    # runaway query should fail loudly, not eat memory. Sized for the
    # strategic noise map (`noise_levels`), which is a 10m raster of
    # modelled receivers along every road/rail line in Berlin — empirically
    # ~3–6M points. Headroom over that. Smaller datasets (schools, hospitals)
    # never approach this; if one ever does, that's the bug we want to catch.
    MAX_FEATURES: int = 10_000_000

    def iter_layer_pages(
        self,
        dataset: str,
        layer: str,
        *,
        src_crs: int = 25833,
    ) -> Iterator[gpd.GeoDataFrame]:
        """Paginated GetFeature, yielding one page as a GeoDataFrame at a time.

        Bounded-memory streaming variant. The loader uses this to drive a
        per-page transform → write pipeline so a 3.8M-row raster (street
        noise) doesn't have to materialise as one giant in-memory frame
        before any row hits Postgres. Each yielded frame is independent;
        the caller decides whether to TRUNCATE+INSERT on the first page or
        just INSERT subsequent ones.

        Terminates when a page returns fewer features than ``PAGE_SIZE``
        (covers servers that don't report ``numberMatched`` reliably).
        Raises ``RuntimeError`` if the running total crosses
        ``MAX_FEATURES`` — runaway-query guard.
        """
        effective_layer = self._resolve_layer_name(dataset, layer)

        base_params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": effective_layer,
            "outputFormat": "application/json",
        }
        logger.info("wfs %s/%s: fetching", dataset, effective_layer)

        seen_total = 0
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
            try:
                resp.raise_for_status()
            except HTTPError:
                if effective_layer != layer and start_index == 0:
                    # Fallback once to the raw configured layer if a
                    # capabilities-based resolution is rejected by GetFeature.
                    logger.warning(
                        "wfs %s/%s: resolved typeName '%s' failed, retrying raw layer",
                        dataset,
                        layer,
                        effective_layer,
                    )
                    effective_layer = layer
                    base_params["typeNames"] = layer
                    continue
                if start_index == 0:
                    fallback_resp = self._first_page_fallback_request(
                        dataset,
                        requested_layer=layer,
                        resolved_layer=effective_layer,
                    )
                    if fallback_resp is not None:
                        resp = fallback_resp
                    else:
                        raise
                else:
                    raise
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
                return
            page_gdf = gpd.GeoDataFrame.from_features(
                features, crs=f"EPSG:{src_crs}"
            )
            yield page_gdf
            # Fallback requests are intentionally unpaged (no startIndex/count)
            # and therefore always complete in one response.
            if "startIndex" not in resp.url and "count" not in resp.url:
                return
            seen_total += page_n
            if seen_total >= self.MAX_FEATURES:
                raise RuntimeError(
                    f"wfs {dataset}/{layer}: reached MAX_FEATURES "
                    f"({self.MAX_FEATURES}) — refusing to keep paginating"
                )
            if page_n < self.PAGE_SIZE:
                return
            start_index += page_n

    def fetch_layer(
        self,
        dataset: str,
        layer: str,
        *,
        src_crs: int = 25833,
    ) -> gpd.GeoDataFrame:
        """Whole-layer convenience wrapper around ``iter_layer_pages``.

        Materialises every page into one big GeoDataFrame. Fine for small
        datasets (schools, hospitals, parks) and the existing pagination
        tests; the bulk loader uses ``iter_layer_pages`` directly to avoid
        the memory blow-up.
        """
        pages = list(self.iter_layer_pages(dataset, layer, src_crs=src_crs))
        if not pages:
            logger.warning("wfs %s/%s: zero features returned", dataset, layer)
            return gpd.GeoDataFrame(geometry=[], crs=f"EPSG:{src_crs}")
        gdf = gpd.GeoDataFrame(
            pd.concat(pages, ignore_index=True), crs=f"EPSG:{src_crs}"
        )
        logger.info("wfs %s/%s: %d features total", dataset, layer, len(gdf))
        return gdf
