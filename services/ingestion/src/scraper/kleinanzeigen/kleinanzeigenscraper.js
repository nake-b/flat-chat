const fs = require('node:fs/promises');
const path = require('node:path');
const vanillaPuppeteer = require('puppeteer');
const db = require('scraper-lib');
const stealth = require('scraper-lib/stealth');

// puppeteer-extra + stealth plugin, wrapping our own puppeteer engine.
const puppeteer = stealth.makeStealthPuppeteer(vanillaPuppeteer);

const SEARCH_URL = 'https://www.kleinanzeigen.de/s-wohnung-mieten/berlin/sortierung:neuste/c203l3331+wohnung_mieten.swap_s:nein';
const ORIGIN = 'https://www.kleinanzeigen.de';
const LISTING_SOURCE = 'kleinanzeigen';
// null → rotate a current Chrome UA per run (see _lib/stealth.js). An explicit
// USER_AGENT env var still pins it.
const USER_AGENT = process.env.USER_AGENT || null;

const DEBUG_DIR = path.resolve(process.env.DEBUG_DIR || __dirname);
const MAX_PAGES = Number.parseInt(process.env.MAX_PAGES || '1', 10);
const MAX_LISTINGS = process.env.MAX_LISTINGS ? Number.parseInt(process.env.MAX_LISTINGS, 10) : null;
const HEADLESS = process.env.HEADLESS !== 'false';
const DETAIL_DELAY_MS = Number.parseInt(process.env.DETAIL_DELAY_MS || '600', 10);
const PAGE_DELAY_MS = Number.parseInt(process.env.PAGE_DELAY_MS || '15000', 10);
const PAGE_TIMEOUT = 30_000;

function printBanner() {
  console.log('');
  console.log('Kleinanzeigen scraper');
  console.log('=====================');
  console.log(`Source:        ${SEARCH_URL}`);
  console.log(`Target pages:  ${MAX_PAGES}`);
  console.log(`Max listings:  ${MAX_LISTINGS ?? 'unbounded'}`);
  console.log(`Page delay:    ${PAGE_DELAY_MS}ms`);
  console.log(`Headless:      ${HEADLESS}`);
  console.log(`Output:        iron_cards table (source=${LISTING_SOURCE})`);
  console.log('');
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function absoluteUrl(href) {
  if (!href) return null;
  try {
    return new URL(href, ORIGIN).toString();
  } catch {
    return null;
  }
}

function stripQuery(url) {
  if (!url) return null;
  const hashIdx = url.indexOf('#');
  const trimmed = hashIdx >= 0 ? url.slice(0, hashIdx) : url;
  const qIdx = trimmed.indexOf('?');
  return qIdx >= 0 ? trimmed.slice(0, qIdx) : trimmed;
}

function extractIdFromUrl(url) {
  if (!url) return null;
  const adMatch = url.match(/\/s-anzeige\/[^/]+\/(\d+)/);
  if (adMatch) return adMatch[1];
  const tailMatch = url.match(/\/(\d{6,})(?:[/?#-]|$)/);
  return tailMatch ? tailMatch[1] : null;
}

function dedupArray(values) {
  return [...new Set((values || []).filter(Boolean))];
}

function canonicalKey(card) {
  const listingUrl = absoluteUrl(card.href);
  const stripped = stripQuery(listingUrl);
  if (stripped) return `url:${stripped}`;
  if (card.external_id) return `id:${card.external_id}`;
  if (card.href) return `href:${String(card.href).trim()}`;
  return `title:${(card.title || '').toLowerCase().slice(0, 80)}`;
}

function buildSearchUrl(pageNumber) {
  if (pageNumber <= 1) return SEARCH_URL;
  return SEARCH_URL.replace('/s-wohnung-mieten/', `/s-wohnung-mieten/seite:${pageNumber}/`);
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

async function scrapeCards(page) {
  return page.$$eval(
    'article[data-adid]',
    (nodes) => {
      const clean = (text) => (text || '').replace(/\s+/g, ' ').trim();
      const collectImages = (root) => {
        const sources = new Set();
        for (const img of root.querySelectorAll('img')) {
          for (const attr of ['src', 'srcset', 'data-imgsrc', 'data-src', 'data-image']) {
            const val = img.getAttribute(attr);
            if (val) sources.add(val);
          }
        }
        return [...sources];
      };
      const collectDataAttrs = (node) => {
        const attrs = {};
        for (const attr of node.attributes) {
          if (attr.name.startsWith('data-')) attrs[attr.name] = attr.value;
        }
        return attrs;
      };
      const parseLdJson = (root) => {
        const scripts = root.querySelectorAll('script[type="application/ld+json"]');
        const results = [];
        for (const s of scripts) {
          try { results.push(JSON.parse(s.textContent || '')); } catch { /* skip */ }
        }
        return results;
      };

      // detect layout variant: old BEM (.aditem) vs new Tailwind (li[data-clickable])
      const isOldLayout = nodes.length > 0 && nodes[0].classList.contains('aditem');

      const seenHrefs = new Set();

      return nodes
        .map((node, index) => {
          const dataAdId = node.getAttribute('data-adid') || null;
          const dataHref = node.getAttribute('data-href') || null;

          let titleAnchor, priceText, locationText, detailsText, descText, sellerName, tags;

          if (isOldLayout) {
            // --- old BEM layout ---
            titleAnchor =
              node.querySelector('h2.text-module-begin a.ellipsis') ||
              node.querySelector('h2.text-module-begin a') ||
              node.querySelector('a.ellipsis') ||
              node.querySelector('h2 a') ||
              node.querySelector('a[href*="/s-anzeige/"]');

            const priceEl =
              node.querySelector('.aditem-main--middle--price-shipping--price') ||
              node.querySelector('p.aditem-main--middle--price') ||
              node.querySelector('[class*="price"]');
            priceText = clean(priceEl?.textContent) || null;

            locationText = clean(node.querySelector('.aditem-main--top--left')?.textContent) || null;
            detailsText = clean(node.querySelector('.aditem-main--top--right')?.textContent) || null;
            descText = clean(node.querySelector('.aditem-main--middle--description')?.textContent) || null;

            const tagListEl = node.querySelector('.simpletag-list, .aditem-main--bottom');
            tags = tagListEl ? [clean(tagListEl.textContent)].filter(Boolean) : [];
            sellerName = null;
          } else {
            // --- new Tailwind layout ---
            titleAnchor =
              node.querySelector('h3 a[href*="/s-anzeige/"]') ||
              node.querySelector('a[href*="/s-anzeige/"]');

            priceText = clean(node.querySelector('p.text-secondary.text-title3')?.textContent) || null;

            const locIcon = node.querySelector('svg[data-title="locationOutline"]');
            locationText = clean(locIcon?.parentElement?.querySelector('span')?.textContent) || null;

            detailsText = clean(node.querySelector('p.font-strong.text-onSurfaceSubdued')?.textContent) || null;
            descText = clean(node.querySelector('p.text-onSurfaceSubdued.text-bodyRegular')?.textContent) || null;
            sellerName = clean(node.querySelector('span.text-bodyRegularStrong.text-onSurfaceSubdued')?.textContent) || null;

            const tagEls = node.querySelectorAll('span.rounded-xsmall.text-bodySmall.text-onSurfaceSubdued');
            tags = [...tagEls].map((el) => clean(el.textContent)).filter(Boolean);
          }

          const titleText = clean(titleAnchor?.textContent);
          const href = titleAnchor?.getAttribute('href') || dataHref;
          if (!href) return null;
          if (seenHrefs.has(href)) return null;
          seenHrefs.add(href);

          return {
            card_index: index,
            external_id: dataAdId,
            href,
            title: titleText || null,
            price_text: priceText,
            location_text: locationText,
            details_text: detailsText,
            description_text: descText,
            tags,
            seller_name: sellerName,
            images: collectImages(node),
            ld_json: parseLdJson(node),
            data_attributes: collectDataAttrs(node),
          };
        })
        .filter(Boolean);
    }
  );
}

async function scrapeDetail(page) {
  return page.evaluate(() => {
    const clean = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const safeAttr = (selector, attr) => document.querySelector(selector)?.getAttribute(attr) || null;
    const meta = (name) =>
      document.querySelector(`meta[property="${name}"]`)?.content ||
      document.querySelector(`meta[name="${name}"]`)?.content ||
      null;

    const ldJson = [...document.querySelectorAll('script[type="application/ld+json"]')]
      .map((script) => {
        try {
          return JSON.parse(script.textContent || '');
        } catch {
          return null;
        }
      })
      .filter(Boolean);

    const title =
      clean(document.querySelector('#viewad-title')?.textContent) ||
      clean(document.querySelector('h1#viewad-title')?.textContent) ||
      clean(document.querySelector('h1')?.textContent) ||
      null;
    const price =
      clean(document.querySelector('#viewad-price')?.textContent) ||
      clean(document.querySelector('h2#viewad-price')?.textContent) ||
      null;
    const locality = clean(document.querySelector('#viewad-locality')?.textContent) || null;
    const street = clean(document.querySelector('#street-address')?.textContent) || null;
    const description =
      clean(document.querySelector('#viewad-description-text')?.textContent) ||
      clean(document.querySelector('#viewad-description')?.textContent) ||
      null;

    const details = [...document.querySelectorAll('.addetailslist--detail')].map((el) => {
      const labelNode = el.cloneNode(true);
      for (const child of [...labelNode.querySelectorAll('.addetailslist--detail--value')]) {
        child.remove();
      }
      return {
        label: clean(labelNode.textContent),
        value: clean(el.querySelector('.addetailslist--detail--value')?.textContent),
      };
    });

    const features = [
      ...new Set(
        [...document.querySelectorAll('.checktag, .checktaglist .checktag, #viewad-extra-info li')]
          .map((el) => clean(el.textContent))
          .filter(Boolean)
      ),
    ];

    const imageNodes = [
      ...document.querySelectorAll(
        '#viewad-image, #viewad-thumbnails img, #viewad-product img, .galleryimage--element, .galleryimage--element--image, [data-imgsrc]'
      ),
    ];
    const images = [
      ...new Set(
        imageNodes
          .flatMap((el) => [
            el.getAttribute('src'),
            el.getAttribute('data-imgsrc'),
            el.getAttribute('data-src'),
            el.getAttribute('data-image'),
            el.getAttribute('content'),
          ])
          .filter(Boolean)
      ),
    ];

    const seller =
      clean(document.querySelector('.userprofile-vip-name')?.textContent) ||
      clean(document.querySelector('#viewad-contact .userprofile-vip a')?.textContent) ||
      null;
    const sellerType =
      clean(document.querySelector('.userprofile-details')?.textContent) ||
      clean(document.querySelector('.userprofile-vip-details')?.textContent) ||
      null;
    const sellerProfileHref = safeAttr('.userprofile-vip-name a, #viewad-contact .userprofile-vip a', 'href');

    const adIdNode = [...document.querySelectorAll('#viewad-ad-id-box, .l-container .text-light')].find((node) =>
      /Anzeigen-?ID/i.test(node.textContent || '')
    );
    const visibleAdId = clean(adIdNode?.textContent);

    const inlineScripts = [...document.querySelectorAll('script:not([src])')];
    const embeddedStateSnippets = inlineScripts
      .map((s) => s.textContent || '')
      .filter((text) => /window\.__|__INITIAL_STATE__|__NUXT__|dataLayer\s*=|liberty\.config|belen_conf/i.test(text))
      .map((text) => text.slice(0, 8000));

    return {
      pageTitle: document.title || null,
      canonical: safeAttr('link[rel="canonical"]', 'href'),
      ogTitle: meta('og:title'),
      ogDescription: meta('og:description'),
      ogImage: meta('og:image'),
      ogUrl: meta('og:url'),
      keywords: meta('keywords'),
      title,
      price,
      locality,
      street,
      description,
      details,
      features,
      images,
      seller,
      sellerType,
      sellerProfileHref,
      visibleAdId,
      ldJson,
      embeddedStateSnippets,
    };
  });
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

async function dumpDebugArtifacts(page, label) {
  const baseDir = DEBUG_DIR;
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

// Upsert each card row into the iron_cards table. Returns the rows we wrote
// (the same shape as the on-disk JSON used to take) so callers can log/preview.
async function persistCardsToIron(pool, allCards) {
  const rows = allCards.map((card) =>
    buildBronzeRow({
      card,
      detail: null,
      finalUrl: null,
      detailStatus: 'cards_only',
      extractionNotes: [],
    })
  );
  for (const row of rows) {
    if (!row.external_id || !row.listing_url) continue;
    await db.upsertIronCard(pool, {
      sourceName: row.listing_source,
      externalId: row.external_id,
      detailUrl: row.listing_url,
      sourceUrl: row.source_url,
      data: row,
      scrapedAt: row.scraped_at,
    });
  }
  return rows;
}

async function collectCards(page, pool) {
  const allCards = [];
  const seenKeys = new Set();
  const stats = { pagesVisited: 0, cardsSeen: 0 };

  for (let pageNumber = 1; pageNumber <= MAX_PAGES; pageNumber += 1) {
    const url = buildSearchUrl(pageNumber);
    console.log(`\n[search ${pageNumber}/${MAX_PAGES}] ${url}`);

    try {
      // domcontentloaded (not networkidle2): kleinanzeigen runs DataDome, whose
      // interstitial never reaches network-idle — networkidle2 silently ate the
      // full timeout on page 2. Now we resolve fast and check for a challenge.
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT });
    } catch (error) {
      console.warn(`  navigation failed: ${error.message}`);
      await dumpDebugArtifacts(page, `search-page-${pageNumber}-nav`);
      break;
    }

    // This scraper previously had NO challenge detection, so a DataDome block
    // looked like a generic timeout. Surface it explicitly and stop.
    const challenge = await stealth.detectChallenge(page);
    if (challenge) {
      console.warn(`  challenge detected (${challenge}); stopping`);
      await dumpDebugArtifacts(page, `search-page-${pageNumber}-${challenge}`);
      break;
    }

    await acceptConsent(page);
    stats.pagesVisited += 1;

    try {
      await page.waitForSelector('article[data-adid]', { timeout: PAGE_TIMEOUT });
    } catch {
      console.warn('  no listings detected on this page; stopping pagination');
      await dumpDebugArtifacts(page, `search-page-${pageNumber}`);
      break;
    }

    const cards = await scrapeCards(page);
    stats.cardsSeen += cards.length;
    console.log(`  cards on page: ${cards.length}`);

    let added = 0;
    for (const card of cards) {
      card.search_page = pageNumber;
      const key = canonicalKey(card);
      if (seenKeys.has(key)) continue;
      seenKeys.add(key);
      allCards.push(card);
      added += 1;
      if (MAX_LISTINGS != null && allCards.length >= MAX_LISTINGS) break;
    }
    console.log(`  new unique cards: ${added} (total: ${allCards.length})`);

    await persistCardsToIron(pool, allCards);
    console.log(`  upserted ${allCards.length} cards into iron_cards`);

    if (MAX_LISTINGS != null && allCards.length >= MAX_LISTINGS) break;

    if (pageNumber < MAX_PAGES && PAGE_DELAY_MS > 0) {
      console.log(`  backing off ${PAGE_DELAY_MS}ms before next page...`);
      await sleep(PAGE_DELAY_MS);
    }
  }

  return { allCards, stats };
}

function buildBronzeRow({ card, detail, finalUrl, detailStatus, extractionNotes }) {
  const listingUrl = absoluteUrl(card.href);
  const externalId =
    card.external_id ||
    extractIdFromUrl(listingUrl) ||
    extractIdFromUrl(finalUrl) ||
    null;

  const images = dedupArray([...(card.images || []), ...((detail && detail.images) || [])]);

  return {
    listing_source: LISTING_SOURCE,
    source_url: SEARCH_URL,
    listing_url: listingUrl,
    external_id: externalId,
    scraped_at: new Date().toISOString(),
    scrape_metadata: {
      search_page: card.search_page ?? null,
      card_index: card.card_index ?? null,
      final_url: finalUrl || listingUrl || null,
      detail_scrape_status: detailStatus,
      extraction_notes: extractionNotes,
    },
    raw_payload: {
      card,
      detail,
      embedded_json: (detail && detail.ldJson) || [],
      images,
      scripts_or_state: (detail && detail.embeddedStateSnippets) || [],
    },
  };
}

async function visitDetail(detailPage, listingUrl) {
  const notes = [];
  let finalUrl = listingUrl;
  let detail = null;
  let status = 'ok';

  try {
    await detailPage.goto(listingUrl, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT });
  } catch (error) {
    notes.push(`goto: ${error.message}`);
    status = 'navigation_failed';
    finalUrl = detailPage.url() || listingUrl;
    return { detail, finalUrl, status, notes };
  }

  finalUrl = detailPage.url() || listingUrl;
  await acceptConsent(detailPage).catch(() => {});

  try {
    await detailPage.waitForSelector('#viewad-title, h1, #viewad-main', { timeout: 15_000 });
  } catch {
    notes.push('detail body selectors not found within timeout');
    status = 'detail_partial';
  }

  try {
    detail = await scrapeDetail(detailPage);
  } catch (error) {
    notes.push(`extract: ${error.message}`);
    status = status === 'ok' ? 'extract_failed' : status;
  }

  return { detail, finalUrl, status, notes };
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
    await preparePage(searchPage);

    const { allCards, stats } = await collectCards(searchPage, pool);

    console.log('');
    console.log('Scrape complete (cards only)');
    console.log(`Search pages visited: ${stats.pagesVisited}`);
    console.log(`Cards observed:       ${stats.cardsSeen}`);
    console.log(`Unique cards:         ${allCards.length}`);
    console.log(`Output:               iron_cards table (source=${LISTING_SOURCE})`);
    console.log('');
  } finally {
    await browser.close();
    await db.closePool();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
