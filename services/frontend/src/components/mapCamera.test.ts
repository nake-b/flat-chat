import { describe, expect, it } from "vitest";

import type { MarkerPoint } from "../state/SessionState";
import {
  IN_VIEW_MIN,
  OVERVIEW_MAX_ZOOM,
  fractionInside,
  markersBBox,
  shouldReframe,
} from "./mapCamera";

const mk = (id: string, lng: number, lat: number): MarkerPoint => ({
  id,
  lng,
  lat,
  lens_value: null,
});

describe("markersBBox", () => {
  it("returns null for an empty set", () => {
    expect(markersBBox([])).toBeNull();
  });

  it("spans min/max lng/lat as [[w,s],[e,n]]", () => {
    const box = markersBBox([
      mk("a", 13.3, 52.5),
      mk("b", 13.5, 52.4),
      mk("c", 13.1, 52.6),
    ]);
    expect(box).toEqual([
      [13.1, 52.4],
      [13.5, 52.6],
    ]);
  });

  it("handles a single marker (degenerate box)", () => {
    expect(markersBBox([mk("a", 13.4, 52.52)])).toEqual([
      [13.4, 52.52],
      [13.4, 52.52],
    ]);
  });
});

describe("fractionInside", () => {
  const rect = { west: 13.0, south: 52.4, east: 13.5, north: 52.6 };

  it("is 0 for an empty set", () => {
    expect(fractionInside([], rect)).toBe(0);
  });

  it("counts markers within the rect, inclusive of edges", () => {
    const markers = [
      mk("in", 13.2, 52.5),
      mk("edge", 13.0, 52.4), // on the SW corner → inside
      mk("out", 14.0, 52.5),
    ];
    expect(fractionInside(markers, rect)).toBeCloseTo(2 / 3);
  });

  it("is 1 when all markers are inside", () => {
    expect(fractionInside([mk("a", 13.2, 52.5)], rect)).toBe(1);
  });

  it("is 0 when all markers are outside", () => {
    expect(fractionInside([mk("a", 0, 0)], rect)).toBe(0);
  });
});

describe("shouldReframe", () => {
  const base = {
    zoom: 13,
    fractionInView: 1,
    hasActiveSelection: false,
    markerCount: 10,
  };

  it("never reframes with no markers", () => {
    expect(shouldReframe({ ...base, markerCount: 0 })).toBe(false);
  });

  it("never reframes while a listing is selected", () => {
    // Even zoomed out with nothing in view, an active selection owns the camera.
    expect(
      shouldReframe({
        ...base,
        hasActiveSelection: true,
        zoom: 9,
        fractionInView: 0,
      }),
    ).toBe(false);
  });

  it("reframes when zoomed out (overview)", () => {
    expect(
      shouldReframe({ ...base, zoom: OVERVIEW_MAX_ZOOM - 0.1 }),
    ).toBe(true);
  });

  it("stays put when zoomed in and the results are in view", () => {
    expect(
      shouldReframe({ ...base, zoom: 13, fractionInView: 1 }),
    ).toBe(false);
  });

  it("reframes when zoomed in but the results are off-screen", () => {
    expect(
      shouldReframe({
        ...base,
        zoom: 13,
        fractionInView: IN_VIEW_MIN - 0.01,
      }),
    ).toBe(true);
  });
});
