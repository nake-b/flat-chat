# Related Projects and Frameworks

Researched 2025-05-09. Reference material for architectural decisions.

## Similar Real Estate AI Projects

- [ai-real-estate-assistant](https://github.com/AleksNeStu/ai-real-estate-assistant) — FastAPI + Next.js, ChromaDB, Mapbox, hybrid agent (RAG + tool-calling). Closest architectural match.
- [EstateWise Chapel Hill Chatbot](https://github.com/hoangsonww/EstateWise-Chapel-Hill-Chatbot) — MoE agent routing, hybrid RAG (vector + graph), Leaflet maps, LangGraph + CrewAI. Has `/context-engineering` and `/mcp` directories.
- [Multi-Agentic Real Estate Chatbot](https://github.com/yug-sinha/Multi-Agentic-Real-Estate-Chatbot) — FastAPI + Next.js, Google Gemini, multi-agent for property queries.
- [Zillow Compliant Real Estate Chatbot](https://github.com/zillow/compliant-real-estate-chatbot) — Fine-tuned Llama-3 with Fair Housing Act compliance benchmarks.
- [Bright Data Real Estate AI Agent](https://github.com/brightdata/real-estate-ai-agent) — CrewAI + MCP for structured data extraction.

## Vector + Geo Search

- [pgvector_with_postgis](https://github.com/scitus-ca/pgvector_with_postgis) — Docker image: PostgreSQL 16 + PostGIS 3.5 + pgvector 0.8.0. Shows combined vector similarity + geospatial queries in single SQL.

## Agent Frameworks

- [Pydantic AI](https://github.com/pydantic/pydantic-ai) — Type-safe Python agent framework. Model-agnostic, `@agent.tool`, dependency injection, Pydantic-native. Best fit for FastAPI stack.
- [Mirascope](https://github.com/Mirascope/mirascope) — Minimal decorator-based LLM interface. Lightest-weight option.
- [Parlant](https://github.com/emcie-co/parlant) — Context engineering harness. Dynamic per-turn context assembly, guidelines, journeys. 18k stars.
- [LangGraph](https://github.com/langchain-ai/langgraph) — Graph-based multi-agent orchestration with durable execution.
- [CrewAI](https://github.com/crewAIInc/crewAI) — Role-based multi-agent collaboration. 44k+ stars, native MCP + A2A.
- [Agno](https://github.com/agno-agi/agno) — Lightweight multi-modal agent framework. 22k+ stars.
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) — OpenAI's lightweight multi-agent framework (successor to Swarm).

## Map Integration

- [react-map-gl](https://github.com/visgl/react-map-gl) — React wrapper for Mapbox GL JS / MapLibre GL JS. Standard choice for React maps.
- [Google Maps Platform AI](https://github.com/googlemaps/platform-ai) — MCP server for Google Maps (geocoding, routing, place search).

## Context Engineering References

- [Anthropic: Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [FastAPI + LLM Backend Structure](https://dev.to/aichannode/how-i-structure-a-fastapi-backend-with-llm-features-from-a-real-project-1kb7) — "Treat LLM as its own domain, not just a tool."
- [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering)
