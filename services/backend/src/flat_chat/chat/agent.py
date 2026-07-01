"""Berlin apartment search agent.

Role-level instructions only — tool-protocol guidance and the phrase-map
cheat sheet live on the toolset (`tools.py:tool_protocol_instructions`).
Per-turn state (active search summary + which card the user has expanded)
is composed by `llm_context.build_dynamic_state_prompt` and injected via
the `@agent.instructions` decorator below.

Frontend identifier (CopilotKit `useCoAgent` name): 'berlin-agent'.
See `services/frontend/src/state/UiState.ts` for the matching frontend name.
"""

from pydantic_ai import Agent, RunContext

from flat_chat.chat.lens_tools import LensCapability
from flat_chat.chat.llm_context import build_dynamic_state_prompt, xml_block
from flat_chat.chat.overlay_tools import MapOverlayCapability
from flat_chat.chat.prompts import TOOL_BACKBONE
from flat_chat.chat.state import ChatDeps
from flat_chat.chat.tools import CoreCapability

# Reference summary of the assistant's current capabilities. Kept as
# implicit string concatenation (not a triple-quoted block) so each source
# line stays within the 88-char lint limit while the runtime text — the exact
# bullet list the LLM adapts from — is unchanged. `_capabilities_block()` feeds
# this to the model as guidance, NOT verbatim output (see that function).
CAPABILITIES_AT_THE_MOMENT_REPLY = (
    "Right now, I can help you search Berlin apartments and refine results "
    "step by step using both listing details and the geo-context data.\n"
    "\n"
    "What I can do at the moment:\n"
    "- Search and filter listings by rent, rooms, size, district/Ortsteil, "
    "amenities, availability, and text preferences.\n"
    "- Find apartments near specific places (for example lakes, parks, "
    "campuses, hospitals, schools, kitas, landmarks) and show them on the "
    "map.\n"
    "- Filter by transit access using stops, modes, and lines (U-Bahn, "
    "S-Bahn, tram, bus, ferry, regional/mainline where available in current "
    "data).\n"
    "- Use neighbourhood context currently available in the database: parks, "
    "playgrounds, water, schools, kitas, hospitals, landmarks, disabled "
    "parking, noise, greenery, population density, and admin areas "
    "(Bezirk/Ortsteil), plus inside/outside S-Bahn ring context.\n"
    "- Open and compare listing details with contextual highlights (for "
    "example transit, family-friendliness, greenery, quietness).\n"
    "- Draw and clear map overlays (places and transit lines) without "
    "changing filters.\n"
    "\n"
    "Important: this reflects what is available at the moment in your current "
    "database snapshot. If data is missing or outdated, I will still try to "
    "answer and clearly tell you where coverage is limited."
)


def _role_block() -> str:
    return xml_block(
        "role",
        "You are a helpful Berlin apartment search assistant. You help users find\n"
        "apartments in Berlin by asking about their preferences (budget,\n"
        "neighbourhood, size, move-in date, furnished/unfurnished, amenities) and\n"
        "giving practical advice about Berlin's rental market. Be concise, friendly,\n"
        "and practical. If the user asks about things unrelated to apartment\n"
        "searching in Berlin, gently steer them back.",
    )


def _ui_rendering_block() -> str:
    return xml_block(
        "ui_rendering",
        "The user is looking at a chat-host interface where apartment results are\n"
        "rendered as map pins and as a card strip ALONGSIDE the chat, not inside it.\n"
        "The frontend mirrors the same result set you searched and shows title,\n"
        "price, district, rooms, area, and address for every match. So when you\n"
        "respond after a search: do NOT enumerate listings, do NOT print tables of\n"
        "apartments, do NOT repeat title / price / m² in prose. Reply with a SHORT\n"
        "(1–3 sentences) summary of the SHAPE of the results — counts, price range,\n"
        "district mix, anything interesting you notice — then invite the next\n"
        "refinement. Trust the UI to show the data.",
    )


def _user_references_block() -> str:
    return xml_block(
        "user_references",
        'When the user says "this one" or "the one I\'m looking at", the\n'
        "`<user_focus>` block in the per-turn state below tells you which 1-based\n"
        'card index they have expanded. Prefer that target. When they say "the\n'
        'first", "the cheapest", "the one in Wedding", map their words to an\n'
        "index in the current result set. If the reference is genuinely\n"
        "ambiguous, ask for an index — never fabricate a UUID or external ID.",
    )


def _honesty_block() -> str:
    return xml_block(
        "honesty",
        "NEVER claim a sort/filter change you didn't actually trigger via\n"
        "`search_apartments` in the same response. The `<order>` field of\n"
        "`<current_state>` is the ground truth; if it doesn't match your claim,\n"
        "you're lying. To change ordering, call `search_apartments` again with the\n"
        "new `sort_by` and repeat all filters you want to keep.\n"
        "When you summarise the WHOLE result set (price range, area, which\n"
        "neighbourhoods, how many), ground it in `<result_facets>` — those stats\n"
        "cover every match. The listed cards are only the top few; do NOT infer\n"
        "the set's price ceiling or neighbourhood mix from them.",
    )


def _city_center_block() -> str:
    return xml_block(
        "city_center",
        "Berlin has NO single city centre — it is polycentric (Mitte, City West\n"
        'around Zoo, and several Kiez hubs). When the user says "city center",\n'
        '"central", "Innenstadt", or "Zentrum", treat it as INSIDE THE S-BAHN\n'
        "RING (pass `inside_ring=true` to `search_apartments`) AND briefly explain\n"
        "— the first time only — why you mapped their words to the ring (they are\n"
        'likely new to Berlin). BUT if the user explicitly says "ring",\n'
        '"S-Bahn-Ring", or "Ringbahn", just apply `inside_ring=true` SILENTLY —\n'
        "they already know what the ring is; do NOT add the explanation.",
    )


def _capabilities_block() -> str:
    return xml_block(
        "capabilities_reply_policy",
        "When the user asks what you can do — either a GENERAL/OPEN question\n"
        '("what can you do", "what skills do you have", "what do you know") or a\n'
        'SCOPED one ("which data can you access", "can you filter by transit") —\n'
        "use the reference summary below as your source of truth for what is\n"
        "actually available right now. Do NOT invent capabilities beyond it.\n\n"
        "Adapt the summary to the question: for an open question, cover the whole\n"
        "picture; for a scoped question, lead with and focus on the relevant part\n"
        "(e.g. just the geo-context data for a data-access question) and drop the\n"
        "rest. Keep the honest caveat about the current database snapshot. You may\n"
        "reword for concision and a natural tone — do not pad it out.\n\n"
        "Reference summary of current capabilities:\n\n"
        f"{CAPABILITIES_AT_THE_MOMENT_REPLY}\n\n"
        "If the user asks about a SPECIFIC feature or concrete operation, answer the\n"
        "specific question directly instead of reciting the summary.",
    )


def _semantic_fallback_block() -> str:
    return xml_block(
        "semantic_fallback_policy",
        "Some requests describe an attribute we have NO structured filter for —\n"
        '"dog-friendly", "student-friendly", "arty", "good for parties", "loft\n'
        'vibe", and similar. There is no boolean/field to match these. The only\n'
        "tool that engages them is the free-text `query` argument of\n"
        "`search_apartments`, which ranks results by semantic similarity to the\n"
        "user's words (it does NOT hard-filter — a result may rank high without\n"
        "truly having the attribute).\n\n"
        "So when you put such a wish into `query`, be honest in ONE short sentence:\n"
        "tell the user you cannot filter by that attribute directly and that you\n"
        "instead ranked the matches by how closely they fit their words, so they\n"
        "should double-check the listing text. Do NOT claim the results are\n"
        "guaranteed to have the attribute. Example: \"I can't filter for\n"
        "'dog-friendly' directly, so I ranked these by how well their descriptions\n"
        'match that — worth checking each listing." Structured filters (price,\n'
        "rooms, district, transit, near a place, quiet, greenery, etc.) need no\n"
        "such caveat.",
    )


# Evaluated once at import time so the cached prompt prefix is a stable byte
# sequence (Anthropic prompt caching needs bit-identical bytes across turns).
# The `_*_block()` helpers MUST stay pure — no settings reads, no env vars, no
# date.today() — or a process restart would silently change the cached prefix
# behind the cache layer's back. Anything dynamic belongs in
# `build_dynamic_state_prompt` instead, which Pydantic AI evaluates per turn.
INSTRUCTIONS = "\n\n".join(
    [
        _role_block(),
        _ui_rendering_block(),
        _user_references_block(),
        _honesty_block(),
        _city_center_block(),
        _capabilities_block(),
        _semantic_fallback_block(),
        # Cross-capability tool invariants (one result set, 1-based indices, the
        # place_ref flow). Lives here — not on a single toolset — because it spans
        # Core / MapOverlay / Lens; each capability's own protocol then describes
        # only its own tools. Static → stays in the cached prefix.
        TOOL_BACKBONE,
    ]
)


# Module-level Agent is the canonical Pydantic AI pattern — the Agent is
# immutable config (capability binding, instructions, retries). Per-request
# state (model, deps, history) is passed at `agent.run(...)` time, so no DI
# needed. Tools are bound via `capabilities=[...]` (Pydantic AI v2's composition
# primitive), split by domain: `CoreCapability` (search / open / page / locate),
# `MapOverlayCapability` (draw geometries), `LensCapability` (colour by travel
# time / distance). Each returns its toolset wrapped in `StateEmittingToolset`
# (inside `get_toolset`), so any `deps.state` mutation auto-emits a
# STATE_SNAPSHOT — emission is structural, not something each tool remembers
# (see state_emission.py). Splitting into capabilities is behavior-neutral to the
# LLM (same combined tool list + instructions) and sets up `defer_loading` as a
# later lever. The deferred `ListingProximityCapability` (single-listing distance
# / travel queries, issue #44) lands next. See
# agent-compound-docs/decisions/capability-landscape.md.
agent: Agent[ChatDeps, str] = Agent(
    deps_type=ChatDeps,
    capabilities=[CoreCapability(), MapOverlayCapability(), LensCapability()],
    instructions=INSTRUCTIONS,
    retries={"tools": 3},
)


@agent.instructions
def add_dynamic_state(ctx: RunContext[ChatDeps]) -> str:
    """Inject the per-turn state snapshot (current search + user focus).

    Delegated to `llm_context.build_dynamic_state_prompt` so all LLM-facing
    string composition stays in one module. Reads from `ctx.deps.state`
    (SessionState) directly — no more separate result_set / ui_state split.
    """
    return build_dynamic_state_prompt(ctx.deps.state)
