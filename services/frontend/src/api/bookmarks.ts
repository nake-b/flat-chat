// Per-user saved listings. Plain fetch, typed return — mirrors api/conversations.ts.
// Bookmarks live on HTTP REST (no agent involvement); add/remove are idempotent
// so optimistic UI can fire freely.

import type { ListingCard } from "../state/SessionState";

export async function listBookmarkIds(signal?: AbortSignal): Promise<string[]> {
  const res = await fetch("/api/bookmarks/ids", { signal });
  if (!res.ok) {
    throw new Error(`Failed to list bookmark ids: ${res.status}`);
  }
  return (await res.json()) as string[];
}

export async function listBookmarks(
  signal?: AbortSignal,
): Promise<ListingCard[]> {
  const res = await fetch("/api/bookmarks", { signal });
  if (!res.ok) {
    throw new Error(`Failed to list bookmarks: ${res.status}`);
  }
  return (await res.json()) as ListingCard[];
}

export async function addBookmark(listingId: string): Promise<void> {
  const res = await fetch(`/api/bookmarks/${encodeURIComponent(listingId)}`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(`Failed to add bookmark: ${res.status}`);
  }
}

export async function removeBookmark(listingId: string): Promise<void> {
  const res = await fetch(`/api/bookmarks/${encodeURIComponent(listingId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(`Failed to remove bookmark: ${res.status}`);
  }
}
