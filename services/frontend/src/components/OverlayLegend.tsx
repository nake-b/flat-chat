// Small legend of the geometries currently drawn on the map, with a dismiss
// (×) per overlay. Lives as a DOM layer over the map (not a MapLibre layer),
// so it's a sibling of <MapLibreMap> inside MapPane's relative wrapper.
//
// Dismissing routes through `useSessionState().dismissOverlay(id)` — an instant
// local hide that the backend honours as authoritative on the next turn
// (sticky; the agent sees it gone). Renders nothing when no overlays are drawn.

import { useSessionState } from "../hooks/useSessionState";
import { overlayColor, overlayShape } from "../state/overlayStyles";

export function OverlayLegend() {
  const { state, dismissOverlay } = useSessionState();
  const overlays = state?.map_overlays ?? [];
  if (overlays.length === 0) return null;

  return (
    <div className="absolute left-3 top-3 z-10 flex max-w-[60%] flex-wrap gap-1.5">
      {overlays.map((o) => {
        const color = overlayColor(o, overlayShape(o.geojson));
        return (
          <span
            key={o.id}
            className="flex items-center gap-1.5 rounded-full bg-white/95 py-1 pl-2 pr-1 text-xs font-medium text-neutral-800 shadow-sm ring-1 ring-black/10"
          >
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: color }}
              aria-hidden
            />
            <span className="max-w-[12rem] truncate">{o.label}</span>
            <button
              type="button"
              onClick={() => dismissOverlay(o.id)}
              aria-label={`Hide ${o.label}`}
              title={`Hide ${o.label}`}
              className="flex h-4 w-4 items-center justify-center rounded-full text-neutral-400 transition-colors hover:bg-neutral-200 hover:text-neutral-700"
            >
              ×
            </button>
          </span>
        );
      })}
    </div>
  );
}
