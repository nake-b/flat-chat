import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useActiveIdMirror, useHover } from "./useHover";

// `useActiveIdMirror` is the fix for the activeId-precedence bug: it mirrors the
// authoritative `SessionState.active_id` into the client-local hover store so
// the agent path (open_listing / reload, which arrive as SSE deltas and never
// run through `activate()`) updates the same selection a card click does.

beforeEach(() => {
  useHover.getState().reset();
});

afterEach(() => {
  useHover.getState().reset();
});

describe("useActiveIdMirror", () => {
  it("mirrors the state.active_id into the hover store", () => {
    renderHook(({ id }) => useActiveIdMirror(id), {
      initialProps: { id: "A" as string | null },
    });
    expect(useHover.getState().activeId).toBe("A");
  });

  it("follows the state.active_id when it changes", () => {
    const { rerender } = renderHook(({ id }) => useActiveIdMirror(id), {
      initialProps: { id: "A" as string | null },
    });
    expect(useHover.getState().activeId).toBe("A");

    rerender({ id: "B" });
    expect(useHover.getState().activeId).toBe("B");

    rerender({ id: null });
    expect(useHover.getState().activeId).toBeNull();
  });

  // The exact regression Fix 1 closes: a manual card click sets the client
  // mirror to A, then the agent opens B (an SSE state.active_id delta). Before
  // the fix the stale client A masked B (map stuck on A while the detail panel
  // followed B); the mirror must let B win.
  it("lets a later agent selection override a stale client click", () => {
    // Simulate the click: activate() writes the client mirror synchronously
    // while state.active_id is still "A".
    act(() => {
      useHover.getState().setActive("A");
    });

    const { rerender } = renderHook(({ id }) => useActiveIdMirror(id), {
      initialProps: { id: "A" as string | null },
    });
    expect(useHover.getState().activeId).toBe("A");

    // Agent opens listing B → arrives as a state.active_id delta.
    rerender({ id: "B" });
    expect(useHover.getState().activeId).toBe("B");
  });
});
