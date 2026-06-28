import type { ListingCard } from "../state/SessionState";

interface Props {
  card: ListingCard;
  active: boolean;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
}

// One detailed row in the bookmark panel. The panel is now wide (covers the
// chat column), so the row is a small card: large thumbnail + title + district
// + warm price + rooms·area + transit. Clicking anywhere on the row body calls
// onSelect → activate(id), which pans the map (easeTo) and opens the detail
// panel. The remove star (top-right) opens the confirm dialog.
export function BookmarkSidebarItem({
  card,
  active,
  onSelect,
  onRemove,
}: Props) {
  const wrapperClasses = active
    ? "border-red bg-red-tint text-ink"
    : "border-transparent text-ink-soft hover:bg-paper-dim hover:text-ink";

  const priceLabel =
    card.price_warm_eur != null
      ? `€${Math.round(card.price_warm_eur).toLocaleString("de-DE")} warm`
      : "—";
  const title = card.title ?? "Untitled";
  const district = card.listing_bezirk ?? card.district ?? "Berlin";

  // Compact "2 rooms · 64 m²" meta line — only the parts we have.
  const metaParts: string[] = [];
  if (card.rooms != null) {
    metaParts.push(`${card.rooms} ${card.rooms === 1 ? "room" : "rooms"}`);
  }
  if (card.area_sqm != null) {
    metaParts.push(`${Math.round(card.area_sqm)} m²`);
  }
  const transit =
    card.nearest_transit_line != null
      ? card.walk_min_to_transit != null
        ? `${card.nearest_transit_line} · ${card.walk_min_to_transit} min`
        : card.nearest_transit_line
      : null;

  return (
    <div
      className={"group relative border-l-2 transition-colors " + wrapperClasses}
    >
      <button
        type="button"
        onClick={() => onSelect(card.id)}
        aria-current={active ? "page" : undefined}
        className="flex w-full items-start gap-4 px-5 py-3.5 pr-10 text-left"
      >
        {card.image_url ? (
          <img
            src={card.image_url}
            alt=""
            loading="lazy"
            className="h-20 w-28 flex-shrink-0 border border-paper-rule object-cover"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        ) : (
          <div className="h-20 w-28 flex-shrink-0 border border-paper-rule bg-paper-dim" />
        )}
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-ink-ghost">
            {district}
          </div>
          <div
            className={
              "truncate font-sans text-base " +
              (card.title === null ? "italic text-ink-soft" : "")
            }
          >
            {title}
          </div>
          <div className="mt-0.5 font-mono text-[13px] tabular-nums text-ink-soft">
            {priceLabel}
          </div>
          {metaParts.length > 0 ? (
            <div className="mt-0.5 font-mono text-[11px] text-ink-ghost">
              {metaParts.join(" · ")}
            </div>
          ) : null}
          {transit ? (
            <div className="mt-0.5 truncate font-mono text-[11px] text-ink-ghost">
              {transit}
            </div>
          ) : null}
        </div>
      </button>
      <button
        type="button"
        aria-label={`Remove bookmark: ${title}`}
        onClick={(e) => {
          e.stopPropagation();
          onRemove(card.id);
        }}
        className="absolute right-2.5 top-3.5 flex h-7 w-7 items-center justify-center text-amber-400 transition-colors hover:text-red"
      >
        {/* Filled star — every row in this list IS a bookmark, so it's
            pre-filled. Clicking un-bookmarks (and the row vanishes on refetch). */}
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden fill="currentColor">
          <polygon points="12 2 14.9 8.6 22 9.3 16.5 14.1 18.2 21 12 17.3 5.8 21 7.5 14.1 2 9.3 9.1 8.6 12 2" />
        </svg>
      </button>
    </div>
  );
}
