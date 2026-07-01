import { create } from "zustand";

// Holds the message from the most recent AG-UI RUN_ERROR so the chat can show a
// visible, retryable banner instead of a silent frozen "thinking" spinner.
//
// Why this exists: the backend emits a terminal RUN_ERROR when an agent run
// fails mid-stream (e.g. the LLM provider errors after its own retries are
// exhausted — see providers/anthropic.py + chat/service.py). CopilotKit 1.10
// doesn't render RUN_ERROR anywhere, so without this the run just stops and the
// UI looks hung. `main.tsx` subscribes to the HttpAgent's `onRunErrorEvent` and
// sets the message here; it `clear()`s on `onRunStartedEvent` so a retry (or any
// new turn) dismisses the banner automatically.
interface RunErrorStore {
  message: string | null;
  setError: (message: string) => void;
  clear: () => void;
}

export const useRunError = create<RunErrorStore>((set) => ({
  message: null,
  setError: (message) => set({ message }),
  clear: () => set({ message: null }),
}));
