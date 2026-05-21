# Agent Framework Selection

Decided 2026-05-10.

## Context

The chat domain needs an agent runtime that can:

- Call multiple custom tools — relational SQL search, pgvector similarity, geo queries (PostGIS), Google Maps API, BVG/VBB transit API, Berlin Open Data.
- Follow procedural workflows (skills). Example: user says "must be on the S1" → agent must (1) call BVG to get S1 station coordinates, (2) call Google Maps for 10-minute walking isochrones, (3) filter listings by geometry.
- Run on free-tier LLMs (Gemini Flash, Groq, Mistral, Cerebras) routed through LiteLLM. No paid Anthropic/OpenAI API access available.
- Stay framework-agnostic at the service layer (constructor takes `db: Session`, runs from FastAPI, scripts, and tests).
- Be small enough to read end-to-end — this is an MSc thesis, not a black box.

The shape of the problem is **one chatbot with many tools**, not a multi-agent negotiation system or a long-horizon planner.

## Frameworks Surveyed

### Pydantic AI
Type-first agent framework from the Pydantic team. Agent is a Python object with typed deps, typed output, registered tools (`@agent.tool`). Internally a state machine via `pydantic-graph`. Provider-agnostic — works with any LiteLLM-compatible endpoint. Has a published pgvector RAG example. Workflows expressed via `instructions=`, dynamic `@agent.system_prompt`, or `pydantic-graph` for explicit multi-node state machines. Lean, inspectable, ~1.0 maturity.

### LangGraph
Graph-based orchestration. Every solution is an explicit directed graph of state-mutating nodes. Killer features: durable execution (resume from crash), checkpointing, LangSmith tracing. Verbose — a hello-world agent is ~50 lines. Best when agents talk to each other or steps must survive restarts. Overkill for a single chatbot.

### LangGraph + Pydantic AI (combined)
The most-cited "production sweet spot" pattern in 2026 writeups. Pydantic AI defines what each agent does (tools, schema, validation); LangGraph defines how agents interact (routing, retries, state). Pydantic models enforced at every cross-agent boundary catch malformed LLM output before it propagates. Worth it only when there are multiple specialist agents.

### Claude Agent SDK
"Give the agent a computer." Built-in Read/Write/Edit/Bash/Glob/Grep/WebSearch/WebFetch tools, hooks, subagents, and first-class **Skills** (SKILL.md + three-stage progressive disclosure). Excellent design but Claude-only — incompatible with the free-tier constraint.

### OpenAI Agents SDK
Lightweight. Handoffs, guardrails, tracing. Hosted tools (web_search, file_search, code_interpreter) run on OpenAI infrastructure. Loses its main advantages off OpenAI; falls back to ordinary tool-calling via LiteLLM.

### Google ADK / Strands Agents (AWS)
ADK pairs naturally with Gemini and has an official Google Maps + BigQuery MCP codelab. Strands is BYO-model in theory but optimal on Bedrock. Both couple to a cloud ecosystem; premature for thesis scope.

### LangChain Deep Agents
Built on LangGraph. Adds planning tool, virtual filesystem, subagents, Claude-Code-style scaffolding. Has SKILL.md support (Mar 2026). The closest non-Anthropic implementation of progressive disclosure. Designed for "Claude Code-shaped" agents that plan and spawn subagents — wrong shape for a single-agent chatbot.

### LlamaIndex
RAG-first. Has Workflows and `FunctionAgent` / `ReActAgent`. Strongest when the agent's job is reasoning over indexed documents. For an agent that calls many APIs and a vector DB, ~80% of LlamaIndex would go unused.

### CrewAI
Roles + Tasks + Crew. Fast prototype (2-4 hours to demo). Production reports are mixed — role abstractions get in the way once workflows get specific. No roles needed here: one agent, many tools.

### AutoGen / Microsoft Agent Framework
Conversation-as-core. Best for negotiating agents (proposer/critic loops). Wrong abstraction for a defined-workflow chatbot.

### Haystack
Pipelines + Agent component. Excellent for evaluated RAG and regulated enterprise. Heavyweight. Pipelines feel academic for a chat loop with tools.

### Smolagents (HuggingFace)
~1k LOC. Agents emit Python code instead of JSON tool calls — fewer steps, higher GAIA scores. Sandboxed via E2B/Docker/Pyodide. Cool but adds sandboxing operations that aren't needed for HTTP API tools.

## Workflow / Skill Mechanisms

| Framework | Workflow / Skill mechanism |
|---|---|
| Claude Agent SDK | Native SKILL.md, three-stage progressive disclosure (frontmatter → body → bundled files). |
| LangChain DeepAgents | SKILL.md support layered on LangGraph + planning + filesystem + subagents. |
| Pydantic AI | No native SKILL.md. Workflows via `instructions=`, dynamic system prompts, or `pydantic-graph` state machines. Skills can be DIY'd in ~50 lines: a `skills/` directory of markdown files, a `list_skills()` tool returning the index for the system prompt, a `load_skill(name)` tool returning the body on demand. |
| LangGraph | Workflows are graph nodes — explicitly model "if user mentions transit → S-Bahn node → Maps node." |
| LlamaIndex Workflows | Event-driven decorator-based steps. Not file-based. |
| CrewAI | Tasks assigned to roles. No skill files. |
| AutoGen / Haystack / Smolagents | Tools + system prompts only. |
| Microsoft Agent Framework | Graph-based Workflows primitive, similar to LangGraph. |

**Caveat for free-tier models:** SKILL.md works *well* with Claude because Claude is trained to scan and invoke skills proactively. For Gemini Flash / Groq Llama / Mistral, instruction-following on "look in skills/ when you see X" is fuzzier. Implement skill loading as an explicit `load_skill(name)` **tool call**, not a prose instruction — small models cooperate with tools more reliably than with header-scanning conventions.

## Tooling for Our Specific APIs

- **Google Maps:** `cablate/mcp-google-map` MCP server exposes `maps_geocode`, `maps_reverse_geocode`, `maps_directions` (driving/walking/bicycling/transit), `maps_distance_matrix`, `maps_search_places`. For a single consumer, wrapping the Google Maps Python client directly as `@agent.tool` functions is simpler than running an MCP server.
- **BVG/VBB transit:** Two routes. (a) `transport.rest` public REST API at `v6.bvg.transport.rest` / `v6.vbb.transport.rest` — no auth, free, clean endpoints (`/locations`, `/stops/:id/departures`, `/journeys`). (b) Official VBB GTFS dump (twice weekly) + HAFAS API with credentials. `transport.rest` is the fastest path for runtime queries; GTFS belongs in ingestion.
- **Berlin Open Data (`daten.berlin.de`):** Mietspiegel, Sozialatlas, etc. — most are GeoJSON/CSV, ingest-side rather than runtime tools.

## Decision

**Pydantic AI as the agent layer. Skills implemented as a `skills/` directory + `load_skill` tool. LangGraph deferred until multi-agent orchestration is actually needed.**

### Rationale

1. **Stack alignment.** Pydantic everywhere already (FastAPI, SQLAlchemy schemas, Pydantic Settings). Tool args validate against existing schemas. Output validation lets the agent return structured `ApartmentSearchResult` objects directly consumable by the frontend.
2. **BYOM is first-class.** Drop in any LiteLLM endpoint. The `flat_chat.llm.gateway` module already targets LiteLLM, and Pydantic AI ships with a `LiteLLMProvider`. Switching from Gemini to Groq is one line.
3. **Inspectable runtime.** The framework is small enough to read end-to-end. Defensible for thesis writeup — every architectural choice can be explained, not handwaved.
4. **Right-sized for the problem.** One chatbot with tools. LangGraph's superpower (durable orchestration of cross-agent state) buys nothing. CrewAI's roles, AutoGen's negotiation, Haystack's pipelines, Smolagents' code-actions — all wrong abstraction for this shape.
5. **Skills are cheap to implement.** A `skills/` directory of markdown files; `list_skills()` returns the index loaded into the system prompt; `load_skill(name)` returns the body on demand. That is progressive disclosure. The S1 example becomes `skills/transit_proximity.md` — when the user mentions a U/S/Tram line, the agent calls `load_skill("transit_proximity")` and follows the steps (BVG → Maps walking isochrone → filter listings).
6. **Cheap escape hatch.** Pydantic AI agents drop into LangGraph nodes with minimal change. If multi-agent orchestration becomes necessary later, the combination is well-trodden. No corner painted.

### Open question for thesis framing

If the thesis itself wants to demonstrate an interesting agent architecture (planner-executor split, evaluator agent that critiques apartment matches), then LangGraph + Pydantic AI from the start gives more academic surface area to write about. If the thesis is about the apartment-search domain (data ingestion, ranking, conversational refinement), Pydantic AI alone is sufficient and ships faster.

## Rejected Alternatives

- **Claude Agent SDK / OpenAI Agents SDK** — model lock-in incompatible with free-tier constraint.
- **Strands / Google ADK** — cloud lock-in, premature for thesis scope.
- **LangGraph alone** — overkill for one chatbot; boilerplate distracts from thesis.
- **DeepAgents** — built for Claude-Code-shaped agents (planner + subagents + filesystem).
- **CrewAI / AutoGen** — wrong abstraction (roles / negotiation).
- **LlamaIndex / Haystack** — RAG-first; search is one tool among many here.
- **Smolagents** — code-action sandboxing not warranted for HTTP API tools.

## Sources

- [Pydantic AI docs](https://ai.pydantic.dev/agent/)
- [Pydantic AI RAG example (pgvector)](https://ai.pydantic.dev/examples/rag/)
- [Pydantic AI tools](https://ai.pydantic.dev/tools/)
- [LangGraph vs Pydantic AI 2026 (ZenML)](https://www.zenml.io/blog/pydantic-ai-vs-langgraph)
- [LangGraph + Pydantic AI combo (Dotzlaw)](https://www.dotzlaw.com/insights/combining-the-power-of-langgraph-with-pydantic-ai-agents/)
- [2026 Agent Framework Decision Guide (DEV)](https://dev.to/linou518/the-2026-ai-agent-framework-decision-guide-langgraph-vs-crewai-vs-pydantic-ai-b2h)
- [Claude Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Claude Skills authoring best practices](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/best-practices)
- [Building Deep Agents + SKILL.md with Langchain](https://abvijaykumar.medium.com/building-deep-agents-skill-md-with-langchain-074176c66dec)
- [LangChain Deep Agents docs](https://docs.langchain.com/oss/python/deepagents/overview)
- [Claude vs OpenAI vs Google ADK 2026 (Composio)](https://composio.dev/content/claude-agents-sdk-vs-openai-agents-sdk-vs-google-adk)
- [Strands Agents (AWS)](https://strandsagents.com/)
- [Haystack docs](https://docs.haystack.deepset.ai/docs/intro)
- [Smolagents (HuggingFace)](https://github.com/huggingface/smolagents)
- [Google Maps MCP server](https://github.com/cablate/mcp-google-map)
- [Location Intelligence ADK + Google Maps codelab](https://codelabs.developers.google.com/adk-mcp-bigquery-maps)
- [transport.rest BVG/VBB API](https://transport.rest/)
- [VBB GTFS via Berlin Open Data](https://daten.berlin.de/datensaetze/vbb-fahrplandaten-via-gtfs)
- [Skills vs Prompts vs Workflows (Confluent)](https://www.confluent.io/compare/prompts-vs-workflows-vs-agents/)
- [Progressive disclosure & MCP (MCPJam)](https://www.mcpjam.com/blog/claude-agent-skills)
