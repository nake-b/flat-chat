// Shared Postgres helpers for the Node scrapers.
//
// Run `npm install` inside this `_lib/` directory once so the `pg` dependency
// is available to scrapers that `require('../_lib/db')`.

const path = require('node:path');
const fs = require('node:fs');

// Auto-load .env from the nearest ancestor directory that contains one, so
// individual scrapers don't need to prefix their `npm run` commands with
// `DATABASE_URL=...`. dotenv does NOT override already-set env vars, so
// docker containers still receive their compose-injected DATABASE_URL.
function findDotenv(startDir) {
  let dir = startDir;
  while (true) {
    const candidate = path.join(dir, '.env');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}
const envPath = findDotenv(process.cwd()) || findDotenv(__dirname);
if (envPath) require('dotenv').config({ path: envPath });

const { Pool } = require('pg');

let _pool;

function getPool() {
  if (!_pool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error('DATABASE_URL is not set');
    }
    _pool = new Pool({ connectionString });
  }
  return _pool;
}

async function closePool() {
  if (_pool) {
    await _pool.end();
    _pool = undefined;
  }
}

// Upsert a card-tier row.
// Returns the iron_cards.id (uuid).
async function upsertIronCard(pool, { sourceName, externalId, detailUrl, sourceUrl, data, scrapedAt }) {
  const sql = `
    INSERT INTO iron_cards (source_name, external_id, detail_url, source_url, data, scraped_at)
    VALUES ($1, $2, $3, $4, $5::jsonb, $6)
    ON CONFLICT ON CONSTRAINT uq_iron_source_external DO UPDATE SET
      detail_url = EXCLUDED.detail_url,
      source_url = EXCLUDED.source_url,
      data       = EXCLUDED.data,
      scraped_at = EXCLUDED.scraped_at
    RETURNING id
  `;
  const params = [
    sourceName,
    String(externalId),
    detailUrl,
    sourceUrl,
    JSON.stringify(data),
    scrapedAt,
  ];
  const res = await pool.query(sql, params);
  return res.rows[0].id;
}

// Fetch iron rows that have not yet been detail-scraped, freshest first.
//
// `scraped_at` carries NO usable freshness signal *within* a crawl: the card
// scraper re-persists the full cumulative card list on every page, so all of
// a crawl's rows get re-stamped with the final persist's timestamp (they land
// within ~1ms of each other). Worse, at finer resolution that timestamp is if
// anything inversely correlated with freshness — deeper, staler pages are
// scraped last. So we must NOT let `scraped_at` dominate ordering.
//
// The real freshness rank lives in the card's search_page + card_index: with
// the site sorted newest-first (sortierung:neuste), page 1 / index 0 is the
// newest ad. So we bucket `scraped_at` to the day (newest crawl-day first),
// walk that day's results top-down by page then index, and only fall back to
// `scraped_at DESC` to break ties between two crawls on the same day. Sources
// whose card payload lacks these keys degrade to day + crawl order (NULLS LAST).
async function fetchPendingIronCards(pool, sourceName, limit = null) {
  const orderBy = `
        ORDER BY scraped_at::date DESC,
                 NULLIF(data->'raw_payload'->'card'->>'search_page', '')::int ASC NULLS LAST,
                 NULLIF(data->'raw_payload'->'card'->>'card_index', '')::int ASC NULLS LAST,
                 scraped_at DESC`;
  const sql = limit
    ? `SELECT id, external_id, detail_url, source_url, data
         FROM iron_cards
        WHERE source_name = $1 AND detail_scraped_at IS NULL
        ${orderBy}
        LIMIT $2`
    : `SELECT id, external_id, detail_url, source_url, data
         FROM iron_cards
        WHERE source_name = $1 AND detail_scraped_at IS NULL
        ${orderBy}`;
  const params = limit ? [sourceName, limit] : [sourceName];
  const res = await pool.query(sql, params);
  return res.rows;
}

// Upsert a detail-tier row.
async function upsertRawListing(pool, { sourceName, externalId, sourceUrl, data, scrapedAt, ironCardId }) {
  const sql = `
    INSERT INTO raw_listings (source_name, external_id, source_url, data, scraped_at, iron_card_id)
    VALUES ($1, $2, $3, $4::jsonb, $5, $6)
    ON CONFLICT ON CONSTRAINT uq_raw_source_external DO UPDATE SET
      source_url   = EXCLUDED.source_url,
      data         = EXCLUDED.data,
      scraped_at   = EXCLUDED.scraped_at,
      iron_card_id = EXCLUDED.iron_card_id
    RETURNING id
  `;
  const params = [
    sourceName,
    String(externalId),
    sourceUrl,
    JSON.stringify(data),
    scrapedAt,
    ironCardId,
  ];
  const res = await pool.query(sql, params);
  return res.rows[0].id;
}

// Flip the cursor on an iron row.
async function markIronCardDetailed(pool, ironCardId) {
  await pool.query(
    'UPDATE iron_cards SET detail_scraped_at = now() WHERE id = $1',
    [ironCardId]
  );
}

module.exports = {
  getPool,
  closePool,
  upsertIronCard,
  fetchPendingIronCards,
  upsertRawListing,
  markIronCardDetailed,
};
