// Phase 2: visit each pending card from iron_cards and write the detail-tier
// record straight into raw_listings (bronze). Once a listing is captured the
// matching iron row is flipped via detail_scraped_at = now() so the next run
// resumes naturally on whatever remains.
//
// Extraction strategy: housinganywhere server-renders the full listing into
// `window.__PRELOADED_STATE__.listing` — entity (price/costs in CENTS,
// facilities map, geo, stay limits, bookable periods, photo URLs), advertiser
// data, and tenant reviews. The DOM is only a fallback (LD+JSON Accommodation
// block + og: meta + data-test-locator selectors) for pages where the state
// is missing or shaped differently.

const path = require('node:path');
const vanillaPuppeteer = require('puppeteer');
const db = require('scraper-lib');
const stealth = require('scraper-lib/stealth');

// puppeteer-extra + stealth plugin, wrapping our own puppeteer engine.
const puppeteer = stealth.makeStealthPuppeteer(vanillaPuppeteer);
const {
  DEFAULT_USER_AGENT,
  DEFAULT_TIMEOUT_MS,
  sleep,
  acceptConsent,
  preparePage,
  dumpDebugArtifacts,
  detectChallenge,
} = require('./lib');

const LISTING_SOURCE = 'housinganywhere';
// null → rotate a current Chrome UA per run (see _lib/stealth.js).
const USER_AGENT = process.env.USER_AGENT || null;

const DEBUG_DIR = path.resolve(process.env.DEBUG_DIR || __dirname);
const MAX_LISTINGS = process.env.MAX_LISTINGS ? Number.parseInt(process.env.MAX_LISTINGS, 10) : null;
const HEADLESS = process.env.HEADLESS !== 'false';
// Conservative defaults matching the kleinanzeigen detail scraper: jittered
// 20–30s between listings + a 5-minute pause every 40 listings.
const MIN_DELAY_MS = Number.parseInt(process.env.MIN_DELAY_MS || '20000', 10);
const MAX_DELAY_MS = Number.parseInt(process.env.MAX_DELAY_MS || '30000', 10);
const BATCH_SIZE = Number.parseInt(process.env.BATCH_SIZE || '40', 10);
const BATCH_PAUSE_MS = Number.parseInt(process.env.BATCH_PAUSE_MS || '300000', 10);
const PAGE_TIMEOUT = DEFAULT_TIMEOUT_MS;

function printBanner(targets) {
  console.log('');
  console.log('housinganywhere.com scraper (detail pages)');
  console.log('==========================================');
  console.log(`Input:         iron_cards (source=${LISTING_SOURCE}, detail_scraped_at IS NULL)`);
  console.log(`Output:        raw_listings table (source=${LISTING_SOURCE})`);
  console.log(`To visit:      ${targets.length}`);
  console.log(`Max listings:  ${MAX_LISTINGS ?? 'unbounded'}`);
  console.log(`Delay:         ${MIN_DELAY_MS}-${MAX_DELAY_MS}ms (batch ${BATCH_SIZE}, pause ${BATCH_PAUSE_MS}ms)`);
  console.log(`Headless:      ${HEADLESS}`);
  console.log('');
}

function buildTargetsFromIron(rows) {
  const targets = [];
  for (const row of rows) {
    const url = row.detail_url;
    const id = row.external_id;
    if (!url || id == null) continue;
    targets.push({
      id,
      url,
      card: row.data || null,
      ironCardId: row.id,
    });
    if (MAX_LISTINGS != null && targets.length >= MAX_LISTINGS) break;
  }
  return targets;
}

function randomDelay() {
  const lo = Math.min(MIN_DELAY_MS, MAX_DELAY_MS);
  const hi = Math.max(MIN_DELAY_MS, MAX_DELAY_MS);
  return lo + Math.floor(Math.random() * (hi - lo + 1));
}

// Browser-side scrape — runs entirely in the page context.
// Returns a plain object; everything that fails returns null/{} without throwing.
async function scrapeDetail(page, expectedId, canonicalUrl) {
  return page.evaluate(
    (expectedIdArg, canonicalUrlArg) => {
      const clean = (text) => (text || '').replace(/\s+/g, ' ').trim();

      const result = {
        externalId: expectedIdArg,
        canonicalUrl: canonicalUrlArg,
        url: window.location.href,
      };

      // ---- Primary: the server-rendered Redux state -----------------------
      // entity.price and entity.costs.* money values are EURO CENTS
      // (86500 = €865). Stored raw in bronze; silver divides by 100.
      const listing = window.__PRELOADED_STATE__?.listing || null;
      const toPlain = (value) => {
        try {
          return value == null ? null : JSON.parse(JSON.stringify(value));
        } catch {
          return null;
        }
      };
      result.entity = listing?.entity?.id ? toPlain(listing.entity) : null;
      // PRIVACY: keep only the advertiser *type* (private/agency). The full
      // advertiser object (name, photo, profile, contact) is not collected.
      // See services/ingestion/src/pii.py.
      result.advertiser = { type: toPlain(listing?.advertiser?.data)?.type ?? null };
      result.overallRating = toPlain(listing?.tenantReviews?.overallRating);
      result.extractionTier = result.entity ? 'preloaded_state' : 'fallback';

      // ---- LD+JSON Accommodation block ------------------------------------
      result.ldjson = (() => {
        for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
          try {
            const d = JSON.parse(s.textContent);
            const type = d && d['@type'];
            if (type === 'Accommodation' || (Array.isArray(type) && type.includes('Accommodation'))) {
              return d;
            }
          } catch {
            // try next block
          }
        }
        return null;
      })();

      // ---- og: meta --------------------------------------------------------
      result.ogMeta = (() => {
        const meta = {};
        document.querySelectorAll('meta[property^="og:"]').forEach((m) => {
          const prop = m.getAttribute('property');
          const content = m.getAttribute('content');
          if (prop && content) meta[prop] = content;
        });
        return meta;
      })();

      // ---- DOM fallback — only worth collecting when the state is missing --
      result.domFallback = result.entity
        ? null
        : (() => {
            const text = (sel) => clean(document.querySelector(sel)?.textContent) || null;
            return {
              title: text('h1'),
              priceText: text('[data-test-locator="Listing/ListingInfo/Price"]'),
              priceSubtitle: text('[data-test-locator="Listing/ListingInfo/Price/Subtitle/Link"]'),
              street: text('[data-test-locator="Listing/ListingInfo/street"]'),
              propertySize: text('[data-test-locator="HighlightsTags/Property"]'),
              bedroomCount: text('[data-test-locator="HighlightsTags/BedroomCount"]'),
              furnished: text('[data-test-locator="HighlightsTags/Furnished"]'),
              freePlaces: text('[data-test-locator="HighlightsTags/FreePlaces"]'),
              description: text('[data-test-locator="Listing/ListingDescription"]'),
              imageUrls: [
                ...new Set(
                  [...document.querySelectorAll('[data-test-locator^="Listing/ImageSlider"] img')]
                    .map((img) => img.getAttribute('src'))
                    .filter(Boolean)
                ),
              ],
            };
          })();

      return result;
    },
    expectedId,
    canonicalUrl
  );
}

function buildOutputRow(card, detail, scrapedAt) {
  return {
    listing_source: LISTING_SOURCE,
    id: detail?.externalId || card?.id,
    scrapeUrl: detail?.url || detail?.canonicalUrl || card?.canonicalUrl || card?.url,
    scrapedAt,
    dump: {
      card,
      ...detail,
    },
  };
}

async function run() {
  const pool = db.getPool();
  const pendingRows = await db.fetchPendingIronCards(pool, LISTING_SOURCE);
  const targets = buildTargetsFromIron(pendingRows);
  printBanner(targets);

  if (targets.length === 0) {
    console.log('Nothing to do — no pending iron cards.');
    await db.closePool();
    return;
  }

  const launchOptions = {
    headless: HEADLESS,
    defaultViewport: { width: 1365, height: 900 },
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled',
      '--lang=en-US,en',
    ],
  };
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  console.log('Launching browser...');
  const browser = await puppeteer.launch(launchOptions);

  const stats = { ok: 0, fallback: 0, errors: 0 };

  try {
    const page = await browser.newPage();
    await preparePage(page, { userAgent: USER_AGENT, timeoutMs: PAGE_TIMEOUT });

    let consentTried = false;

    for (let i = 0; i < targets.length; i += 1) {
      const target = targets[i];
      const label = `[${i + 1}/${targets.length}] id=${target.id}`;
      console.log(`\n${label} ${target.url}`);

      try {
        await page.goto(target.url, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT });
      } catch (error) {
        console.warn(`  navigation failed: ${error.message}`);
        await dumpDebugArtifacts(page, DEBUG_DIR, `detail-${target.id}-nav`);
        stats.errors += 1;
        continue;
      }

      if (!consentTried) {
        await acceptConsent(page);
        consentTried = true;
      }

      const challenge = await detectChallenge(page);
      if (challenge) {
        console.warn(`  challenge detected (${challenge}); aborting run`);
        await dumpDebugArtifacts(page, DEBUG_DIR, `detail-${target.id}-${challenge}`);
        break;
      }

      try {
        await page.waitForFunction(
          () =>
            (window.__PRELOADED_STATE__ &&
              window.__PRELOADED_STATE__.listing &&
              window.__PRELOADED_STATE__.listing.entity) ||
            document.querySelector('script[type="application/ld+json"]'),
          { timeout: PAGE_TIMEOUT }
        );
      } catch {
        console.warn('  detail markers not found — page may have a different layout');
        await dumpDebugArtifacts(page, DEBUG_DIR, `detail-${target.id}-no-markers`);
        stats.errors += 1;
        continue;
      }

      let detail;
      try {
        detail = await scrapeDetail(page, String(target.id), target.url);
      } catch (error) {
        console.warn(`  scrape failed: ${error.message}`);
        await dumpDebugArtifacts(page, DEBUG_DIR, `detail-${target.id}-scrape-err`);
        stats.errors += 1;
        continue;
      }

      const hasUsableData =
        detail.entity || detail.ldjson || (detail.ogMeta && Object.keys(detail.ogMeta).length > 0);
      if (!hasUsableData) {
        console.warn('  no extractable data (state, ld+json and og meta all missing); leaving pending');
        await dumpDebugArtifacts(page, DEBUG_DIR, `detail-${target.id}-no-data`);
        stats.errors += 1;
        continue;
      }

      const row = buildOutputRow(target.card, detail, new Date().toISOString());

      try {
        await db.upsertRawListing(pool, {
          sourceName: LISTING_SOURCE,
          externalId: target.id,
          sourceUrl: row.scrapeUrl,
          data: row,
          scrapedAt: row.scrapedAt,
          ironCardId: target.ironCardId,
        });
        await db.markIronCardDetailed(pool, target.ironCardId);
      } catch (error) {
        console.warn(`  db write failed: ${error.message}`);
        stats.errors += 1;
        continue;
      }

      if (detail.extractionTier === 'preloaded_state') stats.ok += 1;
      else stats.fallback += 1;

      const entity = detail.entity || {};
      const fac = entity.facilities || {};
      console.log(
        `  ok — tier=${detail.extractionTier} title="${(detail.ldjson?.name || target.card?.title || '').slice(0, 60)}" ` +
          `price=${entity.price != null ? (entity.price / 100).toFixed(0) : '–'} ` +
          `size=${fac.total_size ?? '–'} photos=${entity.photoURLList?.length ?? 0}`
      );

      if (i < targets.length - 1) {
        if (BATCH_SIZE > 0 && (i + 1) % BATCH_SIZE === 0 && BATCH_PAUSE_MS > 0) {
          console.log(`  batch of ${BATCH_SIZE} done — pausing ${Math.round(BATCH_PAUSE_MS / 1000)}s`);
          await sleep(BATCH_PAUSE_MS);
        } else {
          await sleep(randomDelay());
        }
      }
    }
  } finally {
    await browser.close();
    await db.closePool();
  }

  console.log('');
  console.log('Detail scrape complete');
  console.log(`OK (state tier):   ${stats.ok}`);
  console.log(`OK (fallback):     ${stats.fallback}`);
  console.log(`Errors skipped:    ${stats.errors}`);
  console.log(`Output:            raw_listings table (source=${LISTING_SOURCE})`);
  console.log('');
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
