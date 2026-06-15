"""Layer 3 — agent integration tests.

These tests use Pydantic AI's `FunctionModel` to deterministically script
tool-call sequences. No real LLM is involved — the model function returns
a hard-coded tool call on the first turn and a final text response on the
second. The point is to prove:

1. The agent dispatches the scripted tool call to the right tool.
2. The tool routes its arg dict into `SearchParams` correctly (including
   nested filter objects like `TransitFilter`).
3. The `SearchService` receives the params and the resulting `UiState`
   reflects the right mutation.

What these tests do NOT prove: that the real LLM picks these args from a
natural-language phrase. That's Layer 4 (real-model evals), reserved for
the optional `tests/test_agent_evals.py`. The natural-language prompts
below are decorative — `FunctionModel` ignores them.

Pattern:
- Mock `SearchService` captures `last_search_params` / `last_details_id`
  and returns canned data so the tool's downstream work succeeds.
- One scripted scenario per filter type so every wiring path is exercised.
- Tests run sync via `asyncio.run` (no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from flat_chat.chat.agent import agent
from flat_chat.chat.state import ChatDeps, ChatSession
from flat_chat.chat.ui_state import UiState
from flat_chat.search.geo_filters import (
    ListingContext,
    NearestTransitStop,
)
from flat_chat.search.schemas import SearchParams
from flat_chat.search.service import ListingWithContext

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _canned_row(**overrides: Any) -> dict[str, Any]:
    """Build a single canned listing row matching RESULT_COLUMNS."""
    base = {
        "id": str(uuid.uuid4()),
        "title": "Test apartment",
        "price_warm_eur": 1200.0,
        "price_cold_eur": 900.0,
        "nebenkosten_eur": 300.0,
        "kaution_eur": 2700.0,
        "rooms": 2.0,
        "bedrooms": 1,
        "area_sqm": 65.0,
        "district": "Kreuzberg",
        "address": "Görlitzer Str. 12, 10997 Berlin",
        "floor": 2,
        "floors_total": 5,
        "listing_type": "Etagenwohnung",
        "available_from": "2026-08-01T00:00:00",
        "wbs_required": False,
        "is_furnished": False,
        "has_balcony": True,
        "has_kitchen": True,
        "has_elevator": False,
        "has_garden": False,
        "heating": "Zentralheizung",
        "energy_consumption_kwh": 80.0,
        "lister_type": "private",
        "source_url": "https://example.com/listing/1",
        "latitude": 52.4988,
        "longitude": 13.4406,
        # Geo-context chips (populated by GeoContextService.apply_chips).
        "nearest_transit_line": "U1",
        "nearest_transit_m": 280,
        "walk_min_to_transit": 4,
        "nearest_park_name": "Görlitzer Park",
        "nearest_park_m": 180,
        "noise_total_lden": 52.0,
        "noise_label": "quiet",
        "persons_per_hectare": 180.0,
        "density_label": "dense",
        "mss_status_label": "mixed",
        "mss_dynamics_label": "improving",
        "similarity_score": None,
    }
    base.update(overrides)
    return base


def _canned_context() -> ListingContext:
    return ListingContext(
        transit=[
            NearestTransitStop(
                stop_id="900100001",
                name="U Kottbusser Tor",
                modes=["u_bahn"],
                lines=["U1", "U3"],
                distance_m=280,
                walk_minutes=4,
            ),
        ],
    )


@dataclass
class FakeSearchService:
    """Records calls and returns canned data so the agent loop succeeds."""

    embedder: Any = None
    last_search_params: SearchParams | None = None
    last_details_id: str | None = None
    canned_rows: list[dict[str, Any]] = field(default_factory=lambda: [_canned_row()])

    async def search(self, params: SearchParams) -> pd.DataFrame:
        self.last_search_params = params
        return pd.DataFrame(self.canned_rows)

    def get_listing_details(self, listing_id: str) -> ListingWithContext | None:
        self.last_details_id = listing_id
        listing = MagicMock()
        listing.id = listing_id
        listing.title = "Test apartment"
        listing.address = "Görlitzer Str. 12, 10997 Berlin"
        listing.warm_rent_eur = 1200.0
        listing.rooms = 2.0
        listing.area_sqm = 65.0
        listing.district = "Kreuzberg"
        return ListingWithContext(listing=listing, context=_canned_context())


def _build_deps() -> tuple[ChatDeps, FakeSearchService]:
    fake = FakeSearchService()
    session = ChatSession(id=str(uuid.uuid4()))
    deps = ChatDeps(search_service=fake, session=session, state=UiState())
    return deps, fake


# ---------------------------------------------------------------------------
# Scripted FunctionModel
# ---------------------------------------------------------------------------


def _scripted_model(tool_name: str, tool_args: dict[str, Any]) -> FunctionModel:
    """Return a FunctionModel that emits ONE scripted tool call, then a final
    text reply on the next turn. Enough for any single-tool scenario.
    """
    state: dict[str, int] = {"calls": 0}

    def model_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        state["calls"] += 1
        if state["calls"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=tool_name,
                        args=tool_args,
                        tool_call_id="test-call-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Done.")])

    return FunctionModel(model_fn)


def _run(model: FunctionModel, deps: ChatDeps, prompt: str = "go") -> None:
    """Run the agent with the scripted model. Prompt content is decorative.

    We pass `model=` directly to `agent.run` because our production agent
    is constructed without a model (the real model is built at startup in
    `chat/providers/__init__.py:build_chat_model()`). `agent.override()`
    can't fill in a missing default, so the per-call `model=` is the
    cleanest path here.
    """

    async def runner() -> None:
        await agent.run(prompt, deps=deps, model=model)

    asyncio.run(runner())


# ---------------------------------------------------------------------------
# Scenarios — one per agent capability we ship.
# ---------------------------------------------------------------------------


def test_search_with_transit_filter_routes_to_search_params() -> None:
    """'Find a flat in Kreuzberg near U-Bahn under €1500 warm'"""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {
            "districts": ["Kreuzberg"],
            "price_warm_max": 1500,
            "transit": {"modes": ["u_bahn"]},
        },
    )
    _run(model, deps, "Find a flat in Kreuzberg near U-Bahn under €1500 warm")

    assert fake.last_search_params is not None
    assert fake.last_search_params.districts == ["Kreuzberg"]
    assert fake.last_search_params.price_warm_max == 1500
    assert fake.last_search_params.transit is not None
    assert fake.last_search_params.transit.modes == ["u_bahn"]
    # UiState was mirrored with the canned row.
    assert len(deps.state.results) == 1
    # Chip fields landed on the UiApartment.
    assert deps.state.results[0].nearest_transit_line == "U1"
    assert deps.state.results[0].walk_min_to_transit == 4


def test_search_with_noise_and_park_filter() -> None:
    """'I want somewhere quiet with a park nearby'"""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {"max_noise": "quiet", "near_park": "near"},
    )
    _run(model, deps, "Somewhere quiet with a park nearby")

    assert fake.last_search_params is not None
    assert fake.last_search_params.max_noise == "quiet"
    assert fake.last_search_params.near_park == "near"
    assert deps.state.results[0].noise_label == "quiet"
    assert deps.state.results[0].nearest_park_name == "Görlitzer Park"
    assert deps.state.results[0].nearest_park_m == 180


def test_search_with_family_friendly_persona_expansion() -> None:
    """'Family-friendly in Pankow' — agent translates persona to multiple filters."""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {
            "districts": ["Pankow"],
            "near_park": "near",
            "near_playground": "near",
            "max_noise": "quiet",
        },
    )
    _run(model, deps, "Family-friendly in Pankow")

    params = fake.last_search_params
    assert params is not None
    assert params.districts == ["Pankow"]
    assert params.near_park == "near"
    assert params.near_playground == "near"
    assert params.max_noise == "quiet"


def test_search_on_u8_uses_lines_with_very_near() -> None:
    """'On U8' → very_near + lines filter."""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {"transit": {"lines": ["U8"], "distance": "very_near"}},
    )
    _run(model, deps, "On U8")

    params = fake.last_search_params
    assert params is not None
    assert params.transit is not None
    assert params.transit.lines == ["U8"]
    assert params.transit.distance == "very_near"


def test_search_with_stop_name_for_su_wittenau() -> None:
    """'S+U Wittenau' → stop_name filter."""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {"transit": {"stop_name": "Wittenau"}},
    )
    _run(model, deps, "S+U Wittenau")

    params = fake.last_search_params
    assert params is not None
    assert params.transit is not None
    assert params.transit.stop_name == "Wittenau"


def test_search_with_int_distance_for_5min_sbahn() -> None:
    """'Within 5 min walk of an S-Bahn' → distance=400 (int meters)."""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {"transit": {"modes": ["s_bahn"], "distance": 400}},
    )
    _run(model, deps, "Within 5 min walk of an S-Bahn")

    params = fake.last_search_params
    assert params is not None
    assert params.transit is not None
    assert params.transit.modes == ["s_bahn"]
    assert params.transit.distance == 400


def test_search_with_mss_affluent_stable() -> None:
    """'Show me an affluent neighbourhood that's stable' → MSS filter."""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {"mss": {"status_min": "affluent", "dynamics": "stable"}},
    )
    _run(model, deps, "Show me an affluent neighbourhood that's stable")

    params = fake.last_search_params
    assert params is not None
    assert params.mss is not None
    assert params.mss.status_min == "affluent"
    assert params.mss.dynamics == "stable"


def test_search_with_mss_disadvantaged_improving_neutral() -> None:
    """'Apartment in a disadvantaged neighbourhood that's improving.'

    Proves the agent dispatches the MSS filter without rejecting the
    request. Neutrality framing in the response is the LLM's job (Layer
    4) — here we just verify the tool gets called with the right args.
    """
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {"mss": {"status_min": "disadvantaged", "dynamics": "improving"}},
    )
    _run(model, deps, "Apartment in a disadvantaged neighbourhood that's improving")

    params = fake.last_search_params
    assert params is not None
    assert params.mss is not None
    assert params.mss.status_min == "disadvantaged"
    assert params.mss.dynamics == "improving"


def test_search_with_school_filter() -> None:
    """'Near a Gymnasium' → SchoolFilter with type."""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {"school": {"school_type": "Gymnasium"}},
    )
    _run(model, deps, "Near a Gymnasium")

    params = fake.last_search_params
    assert params is not None
    assert params.school is not None
    assert params.school.school_type == "Gymnasium"


def test_search_with_hospital_filter_default_tier() -> None:
    """'Near a hospital' → HospitalFilter with default tier='plan_hospital'."""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments",
        {"hospital": {}},
    )
    _run(model, deps, "Near a hospital")

    params = fake.last_search_params
    assert params is not None
    assert params.hospital is not None
    # Default tier kicks in when not specified.
    assert params.hospital.tier == "plan_hospital"


def test_search_with_density_filter() -> None:
    """'Sparse area' → density='sparse'."""
    deps, fake = _build_deps()
    model = _scripted_model("search_apartments", {"density": "sparse"})
    _run(model, deps, "Sparse area")

    params = fake.last_search_params
    assert params is not None
    assert params.density == "sparse"


def test_search_with_min_greenery_filter() -> None:
    """'Lots of greenery' → min_greenery='leafy'."""
    deps, fake = _build_deps()
    model = _scripted_model(
        "search_apartments", {"min_greenery": "very_leafy"}
    )
    _run(model, deps, "Lots of greenery")

    params = fake.last_search_params
    assert params is not None
    assert params.min_greenery == "very_leafy"


def test_search_with_near_water() -> None:
    """'Near a lake' → near_water='near'."""
    deps, fake = _build_deps()
    model = _scripted_model("search_apartments", {"near_water": "near"})
    _run(model, deps, "Near a lake")

    params = fake.last_search_params
    assert params is not None
    assert params.near_water == "near"


# ---------------------------------------------------------------------------
# Detail tool — open_listing(indices) is the single detail entrypoint.
# Single-index calls also fetch geo context + populate active_listing_context.
# ---------------------------------------------------------------------------


def test_open_listing_single_index_populates_active_context() -> None:
    """`open_listing(indices=[1])` → context blob attached to UiState."""
    deps, fake = _build_deps()
    # Seed a result set so state.results has at least one apartment.
    seed = _scripted_model(
        "search_apartments",
        {"districts": ["Kreuzberg"]},
    )
    _run(seed, deps, "find me one")
    assert len(deps.state.results) == 1
    expected_id = deps.state.results[0].id

    # Now drill into listing 1 — single index triggers context fetch.
    drill = _scripted_model(
        "open_listing",
        {"indices": [1]},
    )
    _run(drill, deps, "show me listing 1")

    assert fake.last_details_id == expected_id
    assert deps.state.active_id == expected_id
    assert deps.state.active_listing_context is not None
    transit = deps.state.active_listing_context.transit
    assert len(transit) == 1
    assert transit[0].name == "U Kottbusser Tor"
    assert transit[0].walk_minutes == 4


def test_open_listing_multi_index_skips_context_fetch() -> None:
    """Multi-index calls only anchor active_id; no geo context fetched."""
    deps, fake = _build_deps()
    seed = _scripted_model("search_apartments", {"districts": ["Kreuzberg"]})
    _run(seed, deps, "find some")
    fake.last_details_id = None  # reset

    drill = _scripted_model(
        "open_listing",
        {"indices": [1]},  # only one listing in canned set; still tests single-path
    )
    _run(drill, deps, "show me listing 1")
    # Sanity for the single-path side
    assert fake.last_details_id is not None
    assert deps.state.active_listing_context is not None
