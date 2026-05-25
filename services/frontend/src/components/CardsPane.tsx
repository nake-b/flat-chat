import { useMemo } from "react";

import { useUiState } from "../hooks/useUiState";
import { CardDetail } from "./CardDetail";
import { CardStrip } from "./CardStrip";

// Swaps between the horizontal card strip (default) and the in-place detail
// view (when an apartment is active). The pane's container height is
// animated by the parent App layout — see App.tsx for the Option-X resize.
export function CardsPane() {
  const { state } = useUiState();
  const activeId = state?.active_id ?? null;

  const activeApt = useMemo(
    () => (activeId ? state?.results?.find((a) => a.id === activeId) : null) ?? null,
    [activeId, state?.results],
  );

  return (
    <div className="h-full bg-paper">
      {activeApt ? <CardDetail apt={activeApt} /> : <CardStrip />}
    </div>
  );
}
