import { useEffect, useRef, useState } from "react";

import { useUiState } from "../hooks/useUiState";
import { useHover } from "../hooks/useHover";
import { EMPTY_UI_STATE, type UiApartment } from "../state/UiState";

// Card sizing — pick the integer N (cards visible at once) whose resulting
// per-card width sits in [MIN_W, MAX_W]. Beyond N, horizontal scroll kicks in.
const MIN_CARD_W = 220;
const MAX_CARD_W = 320;

function pickCardCount(containerWidth: number): number {
  if (containerWidth <= 0) return 2;
  // Largest N such that container/N >= MIN_CARD_W
  const maxByMin = Math.max(1, Math.floor(containerWidth / MIN_CARD_W));
  // Smallest N such that container/N <= MAX_CARD_W (so we don't get a huge
  // single card on narrow viewports — round up).
  const minByMax = Math.max(1, Math.ceil(containerWidth / MAX_CARD_W));
  return Math.max(minByMax, Math.min(maxByMin, 3));
}

// Horizontal scroll row of apartment cards. Civic-brochure styling: tall
// hairline borders, monospace prices anchoring the bottom edge, a red rule
// that slides in from the left on hover. Clicking a card sets active_id,
// which the CardsPane reacts to by swapping in <CardDetail/> and triggers
// the parent layout to grow this pane to 50% (Option X).
export function CardStrip() {
  const { state, setState } = useUiState();
  const setHover = useHover((s) => s.setHover);
  const hoverId = useHover((s) => s.hoverId);
  const apartments = state?.results ?? [];

  // Measure the scroll container and compute the per-card width so that an
  // integer number of cards fit edge-to-edge with no cut-off. The effect must
  // re-run when results arrive — the scroller is conditionally rendered (we
  // show an empty state when there are 0 apartments), so on first mount the
  // scroller element doesn't exist yet and `scrollerRef.current` is null.
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const [cardCount, setCardCount] = useState(2);
  const hasApartments = apartments.length > 0;

  useEffect(() => {
    if (!hasApartments) return;
    const el = scrollerRef.current;
    if (!el) return;
    const update = () => setCardCount(pickCardCount(el.clientWidth));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [hasApartments]);

  if (apartments.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
          No results yet
        </span>
        <p className="max-w-md text-sm leading-relaxed text-ink-soft">
          Describe an apartment in the chat. Results will arrive here.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-baseline justify-between border-b border-paper-rule px-6 py-2.5">
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-soft">
          {apartments.length} {apartments.length === 1 ? "result" : "results"}{" "}
          <span className="text-ink-ghost">· scroll →</span>
        </span>
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
          click to expand
        </span>
      </div>
      <div
        ref={scrollerRef}
        className="flex flex-1 snap-x snap-mandatory gap-0 overflow-x-auto overflow-y-hidden"
        style={{ ["--card-count" as string]: cardCount }}
      >
        {apartments.map((a, idx) => (
          <ApartmentCard
            key={a.id}
            apt={a}
            index={idx + 1}
            hovered={hoverId === a.id}
            onHoverChange={(hover) => setHover(hover ? a.id : null)}
            onClick={() => setState((prev) => ({ ...(prev ?? EMPTY_UI_STATE), active_id: a.id }))}
          />
        ))}
      </div>
    </div>
  );
}

function ApartmentCard({
  apt,
  index,
  hovered,
  onClick,
  onHoverChange,
}: {
  apt: UiApartment;
  index: number;
  hovered: boolean;
  onClick: () => void;
  onHoverChange: (hover: boolean) => void;
}) {
  const isBlank =
    apt.title == null && apt.address == null && apt.price_warm_eur == null;
  return (
    <button
      type="button"
      data-hovered={hovered ? "true" : "false"}
      className="fc-card group h-full shrink-0 snap-start border-r border-paper-rule bg-white px-5 py-4 text-left transition-colors duration-200 ease-snap hover:bg-paper-dim/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-red"
      style={{ flex: "0 0 calc(100% / var(--card-count, 2))" }}
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
      onClick={onClick}
    >
      {isBlank ? (
        <div className="flex h-full flex-col justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] tabular-nums uppercase tracking-widest text-ink-ghost">
              {String(index).padStart(2, "0")}
            </span>
            <span className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
              {apt.district ?? "Berlin"}
            </span>
          </div>
          <div className="flex flex-1 items-center justify-center font-mono text-[11px] uppercase tracking-widest text-ink-ghost">
            (no details)
          </div>
        </div>
      ) : (
        <div className="flex h-full flex-col justify-between gap-2">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] tabular-nums uppercase tracking-widest text-ink-ghost">
                {String(index).padStart(2, "0")}
              </span>
              <span className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
                {apt.district ?? "Berlin"}
              </span>
            </div>
            <div className="line-clamp-2 min-h-[3em] font-sans text-[13px] font-medium leading-snug tracking-tight text-ink">
              {apt.title ?? "(untitled)"}
            </div>
            {apt.address ? (
              <div className="line-clamp-1 font-sans text-[11.5px] leading-snug text-ink-soft">
                {apt.address}
              </div>
            ) : null}
            <CardChips apt={apt} />
          </div>

          <div className="flex items-end justify-between border-t border-paper-rule pt-1.5">
            <div className="flex flex-col">
              <span className="font-mono text-[9px] uppercase tracking-widest text-ink-ghost">
                warm
              </span>
              <span className="font-mono text-lg font-medium tabular-nums tracking-tight text-ink">
                {apt.price_warm_eur != null
                  ? `€${Math.round(apt.price_warm_eur).toLocaleString("de-DE")}`
                  : "€—"}
              </span>
            </div>
            <div className="flex flex-col items-end">
              <span className="font-mono text-[9px] uppercase tracking-widest text-ink-ghost">
                rm · m²
              </span>
              <span className="font-mono text-sm tabular-nums text-ink-soft">
                {apt.rooms != null ? `${apt.rooms.toString().replace(/\.0$/, "")}` : "—"}
                <span className="px-1 text-ink-ghost">·</span>
                {apt.area_sqm != null ? `${Math.round(apt.area_sqm)}` : "—"}
              </span>
            </div>
          </div>
        </div>
      )}
    </button>
  );
}

// At most three chips on a compact card — readability budget is tight at
// ~220px. WBS leads because it's a binary requirement; the next two are
// the highest-signal amenities Berliners ask about first. Render `true`
// only; `false`/`null` stay hidden so we never imply absence of data.
function CardChips({ apt }: { apt: UiApartment }) {
  const chips: { key: string; label: string; wbs?: boolean }[] = [];
  if (apt.wbs_required === true) chips.push({ key: "wbs", label: "WBS", wbs: true });
  if (apt.is_furnished === true) chips.push({ key: "furn", label: "möbl." });
  if (apt.has_balcony === true && chips.length < 3) {
    chips.push({ key: "balc", label: "Balkon" });
  }

  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1">
      {chips.map((c) => (
        <span
          key={c.key}
          className={
            c.wbs
              ? "border border-red bg-red px-1 py-px font-mono text-[9px] uppercase tracking-widest text-white"
              : "border border-ink/20 px-1 py-px font-mono text-[9px] uppercase tracking-widest text-ink-soft"
          }
        >
          {c.label}
        </span>
      ))}
    </div>
  );
}
