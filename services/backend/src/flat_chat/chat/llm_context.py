"""LLM-facing context layer.

Owns every byte the LLM sees about result data, tool affordances, and
per-turn state. Tools call into here to format their returns; the agent's
dynamic-instructions decorator calls into here to build the per-turn state
prompt. Nothing outside this module composes prose for the LLM.

`LlmResultSetView` is a thin formatter over the state: it reads
`total_results` for counts and formats whatever card slice the caller hands
it (`preview_cards` for the summary, on-demand-hydrated cards for deeper
pages). The result set itself is `state.result_markers`.

Layout:
  - `LlmResultSetView` — wraps the active SessionState and exposes
    `summary` / `page` / `detail` formatting methods.
  - `format_navigation_footer` — free function building the "what tool can
    I call next" menu appended to list-style views.
  - `format_listing_detail_prose` — neighbourhood-context prose for the
    `<user_focus>` block when a listing is open.
  - `build_dynamic_state_prompt` — composes the XML state snapshot
    injected into the system prompt every turn.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from flat_chat.chat.session_state import SessionState
from flat_chat.listings.context import ListingCard, ListingDetail


def xml_block(tag: str, body: str) -> str:
    """Wrap `body` in `<tag>...</tag>`. Trims surrounding newlines only."""
    return f"<{tag}>\n{body.strip(chr(10))}\n</{tag}>"


def xml_inline(tag: str, body: object) -> str:
    """Single-line `<tag>body</tag>` — for leaf elements nested inside a block
    (as opposed to `xml_block`, which wraps a multi-line body on its own lines)."""
    return f"<{tag}>{body}</{tag}>"


@dataclass
class LlmResultSetView:
    """Thin formatter over the active SessionState — LLM-facing view.

    The result set is `state.result_markers` (the ordered list of every
    match); cards are NOT held here. The view reads `total_results` for the
    headline count and formats whatever card slice the caller hands it:
    `preview_cards` for the summary, on-demand-hydrated cards for deeper
    pages / opened listings.
    """

    state: SessionState

    @property
    def total(self) -> int:
        return self.state.total_results

    def order_label(self) -> str:
        """Human-readable description of the actual sort in effect.

        Never lies: relevance with no query / no embedder falls back to
        recency, and the label reflects that.
        """
        params = self.state.search_params
        if params is None:
            return "most recent first"
        match params.sort_by:
            case "relevance" if params.query:
                return "sorted by relevance to your query"
            case "price":
                return "sorted by lowest warm rent"
            case "area":
                return "sorted by largest area"
            case _:
                return "most recent first"

    def summary(self, cards: list[ListingCard], top_n: int = 5) -> str:
        """Prose overview after a search. `cards` is the preview slice
        (`state.preview_cards`). Always ends with the nav footer."""
        if self.total == 0 or not cards:
            return (
                "No apartments found matching those criteria. "
                "Try broadening your search."
            )

        shown = min(top_n, len(cards))
        lines = [f"Found {self.total} listings, {self.order_label()}."]
        lines.append(f"Showing 1–{shown}:")
        for i in range(shown):
            lines.append(_format_card_prose(cards[i], i + 1))
        lines.append(format_navigation_footer(self, shown_end=shown))
        return "\n".join(lines)

    def page(
        self,
        cards: list[ListingCard],
        *,
        start: int,
        page: int,
        total_pages: int,
        page_size: int = 10,
    ) -> str:
        """CSV body for one page. `cards` is the hydrated slice for this page;
        `start` is its 0-based absolute offset into the result set. Compact
        for the LLM."""
        if not cards:
            return "No results to page through. Run a search first."

        end = start + len(cards)
        rows = ["#,title,warm €,rooms,m²,district"]
        for i, card in enumerate(cards):
            rows.append(_format_card_csv(card, start + i + 1))

        return "\n".join(
            [
                f"Page {page}/{total_pages} — listings {start + 1}–{end} of "
                f"{self.total}, {self.order_label()}.",
                "```csv",
                "\n".join(rows),
                "```",
                format_navigation_footer(self, shown_end=end),
            ]
        )

    def detail(self, items: list[tuple[int, ListingCard | None]]) -> str:
        """Prose, full fields for specific listings. `items` pairs each
        1-based index with its hydrated card (or None when out of range)."""
        if not items:
            return "No results to show details for. Run a search first."

        chunks: list[str] = []
        for idx, card in items:
            if card is None:
                chunks.append(f"#{idx}: out of range (results are 1–{self.total}).")
                continue
            chunks.append(_format_card_detail(card, idx))
        return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Navigation footer
# ---------------------------------------------------------------------------


def format_navigation_footer(view: LlmResultSetView, *, shown_end: int) -> str:
    """Menu of follow-up tool calls appended to list-style views.

    Lives here (next to the view it describes) rather than on
    `LlmResultSetView` so the data class doesn't reference tool names.
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
        lines.append(f"{remaining} more match. To explore further:")
        lines.append("  • get_result_page(page=N)         — next page (10 per page)")
    lines.append(
        "  • open_listing(indices=[N])       — open the detail panel + full info"
    )
    lines.append(
        "  • search_apartments(...)          — refine with new filters or query"
    )
    return "\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-listing detail prose — used in the `<user_focus>` block
# ---------------------------------------------------------------------------


def _format_list_section[T](
    items: list[T] | None,
    heading: str,
    render: Callable[[T], str],
) -> list[str]:
    """Heading + bulleted rows for an optional list. Empty list → no output."""
    if not items:
        return []
    return [heading, *(f"  - {render(x)}" for x in items)]


def format_listing_detail_prose(idx: int, detail: ListingDetail) -> str:
    """LLM-facing neighbourhood-context prose for the active listing.

    Embedded in the `<user_focus>` block — the LLM sees the detail
    alongside the "do NOT call open_listing for #N" guidance, so it
    answers follow-ups without redundant tool calls.
    """
    parts: list[str] = [f"--- Listing #{idx} — full detail ---"]

    # Identity / money block
    bits = []
    if detail.title:
        bits.append(detail.title)
    if detail.price_warm_eur:
        bits.append(f"€{detail.price_warm_eur:.0f} warm")
    if detail.rooms:
        bits.append(f"{detail.rooms:g} rooms")
    if detail.area_sqm:
        bits.append(f"{detail.area_sqm:.0f} m²")
    if detail.district:
        bits.append(detail.district)
    if detail.address:
        bits.append(detail.address)
    if bits:
        parts.append(" | ".join(bits))

    # Geo-context
    parts.extend(
        _format_list_section(
            detail.nearest_transit_stops,
            "Nearby transit:",
            lambda s: (
                f"{s.name} — {', '.join(s.lines) if s.lines else '—'} "
                f"({s.distance_m}m"
                + (f", {s.walk_minutes}min walk" if s.walk_minutes else "")
                + ")"
            ),
        )
    )

    if detail.school_catchment is not None:
        sc = detail.school_catchment
        parts.append(
            "Primary school catchment: "
            f"{sc.school_name or sc.catchment_id or 'unknown'}"
        )

    parts.extend(
        _format_list_section(
            detail.nearest_schools,
            "Nearby schools:",
            lambda s: (
                f"{s.name or 'unnamed'} "
                f"({s.school_type or 'unknown type'}) — {s.distance_m}m"
            ),
        )
    )

    parts.extend(
        _format_list_section(
            detail.nearest_parks,
            "Nearby parks:",
            lambda p: f"{p.name or 'unnamed'} — {p.distance_m}m",
        )
    )

    if detail.nearest_playground is not None:
        pg = detail.nearest_playground
        parts.append(f"Nearest playground: {pg.name or 'unnamed'} — {pg.distance_m}m")

    parts.extend(
        _format_list_section(
            detail.nearest_hospitals,
            "Hospitals nearby:",
            lambda h: f"{h.name or 'unnamed'} ({h.tier}) — {h.distance_m}m",
        )
    )

    if detail.nearest_water is not None:
        w = detail.nearest_water
        parts.append(
            f"Nearest water: {w.name or w.water_kind or 'water'} — {w.distance_m}m"
        )

    parts.extend(
        _format_list_section(
            detail.nearest_kitas,
            "Nearby kitas:",
            lambda k: f"{k.name or 'unnamed'} — {k.distance_m}m",
        )
    )

    parts.extend(
        _format_list_section(
            detail.nearest_landmarks,
            "Nearby landmarks:",
            lambda lm: (
                f"{lm.name or 'unnamed'}"
                + (f" ({lm.category})" if lm.category else "")
                + f" — {lm.distance_m}m"
            ),
        )
    )

    # Admin-area context — ring membership + Bezirk/Ortsteil polygon labels.
    area_bits: list[str] = []
    if detail.inside_ring is not None:
        area_bits.append(
            "inside the S-Bahn ring"
            if detail.inside_ring
            else "outside the S-Bahn ring"
        )
    if detail.listing_bezirk:
        area_bits.append(f"Bezirk {detail.listing_bezirk}")
    if detail.listing_ortsteil:
        area_bits.append(f"Ortsteil {detail.listing_ortsteil}")
    if area_bits:
        parts.append("Location: " + ", ".join(area_bits))

    character_bits: list[str] = []
    if detail.noise and detail.noise.label:
        noise_txt = f"noise: {detail.noise.label}"
        if detail.noise.total_lnight is not None:
            noise_txt += f" ({detail.noise.total_lnight:.0f} dB at night)"
        character_bits.append(noise_txt)
    if detail.greenery and detail.greenery.label:
        character_bits.append(f"greenery: {detail.greenery.label}")
    if detail.density and detail.density.label:
        character_bits.append(f"density: {detail.density.label}")
    if character_bits:
        parts.append("Neighbourhood character: " + ", ".join(character_bits))

    if detail.disabled_parking_count > 0:
        parts.append(
            f"Disabled parking nearby: {detail.disabled_parking_count} "
            "spots within 300m"
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Per-turn dynamic system-prompt builder
# ---------------------------------------------------------------------------


def build_dynamic_state_prompt(state: SessionState) -> str:
    """Per-turn state snapshot injected into the agent's system prompt.

    Two XML blocks, both consumed by the LLM:

    - `<current_state>` — the active search: count, sort order, filters in
      effect. Always present (empty form when no search has run yet).
    - `<user_focus>` — present only when the user has expanded a card.
      Contains the listing detail AND the rule "don't reopen this listing
      — its details are already here". State-dependent → context module
      (NOT toolset instructions, which are prompt-cached).

    XML wrappers are deliberate: Claude attends to tagged blocks more
    reliably than to inline prose, and the stable prefix
    (`<current_state>`) plays well with prompt caching since only the
    inner values change turn to turn.
    """
    view = LlmResultSetView(state)
    blocks: list[str] = [_current_state_block(view)]

    focus_idx = _index_for_active(state)
    if focus_idx is not None and state.active_listing_detail is not None:
        focus_body = (
            f"  The user is viewing listing #{focus_idx} in the detail panel.\n"
            f"  Do NOT call open_listing for index {focus_idx} — its full "
            "details are below.\n\n"
            + xml_block(
                "active_listing",
                "  "
                + format_listing_detail_prose(
                    focus_idx, state.active_listing_detail
                ).replace("\n", "\n  "),
            )
        )
        blocks.append(xml_block("user_focus", focus_body))

    return "\n".join(blocks)


def _current_state_block(view: LlmResultSetView) -> str:
    overlays_line = _map_overlays_line(view.state)

    if view.state.search_params is None:
        body = (
            f"  {xml_inline('total', 0)}\n"
            f"  {xml_inline('note', 'No search has run yet in this conversation.')}"
        )
        if overlays_line:
            body += "\n" + overlays_line
        return xml_block("current_state", body)

    filters = view.state.search_params.model_dump(
        exclude_none=True, exclude_defaults=True
    )
    filters.pop("sort_by", None)  # surfaced separately via <order>
    filters_json = json.dumps(filters, default=str, sort_keys=True)

    lines = [
        f"  {xml_inline('total', view.total)}",
        f"  {xml_inline('loaded', len(view.state.preview_cards))}",
        f"  {xml_inline('order', view.order_label())}",
        f"  {xml_inline('filters', filters_json)}",
    ]
    if overlays_line:
        lines.append(overlays_line)
    return xml_block("current_state", "\n".join(lines))


def _map_overlays_line(state: SessionState) -> str:
    """One line listing geometries currently drawn on the map, so the agent
    knows what's visible — and won't redraw something the user dismissed.

    Empty when nothing is drawn. Lists each overlay as `label (kind, origin)`.
    `origin` is `pinned` (stays until hidden/cleared) or `search` (redraws on the
    next search) — so the agent can hide by label and knows a `search` overlay
    needs the filter dropped, not just hidden, to stay gone."""
    if not state.map_overlays:
        return ""
    drawn = ", ".join(
        f"{o.label} ({o.kind}, {o.origin})" for o in state.map_overlays
    )
    return f"  {xml_inline('map_overlays', drawn)}"


def _index_for_active(state: SessionState) -> int | None:
    """Map SessionState.active_id back to a 1-based index in the result set
    (the ordered `result_markers`)."""
    if state.active_id is None:
        return None
    for i, marker in enumerate(state.result_markers, start=1):
        if marker.id == state.active_id:
            return i
    return None


# ---------------------------------------------------------------------------
# Per-card formatters (used by LlmResultSetView)
# ---------------------------------------------------------------------------


def _format_card_prose(apt: ListingCard, idx: int) -> str:
    parts: list[str] = []
    if apt.title:
        parts.append(apt.title)
    if apt.price_warm_eur:
        parts.append(f"€{apt.price_warm_eur:.0f}")
    if apt.rooms:
        parts.append(f"{apt.rooms:g} rooms")
    if apt.area_sqm:
        parts.append(f"{apt.area_sqm:.0f}m²")
    if apt.district:
        parts.append(apt.district)
    if apt.nearest_transit_line and apt.walk_min_to_transit is not None:
        parts.append(f"{apt.nearest_transit_line} {apt.walk_min_to_transit}min")
    if apt.noise_label:
        parts.append(apt.noise_label)
    if apt.inside_ring:
        parts.append("inside ring")
    return f"  {idx}. " + " | ".join(parts)


def _format_card_csv(apt: ListingCard, idx: int) -> str:
    cells = [
        str(idx),
        _csv_escape(apt.title or ""),
        f"{apt.price_warm_eur:.0f}" if apt.price_warm_eur else "",
        f"{apt.rooms:g}" if apt.rooms else "",
        f"{apt.area_sqm:.0f}" if apt.area_sqm else "",
        _csv_escape(apt.district or ""),
    ]
    return ",".join(cells)


def _format_card_detail(apt: ListingCard, idx: int) -> str:
    lines = [f"--- Listing #{idx} ---"]
    for label, value in [
        ("Title", apt.title),
        (
            "Warm rent",
            f"€{apt.price_warm_eur:.0f}/month" if apt.price_warm_eur else None,
        ),
        (
            "Cold rent",
            f"€{apt.price_cold_eur:.0f}/month" if apt.price_cold_eur else None,
        ),
        ("Rooms", f"{apt.rooms:g}" if apt.rooms else None),
        ("Area", f"{apt.area_sqm:.0f} m²" if apt.area_sqm else None),
        ("Floor", apt.floor),
        ("District", apt.district),
        ("Address", apt.address),
        ("Available from", apt.available_from),
        ("Type", apt.listing_type),
        ("URL", apt.source_url),
    ]:
        if value is None or value == "":
            continue
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


def _csv_escape(text: str) -> str:
    if "," in text or '"' in text:
        return '"' + text.replace('"', '""') + '"'
    return text
