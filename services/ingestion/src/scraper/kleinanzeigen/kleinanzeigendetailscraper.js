// Phase 2: visit each pending card from iron_cards and write a detail-tier
// record straight into raw_listings (bronze). Once a listing is successfully
// captured we set iron_cards.detail_scraped_at so the next run resumes
// naturally on whatever remains.

const fs = require('node:fs/promises');
const path = require('node:path');
const vanillaPuppeteer = require('puppeteer');
const db = require('scraper-lib');
const stealth = require('scraper-lib/stealth');

// puppeteer-extra + stealth plugin, wrapping our own puppeteer engine.
const puppeteer = stealth.makeStealthPuppeteer(vanillaPuppeteer);

const ORIGIN = 'https://www.kleinanzeigen.de';
const LISTING_SOURCE = 'kleinanzeigen';
// null → rotate a current Chrome UA per run (see _lib/stealth.js).
const USER_AGENT = process.env.USER_AGENT || null;

const DEBUG_DIR = path.resolve(process.env.DEBUG_DIR || __dirname);
const MAX_LISTINGS = process.env.MAX_LISTINGS ? Number.parseInt(process.env.MAX_LISTINGS, 10) : null;
const HEADLESS = process.env.HEADLESS !== 'false';
const PAGE_TIMEOUT = 30_000;

// Stealth timing
const MIN_DELAY_MS = Number.parseInt(process.env.MIN_DELAY_MS || '20000', 10);
const MAX_DELAY_MS = Number.parseInt(process.env.MAX_DELAY_MS || '30000', 10);
const BATCH_SIZE = Number.parseInt(process.env.BATCH_SIZE || '40', 10);
const BATCH_PAUSE_MS = Number.parseInt(process.env.BATCH_PAUSE_MS || '300000', 10);

// ---------------------------------------------------------------------------
// Helpers (inlined — matches existing kleinanzeigen convention, no lib.js)
// ---------------------------------------------------------------------------

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomDelay() {
  const ms = MIN_DELAY_MS + Math.random() * (MAX_DELAY_MS - MIN_DELAY_MS);
  return sleep(ms);
}

function buildTargetsFromIron(rows) {
  const targets = [];
  for (const row of rows) {
    const url = row.detail_url;
    const id = row.external_id;
    if (!url || id == null) continue;
    const cardData = row.data?.raw_payload?.card || row.data || null;
    targets.push({
      id: String(id),
      url,
      card: cardData,
      ironCardId: row.id,
    });
    if (MAX_LISTINGS != null && targets.length >= MAX_LISTINGS) break;
  }
  return targets;
}

async function clickIfPresent(page, selectors) {
  for (const selector of selectors) {
    const element = await page.$(selector);
    if (element) {
      try {
        await element.click({ delay: 30 });
        return true;
      } catch {
        // keep trying remaining selectors
      }
    }
  }
  return false;
}

async function acceptConsent(page) {
  const selectors = [
    '#gdpr-banner-accept',
    'button[data-testid="gdpr-banner-accept"]',
    'button#gdpr-banner-accept-all',
    'button.bb-cookie-tile-accept',
    '#cmpwelcomebtnyes',
    '#cmpbntyestxt',
    'button.cmptrckcontaineracceptall',
    'button[aria-label*="Akzeptieren"]',
    'button[aria-label*="accept"]',
    'button[aria-label*="Einverstanden"]',
  ];

  if (await clickIfPresent(page, selectors)) {
    await sleep(400);
    return true;
  }

  for (const frame of page.frames()) {
    if (frame === page.mainFrame()) continue;
    try {
      const clicked = await frame.evaluate(() => {
        const candidates = [...document.querySelectorAll('button, a[role="button"], div[role="button"]')];
        const target = candidates.find((node) =>
          /alle akzeptieren|akzeptieren|einverstanden|zustimmen|accept all|i agree/i.test(
            (node.innerText || node.textContent || '').trim()
          )
        );
        if (!target) return false;
        target.click();
        return true;
      });
      if (clicked) {
        await sleep(400);
        return true;
      }
    } catch {
      // ignore frames we can't access
    }
  }

  return false;
}

async function preparePage(page) {
  // Shared stealth helper: rotating current Chrome UA + matching client hints.
  // The old manual navigator patches are gone — the stealth plugin owns those.
  await stealth.applyStealthToPage(page, {
    userAgent: USER_AGENT,
    acceptLanguage: 'de-DE,de;q=0.9,en-US;q=0.7,en;q=0.6',
    timeoutMs: PAGE_TIMEOUT,
  });
}

async function dumpDebugArtifacts(page, baseDir, label) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const htmlPath = path.join(baseDir, `kleinanzeigen-debug-${label}-${stamp}.html`);
  const pngPath = path.join(baseDir, `kleinanzeigen-debug-${label}-${stamp}.png`);
  try {
    const html = await page.content();
    await fs.writeFile(htmlPath, html);
    console.warn(`  debug HTML saved: ${htmlPath}`);
  } catch (error) {
    console.warn(`  debug HTML capture failed: ${error.message}`);
  }
  try {
    await page.screenshot({ path: pngPath, fullPage: true });
    console.warn(`  debug screenshot saved: ${pngPath}`);
  } catch (error) {
    console.warn(`  debug screenshot failed: ${error.message}`);
  }
}

// detectChallenge now comes from scraper-lib/stealth — the old local copy
// missed DataDome (kleinanzeigen's vendor), which is exactly what blocks us.
const { detectChallenge } = stealth;

function printBanner(targets) {
  console.log('');
  console.log('kleinanzeigen.de scraper (detail pages)');
  console.log('=======================================');
  console.log(`Input:         iron_cards (source=${LISTING_SOURCE}, detail_scraped_at IS NULL)`);
  console.log(`Output:        raw_listings table (source=${LISTING_SOURCE})`);
  console.log(`To visit:      ${targets.length}`);
  console.log(`Max listings:  ${MAX_LISTINGS ?? 'unbounded'}`);
  console.log(`Delay:         ${MIN_DELAY_MS}–${MAX_DELAY_MS}ms`);
  console.log(`Batch pause:   every ${BATCH_SIZE} listings, ${BATCH_PAUSE_MS}ms`);
  console.log(`Headless:      ${HEADLESS}`);
  console.log('');
}

// ---------------------------------------------------------------------------
// scrapeDetail — runs entirely in the page context via page.evaluate()
// ---------------------------------------------------------------------------

async function scrapeDetail(page, expectedId, canonicalUrl) {
  return page.evaluate(
    (expectedIdArg, canonicalUrlArg) => {
      const clean = (text) => (text || '').replace(/\s+/g, ' ').trim();

      const parseGermanNumber = (value) => {
        if (value == null) return null;
        const s = String(value);
        if (!/[\d,.]/.test(s)) return null;
        const normalized = s.replace(/[^\d,.]/g, '').replace(/\./g, '').replace(',', '.');
        if (!normalized) return null;
        const n = Number.parseFloat(normalized);
        return Number.isNaN(n) ? null : n;
      };

      const result = {
        externalId: expectedIdArg,
        canonicalUrl: canonicalUrlArg,
        url: window.location.href,
      };

      // ---- Title + reserved/deleted status --------------------------------
      // #viewad-title ships two hidden status spans (.pvap-reserved-title)
      // reading "Reserviert • " / "Gelöscht • ". They keep the `is-hidden`
      // class until the listing actually gets that status — but .textContent
      // returns hidden text too, so reading the h1 raw prepends a phantom
      // status to EVERY title. Strip those spans for the title, and derive the
      // real status from whether any such span is actually shown (is-hidden
      // removed).
      const titleEl =
        document.querySelector('#viewad-title') || document.querySelector('h1');
      const status = { reserved: false, deleted: false };
      let titleText = null;
      if (titleEl) {
        for (const span of [...titleEl.querySelectorAll('.pvap-reserved-title')]) {
          if (span.classList.contains('is-hidden')) continue;
          const spanText = (span.textContent || '').toLowerCase();
          if (spanText.includes('reserviert')) status.reserved = true;
          if (spanText.includes('gelöscht') || spanText.includes('geloescht')) {
            status.deleted = true;
          }
        }
        const titleClone = titleEl.cloneNode(true);
        for (const span of [...titleClone.querySelectorAll('.pvap-reserved-title')]) {
          span.remove();
        }
        titleText = clean(titleClone.textContent) || null;
      }
      result.title = titleText;
      result.status = status;

      // ---- Locality -------------------------------------------------------
      result.locality =
        clean(document.querySelector('#viewad-locality')?.textContent) || null;

      // ---- Description ----------------------------------------------------
      result.description =
        clean(document.querySelector('#viewad-description-text')?.textContent) ||
        clean(document.querySelector('#viewad-description')?.textContent) ||
        null;

      // ---- Price (from #viewad-price header) ------------------------------
      const priceHeaderText = clean(document.querySelector('#viewad-price')?.textContent);
      const coldRentEur = parseGermanNumber(priceHeaderText);

      // ---- Details list ---------------------------------------------------
      // Parse all label/value pairs from .addetailslist--detail elements
      const detailPairs = [...document.querySelectorAll('.addetailslist--detail')].map((el) => {
        const labelNode = el.cloneNode(true);
        for (const child of [...labelNode.querySelectorAll('.addetailslist--detail--value')]) {
          child.remove();
        }
        return {
          label: clean(labelNode.textContent),
          value: clean(el.querySelector('.addetailslist--detail--value')?.textContent),
        };
      });

      const findDetail = (pattern) => {
        const re = new RegExp(pattern, 'i');
        const match = detailPairs.find((d) => re.test(d.label));
        return match ? match.value : null;
      };

      // Price-related details
      result.price = {
        coldRentEur,
        nebenkostenEur: parseGermanNumber(findDetail('Nebenkosten')),
        heizkostenEur: parseGermanNumber(findDetail('Heizkosten')),
        warmmieteEur: parseGermanNumber(findDetail('Warmmiete')),
        kautionEur: parseGermanNumber(findDetail('Kaution')),
        raw: priceHeaderText,
      };

      // Non-price details
      result.details = {
        wohnflaeche: findDetail('Wohnfläche|Wohnfl'),
        zimmer: findDetail('^Zimmer'),
        schlafzimmer: findDetail('Schlafzimmer'),
        badezimmer: findDetail('Badezimmer'),
        etage: findDetail('Etage'),
        wohnungstyp: findDetail('Wohnungstyp'),
        verfuegbarAb: findDetail('Verfügbar ab|Verfuegbar'),
        tauschangebot: findDetail('Tauschangebot'),
      };

      // ---- Features (checktags) -------------------------------------------
      result.features = [
        ...new Set(
          [...document.querySelectorAll('.checktag, .checktaglist .checktag')]
            .map((el) => clean(el.textContent))
            .filter(Boolean)
        ),
      ];

      // ---- Images ---------------------------------------------------------
      const imageNodes = [
        ...document.querySelectorAll(
          '.galleryimage-element img, [data-imgsrc]'
        ),
      ];
      result.images = [
        ...new Set(
          imageNodes
            .flatMap((el) => [
              el.getAttribute('src'),
              el.getAttribute('data-imgsrc'),
              el.getAttribute('data-src'),
            ])
            .filter(Boolean)
        ),
      ];

      // ---- Geo (og:latitude / og:longitude meta tags) ---------------------
      const lat = document.querySelector('meta[property="og:latitude"]')?.content;
      const lng = document.querySelector('meta[property="og:longitude"]')?.content;
      result.geo = lat && lng ? { lat: Number(lat), lng: Number(lng) } : null;

      // ---- Seller info ----------------------------------------------------
      result.seller = (() => {
        const nameEl = document.querySelector('.userprofile-vip');
        const name = nameEl ? clean(nameEl.textContent) : null;

        const detailTexts = [...document.querySelectorAll('.userprofile-vip-details-text')];
        const type = detailTexts[0] ? clean(detailTexts[0].textContent) : null;
        const activeSince = detailTexts[1] ? clean(detailTexts[1].textContent) : null;

        const phoneEl = document.querySelector('#viewad-contact-phone');
        const phone = phoneEl ? clean(phoneEl.textContent) : null;

        return { name, type, activeSince, phone };
      })();

      // ---- Scraped Ad ID --------------------------------------------------
      const adIdBox = document.querySelector('#viewad-ad-id-box');
      const adIdItems = adIdBox ? [...adIdBox.querySelectorAll('li')] : [];
      result.scrapedAdId = adIdItems.length >= 2 ? clean(adIdItems[1].textContent) : null;

      // ---- LD-JSON --------------------------------------------------------
      result.ldJson = [...document.querySelectorAll('script[type="application/ld+json"]')]
        .map((script) => {
          try {
            return JSON.parse(script.textContent || '');
          } catch {
            return null;
          }
        })
        .filter(Boolean);

      // ---- OG meta --------------------------------------------------------
      const ogMeta = (name) =>
        document.querySelector(`meta[property="${name}"]`)?.content || null;
      result.ogMeta = {
        title: ogMeta('og:title'),
        description: ogMeta('og:description'),
        image: ogMeta('og:image'),
        url: ogMeta('og:url'),
      };

      // ---- Embedded state -------------------------------------------------
      const inlineScripts = [...document.querySelectorAll('script:not([src])')];
      result.embeddedState = inlineScripts
        .map((s) => s.textContent || '')
        .filter((text) => /window\.__|dataLayer\s*=|liberty\.config/i.test(text))
        .map((text) => text.slice(0, 8000));

      return result;
    },
    expectedId,
    canonicalUrl
  );
}

// ---------------------------------------------------------------------------
// buildOutputRow — wraps card + detail into the output format
// ---------------------------------------------------------------------------

function buildOutputRow(target, detail, scrapedAt) {
  return {
    listing_source: LISTING_SOURCE,
    id: target.id,
    scrapeUrl: detail?.url || target.url,
    scrapedAt,
    dump: {
      card: target.card,
      ...detail,
    },
  };
}

// ---------------------------------------------------------------------------
// run — main loop
// ---------------------------------------------------------------------------

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
      '--lang=de-DE,de',
    ],
  };
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  console.log('Launching browser...');
  const browser = await puppeteer.launch(launchOptions);

  const stats = { ok: 0, skipped: 0, errors: 0 };

  try {
    const page = await browser.newPage();
    await preparePage(page);

    let consentTried = false;

    for (let i = 0; i < targets.length; i += 1) {
      const target = targets[i];
      const label = `[${i + 1}/${targets.length}] id=${target.id}`;
      console.log(`\n${label} ${target.url}`);

      // Batch pause every BATCH_SIZE listings
      if (i > 0 && i % BATCH_SIZE === 0) {
        console.log(`  batch pause (${BATCH_PAUSE_MS / 1000}s) after ${i} listings...`);
        await sleep(BATCH_PAUSE_MS);
      }

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
        await page.waitForSelector('#viewad-title, h1, #viewad-main', {
          timeout: PAGE_TIMEOUT,
        });
      } catch {
        console.warn('  detail markers not found — page may have a different layout');
        await dumpDebugArtifacts(page, DEBUG_DIR, `detail-${target.id}-no-markers`);
        stats.errors += 1;
        continue;
      }

      let detail;
      try {
        detail = await scrapeDetail(page, target.id, target.url);
      } catch (error) {
        console.warn(`  scrape failed: ${error.message}`);
        await dumpDebugArtifacts(page, DEBUG_DIR, `detail-${target.id}-scrape-err`);
        stats.errors += 1;
        continue;
      }

      // Reserved/deleted listings are flagged by a *shown* status span inside
      // #viewad-title (scrapeDetail derives detail.status from is-hidden state,
      // NOT from textContent — the spans exist hidden on every listing). They're
      // dead ads — skip persisting them to bronze so silver stays clean, but
      // still mark the iron card detailed so we don't retry it on the next run.
      // We deliberately fall through to the same jitter/backoff as a normal
      // listing (do NOT continue straight to the next card) to keep the request
      // cadence human-looking.
      if (detail.status?.reserved || detail.status?.deleted) {
        const flags = [
          detail.status.reserved ? 'reserved' : null,
          detail.status.deleted ? 'deleted' : null,
        ]
          .filter(Boolean)
          .join('+');
        console.log(`  skipped — ${flags} (title="${(detail.title || '').slice(0, 60)}")`);
        stats.skipped += 1;
        try {
          await db.markIronCardDetailed(pool, target.ironCardId);
        } catch (error) {
          console.warn(`  db mark failed: ${error.message}`);
        }
        if (i < targets.length - 1) {
          await randomDelay();
        }
        continue;
      }

      const row = buildOutputRow(target, detail, new Date().toISOString());
      stats.ok += 1;

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

      console.log(
        `  ok — title="${(detail.title || '').slice(0, 60)}" ` +
          `cold=${detail.price?.coldRentEur ?? '–'} warm=${detail.price?.warmmieteEur ?? '–'} ` +
          `rooms=${detail.details?.zimmer ?? '–'} imgs=${detail.images?.length ?? 0}`
      );

      // Random delay between listings
      if (i < targets.length - 1) {
        await randomDelay();
      }
    }
  } finally {
    await browser.close();
    await db.closePool();
  }

  console.log('');
  console.log('Detail scrape complete');
  console.log(`OK:                ${stats.ok}`);
  console.log(`Reserved/deleted:  ${stats.skipped}`);
  console.log(`Errors skipped:    ${stats.errors}`);
  console.log(`Output:            raw_listings table (source=${LISTING_SOURCE})`);
  console.log('');
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
