import { useCallback } from "react";

import { useCoAgent } from "@copilotkit/react-core";

import {
  AGENT_NAME,
  EMPTY_SESSION_STATE,
  type ListingDetail,
  type SessionState,
} from "../state/SessionState";
import { useHover } from "./useHover";

// Single seam between CopilotKit's shared-state primitive and the rest of
// the app. Every map / card / chat component reads the agent's authoritative
// SessionState through this hook; write-back (e.g. card-click → setState
// ({active_id})) also goes through here. If we ever swap CopilotKit for
// assistant-ui this is the only file that changes.
//
// Returns the full useCoAgent shape (state, setState, etc.) plus a helper
// that handles "user clicked a card" — sets active_id locally for instant
// card highlight, fires `GET /api/listings/{id}` to fetch tier-3 detail,
// then writes the detail back to state so the backend (agent) has it on
// the next turn.
export function useSessionState() {
  const coAgent = useCoAgent<SessionState>({
    name: AGENT_NAME,
    initialState: EMPTY_SESSION_STATE,
  });

  const { setState } = coAgent;

  // Activate a listing: instant UI focus + HTTP-fetched detail + state
  // write-back so the backend has it on the next turn.
  const activate = useCallback(
    async (id: string | null) => {
      // Mirror the selection into the client-local hover store. The map's
      // ApartmentLayer reads `activeId` from there for pan + highlight because
      // this client-side setState does NOT reliably re-render that consumer.
      useHover.getState().setActive(id);

      if (id === null) {
        setState((prev) => ({
          ...(prev ?? EMPTY_SESSION_STATE),
          active_id: null,
          active_listing_detail: null,
        }));
        return;
      }

      // Set active_id immediately so the card highlights / detail panel
      // opens without waiting for the network round-trip.
      setState((prev) => ({
        ...(prev ?? EMPTY_SESSION_STATE),
        active_id: id,
        active_listing_detail: null,
      }));

      try {
        const response = await fetch(`/api/listings/${encodeURIComponent(id)}`);
        if (!response.ok) {
          // Detail fetch failed — leave active_id set so the card stays
          // selected, but log so we can spot listing-not-found races.
          // The detail panel falls back to whatever tier-2 card is in the
          // client card cache (or preview_cards).
          console.warn("listing detail fetch failed", response.status, id);
          return;
        }
        const detail: ListingDetail = await response.json();
        setState((prev) => {
          // Race-condition guard: only apply if active_id is still this id
          // (user might have clicked another card between request + response).
          if ((prev?.active_id ?? null) !== id) return prev ?? EMPTY_SESSION_STATE;
          return {
            ...(prev ?? EMPTY_SESSION_STATE),
            active_listing_detail: detail,
          };
        });
      } catch (err) {
        console.warn("listing detail fetch errored", err);
      }
    },
    [setState],
  );

  // Dismiss a map overlay: remove it locally for an instant hide, then the
  // reduced set is echoed back to the backend on the next turn, where
  // `merge_incoming_state` treats the removal as authoritative (sticky — the
  // agent sees it gone and won't redraw it). Frontend may only REMOVE overlays.
  const dismissOverlay = useCallback(
    (id: string) => {
      setState((prev) => {
        const s = prev ?? EMPTY_SESSION_STATE;
        return {
          ...s,
          map_overlays: (s.map_overlays ?? []).filter((o) => o.id !== id),
        };
      });
    },
    [setState],
  );

  // Dismiss the active lens: revert to the default `price_warm` lens, drop the
  // active lens, AND remove the lens's own anchor overlay (origin="lens") for an
  // instant recolour + clean map. The cleared state is echoed back where
  // `merge_incoming_state` honours it authoritatively (sticky — the agent sees
  // no lens and won't re-apply, and drops the same overlay). Recolour-only (the
  // current result set is kept, like removing an overlay doesn't change the
  // search). Only `origin==="lens"` overlays go — user pins / search overlays stay.
  const dismissLens = useCallback(() => {
    setState((prev) => {
      const s = prev ?? EMPTY_SESSION_STATE;
      return {
        ...s,
        marker_lens: { key: "price_warm", label: null },
        active_lens: null,
        map_overlays: (s.map_overlays ?? []).filter((o) => o.origin !== "lens"),
      };
    });
  }, [setState]);

  return { ...coAgent, activate, dismissOverlay, dismissLens };
}

// Backwards-compat alias — existing components import `useUiState`.
// Same return value (plus the `activate` helper). Migrate at leisure.
export const useUiState = useSessionState;
