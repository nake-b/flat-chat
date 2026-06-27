import { describe, expect, it } from "vitest";

import { formatRelative } from "./relativeTime";

const NOW = new Date("2026-06-27T15:30:00");

describe("formatRelative", () => {
  it("returns time-of-day for the same calendar day", () => {
    const out = formatRelative(new Date("2026-06-27T12:04:00").toISOString(), NOW);
    // Locale + 12/24-hour ambiguity → check digits + colon are present.
    expect(out).toMatch(/12:04/);
  });

  it("returns 'Yesterday' (capitalised) for the previous calendar day", () => {
    const out = formatRelative(new Date("2026-06-26T18:00:00").toISOString(), NOW);
    expect(out.toLowerCase()).toContain("yesterday");
    expect(out.charAt(0)).toBe(out.charAt(0).toUpperCase());
  });

  it("returns 'N days ago' for 2-6 days back", () => {
    const out = formatRelative(new Date("2026-06-24T10:00:00").toISOString(), NOW);
    expect(out).toContain("3");
    expect(out).toContain("day");
  });

  it("returns 'Mon DD' for older dates in the same year", () => {
    const out = formatRelative(new Date("2026-03-12T10:00:00").toISOString(), NOW);
    // "Mar 12" in en-US locales — month abbreviation present + the day number.
    expect(out).toMatch(/[A-Za-z]{3,4}/);
    expect(out).toContain("12");
    expect(out).not.toMatch(/2026/); // year omitted when same as `now`
  });

  it("returns 'Mon DD, YYYY' for older dates in a different year", () => {
    const out = formatRelative(new Date("2024-11-04T10:00:00").toISOString(), NOW);
    expect(out).toContain("2024");
  });

  it("returns empty string for malformed input rather than 'Invalid Date'", () => {
    expect(formatRelative("not-a-date", NOW)).toBe("");
  });
});
