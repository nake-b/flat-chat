import { useUiState } from "../hooks/useUiState";
import type { UiApartment } from "../state/UiState";

// Detail panel: a card-sized white pane sitting at the top of the cards
// slot, with the paper background of `CardsPane` showing beneath. The
// pane sizes to its content — no stretching, no Option-X expansion, no
// filler band. Matches the strip's "card-on-paper" visual density.
export function CardDetail({ apt }: { apt: UiApartment }) {
  const { state, setState } = useUiState();

  const close = () => {
    setState({
      ...(state ?? { results: [], tool_logs: [] }),
      active_id: null,
    });
  };

  return (
    <div className="flex flex-col overflow-hidden bg-white">
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
          value={
            apt.price_warm_eur != null
              ? `€${Math.round(apt.price_warm_eur).toLocaleString("en-US")}`
              : "—"
          }
          accent
        />
        <Stat
          label="Rooms"
          value={apt.rooms?.toString().replace(/\.0$/, "") ?? "—"}
        />
        <Stat
          label="Area"
          value={apt.area_sqm != null ? `${Math.round(apt.area_sqm)} m²` : "—"}
        />
        <Stat label="District" value={apt.district ?? "—"} />
        <Stat
          label="€/m²"
          value={
            apt.price_warm_eur != null && apt.area_sqm
              ? `€${Math.round(apt.price_warm_eur / apt.area_sqm)}`
              : "—"
          }
        />
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

      {/* Computed Notes block — fills the rest of the slot with prose
          derived from the actual fields (no lorem ipsum) so the panel
          doesn't end with dead whitespace. Acts as a soft hook back into
          the chat: tells the user what we *do* know, then invites them to
          ask about what we don't. */}
      <div className="border-b border-paper-rule bg-paper-dim px-7 py-3">
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

  return parts.join(" ");
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
