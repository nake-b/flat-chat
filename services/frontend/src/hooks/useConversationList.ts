import { useCallback, useEffect, useRef, useState } from "react";

import {
  listConversations,
  type ConversationSummary,
} from "../api/conversations";
import { useAgentPhase } from "./useAgentPhase";

export type ListStatus = "idle" | "loading" | "ready" | "error";

interface UseConversationList {
  items: ConversationSummary[];
  status: ListStatus;
  refetch: () => void;
  removeOptimistically: (id: string) => void;
}

// Lightweight fetch-and-refresh state for the conversation sidebar.
// Plain useState + useEffect + AbortController — the project doesn't carry a
// query lib (no TanStack Query, no SWR). Mirrors the AbortController pattern in
// CardStrip.tsx so a slow refetch can't overwrite a fresher one.
export function useConversationList(): UseConversationList {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [status, setStatus] = useState<ListStatus>("idle");

  // Tracks the current in-flight request so a later refetch can abort the
  // older one (no race where a stale response clobbers a fresh one).
  const inFlightRef = useRef<AbortController | null>(null);

  const refetch = useCallback(() => {
    inFlightRef.current?.abort();
    const ctrl = new AbortController();
    inFlightRef.current = ctrl;
    // Skip the skeleton flash on subsequent refetches — only the very first
    // fetch transitions through "loading" when items are empty.
    setStatus((prev) => (prev === "ready" ? "ready" : "loading"));
    void (async () => {
      try {
        const rows = await listConversations(ctrl.signal);
        if (ctrl.signal.aborted) return;
        setItems(rows);
        setStatus("ready");
      } catch (err) {
        if (ctrl.signal.aborted) return;
        if ((err as DOMException)?.name === "AbortError") return;
        setStatus("error");
      }
    })();
  }, []);

  useEffect(() => {
    refetch();
    return () => {
      inFlightRef.current?.abort();
    };
  }, [refetch]);

  // Refresh on a turn-completion edge. `useAgentPhase` returns "idle" when no
  // run is active; the non-idle → idle transition is the moment persistence
  // has run (the list may have grown) and the background title task may have
  // landed (titles may have arrived). Cheap to call; AbortController prevents
  // overlap.
  useRefetchListOnTurnEnd(refetch);

  // Drop a row from the local list immediately. Used by the delete flow so
  // the trash click → confirm sequence feels instant; the caller fires the
  // real DELETE and then refetches to reconcile with the server (on failure
  // the refetch snaps the row back).
  const removeOptimistically = useCallback((id: string) => {
    setItems((prev) => prev.filter((row) => row.id !== id));
  }, []);

  return { items, status, refetch, removeOptimistically };
}

function useRefetchListOnTurnEnd(refetch: () => void): void {
  const phase = useAgentPhase();
  const prev = useRef(phase);
  useEffect(() => {
    if (prev.current !== "idle" && phase === "idle") {
      refetch();
    }
    prev.current = phase;
  }, [phase, refetch]);
}
