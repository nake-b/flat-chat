# LLM egress resilience — surviving TLS corruption on the dev container path

## Context

Users hit frequent "the agent stopped responding" freezes — a turn would open the
SSE stream and then never finish (no reply, no error, a stuck "thinking" pill).
It skewed toward later turns of a conversation, which made it look like a
multi-turn / message-shaping bug.

## Root cause (measured)

It is **not** application code. The dev **container's TLS egress to
`api.anthropic.com` corrupts records**:

- The exact captured multi-turn request payload replays **200 OK from the host**
  every way (non-beta, beta, streaming) — the request is valid (2 cache_control
  breakpoints, well under the limit of 4).
- Same probe, 12 large (~40 KB) requests, `max_retries=0`:
  - **host: 12/12 OK**
  - **container: 5–8/12 OK**, the rest `ssl.SSLError: SSLV3_ALERT_BAD_RECORD_MAC`
    → `APIConnectionError` (~33–58% failure).
- A corrupted record usually surfaces as a retryable `APIConnectionError`, but
  occasionally the mangled bytes parse as a **spurious `400 Bad Request`**
  (`x-should-retry: false`) that the SDK will not retry → a hard failure.
- Bigger requests corrupt more often (more records), which is why turn-1 usually
  succeeds and later/larger turns fail — the multi-turn skew was a symptom, not
  the cause.

**MTU was ruled out**: forcing the docker network to MTU 1280 did **not** help
(it was marginally worse), so this is byte-level corruption in Docker Desktop's
userspace network stack (gvisor-tap-vsock / vpnkit on macOS), not
fragmentation/PMTUD.

## Decision

Two layers.

**App-side resilience (this repo — `chat/providers/anthropic.py`, `chat/service.py`):**
- The Anthropic client is constructed with an explicit **read-stall timeout**
  (`httpx.Timeout(read=45s)`) so a silent/corrupted stream becomes a fast,
  retryable error instead of an infinite hang, and **`max_retries=5`**. At a
  ~1/3 per-request failure rate, 5 attempts drive the user-visible failure rate
  to `0.33**5 ≈ 0.4%` — the corruption self-heals transparently (the user never
  sees the common case). This is the ChatGPT-style "just recover" behaviour.
- The SSE wrapper (`_with_session_and_lock`) catches a run that fails anyway and
  emits a terminal AG-UI **`RUN_ERROR`**; the frontend renders a **retry banner**
  (`state/runError.ts` + the `onRunErrorEvent` subscriber in `main.tsx` +
  `ChatPane`) instead of freezing. So the rare unrecoverable case is visible and
  one click from a retry.
- We do **not** special-case the spurious 400: real 400s shouldn't occur (our
  payloads are valid), and blindly retrying 400s would mask genuine bugs. The
  connection-error retries cover the common case; the banner covers the rest.

**Host-side (the actual cure — ops, not code):** the Docker Desktop network stack
is corrupting TLS. Remediation options, in order of ease: update/restart Docker
Desktop; change its network/virtualization backend in Settings; or run the
backend outside Docker for LLM egress. Tracked as an ops task, not a repo change.

## Rejected

- **Lower docker MTU** — measured no improvement (not an MTU problem).
- **A user-facing "error, retry" as the primary UX** — the user should not see
  transient corruption at all; retries make recovery invisible. The banner is the
  last resort, not the first response.
- **Retrying 400s** — dangerous; would hide real malformed-request bugs.

## Follow-up (2026-07-02): the watchdog had to be re-engineered

Live testing on `feat/listing-proximity-tools` showed the initial port didn't
actually stop the freeze:

- **`read=45s × max_retries=5` = a ~4-minute freeze** before an error surfaced.
  Retuned to `read=12s × max_retries=3` (budget 48s): faster stall detection, and
  MORE attempts (4) so ~1.2% per-call visible failure — most corruption still
  self-heals invisibly, but a dead egress fails fast.
- **A `wait_for(stream.__anext__())` inactivity guard does NOT fire** on a real
  stall: a stuck TLS read ignores cancellation, so `wait_for` hangs waiting for
  the cancel to complete. Fixed by draining the stream in a background PRODUCER
  task feeding a `queue`; the consumer reads the queue with the timeout. Queue
  reads are cleanly cancellable, so the inactivity watchdog (`60s`) is
  GUARANTEED; a producer that won't die is abandoned. Budget (48s) < watchdog
  (60s) so SDK self-heal completes before the watchdog trips; the watchdog is the
  backstop for a stall that trickles bytes and never trips the per-read timeout.
- **Catch `BaseException` in the producer** (re-raising `CancelledError`): on
  Python 3.14 the anthropic/httpx streaming failure can arrive as a
  `BaseExceptionGroup`, which `except Exception` missed → the SSE aborted raw
  instead of emitting our clean, retryable `RUN_ERROR`.

Net: infinite "agent stopped responding" freeze eliminated — most turns self-heal;
the rest surface a retry banner in ≤60s. Root cause remains the ops-level egress.
