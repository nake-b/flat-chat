import { create } from "zustand";

// Tracks whether the conversation's chat history is known yet, so the empty-
// state starter cards (ChatPane) don't FLASH on reload of an existing thread.
//
// On a resumed thread, CopilotKit mounts with `messages: []` and only later
// does `ConversationRecovery` fetch + `setMessages(...)` the transcript over
// HTTP. In that async window "no user message yet" is momentarily true, so the
// starters would render then vanish once history lands.
//
// `historyLoaded` gates that: Bootstrap (`main.tsx`) sets it to `!resumed` the
// moment it resolves the thread (a brand-new thread has nothing to load → show
// immediately; a resumed thread → suppress until hydrated), and
// `ConversationRecovery` flips it `true` once the fetch settles (even on error /
// empty history, so a failed restore can't suppress the starters forever). A
// resumed-but-empty thread correctly re-shows the starters after hydration.
interface RecoveryStore {
  historyLoaded: boolean;
  setHistoryLoaded: (v: boolean) => void;
}

export const useRecovery = create<RecoveryStore>((set) => ({
  historyLoaded: false,
  setHistoryLoaded: (v) => set({ historyLoaded: v }),
}));
