import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ConversationSummary } from "../api/conversations";
import { ConversationSidebar } from "./ConversationSidebar";

// vitest doesn't auto-call @testing-library/react's `cleanup` between tests
// without an explicit setup file; without this, a mounted Escape-listener from
// a prior test leaks across into later renders.
afterEach(() => {
  cleanup();
});

function row(
  id: string,
  title: string | null = null,
  updated: string = "2026-06-27T12:04:00",
): ConversationSummary {
  return {
    id,
    title,
    created_at: updated,
    updated_at: updated,
  };
}

// Render helper so individual tests only override the props they care about
// — keeps the test bodies focused on the assertion.
function renderSidebar(
  overrides: Partial<React.ComponentProps<typeof ConversationSidebar>> = {},
) {
  return render(
    <ConversationSidebar
      open
      items={[]}
      status="ready"
      activeId={null}
      onClose={() => {}}
      onSwitch={() => {}}
      onNewChat={() => {}}
      onDelete={() => {}}
      {...overrides}
    />,
  );
}

describe("ConversationSidebar", () => {
  it("renders a skeleton when loading and the list is empty", () => {
    const { container } = renderSidebar({ status: "loading" });
    // Skeleton bars are aria-hidden, identifiable by the pulse animation class.
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });

  it("renders the empty state when ready and items is empty", () => {
    renderSidebar();
    expect(screen.getByText(/No conversations yet/i)).toBeTruthy();
  });

  it("renders the error message when fetch fails and the list is empty", () => {
    renderSidebar({ status: "error" });
    expect(screen.getByText(/Couldn't load conversations/i)).toBeTruthy();
  });

  it("renders rows with the title and falls back to 'Untitled' when null", () => {
    renderSidebar({ items: [row("a", "Kreuzberg 2-room search"), row("b", null)] });
    expect(screen.getByText("Kreuzberg 2-room search")).toBeTruthy();
    expect(screen.getByText("Untitled")).toBeTruthy();
  });

  it("highlights the active conversation via aria-current", () => {
    renderSidebar({
      items: [row("a", "First"), row("b", "Second")],
      activeId: "b",
    });
    const activeButton = screen.getByText("Second").closest("button");
    expect(activeButton?.getAttribute("aria-current")).toBe("page");
    const inactiveButton = screen.getByText("First").closest("button");
    expect(inactiveButton?.getAttribute("aria-current")).toBeNull();
  });

  it("calls onSwitch with the row id when a conversation is clicked", () => {
    const onSwitch = vi.fn();
    renderSidebar({ items: [row("abc", "Pick me")], onSwitch });
    fireEvent.click(screen.getByText("Pick me"));
    expect(onSwitch).toHaveBeenCalledWith("abc");
  });

  it("calls onNewChat when '+ New chat' is clicked", () => {
    const onNewChat = vi.fn();
    renderSidebar({ onNewChat });
    fireEvent.click(screen.getByText(/\+ New chat/i));
    expect(onNewChat).toHaveBeenCalled();
  });

  it("calls onClose when Escape is pressed while open", () => {
    const onClose = vi.fn();
    renderSidebar({ onClose });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("does NOT listen for Escape when closed (no stray onClose)", () => {
    const onClose = vi.fn();
    renderSidebar({ open: false, onClose });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("calls onClose when the backdrop is clicked", () => {
    const onClose = vi.fn();
    renderSidebar({ onClose });
    fireEvent.click(screen.getByTestId("sidebar-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });

  // --- Delete flow ---------------------------------------------------------

  it("clicking the trash icon opens the confirm dialog with the row's title", () => {
    renderSidebar({ items: [row("abc", "Kreuzberg search")] });
    const trash = screen.getByLabelText("Delete conversation: Kreuzberg search");
    fireEvent.click(trash);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeTruthy();
    expect(dialog.textContent).toContain("Delete this conversation?");
    // The dialog body should quote the title so the user can verify which row.
    expect(dialog.textContent).toContain("Kreuzberg search");
  });

  it("trash click does NOT also fire onSwitch (stopPropagation correctness)", () => {
    const onSwitch = vi.fn();
    renderSidebar({ items: [row("abc", "Kreuzberg search")], onSwitch });
    const trash = screen.getByLabelText("Delete conversation: Kreuzberg search");
    fireEvent.click(trash);
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("confirming the dialog calls onDelete with the row id and closes the dialog", () => {
    const onDelete = vi.fn();
    renderSidebar({ items: [row("abc", "Kreuzberg search")], onDelete });
    fireEvent.click(screen.getByLabelText("Delete conversation: Kreuzberg search"));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledWith("abc");
    // Dialog closes after confirm.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cancelling the dialog does NOT call onDelete and closes the dialog", () => {
    const onDelete = vi.fn();
    renderSidebar({ items: [row("abc", "Kreuzberg search")], onDelete });
    fireEvent.click(screen.getByLabelText("Delete conversation: Kreuzberg search"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Esc inside the dialog cancels and does NOT also close the sidebar", () => {
    const onClose = vi.fn();
    const onDelete = vi.fn();
    renderSidebar({
      items: [row("abc", "Kreuzberg search")],
      onClose,
      onDelete,
    });
    fireEvent.click(screen.getByLabelText("Delete conversation: Kreuzberg search"));
    fireEvent.keyDown(window, { key: "Escape" });
    // Dialog dismissed.
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onDelete).not.toHaveBeenCalled();
    // Sidebar must NOT have received the Escape — the modal owns it.
    expect(onClose).not.toHaveBeenCalled();
  });
});
