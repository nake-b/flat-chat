import { useEffect, useRef } from "react";

import { useSessionState } from "../hooks/useSessionState";
import {
  type ListingDetail,
  type UiApartment,
} from "../state/SessionState";

// Detail panel: a card-sized white pane sitting at the top of the cards
// slot, with the paper background of `CardsPane` showing beneath. The
// pane sizes to its content — no stretching, no Option-X expansion, no
// filler band. Matches the strip's "card-on-paper" visual density.
export function CardDetail({ apt }: { apt: UiApartment }) {
  const { state, activate } = useSessionState();
  const backButtonRef = useRef<HTMLButtonElement | null>(null);

  // Tier-3 detail blob — fetched by `activate(id)` via GET /api/listings/{id}
  // when the user clicks a card (the primary path) OR pushed by the agent's
  // `open_listing` tool via state delta. Either way it lives in SessionState
  // as `active_listing_detail`. We only render it for the *currently active*
  // listing so stale data can't leak across selections.
  const detail: ListingDetail | null =
    state?.active_id === apt.id ? state?.active_listing_detail ?? null : null;
  // Back-compat alias for the existing rendering code below — `ctx` was the
  // old name; ListingDetail is a superset of what ListingContext exposed.
  const ctx = detail;

  const close = () => {
    void activate(null);
  };

  useEffect(() => {
    backButtonRef.current?.focus();
  }, []);

  return (
    <div
      role="region"
      aria-label={`Detail for ${apt.title ?? "apartment"}`}
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          close();
        }
      }}
      className="flex h-full flex-col overflow-y-auto bg-white focus:outline-none"
    >
      <header className="flex items-start justify-between gap-3 border-b-2 border-red px-7 pb-2.5 pt-2.5">
        <div className="min-w-0">
          <div className="font-mono text-[10px] uppercase tracking-widest text-red">
            Detail · {apt.district ?? "Berlin"}
          </div>
          <h2 className="mt-1 line-clamp-2 font-display text-lg font-medium leading-tight tracking-tightest text-ink">
            {apt.title ?? "(untitled)"}
          </h2>
          <div className="mt-0.5 line-clamp-1 text-[13px] text-ink-soft">
            {apt.address ?? apt.district ?? "—"}
          </div>
        </div>
        <button
          ref={backButtonRef}
          type="button"
          className="shrink-0 border border-ink/20 px-3 py-1.5 font-mono text-[10px] uppercase tracking-widest text-ink-soft transition-colors hover:border-ink hover:bg-ink hover:text-white"
          onClick={close}
        >
          ← back
        </button>
      </header>

      {/* Stat row: only cells with real data render. flex-wrap lets the row
          flow naturally when some sources expose fewer fields (wg-gesucht
          rarely has kaution, klein rarely has bedrooms). flex-1 basis-0
          shares row width equally among present cells. */}
      <div className="flex flex-wrap">
        {apt.price_warm_eur != null && (
          <Stat label="Warm rent" value={formatEuro(apt.price_warm_eur)} accent />
        )}
        {apt.price_cold_eur != null && (
          <Stat label="Cold rent" value={formatEuro(apt.price_cold_eur)} />
        )}
        {apt.nebenkosten_eur != null && (
          <Stat label="Nebenkosten" value={formatEuro(apt.nebenkosten_eur)} />
        )}
        {apt.kaution_eur != null && (
          <Stat label="Kaution" value={formatEuro(apt.kaution_eur)} />
        )}
        {apt.rooms != null && (
          <Stat label="Rooms" value={apt.rooms.toString().replace(/\.0$/, "")} />
        )}
        {apt.bedrooms != null && (
          <Stat label="Bedrooms" value={apt.bedrooms.toString()} />
        )}
        {apt.area_sqm != null && (
          <Stat label="Area" value={`${Math.round(apt.area_sqm)} m²`} />
        )}
        {apt.price_warm_eur != null && apt.area_sqm != null && apt.area_sqm > 0 && (
          <Stat
            label="€/m²"
            value={`€${Math.round(apt.price_warm_eur / apt.area_sqm)}`}
          />
        )}
        {apt.floor != null && <Stat label="Floor" value={formatFloor(apt.floor)} />}
        {apt.listing_type != null && (
          <Stat label="Type" value={apt.listing_type} />
        )}
        {apt.available_from != null && (
          <Stat label="Available" value={formatDate(apt.available_from)} />
        )}
        {apt.source_url && (
          <Stat
            label="Source"
            value={
              <a
                href={apt.source_url}
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

      <AmenityChips apt={apt} />

      <ImageGallery detail={detail} apt={apt} />

      <EnergyBlock apt={apt} />

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
          {summarize(apt)}
        </p>
      </div>

    </div>
  );
}

// Computed prose summary built from the apartment's actual fields. We
// only mention what we know; nothing is invented. Falls back gracefully
// when fields are null. Avoids both lorem ipsum and pretending we have
// data we don't.
function summarize(apt: UiApartment): string {
  const parts: string[] = [];
  const here = apt.district ?? "Berlin";
  parts.push(`Located in ${here}${apt.address ? ` (${apt.address})` : ""}.`);

  const room = apt.rooms
    ? `${apt.rooms.toString().replace(/\.0$/, "")}-room`
    : null;
  const area = apt.area_sqm ? `${Math.round(apt.area_sqm)} m²` : null;
  if (room || area) {
    parts.push(
      [room, area].filter(Boolean).join(" · ") + " of living space.",
    );
  }

  if (apt.price_warm_eur != null) {
    const warm = `€${Math.round(apt.price_warm_eur).toLocaleString("en-US")}`;
    const perSqm =
      apt.area_sqm != null && apt.area_sqm > 0
        ? ` (~€${Math.round(apt.price_warm_eur / apt.area_sqm)}/m²)`
        : "";
    parts.push(`Warm rent ${warm}${perSqm}.`);
  }

  if (apt.wbs_required === true) {
    parts.push("WBS (Wohnberechtigungsschein) required.");
  }
  if (apt.lister_type) {
    parts.push(`Listed by ${apt.lister_type}.`);
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
}: {
  detail: ListingDetail | null;
  apt: UiApartment;
}) {
  const images: string[] =
    detail && detail.images.length > 0
      ? detail.images
      : apt.image_url
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
            alt={apt.title ? `${apt.title} — photo ${i + 1}` : `Listing photo ${i + 1}`}
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
function AmenityChips({ apt }: { apt: UiApartment }) {
  const chips: { label: string; tone: "wbs" | "amenity" }[] = [];
  if (apt.wbs_required === true) chips.push({ label: "WBS", tone: "wbs" });
  if (apt.is_furnished === true) chips.push({ label: "Furnished", tone: "amenity" });
  if (apt.has_balcony === true) chips.push({ label: "Balkon", tone: "amenity" });
  if (apt.has_kitchen === true) chips.push({ label: "Einbauküche", tone: "amenity" });
  if (apt.has_elevator === true) chips.push({ label: "Aufzug", tone: "amenity" });
  if (apt.has_garden === true) chips.push({ label: "Garten", tone: "amenity" });

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

// Geo-context block: nearby transit / schools / parks / noise / hospitals.
// Only sections with data render; partial backend wiring produces partial UI,
// not stale empty rows. Pulls from SessionState.active_listing_detail, which
// is populated by the frontend's HTTP fetch on card click OR by the agent's
// `open_listing` tool via state delta.
function GeoContextBlock({ ctx }: { ctx: ListingDetail }) {
  const hasAny =
    ctx.nearest_transit_stops.length > 0 ||
    ctx.nearest_schools.length > 0 ||
    ctx.nearest_kitas.length > 0 ||
    ctx.nearest_parks.length > 0 ||
    ctx.nearest_hospitals.length > 0 ||
    ctx.nearest_water != null ||
    ctx.nearest_playground != null ||
    ctx.trees_within_100_count > 0 ||
    ctx.noise != null ||
    ctx.greenery != null ||
    ctx.density != null ||
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
                {k.operator && <span className="text-ink-ghost"> · {k.operator}</span>}
                <span className="font-mono text-[11px] text-ink-ghost">
                  {" "}
                  · {k.distance_m}m
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {ctx.kitas_within_500_count > 0 && (
        <Section title="Kitas within 500m">
          <div className="text-[12.5px] text-ink-soft">
            {ctx.kitas_within_500_count}
          </div>
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
      {ctx.trees_within_100_count > 0 && (
        <Section title="Trees within 100m">
          <div className="text-[12.5px] text-ink-soft">
            {ctx.trees_within_100_count}
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
// cell only renders when its label is non-null. Now surfaces the raw
// numerics (Lden / m² / persons-per-hectare) so users see the data
// behind the bucket.
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
  if (noise.street_lden != null) parts.push(`street ${noise.street_lden.toFixed(0)}`);
  if (noise.rail_lden != null) parts.push(`rail ${noise.rail_lden.toFixed(0)}`);
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

// Heating-only block. We used to also surface energy_consumption_kwh
// (Energieausweis Verbrauch), but the silver layer can't reliably extract
// it from either source's amenity strings — see the data-quality audit.
// Heating itself only fills ~11% of wg-gesucht and 0% of kleinanzeigen,
// so the block self-hides when empty.
function EnergyBlock({ apt }: { apt: UiApartment }) {
  if (!apt.heating) return null;
  return (
    <div className="border-t border-paper-rule px-7 py-2.5">
      <div className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
        Heating
      </div>
      <div className="mt-1 text-[12.5px] text-ink-soft">{apt.heating}</div>
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
}: {
  label: string;
  value: React.ReactNode;
  accent?: boolean;
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
      >
        {value}
      </div>
    </div>
  );
}
