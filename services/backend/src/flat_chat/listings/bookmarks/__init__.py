"""Bookmarks — per-user saved listings (app-schema).

Grouped as a subpackage rather than `bookmarks_*`-prefixed modules because the
`listings/` package is flat-by-concern (`models.py`, `service.py`, `context.py`
…) with no domain prefixes. Re-exports `Bookmark` + `BookmarkService` so import
sites stay short and importing the package registers the ORM (needed for Alembic
metadata autogenerate / drift detection).
"""

from flat_chat.listings.bookmarks.models import Bookmark
from flat_chat.listings.bookmarks.service import BookmarkService

__all__ = ["Bookmark", "BookmarkService"]
