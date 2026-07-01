"""MapOverlayCapability — draw/hide geometries on the map (agent-owned overlays).

Its own toolset + `<overlay_protocol>`, co-located so renaming an overlay tool is
one edit. `search_apartments` (in CoreCapability) calls `rebuild_search_overlays_hook`
after each search to redraw the geometry the search filters on. This module is the
import SINK of the chat-tool trio — it imports nothing from `tools.py` or
`lens_tools.py`, which is what keeps the three modules acyclic.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AgentToolset

from flat_chat.chat.state import ChatDeps
from flat_chat.chat.state_emission import StateEmittingToolset

toolset: FunctionToolset[ChatDeps] = FunctionToolset()


_OVERLAY_PROTOCOL = """\
<overlay_protocol>
Drawing geometries on the map (this does NOT change the result set):
  - `show_on_map(place_refs=[…], transit_lines=[…])` — DRAW named places (via
    their `place_ref`) and transit lines by name ("U7") WITHOUT filtering. Pass
    several at once. For "draw the U8 so I can see these listings against it",
    "show me the Spree", "where is Tiergarten?".
  - `hide_on_map(targets=[…])` — remove SPECIFIC drawn geometries by label/id (as
    shown in `<current_state>`). Pass several at once. A line that's still an
    active search filter redraws next search — drop the filter to keep it off.
  - `clear_map_overlays()` — wipe ALL drawn geometries ("clear the map").

Two ways a geometry appears:
  1. AUTOMATICALLY — a `search_apartments` call using `near_place_ref` or
     `transit.lines` draws that place/line as a side effect. You call nothing extra.
  2. ON PURPOSE — `show_on_map(...)` draws WITHOUT changing the results. Use it
     when the user wants to see something, or proactively to orient them.
`<current_state>` lists what's already drawn — never redraw something already
there or that the user has dismissed.
</overlay_protocol>

<overlay_phrase_map>
  - "show me the U7" / "trace the M10" → show_on_map(transit_lines=["U7"])
  - "draw the U8 and U9"               → show_on_map(transit_lines=["U8", "U9"])
  - "show me the Spree" /
    "where's Tiergarten?" /
    "draw the Tiergarten"              → locate_place(…) → show_on_map(place_refs=[…])
  - "hide the U8" / "remove that line" → hide_on_map(targets=["U8"])
  - "get rid of the U8 and U9"         → hide_on_map(targets=["U8", "U9"])
  - "clear the map" / "hide everything" → clear_map_overlays()
</overlay_phrase_map>
"""


@toolset.instructions
def overlay_protocol_instructions() -> str:
    return _OVERLAY_PROTOCOL


@toolset.tool
async def show_on_map(
    ctx: RunContext[ChatDeps],
    place_refs: list[str] | None = None,
    transit_lines: list[str] | None = None,
) -> str:
    """Draw one or more places / transit lines on the map WITHOUT changing results.

    Use this when the user wants to SEE geometries in relation to the current
    listings — "draw the U8 so I can see these against it", "show me the Spree",
    "where's Tiergarten?" — or proactively when it helps orient them. It does
    NOT filter; the result set and map markers are untouched. (To filter
    listings near a place, use `search_apartments(near_place_ref=…)` instead —
    that draws the geometry too, as a side effect.)

    Draw SEVERAL at once by passing lists (`transit_lines=["U8", "U9"]`) rather
    than calling this tool repeatedly — one call draws them in a single atomic
    update. Each overlay is PINNED: it stays on the map across subsequent
    searches until removed (`hide_on_map` / `clear_map_overlays`) or the user
    dismisses it. Drawing the same place/line again just refreshes it. The
    `<current_state>` block lists what's already drawn — don't redraw something
    that's there or that the user hid.

    Args:
        place_refs: Zero or more opaque `place_ref`s from `locate_place` (named
            parks, lakes/rivers, landmarks, etc.). Resolve each name with
            `locate_place` first, then pass the chosen candidates' refs here.
        transit_lines: Zero or more line names like ["U7", "S41", "M10"]. Passed
            by name directly — no `locate_place` needed (line names are
            unambiguous).
    """
    places = place_refs or []
    lines = transit_lines or []
    if not places and not lines:
        return (
            "Pass at least one place_ref (from locate_place) or transit_line "
            '(e.g. "U7") to draw something.'
        )

    # Resolve each ref/line to a geometry and pin it; track what resolved
    # (`drawn`) vs what didn't (`missed`) so the return note can report both.
    drawn: list[str] = []
    missed: list[str] = []

    # Places: each opaque place_ref → a named_places geometry via PlaceService.
    for ref in places:
        overlay = await ctx.deps.place_service.overlay_geometry(ref, origin="pinned")
        if overlay is not None:
            _upsert_overlay(ctx.deps.state, overlay)  # replaces a same-id overlay
            drawn.append(overlay.label)
        else:
            missed.append(f'place_ref "{ref}"')

    # Transit lines: line name → route-shape geometry (display-only resolver).
    for line in lines:
        overlay = await ctx.deps.transit_overlay_service.route_geometry(
            line, origin="pinned"
        )
        if overlay is not None:
            _upsert_overlay(ctx.deps.state, overlay)
            drawn.append(overlay.label)
        else:
            missed.append(f'line "{line}"')

    if not drawn:
        return (
            f"Couldn't find {', '.join(missed)} to draw. For a place, resolve it "
            "with locate_place first; transit lines must be a real line name."
        )
    note = f"Drawing {', '.join(drawn)} on the map."
    if missed:
        note += f" (Couldn't find {', '.join(missed)}.)"
    return note


@toolset.tool
async def clear_map_overlays(ctx: RunContext[ChatDeps]) -> str:
    """Remove ALL geometries currently drawn on the map (lines, shapes, zones).

    Use when the user wants a clean map ("clear the map", "remove those lines",
    "hide everything"). Does not touch the result set or markers. To remove just
    one, the user can dismiss it directly in the UI.
    """
    overlays = ctx.deps.state.map_overlays
    if not overlays:
        return "No geometries are currently drawn on the map."
    n = len(overlays)
    ctx.deps.state.map_overlays = []
    return f"Cleared {n} geometr{'y' if n == 1 else 'ies'} from the map."


@toolset.tool
async def hide_on_map(ctx: RunContext[ChatDeps], targets: list[str]) -> str:
    """Remove SPECIFIC drawn geometries from the map (as opposed to
    `clear_map_overlays`, which wipes everything).

    Use when the user wants to hide particular overlays — "hide the U8",
    "remove that line", "get rid of the U8 and U9". Match each target against
    the overlays listed in `<current_state>` by their LABEL or id
    (case-insensitive) — e.g. pass `["U8", "Tiergarten"]`. Hide SEVERAL at once
    in one call rather than calling this tool repeatedly.

    This only edits what's drawn; it never changes the result set. A target
    that isn't currently on the map is reported back, not treated as an error.

    IMPORTANT: a line that is still an ACTIVE search filter (shown as `search`
    in `<current_state>`) will be REDRAWN on the next search. To keep such a
    line off the map for good, drop the filter instead — re-run
    `search_apartments` without it — rather than hiding it here.

    Args:
        targets: Labels or ids of overlays to remove, as shown in
            `<current_state>` (e.g. ["U8", "U9"]). Case-insensitive.
    """
    overlays = ctx.deps.state.map_overlays
    wanted = [t.strip() for t in targets if t and t.strip()]
    if not wanted:
        return "Tell me which overlay to hide (a label or id from the map)."

    # Match case-insensitively on either id or label — the agent passes whatever
    # it saw in <current_state> (usually the label, e.g. "U8").
    norm = {w.casefold() for w in wanted}
    removed = [
        o for o in overlays if o.id.casefold() in norm or o.label.casefold() in norm
    ]
    if not removed:
        return f"Nothing matching {', '.join(wanted)} is on the map right now."

    # Drop the matched overlays by id (label collisions are theoretically
    # possible; id is the stable key).
    removed_ids = {o.id for o in removed}
    ctx.deps.state.map_overlays = [o for o in overlays if o.id not in removed_ids]

    note = f"Removed {', '.join(o.label for o in removed)} from the map."

    # Report any targets that matched nothing (not an error — just feedback).
    matched = {o.id.casefold() for o in removed} | {o.label.casefold() for o in removed}
    not_found = [w for w in wanted if w.casefold() not in matched]
    if not_found:
        note += f" ({', '.join(not_found)} wasn't drawn.)"

    # A `search`-origin overlay is redrawn by the next search (it mirrors an
    # active filter), so warn that hiding alone won't keep it off the map.
    search_tied = [o.label for o in removed if o.origin == "search"]
    if search_tied:
        note += (
            f" Note: {', '.join(search_tied)} is tied to the active search and "
            "will redraw on the next search — drop that filter to keep it off."
        )
    return note


def _upsert_overlay(state, overlay) -> None:
    """Add `overlay`, replacing any existing one with the same id (stable per
    logical place/line — so redrawing refreshes rather than duplicates)."""
    state.map_overlays = [o for o in state.map_overlays if o.id != overlay.id] + [
        overlay
    ]


async def _rebuild_search_overlays(ctx: RunContext[ChatDeps], params) -> None:
    """Recompute the SEARCH-derived overlays from the active search's spatial
    anchors (`near_place_ref` / `transit.lines`), preserving pinned overlays.

    The two anchors mirror the search tool's own asymmetry: a named place goes
    through `near_place_ref`, while "near the U8" rides `transit.lines` (never
    `near_place_ref`). Both feed `map_overlays` so the geometry a search filters
    on is always the geometry drawn. A pinned/lens overlay with the same id wins
    (it's sticky), so we never duplicate it as a search overlay."""
    # Keep pinned (user/agent) AND lens (a lens's anchor) overlays across a
    # refinement; only "search" overlays are rebuilt from the new params.
    kept = [o for o in ctx.deps.state.map_overlays if o.origin in ("pinned", "lens")]
    kept_ids = {o.id for o in kept}
    fresh: list = []

    if params.near_place_ref:
        overlay = await ctx.deps.place_service.overlay_geometry(
            params.near_place_ref, origin="search"
        )
        if overlay is not None and overlay.id not in kept_ids:
            fresh.append(overlay)

    if params.transit is not None and params.transit.lines:
        for line in params.transit.lines:
            overlay = await ctx.deps.transit_overlay_service.route_geometry(
                line, origin="search"
            )
            if overlay is not None and overlay.id not in kept_ids:
                fresh.append(overlay)

    ctx.deps.state.map_overlays = kept + fresh


async def rebuild_search_overlays_hook(ctx: RunContext[ChatDeps]) -> None:
    """Post-search hook invoked by `search_apartments`: redraw the overlays the
    active search filters on (from `state.search_params`), keeping pinned ones."""
    await _rebuild_search_overlays(ctx, ctx.deps.state.search_params)


@dataclass
class MapOverlayCapability(AbstractCapability[ChatDeps]):
    """Map-overlay tools (`show_on_map`, `hide_on_map`, `clear_map_overlays`)
    bundled as a capability. Wrapped in `StateEmittingToolset` so overlay
    mutations auto-emit a STATE_SNAPSHOT (see `state_emission.py`)."""

    def get_toolset(self) -> AgentToolset[ChatDeps] | None:
        return StateEmittingToolset(toolset)
