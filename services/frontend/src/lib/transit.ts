// Transit line label → mode + display icon.
//
// Mirrors the backend prefix heuristic in `listings/labels.py:transit_mode`
// (gold stores only line labels, not GTFS route types): U… = U-Bahn, S… =
// S-Bahn, M<digit> = tram, N… = night bus, everything else = bus. Numeric
// trams (12/16/…) read as "bus" — cosmetic only; the label is always shown.
// The backend already picks the rail-preferred line (`primary_transit_line`),
// so this is purely about the icon shown next to it.

export type TransitMode = "u_bahn" | "s_bahn" | "tram" | "bus" | "night";

export function transitMode(line: string): TransitMode {
  if (!line) return "bus";
  const head = line[0]!.toUpperCase();
  if (head === "U") return "u_bahn";
  if (head === "S") return "s_bahn";
  if (head === "N") return "night";
  if (head === "M" && /\d/.test(line[1] ?? "")) return "tram";
  return "bus";
}

const MODE_ICON: Record<TransitMode, string> = {
  u_bahn: "🚇",
  s_bahn: "🚆",
  tram: "🚊",
  bus: "🚌",
  night: "🌙",
};

export function transitIcon(line: string): string {
  return MODE_ICON[transitMode(line)];
}

// Compact form for the small result cards: "🚇 U7 · 3min".
export function formatTransitCompact(line: string, walkMin: number): string {
  return `${transitIcon(line)} ${line} · ${walkMin}min`;
}

// Detailed form for the bookmark rows: "3 min walk to 🚇 U7" — phrased as one
// unit so it's unambiguous that the minutes are the WALK to that line (not a
// frequency or the line number). Reads as a single tag.
export function formatTransitDetailed(line: string, walkMin: number): string {
  return `${walkMin} min walk to ${transitIcon(line)} ${line}`;
}
