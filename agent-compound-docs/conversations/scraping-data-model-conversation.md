# Scraping & Data Model Discussion

Conversation about designing a database for multi-source apartment scraping (Berlin Apartment AI Assistant project).

---

## Q1 — Scraping multiple websites with different fields: how to model it?

**User:**

I'm building a scraper that's gonna scrape multiple pages, multiple websites for apartment listings. And every website has different information, I guess it doesn't match 100%. And I need to design my database around this so... And I also want to be able to search this stuff. So what is a good technology and a good approach to make this work? so I have different websites with different information on them how should our data model look like should everything be relational and we have one type of table for each data source or so we just try to extract what's common between all of them and create a scheme out of that or do I need some kind of a no sql document store for each kind for each different website or is it just okay to temporarily store the scrape data in memory and then we structure it according to our design schema etc etc i'm guessing there are also many projects online that do this maybe not with apartments but also with apartments but maybe with other stuff too where lots of different data sources are scraped i'm wondering what are good solutions for this so please think for yourself but also search the web and github for similar projects and let's see what other people do and what other people think is the best solution for this

**Assistant:**

Good question, and the right one to ask before you commit any schema. Here's my take, then what's out there.

### What I'd actually do

For your shape of problem — multiple scrapers, drifting per-site fields, Berlin-only, Postgres already in the stack, modest data volume — the answer is **two layers in one Postgres database**, not a separate document store and not in-memory only.

**Layer 1 — `raw_listings` (one table, all sources):** the unmodified payload from each scraper, as JSONB, plus provenance columns. Roughly:

```
id              uuid pk
source          text          -- 'immoscout24', 'wg-gesucht', 'immowelt', ...
source_listing_id text         -- the site's own id
source_url      text
scraped_at      timestamptz
parser_version  text
raw             jsonb         -- whatever the scraper got, untouched
raw_html        text  null    -- optional, useful for re-parsing
content_hash    text          -- to detect changes between scrapes
unique (source, source_listing_id, scraped_at)
```

This is append-only. Every scrape becomes a new row. You never lose anything, and you can re-derive the canonical layer if your parser has a bug — without re-scraping (which matters because sites delete listings and may rate-limit/block you).

**Layer 2 — `listings` (canonical):** the cleaned, normalized record your chatbot and map actually query. Typed columns for everything you'll filter or rank by:

```
id              uuid pk
price_eur       numeric
warm_rent_eur   numeric null
sqm             numeric
rooms           numeric
floor           int null
year_built      int null
address         text
district        text
location        geography(Point, 4326)   -- PostGIS
posted_at       timestamptz
available_from  date null
description     text
description_embedding  vector(1536)      -- pgvector
search_tsv      tsvector                  -- German FTS
listing_type    text       -- 'rent' / 'sale' / 'wg'
attributes      jsonb      -- everything site-specific you didn't promote to a column
canonical_group_id  uuid null            -- for cross-source dedupe later
last_seen_at    timestamptz
is_active       bool
sources         jsonb      -- [{source, source_listing_id, raw_id}, ...]
```

The `attributes` JSONB at the bottom is the escape hatch — energy class from ImmoScout, flatmate count from WG-Gesucht, "Altbau" tags, whatever. Promote a field to a real column the moment you want to query it; leave the long tail in JSONB.

### Why not the other options

**"One table per source" / one schema per source.** Tempting, kills you later. Cross-source search becomes a `UNION` over N tables, every new source is a migration, and your chatbot has to know about each one. Same-shape entities should live in the same table; site differences belong in the `source` column and the `attributes` JSONB.

**"Just keep the common fields, drop the rest."** You're discarding signal that's hard to recover — re-scraping is slow, rate-limited, and listings disappear. Keep raw forever; project to canonical lossily, but reversibly.

**Document DB per source (Mongo, etc.).** Solves a problem you don't have. JSONB in Postgres gives you the schema-flexibility win without splitting your stack, and you keep transactional joins between raw, canonical, and your chat/conversation tables. Adding Mongo for an MVP is overkill and you've already got pgvector + (presumably) PostGIS available in Postgres.

**In-memory intermediate.** Fine for a one-shot ETL where the source is authoritative and re-readable. Not fine for scraped sites you can't re-query reliably. Persist raw before parsing.

This is the same pattern people call **bronze/silver (medallion)** in data engineering, or **staging + canonical** in classic warehousing — both layers in the same Postgres for your scale.

### Search — you don't need Elasticsearch

You already have the building blocks:

- **Structured filters** (price, rooms, district): plain SQL with B-tree indexes.
- **Geo** ("within 2 km of Hermannplatz"): add the PostGIS extension, one line in your init SQL. `geography(Point)` + GIST index.
- **Full-text** ("Altbau", "Balkon"): Postgres `tsvector` with the `german` config + GIN index.
- **Semantic** ("sunny apartment near a park, walkable to bars"): pgvector on a description embedding, hybrid-rerank with the structured filters.

Hybrid search = `WHERE` clauses on structured columns AND ranked by `embedding <=> query_embedding`. This is exactly what pgvector is good at and matches your CLAUDE.md ("PostgreSQL + pgvector for vector search, structured and geo data"). Add Elasticsearch only if you outgrow this — you won't, for Berlin.

### Deduplication across sources

The same flat will be on ImmoScout *and* WG-Gesucht. You'll want this eventually, but **don't build it in week one.** The pragmatic ladder:

1. **Within-source dedupe** (free): unique on `(source, source_listing_id)`. Updates become new raw rows but the same canonical row.
2. **Cross-source blocking + fuzzy match** (later): block on `(postal_code, round(sqm, 0), rooms)`, then score on address similarity + price proximity + description-embedding cosine. Group matches under a `canonical_group_id`. The standard playbook is blocking → matching → merging.
3. **Manual review queue** for the borderline cases — even mature companies do this.

Show the user "also listed on ImmoScout, Immowelt" once you have the group; until then, dupes are a nuisance, not a blocker.

### What others doing the same thing look like

For your exact market, the most useful reference is **[flathunters/flathunter](https://github.com/flathunters/flathunter)** — Python, covers ImmoScout24, Immowelt, WG-Gesucht, Kleinanzeigen, plus Italian sites. It's a notification bot, not a search engine, so it stores less than you'll need (SQLite/Firestore as a "have I already seen this id" log), but the **per-site crawler module structure** is exactly the right model for your ingestion service. Each site becomes its own crawler that yields normalized dicts; one shared post-processor.

Other repos worth a 10-minute skim:

- **[asmaier/ImmoSpider](https://github.com/asmaier/ImmoSpider)** — Scrapy-based ImmoScout24 crawler, dumps to CSV. Good reference for selectors and anti-bot quirks.
- **[m-rey/immoscraper](https://github.com/m-rey/immoscraper)** — ImmoScout + Immowelt → machine-readable output.
- **[venthur/immoscrapy](https://github.com/venthur/immoscrapy)** — minimal ImmoScout scraper, easy to read.
- **[clobatoc21/HouseRentingPriceScraper](https://github.com/clobatoc21/HouseRentingPriceScraper)** — Idealista + WG-Gesucht side-by-side, dataset published to Zenodo.

None of them publish a polished multi-source data model — most are personal projects that flatten everything to CSV. The "raw + canonical with provenance" pattern I described above is more typical of e-commerce aggregators and price-comparison engines than hobbyist scrapers, and it's the part you should carry over even though the scraper code can crib from flathunter.

### A few opinions, since you asked

- **Treat each scrape run as immutable.** Don't update raw rows; insert new ones. Your `is_active` and `last_seen_at` belong in canonical.
- **Version your parsers.** Store `parser_version` on the raw row. When you fix a parser, re-derive canonical from raw — don't re-scrape.
- **Don't put the listing photos in Postgres.** URLs in the canonical row, files in object storage (or just hotlink for MVP — not for production).
- **PostGIS is worth it now,** not later. Migration headaches if you bolt it on after you've stored coordinates as floats.
- **Write the canonical schema first**, *then* the scrapers — even if you only have one scraper. It forces you to define what "an apartment" means in your system, independent of any one site's vocabulary. Otherwise your "common schema" is just a union of whatever the first source happens to expose.

### Sources

- [The Medallion Architecture (Databricks)](https://www.databricks.com/blog/what-is-medallion-architecture)
- [Effective Data Scraping — raw vs canonical, provenance](https://medium.com/@rabbieinhorn/effective-data-scraping-98ae95f15744)
- [When to avoid JSONB in Postgres (Heap)](https://www.heap.io/blog/when-to-avoid-jsonb-in-a-postgresql-schema)
- [JSONB: PostgreSQL's secret weapon for flexible data modeling](https://medium.com/@richardhightower/jsonb-postgresqls-secret-weapon-for-flexible-data-modeling-cf2f5087168f)
- [System Design for Entity Resolution](https://www.sheshbabu.com/posts/system-design-for-entity-resolution/)
- [dedupeio/dedupe — fuzzy matching & record linkage](https://github.com/dedupeio/dedupe)
- [flathunters/flathunter — German rental scraper](https://github.com/flathunters/flathunter)
- [asmaier/ImmoSpider](https://github.com/asmaier/ImmoSpider)
- [m-rey/immoscraper](https://github.com/m-rey/immoscraper)
- [venthur/immoscrapy](https://github.com/venthur/immoscrapy)
- [clobatoc21/HouseRentingPriceScraper](https://github.com/clobatoc21/HouseRentingPriceScraper)

---

## Q2 — Why keep raw at all? Can Postgres do object storage?

**User:**

1. why do i need the raw stuff anyway - can't i just hold the raw scraped results in memory and turn them into the canonical record?
2. can i have object storage in postgres?

**Assistant:**

### 1) Why keep raw at all?

Because your interpretation of the data will change, but the data itself can't be re-fetched cheaply or reliably. Concretely, the things that bite you when you only keep canonical:

- **Your parser is wrong.** You will forget to extract Energieausweis class, or a CSS selector silently breaks and writes null where a value should be. With raw, you fix the parser and re-derive — `UPDATE listings FROM raw_listings`. Without raw, you have to re-scrape, except by then the listing is gone.
- **Listings disappear.** Apartments get rented and taken down within days. Data you didn't extract today is data you can never extract.
- **Schema grows.** Three months in, users start asking about heating type or "Altbau ja/nein." If you stored raw you backfill in one query. If you didn't, that history is lost.
- **Debugging.** "Why is this listing 0 €?" — with raw, you look at the JSONB and see what the site actually returned. Without it, you're guessing.
- **Auditing across sources.** Same flat on ImmoScout and Immowelt with different prices? You want both raw payloads to figure out which is stale.
- **Anti-bot insurance.** Re-scraping at scale gets you blocked, rate-limited, or IP-banned. Raw is one cheap copy that protects you from ever needing to.

And the cost is basically nothing. JSONB for Berlin-scale listings is megabytes-to-low-GBs. Compared to *any one* of the above happening once, it's free.

The one-liner: **raw is what the source said; canonical is what you decided it means. Your meaning will change — keep both.**

You can skip raw only if you can re-fetch the source at zero cost and the source itself is durable. Neither is true for scraping.

What you *can* do, if you want to keep it lighter: hold raw in memory during a scrape run, but **persist it to `raw_listings` before you parse**, not after. The "in memory" part is fine; the "never persisted" part is the trap.

### 2) Object storage in Postgres

Technically yes, three ways, and I'd push you to "don't":

- **`bytea` column** — a binary blob inside the row. Fine for small, low-volume things (a logo, a few placeholders). Painful at scale: bloats tables, kills row caching, makes `pg_dump` huge, makes `SELECT *` a footgun.
- **Large Objects (`pg_largeobject`)** — Postgres's built-in chunked-blob API, up to 4 TB per object, separate system table. Works, but has its own access semantics, doesn't fit nicely with HTTP serving, and you still pay for it in backups and replication.
- **Don't store bytes in Postgres at all.** Run **MinIO** as another Docker Compose service — S3-compatible, free, one container. Store the object key in Postgres, the bytes in MinIO. In production point at S3 / Cloudflare R2 / Backblaze B2 instead. This is the boring correct answer.

Why bytes-in-Postgres is bad at scale: backups go from MB to many GB, WAL volume balloons on inserts, replication lag explodes, and you can't put a CDN in front of a row — you'd have to proxy every image through your backend.

For *your* case specifically: scraped listings already host their photos on the source site. **Don't store the bytes at all in MVP — store the source URLs.** When that breaks (and it will, source URLs 403 eventually, especially for delisted apartments), that's the moment to add MinIO and a small "mirror images we care about" job. Not before.

So in your stack right now: Postgres holds the JSONB and the URL strings; no object store needed yet; MinIO goes in `docker-compose.yml` the day you decide you need to own the image bytes.

---

## Q3 — What is MinIO?

**User:**

i'm gonna use a VPS so no S3. What is MinIO?

**Assistant:**

MinIO is a self-hosted, S3-compatible object storage server. Open source, written in Go, runs as a single binary or — for your case — a Docker container alongside your other services. Think of it as "S3 you run on your VPS."

The killer feature is **API compatibility**: it speaks the exact AWS S3 protocol. So you write your code against `boto3` (or any S3 SDK), point it at `http://minio:9000` instead of `s3.amazonaws.com`, and that's it. If you ever migrate to real S3 / Cloudflare R2 / Backblaze B2 later, you change one URL — no code rewrite. That's why it's the standard "I'm self-hosting" answer.

How it actually stores things: files on a disk volume on your VPS. Buckets are directories, objects are files (plus some metadata). It's not magic. You give it a directory, it gives you S3 semantics over that directory: buckets, objects, signed URLs, lifecycle rules, a web console at port 9001 for browsing, etc.

In your `docker-compose.yml` it's roughly:

```yaml
minio:
  image: minio/minio:latest
  command: server /data --console-address ":9001"
  environment:
    MINIO_ROOT_USER: ${MINIO_ROOT_USER}
    MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
  volumes:
    - minio-data:/data
  # no `ports:` if only your backend talks to it — keep it internal
```

Then your backend (or ingestion service) talks to it like S3:

```python
import boto3
s3 = boto3.client(
    "s3",
    endpoint_url="http://minio:9000",
    aws_access_key_id=os.environ["MINIO_ROOT_USER"],
    aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
)
s3.upload_fileobj(file, "listings", "abc123/photo-01.jpg")
```

**What you take on by self-hosting it on a VPS:**

- Disk capacity is bounded by your VPS — monitor `df`, set alerts. Apartment images add up.
- Backups are your problem. The standard tool is `mc mirror` (MinIO's CLI) to a second location, or just `rsync` the data dir to off-box storage.
- If the VPS dies and you have no off-box backup, the objects die with it.

**What you get vs. cloud S3:**

- Zero egress fees, no per-GB-month bill.
- Same API contract — easy to migrate later if you outgrow the VPS.
- Single point of failure unless you set up multi-node MinIO (don't, not at this scale).

For your MVP I'd still hold off — hotlink images from the source sites until that breaks. When it does, MinIO is a 20-line Compose addition, a bucket called `listings`, and a `photo_url` column in your canonical table that holds either the source URL or your MinIO key. The decision is reversible.

One concrete thing worth doing now even without MinIO: **write your photo-handling code against an `s3.upload_fileobj`-shaped interface from day one**, even if the implementation is just "no-op, store the source URL." That way adding MinIO later is wiring, not refactoring.

---

## Q4 — Tradeoffs: Postgres `bytea` vs MinIO

**User:**

what are the tradeoffs between postgres byte storage vs minIO?

**Assistant:**

Quick verdict up front: for anything you'd send to a browser at scale, MinIO wins on almost every axis except operational simplicity. For tiny, rarely-served files, Postgres `bytea` is fine. The honest tradeoff is "one service vs. two" against "everything else."

**Backups.** This is usually the deciding factor. With `bytea`, your `pg_dump` includes every byte of every image. A DB that should be 200 MB of structured data becomes 80 GB of photos, and dump/restore goes from seconds to hours. With MinIO, your DB stays small and fast to back up; you back up the object store separately (`mc mirror` or `rsync` to off-box storage). Two backup jobs, but each is fast and independent.

**Replication / WAL.** Every `bytea` insert writes the whole blob into the write-ahead log. If you ever set up a replica or point-in-time recovery, you're shipping all those bytes through WAL. MinIO doesn't touch your DB at all, so WAL stays lean.

**Serving.** With `bytea`, every image GET goes: browser → Nginx → FastAPI → Postgres → back. Postgres connection held for the duration. You can't put a CDN in front, you can't do byte-range requests without app code, you can't stream cleanly. With MinIO, your backend hands the browser a signed URL and steps out — Nginx (or directly MinIO) serves the bytes. CDN-compatible. Range requests work natively. Multipart uploads work natively.

**Connection pool pressure.** Postgres has finite connections (typically tens, not thousands). Serving images through `bytea` ties up a connection per image fetch. One bot crawling your photos can starve your chat traffic. MinIO has its own concurrency model and doesn't compete with your DB.

**Storage efficiency.** Postgres TOAST-compresses large `bytea` values automatically, which is fine. But the row-oriented storage means every `SELECT * FROM listings` is heavier, autovacuum has more to do, and table bloat from updates is more painful. MinIO stores each object as a file on disk — boring, predictable, easy to reason about.

**Transactional consistency.** This is the one place `bytea` actually wins. With Postgres-only, "image saved" and "row inserted" are atomic — rollback works, no orphans possible. With MinIO + Postgres you have two systems and need a pattern: write to MinIO first, then `INSERT` the row referencing the key; periodically GC orphan objects whose keys aren't referenced. Standard pattern, not a big deal, but it's real complexity you don't have with `bytea`.

**Failure isolation.** Counterintuitively, MinIO is *better* here. If MinIO falls over, your chat and search keep working — only images break. If Postgres falls over with `bytea`, everything is down anyway, but recovery is now slower because the dump is huge.

**Operational surface.** `bytea` adds zero new things to operate. MinIO adds: a container, a volume, root credentials, a backup job, disk-space monitoring, and bucket/lifecycle config. For a solo VPS this is real but not heavy — call it a half-day of one-time setup plus ongoing "watch the disk fill up."

**Migration path.** `bytea` → real object storage later means writing a migration script (read every row, PUT to bucket, store key, NULL the bytea). Doable but a few hours of careful work. MinIO → AWS S3 / Cloudflare R2 later means: change one endpoint URL, `mc mirror` to copy data over. That's it. So MinIO buys you optionality.

**Cost.** Identical on a VPS — both consume the same disk. MinIO doesn't add hosting cost on a VPS, only complexity.

**Read latency for tiny files.** `bytea` can be marginally faster than MinIO for very small files because it's one round trip to one service. Almost never matters in practice.

**The cutover, in plain terms.** Use `bytea` if you have under a few hundred small files (icons, the occasional uploaded PDF), they're rarely fetched, and they need to be transactionally tied to a row. Use MinIO the moment you're storing user-facing images at scale, anything served via HTTP, anything with growth ahead of it.

**For your case specifically.** Berlin has roughly 10–50k active rental listings at any time, each with maybe 5–15 photos, each photo 100 KB–1 MB. That's order-of-magnitude 100 GB of bytes if you mirror everything. Putting that in Postgres would dominate your DB and make every backup a chore. So: when you do start owning image bytes, MinIO is the right answer.

But the real recommendation hasn't changed from before: **don't store image bytes at all in MVP**, hotlink from the source. Add MinIO when hotlinking breaks (delisted listings 404, hotlink protection, etc.), not before. The decision is cheap to defer.
