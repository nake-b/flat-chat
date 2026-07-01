import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountMenu } from "./AccountMenu";
import { useAuth } from "../hooks/useAuth";

const logout = vi.fn();

beforeEach(() => {
  logout.mockReset();
  useAuth.setState({
    status: "authed",
    // Minimal AuthUser shape for the header; cast keeps the test decoupled
    // from the full user model.
    user: { id: "u1", email: "dev@flatchat.dev" } as never,
    logout,
  });
});

afterEach(() => {
  cleanup();
});

describe("AccountMenu", () => {
  it("shows the email on the trigger and hides the menu until clicked", () => {
    render(<AccountMenu />);
    expect(screen.getAllByText("dev@flatchat.dev").length).toBeGreaterThan(0);
    // Menu closed → Sign out not in the DOM.
    expect(screen.queryByRole("menuitem", { name: "Sign out" })).toBeNull();
  });

  it("opens on click and fires logout from Sign out", () => {
    render(<AccountMenu />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(logout).toHaveBeenCalledOnce();
  });

  it("closes on Escape", () => {
    render(<AccountMenu />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes on an outside click", () => {
    render(<AccountMenu />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
