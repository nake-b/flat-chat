import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { useSessionState } from "../hooks/useSessionState";
import { useHover } from "../hooks/useHover";
import { useCardCache } from "../state/cardCache";
import {
  decodeMarkers,
  type ListingCard,
  type MarkerLens,
  type MarkerPoint,
} from "../state/SessionState";
import { lensStyle } from "../state/lensStyles";

// Card sizing — pick the integer N (cards visible at once) whose resulting
// per-card width sits in [MIN_W, MAX_W]. Beyond N, horizontal scroll kicks in.
const MIN_CARD_W = 220;
const MAX_CARD_W = 320;

// Lazy-hydration tuning. The result set can be up to 5000 markers; only the
// top-10 arrive hot as `preview_cards`. The rest are fetched in batches via
// GET /api/listings?ids=…&view=card as they scroll into view.
const HYDRATION_BATCH = 100; // backend caps ids at 100/request (422 over)
const HYDRATION_DEBOUNCE_MS = 150;
const BUFFER_CARDS = 6; // overscan on each side of the visible window

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
//
// Tiered rendering: the ordered list is driven by `result_markers` (EVERY
// match, ≤5000). The top-10 paint instantly from `preview_cards`; the rest
// are hydrated lazily from the card cache as they scroll into view. At up to
// 5000 markers we cannot mount 5000 DOM cards, so we manually window — only
// the visible range (+ buffer) mounts, inside a spacer of the full width.
//
// Windowing approach: MANUAL (scrollLeft-driven), not react-window. See the
// implementation report — react-window v2's API is vertical-List-first and
// its horizontal story is awkward, and its bundled types clash with the
// stale `@types/react-window@1` on npm. A scrollLeft window is ~30 lines,
// has zero new deps, and is trivially type-safe.
export function CardStrip() {
  const { state, activate } = useSessionState();
  const setHover = useHover((s) => s.setHover);
  const hoverId = useHover((s) => s.hoverId);

  const cardCache = useCardCache((s) => s.cards);
  const mergeCards = useCardCache((s) => s.merge);
  const clearCache = useCardCache((s) => s.clear);

  // The ordered result set — one entry per match. Card data is resolved from
  // the cache by id; missing ids render a skeleton and trigger hydration.
  const markers = useMemo(
    () => decodeMarkers(state?.result_markers),
    [state?.result_markers],
  );
  const total = markers.length;
  const headerCount = state?.total_results ?? total;
  const previewCards = state?.preview_cards;

  // A cheap, stable fingerprint of the result set: length + first/last id. A
  // new search (different filters) yields a different list, hence a different
  // signature; the same result set echoed across turns keeps the same one even
  // though `state.result_markers` is a fresh reference each snapshot. Drives
  // the cache-reset effect below without clearing on every turn.
  const markersSig = useMemo(
    () =>
      `${markers.length}:${markers[0]?.id ?? ""}:${markers[markers.length - 1]?.id ?? ""}`,
    [markers],
  );

  // --- Measure scroller → per-card width (integer N fits edge-to-edge) ----
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const [cardCount, setCardCount] = useState(2);
  const [viewport, setViewport] = useState({ scrollLeft: 0, clientWidth: 0 });
  const hasMarkers = total > 0;

  useEffect(() => {
    if (!hasMarkers) return;
    const el = scrollerRef.current;
    if (!el) return;
    const update = () => {
      setCardCount(pickCardCount(el.clientWidth));
      setViewport({ scrollLeft: el.scrollLeft, clientWidth: el.clientWidth });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [hasMarkers]);

  // --- Windowing: derive the visible index range from scrollLeft ----------
  const cardWidth =
    viewport.clientWidth > 0 ? viewport.clientWidth / cardCount : 0;
  const { startIndex, endIndex } = useMemo(() => {
    if (cardWidth <= 0 || total === 0) {
      return { startIndex: 0, endIndex: Math.min(total, 12) };
    }
    const first = Math.floor(viewport.scrollLeft / cardWidth);
    const visible = Math.ceil(viewport.clientWidth / cardWidth);
    const start = Math.max(0, first - BUFFER_CARDS);
    const end = Math.min(total, first + visible + BUFFER_CARDS);
    return { startIndex: start, endIndex: end };
  }, [viewport.scrollLeft, viewport.clientWidth, cardWidth, total]);

  const onScroll = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    setViewport({ scrollLeft: el.scrollLeft, clientWidth: el.clientWidth });
  }, []);

  // --- Lazy hydration: fetch cards for visible ids not yet in the cache ----
  // `inFlight` dedupes concurrent requests; `notFound` is a tombstone set for
  // ids the backend has no listing for (deleted/expired between search and
  // scroll) — without it those ids would re-fetch forever, since they never
  // land in the cache.
  const inFlight = useRef<Set<string>>(new Set());
  const notFound = useRef<Set<string>>(new Set());
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // On a NEW result set (signature change): drop the stale hydrated-card cache
  // and tombstones, reset the scroll window to the top (the new — possibly
  // shorter — list must not inherit a scrollLeft past its end), then seed the
  // cache from preview_cards so the first paint needs no fetch. Guarded by the
  // signature so it does NOT run on every turn that merely echoes the same
  // result set back.
  const prevSig = useRef<string | null>(null);
  useEffect(() => {
    if (prevSig.current === markersSig) return;
    prevSig.current = markersSig;
    clearCache();
    notFound.current.clear();
    inFlight.current.clear();
    const el = scrollerRef.current;
    if (el) el.scrollLeft = 0;
    setViewport((v) => ({ ...v, scrollLeft: 0 }));
    if (previewCards && previewCards.length > 0) mergeCards(previewCards);
  }, [markersSig, previewCards, clearCache, mergeCards]);

  useEffect(() => {
    // Read the live cache (this effect is intentionally NOT subscribed to the
    // cache — re-running it on every merge would let a merge's effect-cleanup
    // cancel a debounced fetch scheduled for a different window).
    const cache = useCardCache.getState().cards;
    // Collect visible-window ids that are neither cached, in flight, nor known-
    // absent.
    const missing: string[] = [];
    for (let i = startIndex; i < endIndex; i++) {
      const id = markers[i]?.id;
      if (!id) continue;
      if (cache.has(id) || inFlight.current.has(id) || notFound.current.has(id))
        continue;
      missing.push(id);
    }
    if (missing.length === 0) return;

    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      // Re-dedupe against the live cache at fire time (it may have filled
      // during the debounce).
      const live = useCardCache.getState().cards;
      const toFetch = missing.filter(
        (id) =>
          !live.has(id) && !inFlight.current.has(id) && !notFound.current.has(id),
      );
      if (toFetch.length === 0) return;
      for (const id of toFetch) inFlight.current.add(id);

      // Chunk to the backend's 100-id cap.
      for (let i = 0; i < toFetch.length; i += HYDRATION_BATCH) {
        const batch = toFetch.slice(i, i + HYDRATION_BATCH);
        void hydrateBatch(batch, mergeCards, inFlight.current, notFound.current);
      }
    }, HYDRATION_DEBOUNCE_MS);

    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, [startIndex, endIndex, markers, mergeCards]);

  // Keep the window honest on first paint once the scroller exists.
  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (el) setViewport({ scrollLeft: el.scrollLeft, clientWidth: el.clientWidth });
  }, [hasMarkers]);

  if (total === 0) {
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

  // Spacer width = full strip; visible cards are absolutely offset within it
  // so the scrollbar reflects all `total` cards while only the window mounts.
  const visibleMarkers = markers.slice(startIndex, endIndex);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-baseline justify-between border-b border-paper-rule px-6 py-2.5">
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-soft">
          {headerCount} {headerCount === 1 ? "result" : "results"}{" "}
          <span className="text-ink-ghost">· scroll →</span>
        </span>
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
          click to expand
        </span>
      </div>
      <div
        ref={scrollerRef}
        onScroll={onScroll}
        className="relative flex-1 overflow-x-auto overflow-y-hidden"
      >
        {/* Spacer sets the true scroll width for all `total` cards. */}
        <div
          className="relative h-full"
          style={{ width: cardWidth > 0 ? `${cardWidth * total}px` : "100%" }}
        >
          {visibleMarkers.map((m, i) => {
            const idx = startIndex + i;
            const card = cardCache.get(m.id);
            const left = cardWidth > 0 ? cardWidth * idx : 0;
            return (
              <div
                key={m.id}
                className="absolute top-0 h-full"
                style={{
                  left: `${left}px`,
                  width: cardWidth > 0 ? `${cardWidth}px` : undefined,
                }}
              >
                {card ? (
                  <ApartmentCard
                    apt={card}
                    index={idx + 1}
                    hovered={hoverId === m.id}
                    lens={state?.marker_lens ?? null}
                    lensValue={m.lens_value}
                    onHoverChange={(hover) => setHover(hover ? m.id : null)}
                    onClick={() => {
                      void activate(m.id);
                    }}
                  />
                ) : (
                  <SkeletonCard
                    marker={m}
                    lens={state?.marker_lens ?? null}
                    index={idx + 1}
                    hovered={hoverId === m.id}
                    onHoverChange={(hover) => setHover(hover ? m.id : null)}
                    onClick={() => {
                      void activate(m.id);
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// Fetch a batch of tier-2 cards and merge them into the cache. Always clears
// the requested ids from the in-flight set on completion (success or error)
// so a transient failure doesn't permanently block re-fetch. Uses a relative
// URL like useSessionState.activate — works via Vite proxy and Nginx alike.
//
// On a SUCCESSFUL response, any requested id the backend didn't return (no
// matching listing — deleted/expired) is tombstoned in `notFound` so it isn't
// re-requested on the next window pass. A network/HTTP error is left out of
// `notFound` — it's transient, so the id stays eligible for retry.
async function hydrateBatch(
  ids: string[],
  merge: (cards: ListingCard[]) => void,
  inFlight: Set<string>,
  notFound: Set<string>,
): Promise<void> {
  try {
    const params = ids
      .map((id) => `ids=${encodeURIComponent(id)}`)
      .join("&");
    const res = await fetch(`/api/listings?${params}&view=card`);
    if (!res.ok) {
      console.warn("card hydration failed", res.status, ids.length);
      return;
    }
    const cards: ListingCard[] = await res.json();
    merge(cards);
    const returned = new Set(cards.map((c) => c.id));
    for (const id of ids) if (!returned.has(id)) notFound.add(id);
  } catch (err) {
    console.warn("card hydration errored", err);
  } finally {
    for (const id of ids) inFlight.delete(id);
  }
}

function ApartmentCard({
  apt,
  index,
  hovered,
  lens,
  lensValue,
  onClick,
  onHoverChange,
}: {
  apt: ListingCard;
  index: number;
  hovered: boolean;
  lens: MarkerLens | null;
  lensValue: number | null;
  onClick: () => void;
  onHoverChange: (hover: boolean) => void;
}) {
  const isBlank =
    apt.title == null && apt.address == null && apt.price_warm_eur == null;
  // Under an active heatmap lens (e.g. commute), surface its value as a vibrant
  // badge — the thing the user is actually evaluating. Default (price) lens has
  // no style → no badge (the warm-rent figure below already carries price).
  const lensStyleSpec = lensStyle(lens);
  const lensBadge =
    lensStyleSpec && lensValue != null ? lensStyleSpec.format(lensValue) : null;
  return (
    <button
      type="button"
      data-hovered={hovered ? "true" : "false"}
      className="fc-card group h-full w-full border-r border-paper-rule bg-white px-5 pt-4 pb-6 text-left transition-colors duration-200 ease-snap hover:bg-paper-dim/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-red"
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
              {lensBadge ? (
                <span className="ml-auto rounded-full bg-red px-1.5 py-0.5 font-mono text-[10px] font-medium tabular-nums text-white">
                  {lensBadge}
                </span>
              ) : null}
            </div>
            <div className="line-clamp-2 font-sans text-[13px] font-medium leading-snug tracking-tight text-ink">
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

// Placeholder shown for a marker whose tier-2 card hasn't hydrated yet.
// Still clickable — clicking activates the listing (the detail panel works
// from tier-3 alone) and the price from the marker gives an instant anchor.
// Carries hover wiring so map ↔ strip highlight still works pre-hydration.
function SkeletonCard({
  marker,
  lens,
  index,
  hovered,
  onClick,
  onHoverChange,
}: {
  marker: MarkerPoint;
  lens: MarkerLens | null;
  index: number;
  hovered: boolean;
  onClick: () => void;
  onHoverChange: (hover: boolean) => void;
}) {
  // Show the active lens's scalar as the instant anchor: warm rent (€) under
  // the default lens, or the lens's own format (e.g. "32 min") under a
  // travel lens. The full card hydrates the rest tier-2.
  const style = lensStyle(lens);
  const value = marker.lens_value;
  const valueLabel = style ? style.legendTitle : "warm";
  const valueText =
    value != null
      ? style
        ? style.format(value)
        : `€${Math.round(value).toLocaleString("de-DE")}`
      : style
        ? "—"
        : "€—";
  return (
    <button
      type="button"
      data-hovered={hovered ? "true" : "false"}
      className="fc-card group h-full w-full border-r border-paper-rule bg-white px-5 pt-4 pb-6 text-left transition-colors duration-200 ease-snap hover:bg-paper-dim/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-red"
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
      onClick={onClick}
    >
      <div className="flex h-full flex-col justify-between gap-2">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] tabular-nums uppercase tracking-widest text-ink-ghost">
              {String(index).padStart(2, "0")}
            </span>
            <span className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
              Berlin
            </span>
          </div>
          <div className="h-3.5 w-3/4 animate-pulse bg-paper-rule" />
          <div className="h-3 w-1/2 animate-pulse bg-paper-rule" />
        </div>
        <div className="flex items-end justify-between border-t border-paper-rule pt-1.5">
          <div className="flex flex-col">
            <span className="font-mono text-[9px] uppercase tracking-widest text-ink-ghost">
              {valueLabel}
            </span>
            <span className="font-mono text-lg font-medium tabular-nums tracking-tight text-ink">
              {valueText}
            </span>
          </div>
          <div className="h-3 w-10 animate-pulse bg-paper-rule" />
        </div>
      </div>
    </button>
  );
}

// At most four chips on a compact card — readability budget is tight at
// ~220px. Priority order: WBS (binary requirement, red badge), then
// geo-context chips (transit / park / noise), then high-signal amenities.
// Render `true` only; `false`/`null` stay hidden so we never imply absence
// of data. Geo chips use emoji prefixes to set them visually apart from
// uppercase mono amenity chips.
function CardChips({ apt }: { apt: ListingCard }) {
  const chips: { key: string; label: string; wbs?: boolean }[] = [];

  // WBS — binary requirement, always shown when present.
  if (apt.wbs_required === true) {
    chips.push({ key: "wbs", label: "WBS", wbs: true });
  }

  // Geo-context chips — highest signal for apartment hunters.
  if (apt.nearest_transit_line && apt.walk_min_to_transit != null) {
    chips.push({
      key: "transit",
      label: `🚇 ${apt.nearest_transit_line} · ${apt.walk_min_to_transit}min`,
    });
  }
  if (apt.nearest_park_m != null) {
    chips.push({ key: "park", label: `🌳 ${apt.nearest_park_m}m` });
  }
  // Noise chip: only "quiet" — "lively" is the urban norm and "noisy" is
  // signal-negative, neither warrants a card chip. Detail panel surfaces all 3.
  if (apt.noise_label === "quiet") {
    chips.push({ key: "noise", label: "🔇 quiet" });
  }
  // Inside the S-Bahn ring — central-Berlin signal. Only shown when true.
  if (apt.inside_ring === true) {
    chips.push({ key: "ring", label: "⭕ inside ring" });
  }

  // Amenities — fill remaining budget. Cap total chips at 4 for a 220px card.
  const MAX_CHIPS = 4;
  if (apt.is_furnished === true && chips.length < MAX_CHIPS) {
    chips.push({ key: "furn", label: "Furnished" });
  }
  if (apt.has_balcony === true && chips.length < MAX_CHIPS) {
    chips.push({ key: "balc", label: "Balkon" });
  }

  if (chips.length === 0) return null;

  // Hard budget — priority order is preserved by insertion order above
  // (WBS → transit → park → noise → ring → amenities), so a simple slice
  // keeps the highest-signal chips on a tight 220px card.
  const shown = chips.slice(0, MAX_CHIPS);

  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((c) => (
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
