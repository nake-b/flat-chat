import type { ListingCard } from "../state/SessionState";

interface Props {
  card: ListingCard;
  active: boolean;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
}

// One row in the bookmark sidebar. Same dual-button pattern as
// ConversationSidebarItem: an outer <div> hosts both the row-select button
// and a "remove bookmark" star at the right edge (sibling buttons, no
// nesting; e.stopPropagation on the star to keep its click from bubbling).
//
// Richer than the conversation row: thumbnail + district + title + price.
// Falls back to a paper-dim rectangle when image_url is missing or fails.
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
  const district = card.district ?? "Berlin";

  return (
    <div
      className={
        "group relative border-l-2 transition-colors " + wrapperClasses
      }
    >
      <button
        type="button"
        onClick={() => onSelect(card.id)}
        aria-current={active ? "page" : undefined}
        className="flex w-full items-center gap-3 px-4 py-2.5 pr-10 text-left"
      >
        {card.image_url ? (
          <img
            src={card.image_url}
            alt=""
            loading="lazy"
            className="h-12 w-16 flex-shrink-0 border border-paper-rule object-cover"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        ) : (
          <div className="h-12 w-16 flex-shrink-0 border border-paper-rule bg-paper-dim" />
        )}
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-ink-ghost">
            {district}
          </div>
          <div
            className={
              "truncate font-sans text-sm " +
              (card.title === null ? "italic text-ink-soft" : "")
            }
          >
            {title}
          </div>
          <div className="font-mono text-[11px] tabular-nums text-ink-soft">
            {priceLabel}
          </div>
        </div>
      </button>
      <button
        type="button"
        aria-label={`Remove bookmark: ${title}`}
        onClick={(e) => {
          e.stopPropagation();
          onRemove(card.id);
        }}
        className="absolute right-2 top-1/2 -translate-y-1/2 flex h-7 w-7 items-center justify-center text-amber-400 transition-colors hover:text-red"
      >
        {/* Filled star — every row in this list IS a bookmark, so it's
            pre-filled. Clicking un-bookmarks (and the row vanishes on refetch). */}
        <svg
          viewBox="0 0 24 24"
          width="14"
          height="14"
          aria-hidden
          fill="currentColor"
        >
          <polygon points="12 2 14.9 8.6 22 9.3 16.5 14.1 18.2 21 12 17.3 5.8 21 7.5 14.1 2 9.3 9.1 8.6 12 2" />
        </svg>
      </button>
    </div>
  );
}
