// Phase 1: search-result cards only. Detail pages are deferred — `priceEur`
// is the single figure shown on the card (likely Warmmiete) and is recorded
// as both `rentEur` and `warmRentEur`. Phase 2 will visit detail pages to
// split into Kaltmiete / Nebenkosten / Heizkosten.

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

const SEARCH_URL =
  process.env.SEARCH_URL ||
  'https://www.wg-gesucht.de/en/1-zimmer-wohnungen-und-wohnungen-in-Berlin.8.1+2.1.0.html?categories%5B%5D=1&categories%5B%5D=2&rent_types%5B%5D=2&rent_range=0%2C0&min_rent=0&offer_filter=1&city_id=8&sort_order=0&noDeact=1';
const ORIGIN = 'https://www.wg-gesucht.de';
const LISTING_SOURCE = 'wg-gesucht';
// null → rotate a current Chrome UA per run (see _lib/stealth.js). An explicit
// USER_AGENT env var still pins it.
const USER_AGENT = process.env.USER_AGENT || null;

const DEBUG_DIR = path.resolve(process.env.DEBUG_DIR || __dirname);
const MAX_PAGES = Number.parseInt(process.env.MAX_PAGES || '1', 10);
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
  console.log('wg-gesucht.de scraper (cards only)');
  console.log('==================================');
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

// wg-gesucht encodes the page index as the 4th dot-segment of the path,
// 0-based. Page 1 is `.0.html`, page 2 is `.1.html`, etc. Built-in pagination
// links drop user query params, so we rebuild URLs from the start URL to keep
// active filters.
function buildSearchUrl(pageNumber) {
  const idx = Math.max(0, pageNumber - 1);
  const u = new URL(SEARCH_URL);
  u.pathname = u.pathname.replace(/\.(\d+)\.html$/, `.${idx}.html`);
  return u.toString();
}

async function scrapeCards(page) {
  return page.$$eval(
    'div.wgg_card.offer_list_item:not(.clicked_partner)',
    (nodes) => {
      const clean = (text) => (text || '').replace(/\s+/g, ' ').trim();

      const parseGermanNumber = (value) => {
        if (!value) return null;
        const normalized = String(value).replace(/[^\d,.]/g, '').replace(/\./g, '').replace(',', '.');
        const n = Number.parseFloat(normalized);
        return Number.isNaN(n) ? null : n;
      };

      return nodes
        .map((node, index) => {
          try {
            const externalId =
              node.dataset.id ||
              (node.id || '').replace('liste-details-ad-', '') ||
              null;

            // Detail link: prefer printonly anchor (stable, English path),
            // fall back to title anchor or any in-card link.
            const printAnchor = node.querySelector('.printonly a.detailansicht[href]');
            const titleAnchor = node.querySelector('h2.truncate_title a[href]');
            const fallbackAnchor = node.querySelector('a[href*=".html"]');
            const href =
              printAnchor?.getAttribute('href') ||
              titleAnchor?.getAttribute('href') ||
              fallbackAnchor?.getAttribute('href') ||
              null;

            const title = clean(titleAnchor?.textContent) || null;

            // Middle row: price | dates | area
            const middleRow = node.querySelector('.row.middle');
            const priceText =
              clean(middleRow?.querySelector('.col-xs-3 b')?.textContent) || null;
            const dateText =
              clean(middleRow?.querySelector('.col-xs-5')?.textContent) || null;
            const areaText =
              clean(middleRow?.querySelector('.col-xs-3.text-right b')?.textContent) || null;

            const priceEur = parseGermanNumber(priceText);
            const areaSqm = parseGermanNumber(areaText);

            // "27.05.2026 - 01.01.2027" or just "27.05.2026"
            let occupationDate = null;
            let availableUntilDate = null;
            if (dateText) {
              const dm = dateText.match(/(\d{2}\.\d{2}\.\d{4})(?:\s*-\s*(\d{2}\.\d{2}\.\d{4}))?/);
              if (dm) {
                occupationDate = dm[1] || null;
                availableUntilDate = dm[2] || null;
              }
            }

            // "2 Room Flat | Berlin Mitte | Alex-Wedding-Straße 5"
            const locationText =
              clean(node.querySelector('.col-xs-11 span')?.textContent) || null;
            const parts = (locationText || '').split('|').map((s) => s.trim()).filter(Boolean);
            const listingType = parts[0] || null;
            let district = null;
            if (parts[1]) {
              const m = parts[1].match(/^Berlin\s+(.+)$/i);
              district = m ? m[1].trim() : parts[1];
            }
            const address = parts[2] || null;

            // Rooms: "2 Room Flat" / "1 Zimmer Wohnung" / "Studio"
            let rooms = null;
            if (listingType) {
              const rm = listingType.match(/(\d+(?:[.,]\d+)?)\s*(?:Room|Zimmer)/i);
              if (rm) rooms = parseGermanNumber(rm[1]);
              else if (/studio|1[\s-]?zimmer/i.test(listingType)) rooms = 1;
            }

            const imageUrl =
              node.querySelector('.card_image img')?.getAttribute('src') || null;
            // PRIVACY: the poster's name and online-status are deliberately
            // not collected. See services/ingestion/src/pii.py.

            return {
              card_index: index,
              external_id: externalId,
              href,
              title,
              priceText,
              priceEur,
              dateText,
              occupationDate,
              availableUntilDate,
              areaText,
              areaSqm,
              locationText,
              listingType,
              district,
              address,
              rooms,
              imageUrl,
              card_html_length: (node.outerHTML || '').length,
            };
          } catch (err) {
            return { card_index: index, error: err.message };
          }
        })
        .filter((card) => card && (card.href || card.external_id));
    }
  );
}

function buildRow(card, pageNumber, scrapeUrl, scrapedAt) {
  const url = absoluteUrl(card.href);
  const idNum = card.external_id ? Number.parseInt(card.external_id, 10) : null;
  const canonicalUrl = card.external_id ? `${ORIGIN}/${card.external_id}.html` : null;
  const locationParts = [
    card.district ? `Berlin ${card.district}` : null,
    card.address,
  ].filter(Boolean);
  const location = locationParts.length ? locationParts.join(', ') : card.locationText;

  return {
    listing_source: LISTING_SOURCE,
    id: idNum,
    scrapeUrl,
    url,
    canonicalUrl,
    title: card.title,
    rooms: card.rooms,
    areaSqm: card.areaSqm,
    rentEur: card.priceEur,
    warmRentEur: card.priceEur,
    coldRentEur: null,
    nebenkostenEur: null,
    location,
    district: card.district,
    address: card.address,
    listingType: card.listingType,
    occupationDate: card.occupationDate,
    availableUntilDate: card.availableUntilDate,
    imageUrl: card.imageUrl,
    posterName: card.posterName,
    onlineSince: card.onlineSince,
    page: pageNumber,
    scrapedAt,
  };
}

async function persistRowsToIron(pool, rows) {
  for (const row of rows) {
    if (row.id == null) continue;
    const detailUrl = row.canonicalUrl || row.url || `${ORIGIN}/${row.id}.html`;
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

async function collectCards(page, pool) {
  const allRows = [];
  const seenIds = new Set();
  const stats = { pagesVisited: 0, cardsSeen: 0, duplicates: 0, skipped: 0 };

  for (let pageNumber = 1; pageNumber <= MAX_PAGES; pageNumber += 1) {
    const url = buildSearchUrl(pageNumber);
    console.log(`\n[search ${pageNumber}/${MAX_PAGES}] ${url}`);

    try {
      // domcontentloaded (not networkidle2): a Cloudflare "Just a moment"
      // challenge never reaches network-idle, so networkidle2 silently ate the
      // full timeout. domcontentloaded resolves on the challenge HTML, then
      // detectChallenge below catches it immediately.
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT });
    } catch (error) {
      console.warn(`  navigation failed: ${error.message}`);
      await dumpDebugArtifacts(page, DEBUG_DIR, `search-page-${pageNumber}-nav`);
      break;
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

    try {
      await page.waitForSelector('div.wgg_card.offer_list_item', { timeout: PAGE_TIMEOUT });
    } catch {
      console.warn('  no listings detected on this page; stopping pagination');
      await dumpDebugArtifacts(page, DEBUG_DIR, `search-page-${pageNumber}-empty`);
      break;
    }

    const cards = await scrapeCards(page);
    stats.cardsSeen += cards.length;
    console.log(`  cards on page: ${cards.length}`);

    let added = 0;
    for (const card of cards) {
      if (card.error) {
        stats.skipped += 1;
        continue;
      }
      const key = card.external_id || card.href;
      if (!key) {
        stats.skipped += 1;
        continue;
      }
      if (seenIds.has(key)) {
        stats.duplicates += 1;
        continue;
      }
      seenIds.add(key);

      const row = buildRow(card, pageNumber, SEARCH_URL, new Date().toISOString());
      allRows.push(row);
      added += 1;

      if (MAX_LISTINGS != null && allRows.length >= MAX_LISTINGS) break;
    }
    console.log(`  new unique rows: ${added} (total: ${allRows.length})`);

    await persistRowsToIron(pool, allRows);
    console.log(`  upserted ${allRows.length} rows into iron_cards`);

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
      '--lang=de-DE,de',
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
    console.log(`Duplicates skipped:   ${stats.duplicates}`);
    console.log(`Errors skipped:       ${stats.skipped}`);
    console.log(`Rows written:         ${allRows.length}`);
    console.log(`Output:               iron_cards table (source=${LISTING_SOURCE})`);
    console.log('');

    const preview = allRows.slice(0, 10).map((row) => ({
      id: row.id,
      title: (row.title || '').slice(0, 60),
      rooms: row.rooms,
      areaSqm: row.areaSqm,
      rentEur: row.rentEur,
      district: row.district,
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
