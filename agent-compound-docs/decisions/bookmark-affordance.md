# Bookmark affordance, menu, and transit presentation

Status: accepted (2026-07-01). Applies to the bookmarks feature
(`feat/bookmarks-and-previous-conversations`).

## Heart, not star, for "save"

The per-card save control and the bookmarks nav entry use a **heart**, not a
star.

- **Why**: property-search apps converge on the heart for "save / favourite" —
  Zillow, ImmobilienScout24, and Airbnb all use it. A **star** reads as a
  *rating* ("how good is this?"), which is the wrong mental model for a personal
  save list. Heart = "I want this one," which is exactly the bookmark intent.
- Filled heart = saved, in Berliner Rot (`text-red`); outline grey when not.
  Component: `components/BookmarkHeart.tsx` (was `BookmarkStar.tsx`).

## Header: a real two-row menu

The header previously had three ad-hoc controls (a hamburger, a bookmarks
button, and a separate email/Sign-out strip) that read as an accidental menu.
Replaced with a deliberate **two-row header** (`components/ChatPane.tsx`):

1. **Row 1** — centered wordmark + tagline.
2. **Row 2 (utility bar)** — nav icons on the left: **☰** (conversations) and a
   **home-with-heart** glyph (bookmarks, echoing the red save-heart); an
   **account dropdown** on the right (`components/AccountMenu.tsx`) holding the
   email, `Settings` (disabled — "soon"), and `Sign out`.

- **Why this shape**: conversations + bookmarks are frequent navigation, so they
  stay one click (icons), while rare account actions tuck into a dropdown. Chosen
  interactively over a single unified hamburger menu (which makes nav two clicks)
  and over icon-only / words-only one-row variants (words + a centered wordmark
  collide in the narrow chat column).
- No dropdown library is in `package.json`; `AccountMenu` is hand-rolled
  following `ConfirmDialog`'s Escape-to-close pattern + an outside-click close,
  with `role="menu"` / `menuitem`.

## Transit line: rail-preferred + labelled

Cards and bookmark rows show the nearest transit line. Two fixes:

- **Ranking** — the projection used `nearest_transit_lines[0]`, i.e. whatever
  sorted first, so a bus (e.g. `245`) could win over a nearby U-Bahn.
  `listings/labels.py:primary_transit_line` now prefers rail (U/S) > tram > day
  bus > night bus, via `transit_mode(line)` (a prefix heuristic — gold stores
  only labels, not GTFS route types). Used in `listings/projection.py`.
- **Presentation** — a bare `245` with an unexplained `14 min` was confusing.
  The frontend (`lib/transit.ts`) now renders a mode icon and explicit wording:
  compact `🚇 U7 · 3min` on result cards, detailed `🚇 U7 · 8 min walk` on
  bookmark rows. The prefix heuristic is mirrored on the frontend for the icon;
  numeric-tram/bus ambiguity is accepted (the label is always shown).

## Bookmark rows are intentionally more detailed than result cards

The result cards (`CardStrip`) are compact, vertical, and space-constrained. The
bookmark panel is wide (covers the chat column), so `BookmarkSidebarItem` is a
**landscape detail row**: larger thumbnail + title + address + a full price
block (warm + cold/Nebenkosten) + a `rooms · bedrooms · m²` meta line + a
`min walk` transit line + a generous chip row (park / noise / density /
inside-ring / floor / amenities / availability). It borrows the result card's
chip vocabulary and typography for consistency, but the horizontal, detail-heavy
layout keeps it visually distinct. This asymmetry is deliberate — the strip is
for scanning many; the bookmark row is for reviewing a few you already chose.

## Related

- Bookmark **snapshot** (showing a saved listing that has since been delisted)
  is tracked separately — see issue #51. Today bookmarks re-project live from
  `world.listings`, so a delisted listing simply drops out of the panel.
- Data-flow: `agent-vs-http-data-flow.md`. Bookmark card projection reuses the
  shared tier-2 `listings/projection.py` (no separate bookmark card model).
