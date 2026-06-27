import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ListingCard } from "../state/SessionState";
import { BookmarkSidebar } from "./BookmarkSidebar";

afterEach(() => {
  cleanup();
});

// Stub `ListingCard` factory — only the fields the row actually renders matter.
// Fields we don't use stay null so a regression that surfaces them is caught.
function card(
  id: string,
  overrides: Partial<ListingCard> = {},
): ListingCard {
  return {
    id,
    lat: 52.5,
    lng: 13.4,
    price_warm_eur: null,
    price_cold_eur: null,
    nebenkosten_eur: null,
    kaution_eur: null,
    rooms: null,
    bedrooms: null,
    area_sqm: null,
    floor: null,
    floors_total: null,
    available_from: null,
    listing_type: null,
    district: null,
    title: null,
    address: null,
    wbs_required: null,
    is_furnished: null,
    has_balcony: null,
    has_kitchen: null,
    has_elevator: null,
    has_garden: null,
    heating: null,
    energy_consumption_kwh: null,
    lister_type: null,
    source_url: null,
    image_url: null,
    nearest_transit_line: null,
    walk_min_to_transit: null,
    nearest_park_name: null,
    nearest_park_m: null,
    noise_label: null,
    density_label: null,
    mss_status_label: null,
    mss_dynamics_label: null,
    similarity_score: null,
    ...overrides,
  };
}

function renderSidebar(
  overrides: Partial<React.ComponentProps<typeof BookmarkSidebar>> = {},
) {
  return render(
    <BookmarkSidebar
      open
      items={[]}
      status="ready"
      activeId={null}
      onClose={() => {}}
      onSelect={() => {}}
      onRemove={() => {}}
      {...overrides}
    />,
  );
}

describe("BookmarkSidebar", () => {
  it("renders a skeleton when loading and the list is empty", () => {
    const { container } = renderSidebar({ status: "loading" });
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("renders the empty state when ready and items is empty", () => {
    renderSidebar();
    expect(screen.getByText(/No bookmarks yet/i)).toBeTruthy();
  });

  it("renders the error message when fetch fails", () => {
    renderSidebar({ status: "error" });
    expect(screen.getByText(/Couldn't load bookmarks/i)).toBeTruthy();
  });

  it("renders rich rows with title + district + price", () => {
    renderSidebar({
      items: [
        card("a", {
          title: "Sunny corner flat",
          district: "Kreuzberg",
          price_warm_eur: 1450,
        }),
      ],
    });
    expect(screen.getByText("Sunny corner flat")).toBeTruthy();
    expect(screen.getByText("Kreuzberg")).toBeTruthy();
    // Locale-aware formatting: de-DE uses '.' as thousands separator.
    expect(screen.getByText(/1\.450 warm/i)).toBeTruthy();
  });

  it("falls back to 'Untitled' + 'Berlin' when the card lacks them", () => {
    renderSidebar({ items: [card("a")] });
    expect(screen.getByText("Untitled")).toBeTruthy();
    expect(screen.getByText("Berlin")).toBeTruthy();
  });

  it("highlights the active row via aria-current", () => {
    renderSidebar({
      items: [card("a", { title: "First" }), card("b", { title: "Second" })],
      activeId: "b",
    });
    const activeButton = screen.getByText("Second").closest("button");
    expect(activeButton?.getAttribute("aria-current")).toBe("page");
    const inactiveButton = screen.getByText("First").closest("button");
    expect(inactiveButton?.getAttribute("aria-current")).toBeNull();
  });

  it("calls onSelect with the row id when a bookmark is clicked", () => {
    const onSelect = vi.fn();
    renderSidebar({ items: [card("abc", { title: "Pick me" })], onSelect });
    fireEvent.click(screen.getByText("Pick me"));
    expect(onSelect).toHaveBeenCalledWith("abc");
  });

  it("clicking the remove-star calls onRemove and does NOT also fire onSelect", () => {
    const onSelect = vi.fn();
    const onRemove = vi.fn();
    renderSidebar({
      items: [card("abc", { title: "Pick me" })],
      onSelect,
      onRemove,
    });
    const star = screen.getByLabelText("Remove bookmark: Pick me");
    fireEvent.click(star);
    expect(onRemove).toHaveBeenCalledWith("abc");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("calls onClose when Escape is pressed while open", () => {
    const onClose = vi.fn();
    renderSidebar({ onClose });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("does NOT listen for Escape when closed", () => {
    const onClose = vi.fn();
    renderSidebar({ open: false, onClose });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("calls onClose when the backdrop is clicked", () => {
    const onClose = vi.fn();
    renderSidebar({ onClose });
    fireEvent.click(screen.getByTestId("bookmark-sidebar-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });
});
