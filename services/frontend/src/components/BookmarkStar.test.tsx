import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BookmarkStar } from "./BookmarkStar";

afterEach(() => {
  cleanup();
});

describe("BookmarkStar", () => {
  it("renders an outline star when not bookmarked", () => {
    render(<BookmarkStar filled={false} onToggle={() => {}} />);
    const button = screen.getByTestId("bookmark-star");
    expect(button.getAttribute("aria-pressed")).toBe("false");
    const svg = button.querySelector("svg");
    // outline = transparent fill on the polygon.
    expect(svg?.getAttribute("fill")).toBe("none");
  });

  it("renders a filled star when bookmarked", () => {
    render(<BookmarkStar filled={true} onToggle={() => {}} />);
    const button = screen.getByTestId("bookmark-star");
    expect(button.getAttribute("aria-pressed")).toBe("true");
    const svg = button.querySelector("svg");
    // filled = solid fill via currentColor (we explicitly write the literal).
    expect(svg?.getAttribute("fill")).toBe("currentColor");
  });

  it("includes the label in the aria-label for screen readers", () => {
    render(
      <BookmarkStar filled={false} onToggle={() => {}} label="Sunny flat" />,
    );
    expect(screen.getByLabelText("Bookmark: Sunny flat")).toBeTruthy();
  });

  it("flips wording to 'Remove bookmark' when filled", () => {
    render(
      <BookmarkStar filled={true} onToggle={() => {}} label="Sunny flat" />,
    );
    expect(screen.getByLabelText("Remove bookmark: Sunny flat")).toBeTruthy();
  });

  it("fires onToggle when clicked", () => {
    const onToggle = vi.fn();
    render(<BookmarkStar filled={false} onToggle={onToggle} />);
    fireEvent.click(screen.getByTestId("bookmark-star"));
    expect(onToggle).toHaveBeenCalled();
  });

  it("stops propagation so a click does NOT bubble to a parent handler", () => {
    // The star is intended to sit OVER a clickable card; if its click bubbled
    // the card-activate would fire alongside the bookmark toggle.
    const parentClick = vi.fn();
    const onToggle = vi.fn();
    render(
      <div onClick={parentClick} data-testid="parent">
        <BookmarkStar filled={false} onToggle={onToggle} />
      </div>,
    );
    fireEvent.click(screen.getByTestId("bookmark-star"));
    expect(onToggle).toHaveBeenCalled();
    expect(parentClick).not.toHaveBeenCalled();
  });
});
