"""LLM-facing context layer.

Owns every byte the LLM sees about result data, tool affordances, and
per-turn state. Tools call into here to format their returns; the agent's
dynamic-instructions decorator calls into here to build the per-turn state
prompt. Nothing outside this module composes prose for the LLM.

Layout:
  - `LlmResultSetView` — wraps the active search DataFrame + params + notes,
    and exposes `summary` / `page` / `detail` formatting methods.
  - `format_navigation_footer` — free function building the "what tool can
    I call next" menu appended to list-style views. Co-located with the
    tool names it references.
  - `format_geo_context_prose` — neighbourhood-context prose for
    `get_result_details`/`open_listing` single-listing returns.
  - `build_dynamic_state_prompt` — composes the XML state snapshot injected
    into the system prompt every turn (`<current_state>` + `<user_focus>`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from flat_chat.search.schemas import SearchParams

if TYPE_CHECKING:
    from flat_chat.chat.state import ChatDeps
    from flat_chat.chat.ui_state import UiState
    from flat_chat.search.geo_filters import ListingContext


def xml_block(tag: str, body: str) -> str:
    """Wrap `body` in `<tag>...</tag>`. Trims leading/trailing newlines only —
    internal indentation is preserved (matters for the nested elements inside
    `<current_state>`).
    """
    return f"<{tag}>\n{body.strip(chr(10))}\n</{tag}>"

# Single source of truth for the column order used in CSV-style listings shown
# to the LLM. Detail view (prose) handles its own field order via _PROSE_FIELDS.
_LIST_COLUMNS: tuple[tuple[str, str], ...] = (
    ("title", "title"),
    ("price_warm_eur", "warm €"),
    ("rooms", "rooms"),
    ("area_sqm", "m²"),
    ("district", "district"),
)

_PROSE_FIELDS: tuple[tuple[str, str, str], ...] = (
    # (column, label, format_spec). Specs: "s" plain str, "eur", "sqm", "plain".
    ("title", "Title", "s"),
    ("price_warm_eur", "Warm rent", "eur"),
    ("price_cold_eur", "Cold rent", "eur"),
    ("rooms", "Rooms", "plain"),
    ("area_sqm", "Area", "sqm"),
    ("floor", "Floor", "plain"),
    ("district", "District", "s"),
    ("address", "Address", "s"),
    ("available_from", "Available from", "s"),
    ("listing_type", "Type", "s"),
    ("source_url", "URL", "s"),
)


@dataclass
class LlmResultSetView:
    """Apartments currently under discussion in a session — LLM-facing view.

    Persists across messages. The user iterates by refining filters, paging
    through results, and requesting details until the set is small enough to
    decide on. This class owns every listing-formatting concern shown to the
    LLM — there is no listing formatting outside this module.

    `notes` captures soft signals the LLM (and ultimately the user) should
    see alongside the data — e.g. "semantic ranking unavailable, sorted by
    recency instead". Prepended to every formatted view so the model can't
    miss them.
    """

    df: pd.DataFrame
    params: SearchParams
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.df)

    def order_label(self) -> str:
        """Human-readable description of the actual sort in effect.

        Never lies: if no query was given, "relevance" falls back to recency,
        and the label reflects that.
        """
        match self.params.sort_by:
            case "relevance" if self.params.query:
                return "sorted by relevance to your query"
            case "price":
                return "sorted by lowest warm rent"
            case "area":
                return "sorted by largest area"
            case _:
                return "most recent first"

    def summary(self, top_n: int = 5) -> str:
        """Prose overview after a search. Always ends with the nav footer."""
        if self.total == 0:
            return self._notes_prefix() + (
                "No apartments found matching those criteria. "
                "Try broadening your search."
            )

        shown = min(top_n, self.total)
        lines = [f"Found {self.total} listings, {self.order_label()}."]
        if shown > 0:
            lines.append(f"Showing 1–{shown}:")
            for i in range(shown):
                lines.append(self._format_row_prose(self.df.iloc[i], i + 1))
        lines.append(format_navigation_footer(self, shown_end=shown))
        return self._notes_prefix() + "\n".join(lines)

    def page(self, page: int, page_size: int = 10) -> str:
        """CSV body of listings start..end. Compact for the LLM."""
        if self.total == 0:
            return "No results to page through. Run a search first."

        total_pages = max(1, (self.total + page_size - 1) // page_size)
        if page < 1 or page > total_pages:
            return (
                f"Page {page} is out of range. "
                f"There are {self.total} results ({total_pages} pages of {page_size})."
            )

        start = (page - 1) * page_size
        end = min(start + page_size, self.total)
        header = ",".join(["#", *[label for _, label in _LIST_COLUMNS]])
        rows = [header]
        for i in range(start, end):
            rows.append(self._format_row_csv(self.df.iloc[i], i + 1))

        lines = [
            f"Page {page}/{total_pages} — listings {start + 1}–{end} of {self.total}, "
            f"{self.order_label()}.",
            "```csv",
            "\n".join(rows),
            "```",
            format_navigation_footer(self, shown_end=end),
        ]
        return self._notes_prefix() + "\n".join(lines)

    def detail(self, indices: list[int]) -> str:
        """Prose, full fields for specific listings. 1-based indexing."""
        if self.total == 0:
            return self._notes_prefix() + (
                "No results to show details for. Run a search first."
            )

        chunks: list[str] = []
        for idx in indices:
            pos = idx - 1
            if pos < 0 or pos >= self.total:
                chunks.append(f"#{idx}: out of range (results are 1–{self.total}).")
                continue
            chunks.append(self._format_row_detail(self.df.iloc[pos], idx))
        return self._notes_prefix() + "\n\n".join(chunks)

    def _notes_prefix(self) -> str:
        """Soft signals shown ahead of every formatted output."""
        if not self.notes:
            return ""
        return "\n".join(f"Note: {n}" for n in self.notes) + "\n\n"

    # ----- formatting helpers (single source of truth) -----

    def _format_row_prose(self, row: pd.Series, idx: int) -> str:
        parts: list[str] = []
        for col, _label in _LIST_COLUMNS:
            val = row.get(col)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            parts.append(_format_cell(col, val))
        return f"  {idx}. " + " | ".join(parts)

    def _format_row_csv(self, row: pd.Series, idx: int) -> str:
        cells = [str(idx)]
        for col, _label in _LIST_COLUMNS:
            val = row.get(col)
            cells.append(_format_cell(col, val, csv=True))
        return ",".join(cells)

    def _format_row_detail(self, row: pd.Series, idx: int) -> str:
        lines = [f"--- Listing #{idx} ---"]
        for col, label, spec in _PROSE_FIELDS:
            val = row.get(col)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            lines.append(f"{label}: {_format_field(val, spec)}")
        return "\n".join(lines)


def format_navigation_footer(view: LlmResultSetView, *, shown_end: int) -> str:
    """Menu of follow-up tool calls appended to every list-style view.

    Lives in this module (next to the view it describes) rather than on
    `LlmResultSetView` itself so the data class doesn't reference tool
    names — affordances are presentation, not data.
    """
    if view.total == 0:
        return (
            "\nTo explore further:\n"
            "  • search_apartments(...)          — refine with new filters or query"
        )

    remaining = view.total - shown_end
    lines: list[str] = []
    if remaining <= 0:
        lines.append("All results shown above. To explore further:")
    else:
        lines.append(f"{remaining} more available. To explore further:")
        lines.append(
            "  • get_result_page(page=N)         — next page (10 per page)"
        )
    lines.append(
        "  • open_listing(indices=[N])       — open the detail panel + full info"
    )
    lines.append(
        "  • search_apartments(...)          — refine with new filters or query"
    )
    return "\n" + "\n".join(lines)


def _format_list_section[T](
    items: list[T] | None,
    heading: str,
    render: Callable[[T], str],
) -> list[str]:
    """Heading + bulleted rows for an optional list. Empty list → no output."""
    if not items:
        return []
    return [heading, *(f"  - {render(x)}" for x in items)]


def format_geo_context_prose(idx: int, context: ListingContext) -> str:
    """LLM-facing neighbourhood-context prose for one listing.

    Appended to the standard `view.detail([idx])` output by `open_listing`
    when called with a single index. Mirrors what the frontend renders in
    the Neighbourhood-context detail-panel block, but as text the LLM can
    quote when the user asks follow-up questions about transit / schools /
    noise / MSS. Sections only render when they have data — partial backend
    wiring produces partial prose, never empty headings.
    """
    parts: list[str] = [f"--- Listing #{idx} — neighbourhood context ---"]

    parts.extend(_format_list_section(
        context.transit,
        "Nearby transit:",
        lambda s: f"{s.name} — {', '.join(s.lines) if s.lines else '—'} "
                  f"({s.distance_m}m, {s.walk_minutes}min walk)",
    ))

    if context.school_catchment is not None:
        sc = context.school_catchment
        parts.append(
            f"Primary school catchment: {sc.school_name or sc.catchment_id}"
        )

    parts.extend(_format_list_section(
        context.nearest_schools,
        "Nearby schools:",
        lambda s: f"{s.name or 'unnamed'} "
                  f"({s.school_type or 'unknown type'}) — {s.distance_m}m",
    ))

    parts.extend(_format_list_section(
        context.nearest_parks,
        "Nearby parks:",
        lambda p: f"{p.name or 'unnamed'} — {p.distance_m}m",
    ))

    if context.nearest_playground is not None:
        pg = context.nearest_playground
        parts.append(
            f"Nearest playground: {pg.name or 'unnamed'} — {pg.distance_m}m"
        )

    parts.extend(_format_list_section(
        context.nearest_hospitals,
        "Hospitals nearby:",
        lambda h: f"{h.name or 'unnamed'} ({h.tier}) — {h.distance_m}m",
    ))

    if context.nearest_water is not None:
        w = context.nearest_water
        parts.append(
            f"Nearest water: {w.name or w.water_kind or 'water'} — {w.distance_m}m"
        )

    character_bits: list[str] = []
    if context.noise is not None and context.noise.label is not None:
        character_bits.append(f"street noise: {context.noise.label}")
    if context.greenery is not None and context.greenery.label is not None:
        character_bits.append(f"greenery: {context.greenery.label}")
    if context.density is not None and context.density.label is not None:
        character_bits.append(f"density: {context.density.label}")
    if context.mss is not None and context.mss.status_label is not None:
        mss_bits = [context.mss.status_label]
        if context.mss.dynamics_label is not None:
            mss_bits.append(context.mss.dynamics_label)
        character_bits.append(f"Sozialmonitoring: {' · '.join(mss_bits)}")
    if character_bits:
        parts.append("Neighbourhood character: " + ", ".join(character_bits))

    if context.disabled_parking_count > 0:
        count = context.disabled_parking_count
        parts.append(f"Disabled parking nearby: {count} spots within 300m")
    return "\n".join(parts)


def build_dynamic_state_prompt(deps: ChatDeps) -> str:
    """Per-turn state snapshot injected into the agent's system prompt.

    Two XML blocks, both consumed by the LLM:

    - `<current_state>` — the active search: count, sort order, filters in
      effect, and any soft-fallback notes. Always present (empty form when
      no search has run yet).
    - `<user_focus>` — present only when the user has expanded a card via
      click. Tells the model which 1-based index "this one" / "the one I'm
      looking at" refers to.

    XML wrappers are deliberate: Claude attends to tagged blocks more
    reliably than to inline prose, and the stable prefix (`<current_state>`)
    plays well with prompt caching since only the inner values change turn
    to turn.
    """
    view = deps.session.result_set
    state = deps.state

    blocks: list[str] = [_current_state_block(view)]

    # `_index_for_active` returns None when no card is expanded OR when the
    # active_id no longer maps to a row in the current result set (stale id
    # after a refining search). Both should suppress the focus block.
    focus_idx = _index_for_active(state)
    if focus_idx is not None:
        blocks.append(
            xml_block("user_focus", f"  <expanded_card>{focus_idx}</expanded_card>")
        )

    return "\n".join(blocks)


def _current_state_block(view: LlmResultSetView | None) -> str:
    if view is None:
        return xml_block(
            "current_state",
            "  <total>0</total>\n"
            "  <note>No search has run yet in this conversation.</note>",
        )

    filters = view.params.model_dump(exclude_none=True, exclude_defaults=True)
    # `sort_by` is a structural field, surfaced separately via <order>.
    filters.pop("sort_by", None)
    filters_json = json.dumps(filters, default=str, sort_keys=True)

    lines = [
        f"  <total>{view.total}</total>",
        f"  <order>{view.order_label()}</order>",
        f"  <filters>{filters_json}</filters>",
    ]
    for note in view.notes:
        lines.append(f"  <note>{note}</note>")
    return xml_block("current_state", "\n".join(lines))


def _index_for_active(state: UiState) -> int | None:
    """Map UiState.active_id back to a 1-based index in the result set."""
    if state.active_id is None:
        return None
    for i, apt in enumerate(state.results, start=1):
        if apt.id == state.active_id:
            return i
    return None


def _format_cell(col: str, val, *, csv: bool = False) -> str:
    """Compact formatting used in summary/page rows."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if col == "price_warm_eur":
        return f"€{float(val):.0f}" if not csv else f"{float(val):.0f}"
    if col == "rooms":
        return f"{float(val):g}rm" if not csv else f"{float(val):g}"
    if col == "area_sqm":
        return f"{float(val):.0f}m²" if not csv else f"{float(val):.0f}"
    text = str(val)
    if csv and ("," in text or '"' in text):
        return '"' + text.replace('"', '""') + '"'
    return text


def _format_field(val, spec: str) -> str:
    """Verbose formatting used in detail view."""
    if spec == "eur":
        return f"€{float(val):.0f}/month"
    if spec == "sqm":
        return f"{float(val):.0f} m²"
    if spec == "plain":
        if isinstance(val, float):
            return f"{val:g}"
        return str(val)
    return str(val)
