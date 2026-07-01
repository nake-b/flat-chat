import { useEffect, useMemo, useRef } from "react";

import { useSessionState } from "../hooks/useSessionState";
import {
  decodeMarkers,
  type ListingDetail,
  type ListingCard,
} from "../state/SessionState";
import { lensColorForValue, lensDomain, lensStyle } from "../state/lensStyles";
import { useBookmarks } from "../state/useBookmarks";
import { BookmarkHeart } from "./BookmarkHeart";

// The subset of fields the detail body reads — present on BOTH ListingCard
// and ListingDetail. We render from `active_listing_detail ?? apt` so the
// panel works even when no tier-2 card is cached (agent opened it before
// hydration). `image_url` is ListingCard-only (ListingDetail carries the
// full `images[]` array instead), so it's handled separately by ImageGallery.
interface DetailView {
  id: string;
  title: string | null;
  district: string | null;
  address: string | null;
  price_warm_eur: number | null;
  price_cold_eur: number | null;
  nebenkosten_eur: number | null;
  kaution_eur: number | null;
  rooms: number | null;
  bedrooms: number | null;
  area_sqm: number | null;
  floor: number | null;
  listing_type: string | null;
  available_from: string | null;
  source_url: string | null;
  wbs_required: boolean | null;
  is_furnished: boolean | null;
  has_balcony: boolean | null;
  has_kitchen: boolean | null;
  has_elevator: boolean | null;
  has_garden: boolean | null;
  heating: string | null;
  lister_type: string | null;
}

// Project a tier-3 ListingDetail OR tier-2 ListingCard down to the shared
// DetailView the body renders. Both carry every field by the same name, so
// this is a structural narrowing — TypeScript verifies the overlap.
function toDetailView(src: ListingDetail | ListingCard): DetailView {
  return {
    id: src.id,
    title: src.title,
    district: src.district,
    address: src.address,
    price_warm_eur: src.price_warm_eur,
    price_cold_eur: src.price_cold_eur,
    nebenkosten_eur: src.nebenkosten_eur,
    kaution_eur: src.kaution_eur,
    rooms: src.rooms,
    bedrooms: src.bedrooms,
    area_sqm: src.area_sqm,
    floor: src.floor,
    listing_type: src.listing_type,
    available_from: src.available_from,
    source_url: src.source_url,
    wbs_required: src.wbs_required,
    is_furnished: src.is_furnished,
    has_balcony: src.has_balcony,
    has_kitchen: src.has_kitchen,
    has_elevator: src.has_elevator,
    has_garden: src.has_garden,
    heating: src.heating,
    lister_type: src.lister_type,
  };
}

// Detail panel: a card-sized white pane sitting at the top of the cards
// slot, with the paper background of `CardsPane` showing beneath. The
// pane sizes to its content — no stretching, no Option-X expansion, no
// filler band. Matches the strip's "card-on-paper" visual density.
//
// `apt` (tier-2 card) is OPTIONAL: the agent's `open_listing` tool can set
// `active_id` before any card is hydrated. In that case we fall back to the
// tier-3 `active_listing_detail` (a superset of the card fields) for the
// header and stat body. Gating reads on `state.active_id` keeps stale detail
// from leaking across selections.
export function CardDetail({ apt }: { apt?: ListingCard }) {
  const { state, activate } = useSessionState();
  const backButtonRef = useRef<HTMLButtonElement | null>(null);

  const activeId = state?.active_id ?? null;
  // Star binds to the active listing id — when active_id changes the star
  // flips with it. Same store the cards subscribe to, so a toggle here
  // updates every visible star with the same id.
  const isBookmarked = useBookmarks((s) =>
    activeId != null ? s.ids.has(activeId) : false,
  );
  const toggleBookmark = useBookmarks((s) => s.toggle);

  // Tier-3 detail blob — fetched by `activate(id)` via GET /api/listings/{id}
  // when the user clicks a card (the primary path) OR pushed by the agent's
  // `open_listing` tool via state delta. Either way it lives in SessionState
  // as `active_listing_detail`. We only render it for the *currently active*
  // listing so stale data can't leak across selections.
  // Gate on the detail blob's OWN id matching active_id (not just `apt`): when
  // a new active_id arrives before its detail delta is applied, the previous
  // listing's `active_listing_detail` is still in state — keying on `apt`
  // alone would render that stale tier-3 data for a frame. `detail.id ===
  // activeId` is the only safe condition for both the card-click and the
  // agent (`apt == null`) paths.
  const candidateDetail = state?.active_listing_detail ?? null;
  const detail: ListingDetail | null =
    activeId != null && candidateDetail?.id === activeId ? candidateDetail : null;
  // Back-compat alias for the existing rendering code below — `ctx` was the
  // old name; ListingDetail is a superset of what ListingContext exposed.
  const ctx = detail;

  // Unified view for the header + stat body: prefer tier-3 detail (richer,
  // authoritative) and fall back to the tier-2 card. At least one is present
  // whenever active_id is set, but guard defensively just in case.
  const source: ListingDetail | ListingCard | null = detail ?? apt ?? null;
  const view: DetailView | null = source ? toDetailView(source) : null;

  // Active lens value for THIS listing (e.g. commute minutes / distance),
  // looked up by id from the markers — the only tier carrying `lens_value`.
  // Shown as a stat only under a heatmap lens; the stat label comes from the
  // active lens kind (Drive/Transit for travel time, Distance for distance).
  const lens = state?.marker_lens;
  const activeLens = state?.active_lens;
  const lensStyleSpec = lensStyle(lens);
  const lensValue = useMemo(() => {
    if (!lensStyleSpec || activeId == null) return null;
    const m = decodeMarkers(state?.result_markers).find((mk) => mk.id === activeId);
    return m?.lens_value ?? null;
  }, [lensStyleSpec, activeId, state?.result_markers]);
  const lensStatLabel =
    activeLens?.kind === "travel_time"
      ? activeLens.mode === "car"
        ? "Drive"
        : "Transit"
      : "Distance";
  // Colour the lens stat by the lens ramp (matches the map pin), over the same
  // adaptive domain the map uses.
  const lensDomainValue = useMemo(
    () => lensDomain(decodeMarkers(state?.result_markers).map((m) => m.lens_value), lens),
    [state?.result_markers, lens],
  );
  const lensStat =
    lensStyleSpec && lensValue != null
      ? {
          label: lensStatLabel,
          value: lensStyleSpec.format(lensValue),
          color: lensColorForValue(lens, lensValue, lensDomainValue),
        }
      : null;

  const close = () => {
    void activate(null);
  };

  // Re-focus the back button when the active listing changes, not just on
  // mount — CardsPane keeps this component mounted and swaps active_id when
  // navigating card→card, so a `[]` dep would leave focus (and the
  // Escape-to-close handler) on the previously focused element.
  useEffect(() => {
    backButtonRef.current?.focus();
  }, [activeId]);

  // Defensive: active_id set but neither tier-2 card nor tier-3 detail has
  // arrived yet (e.g. agent set active_id, HTTP fetch in flight). Render a
  // minimal loading shell rather than crashing on undefined field reads.
  if (!view) {
    return (
      <div
        role="region"
        aria-label="Loading detail"
        tabIndex={-1}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            close();
          }
        }}
        className="flex h-full flex-col bg-white animate-detail-rise focus:outline-none"
      >
        <header className="flex items-start justify-between gap-3 border-b-2 border-red px-7 pb-2.5 pt-2.5">
          <div className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
            Loading…
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {activeId != null && (
              <BookmarkHeart
                filled={isBookmarked}
                onToggle={() => void toggleBookmark(activeId)}
                size="md"
              />
            )}
            <button
              ref={backButtonRef}
              type="button"
              className="shrink-0 border border-ink/20 px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest text-ink-soft transition-colors hover:border-ink hover:bg-ink hover:text-white"
              onClick={close}
            >
              ← back
            </button>
          </div>
        </header>
      </div>
    );
  }

  return (
    <div
      role="region"
      aria-label={`Detail for ${view.title ?? "apartment"}`}
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          close();
        }
      }}
      className="flex h-full flex-col overflow-y-auto bg-white animate-detail-rise focus:outline-none"
    >
      <header className="flex items-start justify-between gap-3 border-b-2 border-red px-7 pb-2.5 pt-2.5">
        <div className="min-w-0">
          <div className="font-mono text-[10px] uppercase tracking-widest text-red">
            Detail · {view.district ?? "Berlin"}
          </div>
          <h2 className="mt-1 line-clamp-2 font-display text-lg font-medium leading-tight tracking-tightest text-ink">
            {view.title ?? "(untitled)"}
          </h2>
          <div className="mt-0.5 line-clamp-1 text-[13px] text-ink-soft">
            {view.address ?? view.district ?? "—"}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <BookmarkHeart
            filled={isBookmarked}
            onToggle={() => void toggleBookmark(view.id)}
            size="md"
            label={view.title ?? "this listing"}
          />
          <button
            ref={backButtonRef}
            type="button"
            className="shrink-0 border border-ink/20 px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest text-ink-soft transition-colors hover:border-ink hover:bg-ink hover:text-white"
            onClick={close}
          >
            ← back
          </button>
        </div>
      </header>

      {/* Stat row: only cells with real data render. flex-wrap lets the row
          flow naturally when some sources expose fewer fields (wg-gesucht
          rarely has kaution, klein rarely has bedrooms). flex-1 basis-0
          shares row width equally among present cells. */}
      <div className="flex flex-wrap">
        {lensStat && (
          <Stat
            label={lensStat.label}
            value={lensStat.value}
            accent
            accentColor={lensStat.color ?? undefined}
          />
        )}
        {view.price_warm_eur != null && (
          <Stat label="Warm rent" value={formatEuro(view.price_warm_eur)} accent />
        )}
        {view.price_cold_eur != null && (
          <Stat label="Cold rent" value={formatEuro(view.price_cold_eur)} />
        )}
        {view.nebenkosten_eur != null && (
          <Stat label="Nebenkosten" value={formatEuro(view.nebenkosten_eur)} />
        )}
        {view.kaution_eur != null && (
          <Stat label="Kaution" value={formatEuro(view.kaution_eur)} />
        )}
        {view.rooms != null && (
          <Stat label="Rooms" value={view.rooms.toString().replace(/\.0$/, "")} />
        )}
        {view.bedrooms != null && (
          <Stat label="Bedrooms" value={view.bedrooms.toString()} />
        )}
        {view.area_sqm != null && (
          <Stat label="Area" value={`${Math.round(view.area_sqm)} m²`} />
        )}
        {view.price_warm_eur != null && view.area_sqm != null && view.area_sqm > 0 && (
          <Stat
            label="€/m²"
            value={`€${Math.round(view.price_warm_eur / view.area_sqm)}`}
          />
        )}
        {view.floor != null && <Stat label="Floor" value={formatFloor(view.floor)} />}
        {view.listing_type != null && (
          <Stat label="Type" value={view.listing_type} />
        )}
        {view.available_from != null && (
          <Stat label="Available" value={formatDate(view.available_from)} />
        )}
        {view.source_url && (
          <Stat
            label="Source"
            value={
              <a
                href={view.source_url}
                target="_blank"
                rel="noreferrer"
                className="text-red underline-offset-2 hover:underline"
              >
                Open →
              </a>
            }
          />
        )}
      </div>

      <AmenityChips view={view} />

      <ImageGallery detail={detail} apt={apt} title={view.title} />

      <EnergyBlock view={view} />

      {ctx && <GeoContextBlock ctx={ctx} />}

      {/* Computed Notes block — fills the rest of the slot with prose
          derived from the actual fields (no lorem ipsum) so the panel
          doesn't end with dead whitespace. Acts as a soft hook back into
          the chat: tells the user what we *do* know, then invites them to
          ask about what we don't. */}
      <div className="border-y border-paper-rule bg-paper-dim px-7 py-3">
        <div className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
          Notes
        </div>
        <p className="mt-1.5 text-[13px] leading-relaxed text-ink-soft">
          {summarize(view)}
        </p>
      </div>

    </div>
  );
}

// Computed prose summary built from the apartment's actual fields. We
// only mention what we know; nothing is invented. Falls back gracefully
// when fields are null. Avoids both lorem ipsum and pretending we have
// data we don't.
function summarize(view: DetailView): string {
  const parts: string[] = [];
  const here = view.district ?? "Berlin";
  parts.push(`Located in ${here}${view.address ? ` (${view.address})` : ""}.`);

  const room = view.rooms
    ? `${view.rooms.toString().replace(/\.0$/, "")}-room`
    : null;
  const area = view.area_sqm ? `${Math.round(view.area_sqm)} m²` : null;
  if (room || area) {
    parts.push(
      [room, area].filter(Boolean).join(" · ") + " of living space.",
    );
  }

  if (view.price_warm_eur != null) {
    const warm = `€${Math.round(view.price_warm_eur).toLocaleString("en-US")}`;
    const perSqm =
      view.area_sqm != null && view.area_sqm > 0
        ? ` (~€${Math.round(view.price_warm_eur / view.area_sqm)}/m²)`
        : "";
    parts.push(`Warm rent ${warm}${perSqm}.`);
  }

  if (view.wbs_required === true) {
    parts.push("WBS (Wohnberechtigungsschein) required.");
  }
  if (view.lister_type) {
    parts.push(`Listed by ${view.lister_type}.`);
  }

  return parts.join(" ");
}

// Image gallery. Uses the full list from ListingDetail.images when available
// (HTTP-fetched on card click); falls back to the single image_url tier-2
// thumbnail for the initial render before the detail fetch completes.
// Lazy `loading="lazy"` so a long gallery doesn't block the rest of the
// detail render.
function ImageGallery({
  detail,
  apt,
  title,
}: {
  detail: ListingDetail | null;
  apt?: ListingCard;
  title: string | null;
}) {
  const images: string[] =
    detail && detail.images.length > 0
      ? detail.images
      : apt?.image_url
      ? [apt.image_url]
      : [];
  if (images.length === 0) return null;

  return (
    <div className="border-t border-paper-rule px-7 py-3">
      <div className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
        {images.length === 1 ? "Photo" : `Photos · ${images.length}`}
      </div>
      <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
        {images.map((src, i) => (
          <img
            key={`${src}-${i}`}
            src={src}
            alt={title ? `${title} — photo ${i + 1}` : `Listing photo ${i + 1}`}
            loading="lazy"
            decoding="async"
            className="h-32 w-auto flex-shrink-0 border border-paper-rule object-cover"
            onError={(e) => {
              // Hide broken images rather than showing the alt-text placeholder
              // — many scraper URLs 403 after a few hours.
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        ))}
      </div>
    </div>
  );
}

// Chip row for the most-asked amenities. Render `true` only — `false` and
// `null` both stay hidden so the row reflects confirmed features, not
// absence of data. WBS gets the leading slot because it's a hard
// requirement signal in Berlin.
function AmenityChips({ view }: { view: DetailView }) {
  const chips: { label: string; tone: "wbs" | "amenity" }[] = [];
  if (view.wbs_required === true) chips.push({ label: "WBS", tone: "wbs" });
  if (view.is_furnished === true) chips.push({ label: "Furnished", tone: "amenity" });
  if (view.has_balcony === true) chips.push({ label: "Balkon", tone: "amenity" });
  if (view.has_kitchen === true) chips.push({ label: "Einbauküche", tone: "amenity" });
  if (view.has_elevator === true) chips.push({ label: "Aufzug", tone: "amenity" });
  if (view.has_garden === true) chips.push({ label: "Garten", tone: "amenity" });

  if (chips.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5 border-t border-paper-rule px-7 py-2.5">
      {chips.map((c) => (
        <span
          key={c.label}
          className={
            c.tone === "wbs"
              ? "border border-red bg-red px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-white"
              : "border border-ink/20 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-ink-soft"
          }
        >
          {c.label}
        </span>
      ))}
    </div>
  );
}

// Geo-context block: nearby transit / schools / kitas / parks / landmarks /
// noise / hospitals / admin area / inside-ring.
// Only sections with data render; partial backend wiring produces partial UI,
// not stale empty rows. Pulls from SessionState.active_listing_detail, which
// is populated by the frontend's HTTP fetch on card click OR by the agent's
// `open_listing` tool via state delta.
function GeoContextBlock({ ctx }: { ctx: ListingDetail }) {
  const hasAny =
    ctx.nearest_transit_stops.length > 0 ||
    ctx.nearest_schools.length > 0 ||
    ctx.nearest_parks.length > 0 ||
    ctx.nearest_hospitals.length > 0 ||
    ctx.nearest_water != null ||
    ctx.nearest_playground != null ||
    ctx.nearest_kitas.length > 0 ||
    ctx.nearest_landmarks.length > 0 ||
    ctx.noise != null ||
    ctx.greenery != null ||
    ctx.density != null ||
    ctx.inside_ring != null ||
    ctx.listing_bezirk != null ||
    ctx.listing_ortsteil != null ||
    ctx.school_catchment != null ||
    ctx.disabled_parking_count > 0;
  if (!hasAny) return null;

  return (
    <div className="border-t border-paper-rule px-7 py-3">
      <div className="font-mono text-[10px] uppercase tracking-widest text-red">
        Neighbourhood context
      </div>

      {ctx.nearest_transit_stops.length > 0 && (
        <Section title="Transit">
          <ul className="space-y-0.5">
            {ctx.nearest_transit_stops.map((stop) => (
              <li key={stop.stop_id} className="text-[12.5px] text-ink-soft">
                <span className="text-ink">{stop.name}</span>
                {stop.lines.length > 0 && (
                  <span className="text-ink-soft"> · {stop.lines.join(", ")}</span>
                )}
                <span className="font-mono text-[11px] text-ink-ghost">
                  {" "}
                  · {stop.distance_m}m / {stop.walk_minutes}min
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {ctx.school_catchment != null && (
        <Section title="Primary-school catchment">
          <div className="text-[12.5px] text-ink-soft">
            {ctx.school_catchment.school_name ?? ctx.school_catchment.catchment_id}
          </div>
        </Section>
      )}

      {ctx.nearest_schools.length > 0 && (
        <Section title="Schools nearby">
          <ul className="space-y-0.5">
            {ctx.nearest_schools.map((s, i) => (
              <li key={i} className="text-[12.5px] text-ink-soft">
                <span className="text-ink">{s.name ?? "unnamed"}</span>
                {s.school_type && (
                  <span className="text-ink-ghost"> · {s.school_type}</span>
                )}
                <span className="font-mono text-[11px] text-ink-ghost">
                  {" "}
                  · {s.distance_m}m
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {ctx.nearest_kitas.length > 0 && (
        <Section title="Kitas nearby">
          <ul className="space-y-0.5">
            {ctx.nearest_kitas.map((k, i) => (
              <li key={i} className="text-[12.5px] text-ink-soft">
                <span className="text-ink">{k.name ?? "unnamed"}</span>
                <span className="font-mono text-[11px] text-ink-ghost">
                  {" "}
                  · {k.distance_m}m
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {ctx.nearest_parks.length > 0 && (
        <Section title="Parks nearby">
          <ul className="space-y-0.5">
            {ctx.nearest_parks.map((p, i) => (
              <li key={i} className="text-[12.5px] text-ink-soft">
                <span className="text-ink">{p.name ?? "unnamed"}</span>
                <span className="font-mono text-[11px] text-ink-ghost">
                  {" "}
                  · {p.distance_m}m
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {ctx.nearest_landmarks.length > 0 && (
        <Section title="Landmarks nearby">
          <ul className="space-y-0.5">
            {ctx.nearest_landmarks.map((l, i) => (
              <li key={i} className="text-[12.5px] text-ink-soft">
                <span className="text-ink">{l.name ?? "unnamed"}</span>
                {l.category && (
                  <span className="text-ink-ghost"> · {l.category}</span>
                )}
                <span className="font-mono text-[11px] text-ink-ghost">
                  {" "}
                  · {l.distance_m}m
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {ctx.nearest_playground != null && (
        <Section title="Nearest playground">
          <div className="text-[12.5px] text-ink-soft">
            {ctx.nearest_playground.name ?? "unnamed"}
            <span className="font-mono text-[11px] text-ink-ghost">
              {" "}
              · {ctx.nearest_playground.distance_m}m
            </span>
          </div>
        </Section>
      )}

      {ctx.nearest_water != null && (
        <Section title="Nearest water">
          <div className="text-[12.5px] text-ink-soft">
            {ctx.nearest_water.name ?? ctx.nearest_water.water_kind ?? "water"}
            <span className="font-mono text-[11px] text-ink-ghost">
              {" "}
              · {ctx.nearest_water.distance_m}m
            </span>
          </div>
        </Section>
      )}

      {ctx.nearest_hospitals.length > 0 && (
        <Section title="Hospitals nearby">
          <ul className="space-y-0.5">
            {ctx.nearest_hospitals.map((h, i) => (
              <li key={i} className="text-[12.5px] text-ink-soft">
                <span className="text-ink">{h.name ?? "unnamed"}</span>
                <span className="text-ink-ghost"> · {h.tier}</span>
                <span className="font-mono text-[11px] text-ink-ghost">
                  {" "}
                  · {h.distance_m}m
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(ctx.listing_bezirk != null || ctx.listing_ortsteil != null) && (
        <Section title="Administrative area">
          <div className="text-[12.5px] text-ink-soft">
            {[ctx.listing_bezirk, ctx.listing_ortsteil]
              .filter(Boolean)
              .join(" · ") || "—"}
          </div>
        </Section>
      )}

      {ctx.inside_ring != null && (
        <Section title="Inside the S-Bahn ring">
          <div className="text-[12.5px] text-ink-soft">
            {ctx.inside_ring ? "yes" : "no"}
          </div>
        </Section>
      )}

      {/* Character labels — short row of label-value pairs. */}
      <CharacterRow ctx={ctx} />

      {ctx.disabled_parking_count > 0 && (
        <Section title="Disabled parking nearby">
          <div className="text-[12.5px] text-ink-soft">
            {ctx.disabled_parking_count} spots within 300m
          </div>
        </Section>
      )}
    </div>
  );
}

// Sub-section helper inside the geo block — section title + body, no border.
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-2">
      <div className="font-mono text-[9px] uppercase tracking-widest text-ink-ghost">
        {title}
      </div>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}

// Single-row strip of character labels with raw values underneath. Each
// cell only renders when its label is non-null. Surfaces the raw numerics
// (Lden / Lnight / m² / persons-per-hectare) so users see the data behind
// the bucket.
function CharacterRow({ ctx }: { ctx: ListingDetail }) {
  const cells: { label: string; value: string; sub?: string }[] = [];
  if (ctx.noise?.label) {
    const sub = formatNoiseBreakdown(ctx.noise);
    cells.push({ label: "Noise", value: ctx.noise.label, sub });
  }
  if (ctx.greenery?.label) {
    const sub =
      ctx.greenery.green_m2_within_300m != null
        ? `${Math.round(ctx.greenery.green_m2_within_300m).toLocaleString()} m² / 300m`
        : undefined;
    cells.push({ label: "Greenery", value: ctx.greenery.label, sub });
  }
  if (ctx.density?.label) {
    const sub =
      ctx.density.persons_per_hectare != null
        ? `${Math.round(ctx.density.persons_per_hectare)} ppl/ha`
        : undefined;
    cells.push({ label: "Density", value: ctx.density.label, sub });
  }
  if (cells.length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5">
      {cells.map((c) => (
        <div key={c.label} className="flex flex-col">
          <span className="font-mono text-[9px] uppercase tracking-widest text-ink-ghost">
            {c.label}
          </span>
          <span className="text-[12.5px] text-ink-soft">{c.value}</span>
          {c.sub && (
            <span className="font-mono text-[10px] text-ink-ghost">{c.sub}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function formatNoiseBreakdown(noise: ListingDetail["noise"]): string | undefined {
  if (!noise) return undefined;
  const parts: string[] = [];
  if (noise.total_lden != null) parts.push(`Lden ${noise.total_lden.toFixed(0)} dB`);
  if (noise.total_lnight != null)
    parts.push(`Lnight ${noise.total_lnight.toFixed(0)} dB`);
  if (noise.street_lden != null) parts.push(`street ${noise.street_lden.toFixed(0)}`);
  if (noise.rail_lden != null) parts.push(`rail ${noise.rail_lden.toFixed(0)}`);
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

// Heating-only block. We used to also surface energy_consumption_kwh
// (Energieausweis Verbrauch), but the silver layer can't reliably extract
// it from either source's amenity strings — see the data-quality audit.
// Heating itself only fills ~11% of wg-gesucht and 0% of kleinanzeigen,
// so the block self-hides when empty.
function EnergyBlock({ view }: { view: DetailView }) {
  if (!view.heating) return null;
  return (
    <div className="border-t border-paper-rule px-7 py-2.5">
      <div className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
        Heating
      </div>
      <div className="mt-1 text-[12.5px] text-ink-soft">{view.heating}</div>
    </div>
  );
}

function formatEuro(v: number | null): string {
  if (v == null) return "—";
  return `€${Math.round(v).toLocaleString("en-US")}`;
}

function formatFloor(floor: number | null): string {
  if (floor == null) return "—";
  return floor === 0 ? "EG" : floor.toString();
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  // Tolerate both ISO datetimes and bare date strings. If the parse fails
  // (e.g. legacy free-form text from before the schema migration), surface
  // the raw string rather than swallowing it.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("de-DE", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function Stat({
  label,
  value,
  accent = false,
  accentColor,
}: {
  label: string;
  value: React.ReactNode;
  accent?: boolean;
  // When set (a lens value), overrides the default red accent so the stat
  // matches the lens ramp / map pin. Ignored for non-accent stats.
  accentColor?: string;
}) {
  return (
    <div className="flex min-w-[100px] flex-1 basis-0 flex-col border-b border-r border-paper-rule px-3 py-2">
      <div className="truncate font-mono text-[9px] uppercase tracking-widest text-ink-ghost">
        {label}
      </div>
      <div
        className={
          accent
            ? "mt-0.5 truncate font-mono text-base font-medium tabular-nums tracking-tight text-red"
            : "mt-0.5 truncate font-mono text-sm tabular-nums text-ink"
        }
        style={accent && accentColor ? { color: accentColor } : undefined}
      >
        {value}
      </div>
    </div>
  );
}
