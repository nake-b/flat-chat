"""Routing error type — its own leaf module so the OSRM/MOTIS clients can raise
it without importing `routing/service.py` (which imports them)."""

from __future__ import annotations


class RoutingError(RuntimeError):
    """A routing engine was unreachable or returned an unusable response.

    Raised so the calling tool can surface a graceful "couldn't compute travel
    times" message instead of half-applying a lens."""
