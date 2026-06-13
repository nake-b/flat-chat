# Icebox

Code that's intentionally **deferred but not deleted** — not wired into
the pipeline, not imported by any other module, kept here so a future
contributor doesn't have to re-derive it from scratch when reviving it.

If you're looking at something here and wondering whether to use it:
**don't import directly.** Read the per-folder README, decide whether the
work is worth resuming, then copy the relevant pieces into the live
codebase (`extract/`, `transform/`, `load/`, plus a new alembic migration
and a `datasets.yaml` entry).

## What's here

| Folder | Why iceboxed | Decided at |
|---|---|---|
| `population_density_change_entw/` | Single-year `_entw` change table — signal too noisy at 1y granularity for an apartment search; the absolute density in `population_density_2025` is what the agent uses today | PR #8 review |

## Reviving an iceboxed dataset

1. Copy `migration_block.py` into a fresh alembic revision body.
2. Copy the entry from `transform.py` into `geo_context/transform/aliases.py`.
3. Add a `datasets.yaml` entry with `enabled: true, status: wip`.
4. Run `python -m geo_context.run --only <key>` to smoke-test.
5. Delete the icebox folder once you're satisfied.
