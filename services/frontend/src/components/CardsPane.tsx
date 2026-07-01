import { useMemo } from "react";

import { useUiState } from "../hooks/useUiState";
import { useCardCache } from "../state/cardCache";
import { CardDetail } from "./CardDetail";
import { CardStrip } from "./CardStrip";

// Swaps between the horizontal card strip (default) and the in-place detail
// view (when an apartment is active). The pane's container height is
// animated by the parent App layout — see App.tsx for the Option-X resize.
//
// Gate on `active_id` (not on finding the card in a list): the agent can set
// `active_id` via `open_listing` before any tier-2 card is hydrated, and the
// detail panel must still render from `active_listing_detail` alone. We
// resolve the active tier-2 card from the client card cache (filled by
// CardStrip during scroll-hydration) falling back to `preview_cards`, and
// hand whatever we find to CardDetail — it tolerates `apt` being undefined.
export function CardsPane() {
  const { state } = useUiState();
  const activeId = state?.active_id ?? null;
  const cards = useCardCache((s) => s.cards);

  const activeApt = useMemo(() => {
    if (!activeId) return undefined;
    return (
      cards.get(activeId) ??
      state?.preview_cards?.find((c) => c.id === activeId) ??
      undefined
    );
  }, [activeId, cards, state?.preview_cards]);

  return (
    <div className="h-full bg-paper">
      {activeId ? (
        // `key={activeId}` remounts per listing so the entrance animation
        // (and a fresh scroll-to-top) replays each time a new card is opened.
        <CardDetail key={activeId} apt={activeApt} />
      ) : (
        <CardStrip />
      )}
    </div>
  );
}
