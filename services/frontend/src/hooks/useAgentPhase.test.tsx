import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Controllable inputs, hoisted so the vi.mock factories (which vitest lifts
// above imports) can close over them. Tests mutate these before renderHook.
const ctrl = vi.hoisted(() => ({
  running: false as boolean,
  messages: [] as Array<{ id?: string; role?: string; content?: unknown }>,
}));

// `running` flows through useUiState (useCoAgent); the streaming check reads
// useCopilotChatInternal().messages. Mock the CopilotKit boundary so the phase
// hook is exercised in isolation.
vi.mock("@copilotkit/react-core", () => ({
  useCopilotChatInternal: () => ({ messages: ctrl.messages }),
  useCoAgent: () => ({ running: ctrl.running }),
  useCopilotAction: () => undefined,
}));

vi.mock("./useSessionState", () => ({
  useUiState: () => ({ running: ctrl.running }),
  useSessionState: () => ({ running: ctrl.running }),
}));

// Imported AFTER the mocks. `useActiveToolCount` is the real zustand store the
// phase hook reads — drive it directly to exercise the `tool` branch.
import { useAgentPhase } from "./useAgentPhase";
import { useActiveToolCount } from "./useToolStatus";

beforeEach(() => {
  ctrl.running = false;
  ctrl.messages = [];
  useActiveToolCount.setState({ count: 0 });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useAgentPhase", () => {
  it("is idle when the run is not active", () => {
    ctrl.running = false;
    // Even with a tool counted / an assistant message present, !running wins.
    useActiveToolCount.setState({ count: 1 });
    ctrl.messages = [{ role: "assistant", content: "Hello" }];
    const { result } = renderHook(() => useAgentPhase());
    expect(result.current).toBe("idle");
  });

  it("is tool while a backend tool is executing", () => {
    ctrl.running = true;
    useActiveToolCount.setState({ count: 1 });
    const { result } = renderHook(() => useAgentPhase());
    expect(result.current).toBe("tool");
  });

  // Fix-2 tripwire: asserts the streaming branch against the real CopilotKit
  // message shape {role, content}. If an upgrade reshapes messages, this fails.
  it("is streaming when the latest message is assistant text with content", () => {
    ctrl.running = true;
    ctrl.messages = [
      { role: "user", content: "find me a flat" },
      { role: "assistant", content: "Sure, looking now" },
    ];
    const { result } = renderHook(() => useAgentPhase());
    expect(result.current).toBe("streaming");
  });

  it("is reasoning when running with no tool and no streamed text yet", () => {
    ctrl.running = true;
    ctrl.messages = [];
    const { result } = renderHook(() => useAgentPhase());
    expect(result.current).toBe("reasoning");
  });

  it("is reasoning when the assistant message is still empty", () => {
    ctrl.running = true;
    ctrl.messages = [{ role: "assistant", content: "" }];
    const { result } = renderHook(() => useAgentPhase());
    expect(result.current).toBe("reasoning");
  });

  it("is reasoning when the latest message is from the user", () => {
    ctrl.running = true;
    ctrl.messages = [{ role: "user", content: "hi" }];
    const { result } = renderHook(() => useAgentPhase());
    expect(result.current).toBe("reasoning");
  });
});
