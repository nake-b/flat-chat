"""Drive scripted chat conversations against the local stack.

Used for post-refactor smoke testing — exercises the agent end-to-end via
the real AG-UI HTTP route so traces land in Phoenix the same way they
would in the browser. Logs every assistant turn + key state-snapshot
events to stdout for inspection.

Run from the host:
    docker compose exec backend uv run python /scripts/smoke_conversations.py

(Mount `scripts/` into /scripts in compose, or copy in via docker cp.)
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

import httpx

import os

# Default to nginx on the host (port 80, same path the browser uses).
# Override with SMOKE_BASE=http://localhost:8000 to hit the backend
# container directly, e.g. from inside docker compose exec.
BASE = os.environ.get("SMOKE_BASE", "http://localhost")


def make_user_msg(text: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "role": "user",
        "content": text,
    }


async def create_conversation(client: httpx.AsyncClient) -> str:
    r = await client.post(f"{BASE}/api/conversations")
    r.raise_for_status()
    return r.json()["id"]


async def send_turn(
    client: httpx.AsyncClient,
    thread_id: str,
    messages: list[dict],
    state: dict | None,
) -> tuple[str, dict]:
    """Send a turn, stream SSE, return (assistant_text, final_state)."""
    envelope = {
        "thread_id": thread_id,
        "run_id": str(uuid.uuid4()),
        "state": state or {},
        "messages": messages,
        "tools": [],
        "context": [],
        "forwarded_props": {},
    }

    assistant_text_parts: list[str] = []
    last_state: dict = state or {}
    tool_calls: list[dict] = []

    async with client.stream(
        "POST",
        f"{BASE}/api/agent",
        json=envelope,
        headers={"Accept": "text/event-stream"},
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = event.get("type", "")
            if etype == "TEXT_MESSAGE_CONTENT":
                assistant_text_parts.append(event.get("delta", ""))
            elif etype == "STATE_SNAPSHOT":
                last_state = event.get("snapshot", last_state)
            elif etype == "TOOL_CALL_START":
                tool_calls.append(
                    {
                        "name": event.get("tool_call_name"),
                        "args": "",
                    }
                )
            elif etype == "TOOL_CALL_ARGS":
                if tool_calls:
                    tool_calls[-1]["args"] += event.get("delta", "")

    return "".join(assistant_text_parts), last_state, tool_calls


async def run_conversation(label: str, turns: list[str]) -> None:
    print(f"\n{'=' * 70}\nCONVERSATION: {label}\n{'=' * 70}")
    async with httpx.AsyncClient() as client:
        thread_id = await create_conversation(client)
        print(f"thread_id: {thread_id}")

        messages: list[dict] = []
        # Empty SessionState (tiered result set — see
        # agent-compound-docs/decisions/session-state-design.md). `result_markers`
        # is the columnar wire shape every match ships in; `preview_cards` is the
        # hot top-N; the frontend-owned focus is active_id/active_listing_detail.
        state: dict = {
            "search_params": None,
            "total_results": 0,
            "result_markers": {"ids": [], "lats": [], "lngs": [], "prices": []},
            "preview_cards": [],
            "active_id": None,
            "active_listing_detail": None,
        }

        for i, user_text in enumerate(turns, start=1):
            print(f"\n--- Turn {i} ---")
            print(f"USER: {user_text}")
            messages.append(make_user_msg(user_text))
            text, state, tools = await send_turn(client, thread_id, messages, state)
            messages.append({"id": str(uuid.uuid4()), "role": "assistant", "content": text})
            for tc in tools:
                args_preview = tc["args"][:200] if isinstance(tc["args"], str) else ""
                print(f"  TOOL: {tc['name']}({args_preview}{'…' if len(tc['args']) > 200 else ''})")
            markers = (state.get("result_markers") or {}).get("ids", []) or []
            n_preview = len(state.get("preview_cards", []) or [])
            total = state.get("total_results")
            active = state.get("active_id")
            print(
                f"  STATE: {len(markers)} markers, {n_preview} preview cards, "
                f"total={total}, active_id={active}"
            )
            print(f"ASSISTANT: {text.strip()[:600]}")


CONVERSATIONS = [
    (
        "1 — basic search + price refine",
        [
            "find me apartments in Kreuzberg under €1500",
            "make those smaller, max 50 m²",
        ],
    ),
    (
        "2 — family-friendly persona",
        [
            "I want a quiet family-friendly apartment near a U-Bahn, 2-3 rooms",
            "show me the first one",
        ],
    ),
    (
        "3 — MSS gentrification phrase",
        [
            "find me something in an up-and-coming neighbourhood with good greenery",
            "actually I prefer somewhere already affluent and stable",
        ],
    ),
    (
        "4 — detail drill + follow-up",
        [
            "apartments in Pankow under €1800, at least 60 m²",
            "open #2 and tell me what stands out about transit",
        ],
    ),
    (
        "5 — honesty stress test (sort claim)",
        [
            "any apartments in Neukölln with a balcony, under €1300?",
            "sort them by price from cheapest first",
        ],
    ),
]


async def main() -> None:
    for label, turns in CONVERSATIONS:
        try:
            await run_conversation(label, turns)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED: {label}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
