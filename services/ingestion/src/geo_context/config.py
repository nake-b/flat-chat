"""Configuration for the geo-context ingestion pipeline.

Reads the YAML catalog of datasets and exposes typed records the
orchestrator iterates over. DATABASE_URL is reused from the existing
ingestion-level `config.py` (env-loaded via python-dotenv).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

DATASETS_YAML = Path(__file__).parent / "datasets.yaml"


Status = Literal["stable", "wip", "discovery", "deprecated"]


@dataclass(frozen=True)
class WfsDataset:
    key: str
    dataset: str
    layer: str
    table: str
    geom_type: str
    enabled: bool
    status: Status
    license: str
    source_url: str
    update_cadence: str
    description: str
    extra: dict[str, str | int | bool] | None = None


@dataclass(frozen=True)
class GtfsConfig:
    feed_url: str
    license: str
    update_cadence: str
    enabled: bool
    output_tables: list[str]


@dataclass(frozen=True)
class Catalog:
    wfs: list[WfsDataset]
    gtfs: GtfsConfig


def load_catalog(path: Path = DATASETS_YAML) -> Catalog:
    """Parse `datasets.yaml` into typed records."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    wfs = [
        WfsDataset(
            key=entry["key"],
            dataset=entry["dataset"],
            layer=entry["layer"],
            table=entry["table"],
            geom_type=entry["geom_type"],
            enabled=bool(entry.get("enabled", False)),
            status=entry["status"],
            license=entry.get("license", ""),
            source_url=entry.get("source_url", ""),
            update_cadence=entry.get("update_cadence", "unknown"),
            description=entry.get("description", ""),
            extra=entry.get("extra"),
        )
        for entry in (raw.get("wfs") or [])
    ]

    gtfs_raw = raw.get("gtfs") or {}
    gtfs = GtfsConfig(
        feed_url=gtfs_raw.get("feed_url", ""),
        license=gtfs_raw.get("license", ""),
        update_cadence=gtfs_raw.get("update_cadence", "unknown"),
        enabled=bool(gtfs_raw.get("enabled", False)),
        output_tables=[o["table"] for o in (gtfs_raw.get("outputs") or [])],
    )

    return Catalog(wfs=wfs, gtfs=gtfs)
