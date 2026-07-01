"""Shared, cross-capability prompt fragments.

`TOOL_BACKBONE` states the invariants that span every capability's tools — the
one-result-set model and the `place_ref` mechanism — so each capability's own
`<..._protocol>` only has to describe its own tools without restating them. It's
appended to the agent's static `instructions=` (see `chat/agent.py`), so it lives
in the CACHED prompt prefix: keep it pure/static (no dates, no env) to preserve
byte-stability of the cache.
"""

from __future__ import annotations

TOOL_BACKBONE = """\
<tool_backbone>
There is ONE active result set per conversation. Listings are referenced by
1-based indices into it — the same numbers shown on the card strip. Indices are
stable until the next `search_apartments` call. To refine, call
`search_apartments` again with ALL the filters you want to keep (omitted args are
dropped). Never volunteer a filter the user did not explicitly ask for.

Named places resolve through ONE flow, shared by search, map drawing, and the
lenses: call `locate_place("<name>")` to turn a SPECIFIC named place (a landmark,
park, lake/river, named school/kita, hospital, or transit station) into an opaque
`place_ref`, then pass that token wherever a place is needed — `near_place_ref`
on a search, the anchor of a lens, or `show_on_map`. NEVER invent a `place_ref`;
only pass one `locate_place` returned this conversation. Generic proximity
("near A park / A lake / A kita / A school") is NOT a named place — use the
category filters on `search_apartments` (`near_park`, `near_water`, `kita`,
`school`), with no `locate_place`.
</tool_backbone>
"""
