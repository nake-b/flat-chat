from datetime import datetime

from pydantic import BaseModel, Field

from flat_chat.listings.context import ListingCard, ListingDetail
from flat_chat.search.schemas import ResultFacets, SearchParams


class ConversationResponse(BaseModel):
    id: str
    created_at: datetime


class ConversationSummary(BaseModel):
    """Sidebar list row — one conversation, lightweight.

    `title` is null briefly between first persistence and background title-gen
    completion; the frontend renders "Untitled" for that window. `updated_at`
    drives both the sidebar's sort order (DESC) and its relative-time label.
    """

    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime


class ColumnarMarkers(BaseModel):
    """The wire form of `SessionState.result_markers` — four parallel columns.

    `SessionState` keeps markers as a `list[Marker]` in memory but serializes
    them columnar (`{ids, lats, lngs, prices}`) via a `@field_serializer` to
    halve the payload at 5k markers. Index `i` across all four columns is one
    marker. See `chat/session_state.py:_serialize_markers`.
    """

    ids: list[str] = Field(default_factory=list)
    lats: list[float] = Field(default_factory=list)
    lngs: list[float] = Field(default_factory=list)
    prices: list[float | None] = Field(default_factory=list)


class SessionStateResponse(BaseModel):
    """OpenAPI-accurate response shape for `GET /api/conversations/{id}/state`.

    Mirrors `SessionState` field-for-field EXCEPT `result_markers`: that field
    serializes columnar (see `ColumnarMarkers`), so typing the endpoint as
    `SessionState` would publish a schema declaring `array<Marker>` while the
    actual bytes on the wire are the columnar object — the schema would lie.
    This model declares the real wire shape so the OpenAPI schema matches what
    the frontend receives.

    Kept in lock-step with `SessionState` by
    `tests/unit/test_session_state_response.py` (a drift guard that fails if a
    field is added to one and not the other).
    """

    search_params: SearchParams | None = None
    total_results: int = 0
    result_markers: ColumnarMarkers = Field(default_factory=ColumnarMarkers)
    preview_cards: list[ListingCard] = Field(default_factory=list)
    facets: ResultFacets | None = None
    active_id: str | None = None
    active_listing_detail: ListingDetail | None = None
