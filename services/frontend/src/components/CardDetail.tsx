import { useEffect, useRef } from "react";

import { useUiState } from "../hooks/useUiState";
import { EMPTY_UI_STATE, type UiApartment } from "../state/UiState";

// Detail panel: a card-sized white pane sitting at the top of the cards
// slot, with the paper background of `CardsPane` showing beneath. The
// pane sizes to its content — no stretching, no Option-X expansion, no
// filler band. Matches the strip's "card-on-paper" visual density.
export function CardDetail({ apt }: { apt: UiApartment }) {
  const { setState } = useUiState();
  const backButtonRef = useRef<HTMLButtonElement | null>(null);

  const close = () => {
    setState((prev) => ({ ...(prev ?? EMPTY_UI_STATE), active_id: null }));
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
      className="flex flex-col overflow-hidden bg-white focus:outline-none"
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

      <div className="grid grid-cols-6">
        <Stat
          label="Warm rent"
          value={formatEuro(apt.price_warm_eur)}
          accent
        />
        <Stat label="Cold rent" value={formatEuro(apt.price_cold_eur)} />
        <Stat label="Nebenkosten" value={formatEuro(apt.nebenkosten_eur)} />
        <Stat label="Kaution" value={formatEuro(apt.kaution_eur)} />
        <Stat
          label="Rooms"
          value={apt.rooms?.toString().replace(/\.0$/, "") ?? "—"}
        />
        <Stat
          label="Area"
          value={apt.area_sqm != null ? `${Math.round(apt.area_sqm)} m²` : "—"}
        />
      </div>

      <div className="grid grid-cols-6 border-t border-paper-rule">
        <Stat
          label="Bedrooms"
          value={apt.bedrooms != null ? apt.bedrooms.toString() : "—"}
        />
        <Stat
          label="€/m²"
          value={
            apt.price_warm_eur != null && apt.area_sqm
              ? `€${Math.round(apt.price_warm_eur / apt.area_sqm)}`
              : "—"
          }
        />
        <Stat label="Floor" value={formatFloor(apt.floor, apt.floors_total)} />
        <Stat label="Type" value={apt.listing_type ?? "—"} />
        <Stat label="Available" value={formatDate(apt.available_from)} />
        <Stat
          label="Source"
          value={
            apt.source_url ? (
              <a
                href={apt.source_url}
                target="_blank"
                rel="noreferrer"
                className="text-red underline-offset-2 hover:underline"
              >
                Open →
              </a>
            ) : (
              "—"
            )
          }
        />
      </div>

      <AmenityChips apt={apt} />

      <EnergyBlock apt={apt} />

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

      <div className="px-7 py-3">
        <div className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
          Ask the chat
        </div>
        <ul className="mt-1.5 space-y-1 text-[12.5px] leading-relaxed text-ink-soft">
          <li>
            <span className="text-ink-ghost">›</span> "Is this an
            Altbau? What floor?"
          </li>
          <li>
            <span className="text-ink-ghost">›</span> "What U-Bahn /
            S-Bahn is closest?"
          </li>
          <li>
            <span className="text-ink-ghost">›</span> "Compare this to
            cheaper options nearby."
          </li>
        </ul>
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

// Chip row for the most-asked amenities. Render `true` only — `false` and
// `null` both stay hidden so the row reflects confirmed features, not
// absence of data. WBS gets the leading slot because it's a hard
// requirement signal in Berlin.
function AmenityChips({ apt }: { apt: UiApartment }) {
  const chips: { label: string; tone: "wbs" | "amenity" }[] = [];
  if (apt.wbs_required === true) chips.push({ label: "WBS", tone: "wbs" });
  if (apt.is_furnished === true) chips.push({ label: "möbl.", tone: "amenity" });
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

// Energy block — only renders when there's data to show. Heating type and
// consumption are the two most-glanced fields in a Berlin Energieausweis;
// the rest stays in raw until users ask.
function EnergyBlock({ apt }: { apt: UiApartment }) {
  if (!apt.heating && apt.energy_consumption_kwh == null) return null;
  return (
    <div className="border-t border-paper-rule px-7 py-2.5">
      <div className="font-mono text-[10px] uppercase tracking-widest text-ink-ghost">
        Energy
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ink-soft">
        {apt.heating && (
          <span>
            <span className="text-ink-ghost">Heating:</span> {apt.heating}
          </span>
        )}
        {apt.energy_consumption_kwh != null && (
          <span>
            <span className="text-ink-ghost">Consumption:</span>{" "}
            {Math.round(apt.energy_consumption_kwh)} kWh/(m²·a)
          </span>
        )}
      </div>
    </div>
  );
}

function formatEuro(v: number | null): string {
  if (v == null) return "—";
  return `€${Math.round(v).toLocaleString("en-US")}`;
}

function formatFloor(floor: number | null, total: number | null): string {
  if (floor == null) return "—";
  const label = floor === 0 ? "EG" : floor.toString();
  return total != null ? `${label} / ${total}` : label;
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
    <div className="min-w-0 border-r border-paper-rule px-3 py-2 last:border-r-0">
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
