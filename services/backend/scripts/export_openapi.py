"""Export the FastAPI OpenAPI schema to a static file.

No running server required — imports the app and calls `app.openapi()`
directly. Relies on the "no side effects at module import" rule (setup lives
in the lifespan), so this needs no DB, no network, no env beyond what import
already tolerates.

Run from the backend package root:

    uv run python scripts/export_openapi.py

Writes `docs/openapi.json` (pretty-printed, stable key order so diffs are
minimal). Commit the result — it's the documentation artifact reviewers read
without spinning up the stack.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# `config.py` instantiates `Settings()` at import, which requires these fields.
# They are never *used* to build the schema (no DB/network is touched by
# `app.openapi()`), so placeholder values are safe. `setdefault` lets a real
# environment win when present.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://export:export@localhost/export"
)
os.environ.setdefault("JWT_SECRET", "openapi-export-placeholder")

from flat_chat.main import app  # noqa: E402  (import after env defaults)

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def main() -> None:
    schema = app.openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    paths = len(schema.get("paths", {}))
    print(f"wrote {OUTPUT} ({paths} paths, OpenAPI {schema.get('openapi')})")


if __name__ == "__main__":
    main()
