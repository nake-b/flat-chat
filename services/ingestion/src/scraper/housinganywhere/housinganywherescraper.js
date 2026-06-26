// Phase 1: search-result cards only. Detail pages are deferred — the card
// shows a single all-inclusive monthly figure (microdata, whole euros) which
// is recorded as `priceEur`. Phase 2 visits detail pages for the full cost
// breakdown out of `window.__PRELOADED_STATE__`.
//
// Multi-unit student-accommodation complexes (hrefs like
// /s/Berlin--Germany/student-accommodation/{slug}-{ID}) are intentionally
// skipped — only individual units (/room/ut{ID}/...) enter iron_cards.

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
  extractRoomId,
  waitForCardsSettled,
} = require('./lib');

const SEARCH_URL =
  process.env.SEARCH_URL || 'https://housinganywhere.com/s/Berlin--Germany?calendarMode=exact';
const ORIGIN = 'https://housinganywhere.com';
const LISTING_SOURCE = 'housinganywhere';
// null → rotate a current Chrome UA per run (see _lib/stealth.js).
const USER_AGENT = process.env.USER_AGENT || null;

const DEBUG_DIR = path.resolve(process.env.DEBUG_DIR || __dirname);
const MAX_PAGES = Number.parseInt(process.env.MAX_PAGES || '3', 10);
const MAX_LISTINGS = process.env.MAX_LISTINGS ? Number.parseInt(process.env.MAX_LISTINGS, 10) : null;
const HEADLESS = process.env.HEADLESS !== 'false';
// Conservative defaults matching the kleinanzeigen search scraper (15s base),
// plus jitter on top so page-to-page timing isn't perfectly periodic.
const PAGE_DELAY_MS = Number.parseInt(process.env.PAGE_DELAY_MS || '15000', 10);
const PAGE_JITTER_MS = Number.parseInt(process.env.PAGE_JITTER_MS || '5000', 10);
const PAGE_TIMEOUT = DEFAULT_TIMEOUT_MS;

function pageDelay() {
  return PAGE_DELAY_MS + Math.floor(Math.random() * Math.max(0, PAGE_JITTER_MS + 1));
}

function printBanner() {
  console.log('');
  console.log('housinganywhere.com scraper (cards only)');
  console.log('========================================');
  console.log(`Source:        ${SEARCH_URL}`);
  console.log(`Target pages:  ${MAX_PAGES}`);
  console.log(`Max listings:  ${MAX_LISTINGS ?? 'unbounded'}`);
  console.log(`Headless:      ${HEADLESS}`);
  console.log(`Output:        iron_cards table (source=${LISTING_SOURCE})`);
  console.log('');
}

function absoluteUrl(href) {
  if (!href) return null;
  try {
    return new URL(href, ORIGIN).toString();
  } catch {
    return null;
  }
}

// Pagination is rendered as MUI buttons (no ?page= hrefs in the markup), so
// `?page=N` support is optimistic. collectCards verifies the card set actually
// changed after each URL navigation and falls back to clicking the pagination
// buttons (SPA navigation) when the param is ignored.
function buildSearchUrl(pageNumber) {
  const u = new URL(SEARCH_URL);
  if (pageNumber > 1) u.searchParams.set('page', String(pageNumber));
  else u.searchParams.delete('page');
  return u.toString();
}

async function scrapeCards(page) {
  return page.$$eval('a[data-test-locator="ListingCard/Anchor"]', (anchors) => {
    const clean = (text) => (text || '').replace(/\s+/g, ' ').trim();

    return anchors
      .map((anchor, index) => {
        try {
          const q = (sel) => anchor.querySelector(sel);
          const text = (sel) => clean(q(sel)?.textContent) || null;

          const href = anchor.getAttribute('href') || null;
          const title = text('[data-test-locator="ListingCard/Title"]');
          const priceText = text('[data-test-locator="ListingCard/Price"]');
          const priceLabel = text('[data-test-locator="ListingCard/PriceLabel"]');

          // Microdata price is machine-readable and in WHOLE EUROS
          // (content="530" matches the displayed "€530").
          const priceMeta = q('meta[itemprop="price"]')?.getAttribute('content');
          const priceEur = priceMeta != null && priceMeta !== '' ? Number(priceMeta) : null;
          const currency = q('meta[itemprop="priceCurrency"]')?.getAttribute('content') || null;

          // "4.9 (8)" — textContent may contain <!-- --> comment-node gaps.
          const ratingText = text('[data-test-locator="ListingCard/Rating"]');
          let rating = null;
          let ratingCount = null;
          if (ratingText) {
            const rm = ratingText.match(/([\d.]+)\s*\(([\d,]+)\)/);
            if (rm) {
              rating = Number.parseFloat(rm[1]);
              ratingCount = Number.parseInt(rm[2].replace(/,/g, ''), 10);
            }
          }

          const availabilityText = text('[data-test-locator="ListingCard/Availability"]');

          const facilitiesEl = q('[data-test-locator="ListingCard/AttributesFacilities"]');
          const facilitiesText = facilitiesEl?.getAttribute('title') || clean(facilitiesEl?.textContent) || null;
          const facilities = facilitiesText
            ? facilitiesText.split(/[,·]/).map((s) => s.trim()).filter(Boolean)
            : [];

          const imageUrls = [
            ...new Set(
              [...anchor.querySelectorAll('[data-test-locator="ListingCardPhotoGallery/Photo"]')]
                .map((img) => img.getAttribute('src'))
                .filter(Boolean)
            ),
          ].slice(0, 3);

          const badges = [...anchor.querySelectorAll('[data-test-locator^="ListingCard/Highlight"]')]
            .map((el) => el.getAttribute('title') || clean(el.textContent))
            .filter(Boolean);
          const hasMultiplePlaces = !!q('[data-test-locator="ListingCard/BadgeMultiplePlaces"]');

          return {
            card_index: index,
            href,
            title,
            priceText,
            priceLabel,
            priceEur: Number.isNaN(priceEur) ? null : priceEur,
            currency,
            ratingText,
            rating,
            ratingCount,
            availabilityText,
            facilitiesText,
            facilities,
            imageUrls,
            badges,
            hasMultiplePlaces,
            card_html_length: (anchor.outerHTML || '').length,
          };
        } catch (err) {
          return { card_index: index, error: err.message };
        }
      })
      .filter((card) => card && (card.href || card.error));
  });
}

// One-shot: the AggregateOffer LD+JSON on the search page carries the total
// result count — purely informational, logged for run-size context.
async function readOfferCount(page) {
  try {
    return await page.evaluate(() => {
      for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
          const d = JSON.parse(s.textContent);
          if (d?.offers?.offerCount) return d.offers.offerCount;
        } catch {
          // try next block
        }
      }
      return null;
    });
  } catch {
    return null;
  }
}

function buildRow(card, externalId, pageNumber, scrapeUrl, scrapedAt) {
  const url = absoluteUrl(card.href);
  return {
    listing_source: LISTING_SOURCE,
    id: externalId,
    scrapeUrl,
    url,
    canonicalUrl: url,
    title: card.title,
    priceEur: card.priceEur,
    priceText: card.priceText,
    priceLabel: card.priceLabel,
    currency: card.currency,
    rating: card.rating,
    ratingCount: card.ratingCount,
    availabilityText: card.availabilityText,
    facilities: card.facilities,
    facilitiesText: card.facilitiesText,
    badges: card.badges,
    imageUrls: card.imageUrls,
    page: pageNumber,
    scrapedAt,
  };
}

async function persistRowsToIron(pool, rows) {
  for (const row of rows) {
    if (row.id == null) continue;
    const detailUrl = row.canonicalUrl || row.url;
    if (!detailUrl) continue;
    await db.upsertIronCard(pool, {
      sourceName: row.listing_source,
      externalId: row.id,
      detailUrl,
      sourceUrl: row.scrapeUrl,
      data: row,
      scrapedAt: row.scrapedAt,
    });
  }
}

function idSetOf(cards) {
  return new Set(cards.map((c) => c.href).filter(Boolean));
}

function isSubset(set, supersetCandidate) {
  for (const v of set) {
    if (!supersetCandidate.has(v)) return false;
  }
  return true;
}

// Click-based fallback navigation: prefer the numbered button, else "next".
// Waits for the first card href to change before settling.
async function clickToPage(page, pageNumber, prevFirstHref) {
  const clicked =
    (await page
      .click(`button[aria-label="Go to page ${pageNumber}"]`)
      .then(() => true)
      .catch(() => false)) ||
    (await page
      .click('button[aria-label="Go to next page"]')
      .then(() => true)
      .catch(() => false));
  if (!clicked) return false;

  try {
    await page.waitForFunction(
      (prev) => {
        const a = document.querySelector('a[data-test-locator="ListingCard/Anchor"]');
        return a && a.href !== prev;
      },
      { timeout: PAGE_TIMEOUT },
      prevFirstHref
    );
  } catch {
    return false;
  }
  await waitForCardsSettled(page);
  return true;
}

async function collectCards(page, pool) {
  const allRows = [];
  const seenIds = new Set();
  const stats = { pagesVisited: 0, cardsSeen: 0, duplicates: 0, skipped: 0, complexesSkipped: 0 };

  // 'url' until proven ignored, then sticky 'click' (SPA pagination).
  let mode = 'url';
  let prevIds = null;
  let prevFirstHref = null;

  for (let pageNumber = 1; pageNumber <= MAX_PAGES; pageNumber += 1) {
    if (pageNumber === 1 || mode === 'url') {
      const url = buildSearchUrl(pageNumber);
      console.log(`\n[search ${pageNumber}/${MAX_PAGES}] ${url}`);
      try {
        // domcontentloaded + waitForCardsSettled (below) instead of
        // networkidle2 — a DataDome interstitial never reaches network-idle, so
        // networkidle2 burned the full timeout before detectChallenge ran.
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT });
      } catch (error) {
        console.warn(`  navigation failed: ${error.message}`);
        await dumpDebugArtifacts(page, DEBUG_DIR, `search-page-${pageNumber}-nav`);
        break;
      }
    } else {
      console.log(`\n[search ${pageNumber}/${MAX_PAGES}] (button pagination)`);
      const ok = await clickToPage(page, pageNumber, prevFirstHref);
      if (!ok) {
        console.warn('  button pagination failed; stopping');
        await dumpDebugArtifacts(page, DEBUG_DIR, `search-page-${pageNumber}-click`);
        break;
      }
    }

    const challenge = await detectChallenge(page);
    if (challenge) {
      console.warn(`  challenge detected (${challenge}); aborting`);
      await dumpDebugArtifacts(page, DEBUG_DIR, `search-page-${pageNumber}-${challenge}`);
      break;
    }

    if (pageNumber === 1) {
      await acceptConsent(page);
    }
    stats.pagesVisited += 1;

    const settledCount = await waitForCardsSettled(page);
    if (settledCount === 0) {
      console.warn('  no listings detected on this page; stopping pagination');
      await dumpDebugArtifacts(page, DEBUG_DIR, `search-page-${pageNumber}-empty`);
      break;
    }

    if (pageNumber === 1) {
      const offerCount = await readOfferCount(page);
      if (offerCount) console.log(`  total listings advertised: ${offerCount}`);
    }

    let cards = await scrapeCards(page);
    let ids = idSetOf(cards);

    // Verify-change guard: if the URL param was ignored we are still looking
    // at the previous page's cards — switch to button pagination once.
    if (pageNumber > 1 && prevIds && isSubset(ids, prevIds)) {
      if (mode === 'url') {
        console.warn('  ?page= param ignored; falling back to button pagination');
        mode = 'click';
        const ok = await clickToPage(page, pageNumber, prevFirstHref);
        if (ok) {
          cards = await scrapeCards(page);
          ids = idSetOf(cards);
        }
      }
      if (prevIds && isSubset(ids, prevIds)) {
        console.warn('  card set unchanged after fallback; stopping pagination');
        await dumpDebugArtifacts(page, DEBUG_DIR, `search-page-${pageNumber}-stuck`);
        break;
      }
    }

    stats.cardsSeen += cards.length;
    console.log(`  cards on page: ${cards.length}`);

    let added = 0;
    let complexesOnPage = 0;
    for (const card of cards) {
      if (card.error) {
        stats.skipped += 1;
        continue;
      }
      const externalId = extractRoomId(card.href);
      if (!externalId || card.hasMultiplePlaces) {
        stats.complexesSkipped += 1;
        complexesOnPage += 1;
        continue;
      }
      if (seenIds.has(externalId)) {
        stats.duplicates += 1;
        continue;
      }
      seenIds.add(externalId);

      const row = buildRow(card, externalId, pageNumber, SEARCH_URL, new Date().toISOString());
      allRows.push(row);
      added += 1;

      if (MAX_LISTINGS != null && allRows.length >= MAX_LISTINGS) break;
    }
    console.log(`  new unique rows: ${added}, complexes skipped: ${complexesOnPage} (total: ${allRows.length})`);

    await persistRowsToIron(pool, allRows);
    console.log(`  upserted ${allRows.length} rows into iron_cards`);

    prevIds = ids;
    prevFirstHref = cards.find((c) => c.href)?.href || prevFirstHref;

    if (MAX_LISTINGS != null && allRows.length >= MAX_LISTINGS) break;
    if (cards.length === 0) {
      console.log('  empty page; stopping');
      break;
    }

    if (pageNumber < MAX_PAGES && PAGE_DELAY_MS > 0) {
      await sleep(pageDelay());
    }
  }

  return { allRows, stats };
}

async function main() {
  printBanner();

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

  const pool = db.getPool();

  try {
    const searchPage = await browser.newPage();
    await preparePage(searchPage, { userAgent: USER_AGENT, timeoutMs: PAGE_TIMEOUT });

    const { allRows, stats } = await collectCards(searchPage, pool);

    await persistRowsToIron(pool, allRows);

    console.log('');
    console.log('Scrape complete');
    console.log(`Search pages visited: ${stats.pagesVisited}`);
    console.log(`Cards observed:       ${stats.cardsSeen}`);
    console.log(`Complexes skipped:    ${stats.complexesSkipped}`);
    console.log(`Duplicates skipped:   ${stats.duplicates}`);
    console.log(`Errors skipped:       ${stats.skipped}`);
    console.log(`Rows written:         ${allRows.length}`);
    console.log(`Output:               iron_cards table (source=${LISTING_SOURCE})`);
    console.log('');

    const preview = allRows.slice(0, 10).map((row) => ({
      id: row.id,
      title: (row.title || '').slice(0, 60),
      priceEur: row.priceEur,
      rating: row.rating,
      availability: (row.availabilityText || '').slice(0, 30),
    }));
    if (preview.length) console.table(preview);
  } finally {
    await browser.close();
    await db.closePool();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
