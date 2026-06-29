from datetime import datetime

from pydantic import BaseModel, Field

from flat_chat.listings.context import (
    ListingCard,
    ListingDetail,
    MarkerChannel,
    TravelTimeFilter,
)
from flat_chat.listings.overlays import MapOverlay
from flat_chat.search.schemas import ResultFacets, SearchParams


class ConversationResponse(BaseModel):
    id: str
    created_at: datetime


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime


class ColumnarMarkers(BaseModel):
    """The wire form of `SessionState.result_markers` — four parallel columns.

    `SessionState` keeps markers as a `list[Marker]` in memory but serializes
    them columnar (`{ids, lats, lngs, values}`) via a `@field_serializer` to
    halve the payload at 5k markers. Index `i` across all four columns is one
    marker. `values` is the single active-channel scalar (warm rent by default,
    commute minutes under a travel lens — see `MarkerChannel`). See
    `chat/session_state.py:_serialize_markers`.
    """

    ids: list[str] = Field(default_factory=list)
    lats: list[float] = Field(default_factory=list)
    lngs: list[float] = Field(default_factory=list)
    values: list[float | None] = Field(default_factory=list)


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
    map_overlays: list[MapOverlay] = Field(default_factory=list)
    marker_channel: MarkerChannel = Field(default_factory=MarkerChannel)
    travel_time_filter: TravelTimeFilter | None = None
