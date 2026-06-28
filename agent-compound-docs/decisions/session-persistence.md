# Session & conversation persistence

Durable, user-scoped conversations in Postgres (`app.*`) with single-conversation
reload recovery. Replaces the process-lifetime `InMemorySessionStore` that lost
everything on restart and made every page reload start a brand-new conversation.

Status: **implemented** (this is the as-built record; supersedes the former
`PLAN-session-persistence.md` + `AUTH-OPTIONS-RESEARCH.md` +
`STORAGE-PATTERNS-RESEARCH.md` scratch docs, now deleted).

## What shipped

- **`app.*` schema** (backend-owned, migration `0001_app_users_sessions`):
  `users`, `conversations`, `messages`, `session_state`.
- **`DbSessionStore`** behind the existing `SessionStore` Protocol (now async).
- **`get_user_id()` seam** — now resolves the authenticated fastapi-users user
  from the session cookie (real password auth; see [`AUTH.md`](../../AUTH.md)).
- **Backend is history-authoritative** — it can run the agent on DB-reconstructed
  history, so a reload preserves agent memory even if the frontend chat is empty.
- **Endpoints**: `GET /api/conversations/{id}/state` (new recovery primitive),
  plus the existing `POST /api/conversations` and `GET /…/messages`, now async,
  persisted, and ownership-checked.
- **Frontend**: conversation id persisted (URL `/c/{id}` + localStorage); on
  reload, map/cards restore via `useCoAgent().setState` and the transcript via
  `useCopilotChatInternal().setMessages`; a "New conversation" button.

## Data model (`app.*`)

- `users(id, created_at, updated_at, email, hashed_password, is_active,
  is_superuser, is_verified)` — the auth columns (migration `0002`) are **NOT NULL**
  for `email`/`hashed_password`: every user is a real account, no dummy/placeholder
  rows. `DbSessionStore.create` no longer fabricates a user — a conversation can
  only reference a user that already exists (created by the seed script). The dev
  user is created by `scripts/seed_users.py` (idempotent operational script), NOT a
  migration — keeps migrations pure-schema. `0002` adds NOT-NULL columns with no
  default, so it runs against an empty `app.users` (fresh/refreshed dev DB).
- `conversations(id pk, user_id fk→app.users CASCADE, title, archived_at, created_at, updated_at)`
  — `id` == AG-UI `thread_id` (client-supplied, no server default). `title` /
  `archived_at` are unused this PR (carried so a future sidebar is additive).
- `messages(id, conversation_id fk CASCADE, seq, kind, content jsonb, created_at)`
  — **row-per-message**, append-only, ordered by `seq`. `content` is the opaque
  `ModelMessagesTypeAdapter` form of one Pydantic-AI `ModelMessage`; `kind` is the
  message-level discriminator (`request`/`response`), NOT a part-level role.
  `UniqueConstraint(conversation_id, seq)`.
- `session_state(conversation_id pk/fk CASCADE, snapshot jsonb, updated_at)` —
  ONE row, overwritten per turn, holding `SessionState.model_dump(mode="json")`.

## Decisions (chosen → rejected, why)

- **Row-per-message JSONB, not a blob-per-conversation.** Mainstream practice
  (Vercel ai-chatbot, etc.); blob-per-conversation is the anti-pattern (opaque to
  SQL, whole-conversation rewrite per turn). We keep `content` opaque per message
  (no `message_parts` normalization) because we reload the list to rehydrate the
  agent and never query inside a part — `message_parts` is a clean additive child
  table later if tool-call analytics are ever needed.
- **Snapshot overwrite, not an append-only state log.** `SessionState` is *derived*
  presentation state (markers/cards/active listing), rebuildable from messages.
  So one overwritten row is right — NOT LangGraph-style append-only checkpoints
  (write amplification for state we can regenerate) and NOT CopilotKit event-replay
  (mid-stream-reconnect optimization, out of scope). Authoritative = `messages`;
  the snapshot is a rebuildable cache, so its shape can change with no data migration.
- **`model_dump(mode="json")` for the snapshot + `dump_python(…, mode="json")` for
  messages** — datetimes/enums become JSON-native so asyncpg's JSONB codec binds
  them; round-trips back through `model_validate` / `validate_python`.
- **Store owns its own DB sessions** (`AsyncSessionLocal`), not the request-scoped
  `get_async_db` — because `save()` runs in `on_complete` at the END of the SSE
  stream, after the request scope is gone. The factory is injectable so tests bind
  it to a savepoint connection.
- **Backend history-authoritative.** `AGUIAdapter.run_stream(message_history=…)`
  prepends supplied history to the envelope messages. We inject the stored history
  **only when the frontend sent ≤1 message** (the reload-fallback case — just the
  new prompt); in normal live turns and the `setMessages`-success path the frontend
  already carries the thread, so we pass nothing (avoids duplication). The ≤1 test
  is robust to tool-message count inflation that would break a length comparison.
- **Linear history, no branching.** A goal-directed search assistant doesn't need
  it; adding a nullable `parent_id` later is additive. `seq` is display-order only.
- **Auth: real password login via fastapi-users** (shipped — see
  [`AUTH.md`](../../AUTH.md)). The single `get_user_id()` dependency is still the
  only identity seam, but it now resolves the authenticated fastapi-users
  `current_active_user` from a signed httpOnly JWT cookie instead of a constant.
  Passwords are Argon2-hashed via `pwdlib`. The planned stage 2 (anonymous cookie)
  was **skipped** — there was no anonymous data to preserve for a fresh,
  reviewer-facing deployment, so we went straight to real accounts. **Logto** is
  kept as the documented future migration (social/OIDC, multi-app); **Authlib**
  (social) is deferred; **Clerk/Firebase** stay disqualified (US data, GDPR).
  The `app.users` PK is unchanged (`gen_random_uuid()` from `0001`), so
  conversations/bookmarks never re-key.
- **Bookmarks: planned, not built.** When added: a per-user join table keyed
  `UNIQUE(user_id, listing_id)`. Decision (overriding the earlier snapshot
  recommendation): **keep a plain `listing_id` reference** (the team's call). Note
  the dangling-reference tradeoff — listings expire/purge, so a bare FK with
  `ON DELETE CASCADE` makes the saved item vanish (the Zillow/Indeed pattern keeps
  it with an "expired" tag via a snapshot). Revisit if that UX matters.
- **Rejected: a stateless `/api/chat` external surface** (from the pre-refactor
  design docs) — conflicts with AG-UI-everywhere and has no client.

## CopilotKit transcript restore — the constraint and the resolution

The plan flagged this as the risky piece. Findings against the installed
**CopilotKit 1.57.3** + our direct-`HttpAgent` (`agents__unsafe_dev_only`) topology:

- The `<CopilotChat>` wrapper is opaque — no `initialMessages` / message props.
- The public `useCopilotChat()` **omits** `setMessages` (it's `Omit<…, "setMessages" | "messages" | …>`).
- `useCopilotChatHeadless_c()` has `setMessages` but its docs gate it behind a
  `publicApiKey` (CopilotKit Cloud).
- **Resolution:** `useCopilotChatInternal()` is **exported and typed**, returns the
  full `setMessages`, and works **without** a `publicApiKey`. So the native
  transcript restore is possible after all — `setMessages` with plain AG-UI
  `{id, role, content}` objects from `GET /…/messages`. The read-only-panel
  fallback the plan budgeted for wasn't needed.
- `useCopilotChatInternal` is an internal-named export — if a CopilotKit upgrade
  drops it, fall back to a read-only history panel above the composer. The backend
  being history-authoritative means the agent keeps context regardless.

## Reload / recovery flow (frontend)

1. On mount, read the conversation id from URL `/c/{id}` or localStorage.
2. If present, **verify it still exists** (`GET /…/state` ≠ 404) before resuming —
   a stale id (DB wiped / different user) falls back to creating a fresh
   conversation, so `/api/agent` never 404s on an unknown thread.
3. `<CopilotKit key={id}>` — changing the key on "New conversation" is a clean
   remount (fresh `useCoAgent` state + empty chat).
4. `ConversationRecovery` (inside CopilotKit, renders nothing) hydrates a resumed
   thread: `setState(GET /state)` for map/cards, `setMessages(GET /messages)` for
   the transcript. Runs once per id (StrictMode-guarded).

## Known limits (accepted for MVP)

- **`get()` runs outside the per-session lock** (`dispatch_agent_request` resolves
  the session before the lock, which is held for the SSE stream's lifetime). Two
  concurrent turns on one thread (double-send, two tabs, a client retry) could race:
  the second turn's `get()` can read the history *before* the first turn persists,
  so the agent answers it on a stale snapshot, and its append then collides at the
  same `seq`. The `(conversation_id, seq)` unique constraint backstops this — the
  losing turn's save aborts with a loud `IntegrityError` (no silent duplication, no
  interleaving; state stays consistent at the prior turn). **Correctness is
  guaranteed by the constraint; the lock only optimizes the happy path.** The proper
  fix (read under the lock) is *not* a quick refactor — it collides with the SSE
  contract: the handler must `get()` *before* streaming starts so an unknown/foreign
  thread returns an HTTP **404 before any bytes flow**. Moving `get()` inside the
  lock pushes resolution into the streaming generator, where a miss can no longer be
  a 404 — it becomes a mid-stream `RUN_ERROR` event. That's a behavioral change to
  the error contract, not a wiring tweak, so it's deliberately deferred.
- **No mid-stream-crash resume.** Persistence is atomic at `on_complete`; a turn
  that dies before it is lost entirely (history + state stay consistent at turn N-1).
- **In-process `asyncio.Lock`** — correct for single-process only. Multi-process
  needs a Postgres advisory lock held for the stream (a deliberate later redesign).
- **Full-history load** per dispatch / `GET /messages` (no cursor pagination yet) —
  fine at MVP conversation sizes. Note that cursor pagination is the *wrong* lever
  for the hot path anyway: the agent turn needs the **entire** history every time
  (that is how the LLM keeps context — you cannot paginate what you must send in
  full), so DB-side paging wouldn't shrink the agent path at all. The real lever for
  long conversations is **LLM-side summarization / truncation** — a Pydantic AI
  history processor that compacts old turns, shrinking *both* what we load and what
  we send to the model. Pagination would only ever help **`GET /messages`** when
  *rendering* a very long transcript in the frontend (hundreds of messages), which
  is a display concern, not a context one. Neither is needed at MVP sizes.

## Tests

`tests/integration/test_session_store.py` (round-trip, append-across-turns,
divergence→rewrite, snapshot round-trip incl. columnar markers, unknown/malformed
id), `test_conversations_api.py` (create, `GET /state` default+persisted, `GET
/messages` projection, foreign-conversation 404), `test_app_schema.py` (autogenerate
drift guard — ORM == migration). Store tests bind the store's `session_factory` to
the test connection via `join_transaction_mode="create_savepoint"` so writes roll
back. Verified end-to-end against the live stack: a real agent turn persists
messages+state, survives a backend restart, and a follow-up with only the new
message keeps full context (history injection).
