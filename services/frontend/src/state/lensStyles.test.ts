import { describe, expect, it } from "vitest";

import type { MarkerLens } from "./SessionState";
import {
  lensColorExpression,
  lensDomain,
  lensLegend,
  rampColorExpression,
} from "./lensStyles";

const COMMUTE: MarkerLens = { key: "commute_min", label: "min to TU Berlin" };
const PRICE: MarkerLens = { key: "price_warm", label: null };

// Pull the [value, value, ...] stops out of a rampColorExpression result:
// ["case", noData, NO_DATA, ["interpolate", ["linear"], value, v0,c0, v1,c1...]]
function stopValues(expr: unknown): number[] {
  const interp = (expr as unknown[])[3] as unknown[];
  const tail = interp.slice(3); // after "interpolate", ["linear"], value
  return tail.filter((_, i) => i % 2 === 0) as number[];
}

describe("lensDomain", () => {
  it("returns [floor(min), ceil(max)] for a heatmap lens", () => {
    expect(lensDomain([12.4, 8.1, 33.9, null, undefined], COMMUTE)).toEqual([8, 34]);
  });

  it("falls back to the native domain when there are no values", () => {
    expect(lensDomain([null, undefined], COMMUTE)).toEqual([0, 60]);
  });

  it("guards a degenerate single-value set", () => {
    expect(lensDomain([20, 20], COMMUTE)).toEqual([20, 21]);
  });

  it("returns undefined for the default (non-heatmap) lens", () => {
    expect(lensDomain([1, 2, 3], PRICE)).toBeUndefined();
  });
});

describe("adaptive ramp remap", () => {
  it("remaps the native ramp stops onto the adaptive domain", () => {
    const expr = rampColorExpression(
      COMMUTE,
      ["to-number", ["get", "lens_value"]] as never,
      ["==", ["get", "lens_value"], null] as never,
      [10, 30],
    );
    // Native stops 0/15/30/45/60 (fracs 0/.25/.5/.75/1) over [10,30].
    expect(stopValues(expr)).toEqual([10, 15, 20, 25, 30]);
  });

  it("uses the native domain when no override is given", () => {
    const expr = rampColorExpression(
      COMMUTE,
      ["to-number", ["get", "lens_value"]] as never,
      ["==", ["get", "lens_value"], null] as never,
    );
    expect(stopValues(expr)).toEqual([0, 15, 30, 45, 60]);
  });

  it("lensColorExpression returns the plain colour for the default lens", () => {
    expect(lensColorExpression(PRICE, "#5A5A5A")).toBe("#5A5A5A");
  });
});

describe("lensLegend adaptive labels", () => {
  it("labels min/max from the adaptive domain, keeps the title", () => {
    const legend = lensLegend(COMMUTE, [8, 34]);
    expect(legend?.minLabel).toBe("8 min");
    expect(legend?.maxLabel).toBe("34 min");
    expect(legend?.title).toBe("min to TU Berlin");
  });

  it("returns null for the default (non-heatmap) lens", () => {
    expect(lensLegend(PRICE)).toBeNull();
  });
});
