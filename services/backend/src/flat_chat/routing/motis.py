"""MotisClient — transit reachability + timetable-feed freshness.

MOTIS has NO point-to-point matrix for TRANSIT — `one-to-many` is street-modes
only. Transit reachability is `GET /api/v1/one-to-all` with `one=lat,lon`,
`transitModes=TRANSIT`, `maxTravelTime=<minutes>`, `time=<iso>`, `arriveBy=false`,
which returns `{"all": [{place:{lat,lon,…}, duration:<minutes>}, …]}` — the
transit time from the anchor (incl. its first-mile walk) to EVERY reachable
stop. `one` is **lat,lon** (the opposite order from OSRM). `maxTravelTime` is in
MINUTES and the server caps it (default 90). A listing isn't a stop, so the
*last mile* (stop→listing walk) is added by the orchestrator, not here.

Feed freshness: MOTIS loads a finite VBB timetable window and exposes its bounds
on the Prometheus `/metrics` endpoint. We read them so the departure can be
CLAMPED into a covered day (a stale feed otherwise yields ~0 reachable stops)
and results LABELLED "schedule as of <date>". The read is cached per instance.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

import httpx

from flat_chat.listings.context import Anchor
from flat_chat.routing.errors import RoutingError

logger = logging.getLogger(__name__)

_BERLIN_TZ = ZoneInfo("Europe/Berlin")
_TIMEOUT = 20.0

# MOTIS Prometheus gauges for the loaded nigiri timetable window.
_METRIC_FIRST_DAY = "nigiri_timetable_first_day_timestamp_seconds"
_METRIC_LAST_DAY = "nigiri_timetable_last_day_timestamp_seconds"

# The window only changes on a MOTIS re-import, so cache successful reads.
# Failures are NOT cached, so recovery is immediate.
_FEED_WINDOW_TTL = 300.0

# one-to-all server cap on `maxTravelTime` (config `onetoall_max_travel_minutes`,
# default 90). Requesting more is a 400, so clamp to it.
_MOTIS_MAX_MINUTES = 90

# MOTIS HTTP endpoints (appended to the instance `motis_url`).
_METRICS_PATH = "/metrics"
_ONE_TO_ALL_PATH = "/api/v1/one-to-all"


# A loaded timetable window as (first, last) Berlin-local dates.
FeedWindow = tuple[date, date]


class CommuteDeparture(NamedTuple):
    """A transit departure clamped into the loaded feed window.

    `iso` is the ISO datetime handed to MOTIS; `stale` is True only when the feed
    has lapsed (today past its last day); `as_of` is the ISO date actually used
    when clamping was needed, else None (in-window → current)."""

    iso: str
    stale: bool
    as_of: str | None


class ReachableStop(NamedTuple):
    """One transit-reachable stop: its position + anchor→stop minutes."""

    lat: float
    lon: float
    minutes: float


def _parse_metrics_window(body: str) -> FeedWindow | None:
    """Parse the loaded-timetable window from MOTIS Prometheus `/metrics`.

    Returns `(first, last)` as Berlin-local dates, or `None` if either gauge is
    absent (older MOTIS / no timetable loaded)."""
    first: float | None = None
    last: float | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        if not value:
            continue
        metric = name.split("{", 1)[0].strip()
        try:
            ts = float(value)
        except ValueError:
            continue
        if metric == _METRIC_FIRST_DAY:
            first = ts
        elif metric == _METRIC_LAST_DAY:
            last = ts
    if first is None or last is None:
        return None
    return (
        datetime.fromtimestamp(first, _BERLIN_TZ).date(),
        datetime.fromtimestamp(last, _BERLIN_TZ).date(),
    )


def feed_window_stale(window: FeedWindow | None) -> bool:
    """True when the loaded feed window has already lapsed (today is past its
    last day). `None` (window unknown) is treated as not-stale — we can't claim
    staleness without knowing the window."""
    if window is None:
        return False
    return datetime.now(_BERLIN_TZ).date() > window[1]


def _roll_to_weekday(d: date, *, forward: bool) -> date:
    """Nudge `d` to the nearest weekday (Mon–Fri), stepping forward or back."""
    step = timedelta(days=1 if forward else -1)
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d += step
    return d


def _commute_departure(
    window: FeedWindow | None = None,
    *,
    now: datetime | None = None,
) -> CommuteDeparture:
    """Departure for a transit commute query, clamped into the loaded feed.

    A "commute" means a typical workday trip, not literally now — querying at
    02:00 on a Sunday returns night/weekend service and makes most listings look
    unreachable. So the base pick is the NEXT weekday 08:00 Berlin.

    When a feed `window` is known and that date falls outside it, clamp into the
    window (roll to its last weekday when the feed has lapsed, to its first
    weekday when the window is entirely future) so MOTIS still returns real
    reachability. `now` is injectable for deterministic tests."""
    now = now or datetime.now(_BERLIN_TZ)
    dep = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if dep <= now:
        dep += timedelta(days=1)
    while dep.weekday() >= 5:  # roll to Monday
        dep += timedelta(days=1)

    if window is None:
        return CommuteDeparture(dep.isoformat(), False, None)

    first, last = window
    if first <= dep.date() <= last:
        return CommuteDeparture(dep.isoformat(), False, None)

    # Outside the window — clamp to a valid in-window weekday.
    if dep.date() > last:  # feed lapsed
        # The gauge's last day is typically a partial/dead boundary — a GTFS
        # feed's final calendar day often has little to no service. Back off one
        # day before rolling to a weekday so we land on a fully-served day.
        target = _roll_to_weekday(last - timedelta(days=1), forward=False)
        if target < first:
            target = first
        stale = True
    else:  # window entirely in the future
        target = _roll_to_weekday(first, forward=True)
        if target > last:
            target = last
        stale = False

    clamped = datetime(target.year, target.month, target.day, 8, 0, tzinfo=_BERLIN_TZ)
    return CommuteDeparture(clamped.isoformat(), stale, target.isoformat())


class MotisClient:
    """Thin async client over MOTIS one-to-all + the `/metrics` feed window.

    Owns an instance-level TTL cache for the feed window — construct a fresh
    client for a clean cache (tests do exactly this)."""

    def __init__(self, *, motis_url: str, timeout: float = _TIMEOUT):
        self.motis_url = motis_url.rstrip("/")
        self.timeout = timeout
        self._feed_window_cache: tuple[float, FeedWindow] | None = None

    async def feed_window(self) -> FeedWindow | None:
        """The (first, last) loaded-timetable dates from MOTIS `/metrics`.

        Returns `None` when MOTIS is unreachable or the gauges are absent (caller
        then falls back to the plain next-weekday departure). Successful reads are
        cached for `_FEED_WINDOW_TTL` seconds; failures are not cached."""
        now = time.monotonic()
        cached = self._feed_window_cache
        if cached is not None and now - cached[0] < _FEED_WINDOW_TTL:
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.motis_url}{_METRICS_PATH}")
                resp.raise_for_status()
                window = _parse_metrics_window(resp.text)
        except httpx.HTTPError as exc:
            logger.warning("MOTIS /metrics unreachable — feed window unknown: %s", exc)
            return None
        if window is not None:
            self._feed_window_cache = (now, window)
        return window

    async def one_to_all(
        self, anchor: Anchor, budget_min: int | None
    ) -> tuple[list[ReachableStop], CommuteDeparture]:
        """Transit-reachable stops from `anchor` and the departure actually used.

        The caller adds the last-mile walk to turn stops into per-listing times.
        Raises `RoutingError` if MOTIS is unreachable or the response is malformed.
        """
        one = f"{anchor.lat},{anchor.lon}"  # one-to-all is lat,lon
        window = await self.feed_window()
        departure = _commute_departure(window)
        budget = int(budget_min or _MOTIS_MAX_MINUTES)
        max_minutes = max(1, min(budget, _MOTIS_MAX_MINUTES))
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.motis_url}{_ONE_TO_ALL_PATH}",
                    params={
                        "one": one,
                        "transitModes": "TRANSIT",
                        "maxTravelTime": max_minutes,
                        "arriveBy": "false",
                        "time": departure.iso,
                        "maxMatchingDistance": 500,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise RoutingError(f"MOTIS unreachable: {exc}") from exc

        reachable = data.get("all") if isinstance(data, dict) else None
        if not isinstance(reachable, list):
            raise RoutingError("MOTIS: unexpected response shape")

        stops: list[ReachableStop] = []
        for entry in reachable:
            if not isinstance(entry, dict):
                continue
            place = entry.get("place")
            dur = entry.get("duration")
            if not isinstance(place, dict) or not isinstance(dur, int | float):
                continue
            lat, lon = place.get("lat"), place.get("lon")
            if isinstance(lat, int | float) and isinstance(lon, int | float):
                stops.append(ReachableStop(float(lat), float(lon), float(dur)))
        return stops, departure
