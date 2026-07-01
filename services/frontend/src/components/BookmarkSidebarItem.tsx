import type { ListingCard } from "../state/SessionState";
import { formatTransitDetailed } from "../lib/transit";
import { BookmarkHeart } from "./BookmarkHeart";

interface Props {
  card: ListingCard;
  active: boolean;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
}

const nf = new Intl.NumberFormat("de-DE");

// One row in the bookmark panel. Intentionally RICHER than the compact result
// card (CardStrip): the panel is wide (covers the chat column), so this is a
// landscape detail row — big thumbnail + title + address + a full price block +
// a rooms/size/floor meta line + a transit line + a generous chip row. It
// borrows the result card's chip vocabulary and typography tokens for a
// consistent feel, but the horizontal, detail-heavy layout keeps it visually
// distinct from the narrow vertical result cards.
//
// Clicking the row body → onSelect(id) (pans the map + opens the detail panel).
// The remove heart (top-right) → onRemove(id), which opens the confirm dialog.
export function BookmarkSidebarItem({
  card,
  active,
  onSelect,
  onRemove,
}: Props) {
  const wrapperClasses = active
    ? "border-red bg-red-tint text-ink"
    : "border-transparent text-ink-soft hover:bg-paper-dim hover:text-ink";

  const title = card.title ?? "Untitled";
  const district =
    card.listing_ortsteil ?? card.listing_bezirk ?? card.district ?? "Berlin";

  // Price: warm prominent, cold + Nebenkosten as a secondary line.
  const warm =
    card.price_warm_eur != null ? `€${nf.format(Math.round(card.price_warm_eur))}` : null;
  const priceSub: string[] = [];
  if (card.price_cold_eur != null) {
    priceSub.push(`€${nf.format(Math.round(card.price_cold_eur))} kalt`);
  }
  if (card.nebenkosten_eur != null) {
    priceSub.push(`€${nf.format(Math.round(card.nebenkosten_eur))} NK`);
  }

  // rooms · bedrooms · m²
  const meta: string[] = [];
  if (card.rooms != null) {
    meta.push(`${card.rooms} ${card.rooms === 1 ? "room" : "rooms"}`);
  }
  if (card.bedrooms != null) meta.push(`${card.bedrooms} bd`);
  if (card.area_sqm != null) meta.push(`${Math.round(card.area_sqm)} m²`);

  const transit =
    card.nearest_transit_line != null && card.walk_min_to_transit != null
      ? formatTransitDetailed(card.nearest_transit_line, card.walk_min_to_transit)
      : card.nearest_transit_line;

  // Chip row — richer than the result card (this is the detail view). Cap at 8
  // so a heavily-tagged listing stays scannable.
  const chips: { key: string; label: string; wbs?: boolean }[] = [];
  if (card.wbs_required === true) chips.push({ key: "wbs", label: "WBS", wbs: true });
  if (card.inside_ring === true) chips.push({ key: "ring", label: "⭕ inside ring" });
  if (card.nearest_park_m != null) {
    chips.push({ key: "park", label: `🌳 ${card.nearest_park_m}m` });
  }
  if (card.noise_label != null) {
    chips.push({ key: "noise", label: `🔉 ${card.noise_label}` });
  }
  if (card.density_label != null) {
    chips.push({ key: "density", label: `🏙 ${card.density_label}` });
  }
  if (card.floor != null) {
    chips.push({
      key: "floor",
      label:
        card.floors_total != null
          ? `Floor ${card.floor}/${card.floors_total}`
          : `Floor ${card.floor}`,
    });
  }
  if (card.is_furnished === true) chips.push({ key: "furn", label: "Furnished" });
  if (card.has_balcony === true) chips.push({ key: "balc", label: "Balkon" });
  if (card.has_kitchen === true) chips.push({ key: "kitchen", label: "Küche" });
  if (card.has_elevator === true) chips.push({ key: "elev", label: "Aufzug" });
  if (card.has_garden === true) chips.push({ key: "garden", label: "Garten" });
  if (card.available_from != null) {
    // available_from can arrive as a full ISO timestamp; show just the
    // YYYY-MM-DD date portion.
    chips.push({ key: "avail", label: `ab ${card.available_from.slice(0, 10)}` });
  }
  const shownChips = chips.slice(0, 8);

  return (
    <div className={"group relative border-l-2 transition-colors " + wrapperClasses}>
      <button
        type="button"
        onClick={() => onSelect(card.id)}
        aria-current={active ? "page" : undefined}
        className="flex w-full items-start gap-4 px-5 py-4 pr-10 text-left"
      >
        {card.image_url ? (
          <img
            src={card.image_url}
            alt=""
            loading="lazy"
            className="h-24 w-32 flex-shrink-0 border border-paper-rule object-cover"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        ) : (
          <div className="h-24 w-32 flex-shrink-0 border border-paper-rule bg-paper-dim" />
        )}

        <div className="min-w-0 flex-1">
          <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-ink-ghost">
            {district}
          </div>
          <div
            className={
              "truncate font-sans text-base font-medium leading-snug " +
              (card.title === null ? "italic text-ink-soft" : "")
            }
          >
            {title}
          </div>
          {card.address ? (
            <div className="mt-0.5 line-clamp-1 font-sans text-[11.5px] text-ink-soft">
              {card.address}
            </div>
          ) : null}

          {/* Price block */}
          <div className="mt-1.5 flex items-baseline gap-2">
            <span className="font-mono text-[15px] font-medium tabular-nums text-ink">
              {warm ?? "—"}
            </span>
            {warm ? (
              <span className="font-mono text-[9px] uppercase tracking-widest text-ink-ghost">
                warm
              </span>
            ) : null}
          </div>
          {priceSub.length > 0 ? (
            <div className="font-mono text-[10px] tabular-nums text-ink-ghost">
              {priceSub.join(" · ")}
            </div>
          ) : null}

          {meta.length > 0 ? (
            <div className="mt-0.5 font-mono text-[11px] text-ink-ghost">
              {meta.join(" · ")}
            </div>
          ) : null}
          {transit ? (
            <div className="mt-0.5 truncate font-mono text-[11px] text-ink-ghost">
              {transit}
            </div>
          ) : null}

          {shownChips.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {shownChips.map((c) => (
                <span
                  key={c.key}
                  className={
                    "px-1 py-px font-mono text-[9px] uppercase tracking-widest " +
                    (c.wbs
                      ? "border border-red bg-red text-white"
                      : "border border-ink/20 text-ink-soft")
                  }
                >
                  {c.label}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </button>

      <div className="absolute right-2.5 top-3.5">
        <BookmarkHeart
          filled
          onToggle={() => onRemove(card.id)}
          label={title}
        />
      </div>
    </div>
  );
}
