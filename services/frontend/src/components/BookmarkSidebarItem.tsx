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
    priceSub.push(`€${nf.format(Math.round(card.nebenkosten_eur))} Nebenkosten`);
  }

  // Full-word facts line — uses the panel's horizontal width instead of a wall
  // of abbreviated chips. rooms · bedrooms · size · floor · availability.
  const facts: string[] = [];
  if (card.rooms != null) {
    facts.push(`${card.rooms} ${card.rooms === 1 ? "room" : "rooms"}`);
  }
  if (card.bedrooms != null) {
    facts.push(`${card.bedrooms} ${card.bedrooms === 1 ? "bedroom" : "bedrooms"}`);
  }
  if (card.area_sqm != null) facts.push(`${Math.round(card.area_sqm)} m²`);
  if (card.floor != null) {
    facts.push(
      card.floors_total != null
        ? `floor ${card.floor} of ${card.floors_total}`
        : `floor ${card.floor}`,
    );
  }
  if (card.available_from != null) {
    // available_from can arrive as a full ISO timestamp; show just the date.
    facts.push(`available ${card.available_from.slice(0, 10)}`);
  }

  const transit =
    card.nearest_transit_line != null && card.walk_min_to_transit != null
      ? formatTransitDetailed(card.nearest_transit_line, card.walk_min_to_transit)
      : card.nearest_transit_line;

  // Chips are now only for the qualitative/boolean features (facts moved to the
  // text line above), so they stay on one or two rows rather than a wall.
  const chips: { key: string; label: string; wbs?: boolean }[] = [];
  if (card.wbs_required === true) {
    chips.push({ key: "wbs", label: "WBS required", wbs: true });
  }
  if (card.inside_ring === true) chips.push({ key: "ring", label: "⭕ inside the ring" });
  if (card.nearest_park_m != null) {
    chips.push({ key: "park", label: `🌳 park ${card.nearest_park_m} m` });
  }
  if (card.noise_label != null) {
    chips.push({ key: "noise", label: `🔉 ${card.noise_label}` });
  }
  if (card.density_label != null) {
    chips.push({ key: "density", label: `🏙 ${card.density_label}` });
  }
  if (card.is_furnished === true) chips.push({ key: "furn", label: "furnished" });
  if (card.has_balcony === true) chips.push({ key: "balc", label: "balcony" });
  if (card.has_kitchen === true) chips.push({ key: "kitchen", label: "fitted kitchen" });
  if (card.has_elevator === true) chips.push({ key: "elev", label: "elevator" });
  if (card.has_garden === true) chips.push({ key: "garden", label: "garden" });
  const shownChips = chips;

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

          {facts.length > 0 ? (
            <div className="mt-1 font-mono text-[11px] text-ink-ghost">
              {facts.join(" · ")}
            </div>
          ) : null}
          {transit ? (
            <div className="mt-0.5 truncate font-mono text-[11px] text-ink-soft">
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
