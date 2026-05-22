from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd
from pydantic_ai.messages import ModelMessage
from sqlalchemy.orm import Session as DbSession

from flat_chat.search.schemas import SearchParams
from flat_chat.search.service import SearchService

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
class ResultSet:
    """Apartments currently under discussion in a session.

    Persists across messages. The user iterates by refining filters, paging
    through results, and requesting details until the set is small enough to
    decide on. Owns every formatting concern shown to the LLM — there is no
    listing formatting outside this class.

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
        lines.append(self._navigation_footer(shown_end=shown))
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
            self._navigation_footer(shown_end=end),
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

    def describe_for_instructions(self) -> str:
        """One-line state snapshot injected into agent instructions every turn."""
        active = self.params.model_dump(exclude_none=True)
        base = (
            f"Active result set: {self.total} listings, {self.order_label()}. "
            f"Params: {active}"
        )
        if self.notes:
            base += " Notes: " + "; ".join(self.notes)
        return base

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

    def _navigation_footer(self, shown_end: int) -> str:
        remaining = self.total - shown_end
        if remaining <= 0 and self.total <= shown_end:
            tail = "All results shown above."
        else:
            tail = f"{remaining} more available."
        return (
            "\n"
            f"{tail} To explore further:\n"
            "  • get_result_page(page=N)         — next page (10 per page)\n"
            "  • get_result_details(indices=[N]) — full info for specific listings\n"
            "  • search_apartments(...)          — refine with new filters or query"
        )


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


@dataclass
class ChatSession:
    """One user conversation thread.

    Owns the message history (Pydantic AI's ModelMessage list) and the
    current ResultSet under discussion. Lives in a SessionStore so the
    storage backend (in-memory now, Postgres later) can swap without
    touching anything else.
    """

    id: str
    message_history: list[ModelMessage] = field(default_factory=list)
    result_set: ResultSet | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ChatDeps:
    """Per-request deps handed to the agent and its tools.

    Bridges request-scoped services (db, search_service) with the
    session-scoped state (the ChatSession instance). Tools mutate
    `session.result_set` so it persists across messages.
    """

    db: DbSession
    search_service: SearchService
    session: ChatSession
