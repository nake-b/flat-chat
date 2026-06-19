const fs = require('node:fs/promises');
const path = require('node:path');
const vanillaPuppeteer = require('puppeteer');
const db = require('scraper-lib');
const stealth = require('scraper-lib/stealth');

// puppeteer-extra + stealth plugin, wrapping our own puppeteer engine.
const puppeteer = stealth.makeStealthPuppeteer(vanillaPuppeteer);

const URL = 'https://www.inberlinwohnen.de/wohnungsfinder/';
const LISTING_SOURCE = 'wohninberlin';
const OUTPUT_FILE = path.join(__dirname, 'wohninberlin.json');
// inberlinwohnen lists ~250 flats at 10 per page. The page loop terminates
// naturally when the "Vor" (next) button disappears, so this is just an upper
// bound generous enough to walk the whole result set.
const MAX_PAGES = Number.parseInt(process.env.MAX_PAGES || '30', 10);
const PAGE_SIZE = 10;

function printBanner() {
  console.log('');
  console.log('Wohninberlin scraper');
  console.log('====================');
  console.log(`Source: ${URL}`);
  console.log(`Target pages: ${MAX_PAGES}`);
  console.log(`Output: ${OUTPUT_FILE}`);
  console.log('');
}

function progressBar(current, total, width = 28) {
  const safeTotal = Math.max(total, 1);
  const ratio = Math.min(current / safeTotal, 1);
  const filled = Math.round(ratio * width);
  return `${'#'.repeat(filled)}${'-'.repeat(width - filled)}`;
}

function renderProgress({ completedPages, activePage, pageCount, totalScraped, status }) {
  const bar = progressBar(completedPages, pageCount);
  const percent = Math.round((Math.min(completedPages, pageCount) / Math.max(pageCount, 1)) * 100);
  const expectedListings = pageCount * PAGE_SIZE;
  const line = `[${bar}] ${percent}% | page ${activePage}/${pageCount} | ${totalScraped}/${expectedListings} listings | ${status}`;

  if (process.stdout.isTTY) {
    process.stdout.clearLine(0);
    process.stdout.cursorTo(0);
    process.stdout.write(line);
    return;
  }

  console.log(line);
}

function finishProgressLine() {
  if (process.stdout.isTTY) {
    process.stdout.write('\n');
  }
}

function parseGermanNumber(value) {
  if (value == null || value === '') return null;
  // Snapshot fields (rentNet, extraCosts) arrive as raw numbers; only the
  // detailValue() strings need German-format normalization. Guard the
  // number case so `.replace` is never called on a non-string.
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;

  const normalized = String(value)
    .replace(/[^\d,.]/g, '')
    .replace(/\./g, '')
    .replace(',', '.');

  const number = Number.parseFloat(normalized);
  return Number.isNaN(number) ? null : number;
}

function splitHeadline(headline) {
  const match = headline.match(
    /(?<rooms>[\d,.]+)\s*Zimmer,\s*(?<area>[\d,.]+)\s*m[²2],\s*(?<rent>[\d,.]+)\s*€\s*\|\s*(?<location>.+)/i
  );

  if (!match?.groups) {
    return {
      roomsText: null,
      rooms: null,
      areaText: null,
      areaSqm: null,
      rentText: null,
      rentEur: null,
      location: null,
    };
  }

  const { rooms, area, rent, location } = match.groups;

  return {
    roomsText: rooms || null,
    rooms: parseGermanNumber(rooms),
    areaText: area || null,
    areaSqm: parseGermanNumber(area),
    rentText: rent || null,
    rentEur: parseGermanNumber(rent),
    location: location || null,
  };
}

async function clickIfPresent(page, selectors) {
  for (const selector of selectors) {
    const element = await page.$(selector);
    if (element) {
      await element.click();
      return true;
    }
  }

  return false;
}

async function scrapeApartments(page) {
  return page.$$eval('button.list__item__title', (nodes, listingSource) => {
    const clean = (text) => text.replace(/\s+/g, ' ').trim();
    const isApartmentHeadline = (text) => /Zimmer/i.test(text) && /m²|m2/i.test(text) && /€|EUR/i.test(text);
    const isVisible = (node) => {
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const findSnapshotNode = (node) => {
      let current = node;

      while (current && !current.hasAttribute('wire:snapshot')) {
        current = current.parentElement;
      }

      return current;
    };
    const collectObjects = (value, predicate, results = []) => {
      if (!value) return results;

      if (Array.isArray(value)) {
        for (const item of value) {
          collectObjects(item, predicate, results);
        }
        return results;
      }

      if (typeof value === 'object') {
        if (predicate(value)) results.push(value);

        for (const item of Object.values(value)) {
          collectObjects(item, predicate, results);
        }
      }

      return results;
    };
    const collectNumbers = (value, results = []) => {
      if (typeof value === 'number') {
        results.push(value);
        return results;
      }

      if (Array.isArray(value)) {
        for (const item of value) collectNumbers(item, results);
        return results;
      }

      if (value && typeof value === 'object') {
        for (const item of Object.values(value)) collectNumbers(item, results);
      }

      return results;
    };
    const flattenDetails = (details) => collectObjects(details, (item) => Boolean(item.label));
    const detailValue = (details, label) => {
      const detail = details.find((item) => clean(item.label).toLowerCase() === label.toLowerCase());
      return detail ? clean(String(detail.value || '').replace(/<br\s*\/?>/gi, ' ')) : null;
    };
    const attributeIds = (snapshotItem, attributesSnapshot) => {
      const ids = new Set();

      for (const id of collectNumbers(attributesSnapshot?.data?.itemAttributes)) {
        ids.add(Number(id));
      }

      for (const attribute of collectObjects(snapshotItem?.attributes, (item) => item.flat_attribute_id)) {
        ids.add(Number(attribute.flat_attribute_id));
      }

      return ids;
    };
    const nestedSnapshots = (node) => {
      return [...node.getElementsByTagName('*')]
        .filter((element) => element.hasAttribute('wire:snapshot'))
        .map((element) => {
          try {
            return JSON.parse(element.getAttribute('wire:snapshot'));
          } catch {
            return null;
          }
        })
        .filter(Boolean);
    };

    return nodes
      .filter(isVisible)
      .map((node) => {
        const headline = clean(node.textContent || '');

        if (!isApartmentHeadline(headline)) {
          return null;
        }

        const itemNode = findSnapshotNode(node);

        let snapshotItem = null;
        const snapshot = itemNode?.getAttribute('wire:snapshot');

        if (snapshot) {
          try {
            const parsed = JSON.parse(snapshot);
            snapshotItem = parsed.data?.item?.[0] || null;
          } catch {
            snapshotItem = null;
          }
        }

        const company = snapshotItem?.company?.[0] || null;
        const details = flattenDetails(snapshotItem?.details);
        const attributesSnapshot = nestedSnapshots(itemNode).find(
          (nestedSnapshot) => nestedSnapshot.memo?.name === 'apartment-finder.item.partials.attributes-list'
        );
        const attributeSet = attributeIds(snapshotItem, attributesSnapshot);
        const attributesText = clean(
          itemNode
            ? [...itemNode.getElementsByTagName('*')]
                .filter((element) => element.hasAttribute('wire:snapshot'))
                .map((element) => {
                  try {
                    const parsed = JSON.parse(element.getAttribute('wire:snapshot'));
                    return parsed.memo?.name === 'apartment-finder.item.partials.attributes-list'
                      ? element.innerText
                      : '';
                  } catch {
                    return '';
                  }
                })
                .join(' ')
            : ''
        );
        const wbsText = detailValue(details, 'WBS');

        return {
          listing_source: listingSource,
          scrapeUrl: location.href,
          id: snapshotItem?.id || null,
          headline,
          title: snapshotItem?.title || null,
          objectId: snapshotItem?.objectId || null,
          occupationDate: snapshotItem?.occupationDate || null,
          floor: snapshotItem?.level ?? null,
          floorsTotal: snapshotItem?.levelsTotal ?? null,
          constructionYear: snapshotItem?.constructionYear || null,
          heating: detailValue(details, 'Heizung'),
          mainEnergySource: detailValue(details, 'Hauptenergieträger'),
          energyConsumptionValue: detailValue(details, 'Energieverbrauchskennwert'),
          finalEnergyValue: snapshotItem?.finalEnergyValue || null,
          energyPassType: snapshotItem?.energyPassType || null,
          warmRent: detailValue(details, 'Gesamtmiete'),
          warmRentEur: detailValue(details, 'Gesamtmiete'),
          coldRent: detailValue(details, 'Kaltmiete') || snapshotItem?.rentNet || null,
          coldRentEur: detailValue(details, 'Kaltmiete') || snapshotItem?.rentNet || null,
          nebenkosten: detailValue(details, 'Nebenkosten') || snapshotItem?.extraCosts || null,
          nebenkostenEur: detailValue(details, 'Nebenkosten') || snapshotItem?.extraCosts || null,
          nebentkosten: detailValue(details, 'Nebenkosten') || snapshotItem?.extraCosts || null,
          nebentkostenEur: detailValue(details, 'Nebenkosten') || snapshotItem?.extraCosts || null,
          rentGrossEur: snapshotItem?.rentGross ?? null,
          extraCostsText: snapshotItem?.extraCosts || null,
          wbsText,
          wbsRequired: wbsText ? /^erforderlich$/i.test(wbsText) : null,
          elevator: attributeSet.has(9) || /Aufzug/i.test(attributesText),
          balcony: attributeSet.has(12) || /Balkon|Loggia|Terrasse/i.test(attributesText),
          basement: attributeSet.has(17) || /Keller/i.test(attributesText),
          company: company?.name?.trim() || null,
          companyWebsite: company?.website || null,
          url: snapshotItem?.deeplink || null,
        };
      })
      .filter(Boolean);
  }, LISTING_SOURCE);
}

async function clickNextPage(page) {
  const firstHeadline = await page.evaluate(() => {
    const isApartmentHeadline = (text) => /Zimmer/i.test(text) && /m²|m2/i.test(text) && /€|EUR/i.test(text);
    const buttons = [...document.querySelectorAll('button.list__item__title')];
    const visibleButton = buttons.find((button) => {
      const style = window.getComputedStyle(button);
      const rect = button.getBoundingClientRect();
      return (
        isApartmentHeadline(button.innerText) &&
        style.visibility !== 'hidden' &&
        style.display !== 'none' &&
        rect.width > 0 &&
        rect.height > 0
      );
    });

    return visibleButton?.innerText.trim();
  });

  const clicked = await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('button')];
    const button = buttons.find((candidate) => candidate.innerText.trim() === 'Vor' && !candidate.disabled);
    button?.click();
    return Boolean(button);
  });

  if (!clicked) return false;

  await page.waitForFunction(
    (previousHeadline) => {
      const isApartmentHeadline = (text) => /Zimmer/i.test(text) && /m²|m2/i.test(text) && /€|EUR/i.test(text);
      const buttons = [...document.querySelectorAll('button.list__item__title')];
      const visibleButton = buttons.find((button) => {
        const style = window.getComputedStyle(button);
        const rect = button.getBoundingClientRect();
        return (
          isApartmentHeadline(button.innerText) &&
          style.visibility !== 'hidden' &&
          style.display !== 'none' &&
          rect.width > 0 &&
          rect.height > 0
        );
      });
      const currentHeadline = visibleButton?.innerText.trim();

      return currentHeadline && currentHeadline !== previousHeadline;
    },
    { timeout: 30_000 },
    firstHeadline
  );
  return true;
}

async function main() {
  printBanner();

  const launchOptions = {
    headless: process.env.HEADLESS !== 'false',
    defaultViewport: { width: 1365, height: 900 },
  };

  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  const browser = await puppeteer.launch({
    ...launchOptions,
  });

  try {
    const page = await browser.newPage();

    // Shared stealth helper: rotating current Chrome UA + matching client hints
    // (replaces the hardcoded Chrome/124 Linux UA). USER_AGENT env still pins it.
    await stealth.applyStealthToPage(page, {
      userAgent: process.env.USER_AGENT || null,
      acceptLanguage: 'de-DE,de;q=0.9,en-US;q=0.7,en;q=0.6',
      timeoutMs: 30_000,
    });

    console.log('Starting browser session...');
    console.log(`Opening ${URL}`);
    await page.goto(URL, { waitUntil: 'domcontentloaded' });

    await clickIfPresent(page, [
      '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
      'button[aria-label*="Akzeptieren"]',
      'button[aria-label*="accept"]',
      'button.cookie-accept',
    ]);

    await page.waitForFunction(
      () => /Zimmer/i.test(document.body.innerText) && (/m²/i.test(document.body.innerText) || /m2/i.test(document.body.innerText)),
      { timeout: 30_000 }
    );

    const apartmentsById = new Map();

    for (let pageNumber = 1; pageNumber <= MAX_PAGES; pageNumber += 1) {
      renderProgress({
        completedPages: pageNumber - 1,
        activePage: pageNumber,
        pageCount: MAX_PAGES,
        totalScraped: apartmentsById.size,
        status: 'reading listings',
      });

      const pageApartments = new Map();
      const scrapedRows = (await scrapeApartments(page)).map((apartment) => ({
        ...splitHeadline(apartment.headline),
        ...apartment,
        warmRentEur: parseGermanNumber(apartment.warmRentEur),
        coldRentEur: parseGermanNumber(apartment.coldRentEur),
        nebenkostenEur: parseGermanNumber(apartment.nebenkostenEur),
        nebentkostenEur: parseGermanNumber(apartment.nebentkostenEur),
        energyConsumptionKwh: parseGermanNumber(apartment.energyConsumptionValue),
        finalEnergyValueKwh: parseGermanNumber(apartment.finalEnergyValue),
        page: pageNumber,
        scrapedAt: new Date().toISOString(),
      }));

      for (const apartment of scrapedRows) {
        pageApartments.set(apartment.id || apartment.headline, apartment);
        apartmentsById.set(apartment.id || apartment.headline, apartment);
      }

      renderProgress({
        completedPages: pageNumber,
        activePage: pageNumber,
        pageCount: MAX_PAGES,
        totalScraped: apartmentsById.size,
        status: `page complete, +${pageApartments.size}`,
      });

      if (pageNumber >= MAX_PAGES || !(await clickNextPage(page))) {
        break;
      }

      renderProgress({
        completedPages: pageNumber,
        activePage: Math.min(pageNumber + 1, MAX_PAGES),
        pageCount: MAX_PAGES,
        totalScraped: apartmentsById.size,
        status: 'loading next page',
      });
    }

    finishProgressLine();

    const apartments = [...apartmentsById.values()];

    await fs.writeFile(OUTPUT_FILE, `${JSON.stringify(apartments, null, 2)}\n`);

    // Bronze insert — wohninberlin is a single-step source: the card already
    // carries the full listing, so we skip the iron_cards + detail-scrape
    // round-trip and write each apartment straight into raw_listings. The
    // silver wohninberlin transformer reads `data.dump` from here, matching
    // the `dump` envelope the two-step scrapers produce.
    let inserted = 0;
    if (process.env.DATABASE_URL) {
      const pool = db.getPool();
      for (const apartment of apartments) {
        const externalId = String(apartment.id ?? apartment.objectId ?? apartment.headline);
        const data = {
          listing_source: LISTING_SOURCE,
          id: externalId,
          scrapeUrl: apartment.url || apartment.scrapeUrl,
          scrapedAt: apartment.scrapedAt,
          dump: apartment,
        };
        await db.upsertRawListing(pool, {
          sourceName: LISTING_SOURCE,
          externalId,
          sourceUrl: apartment.url || apartment.scrapeUrl,
          data,
          scrapedAt: apartment.scrapedAt,
          ironCardId: null,
        });
        inserted += 1;
      }
    } else {
      console.warn('DATABASE_URL not set — skipping bronze insert (JSON only).');
    }

    console.log('');
    console.log('Scrape complete');
    console.log(`Pages requested: ${MAX_PAGES}`);
    console.log(`Apartments saved: ${apartments.length}`);
    console.log(`Bronze rows upserted: ${inserted}`);
    console.log(`File: ${OUTPUT_FILE}`);
    console.log('');
    console.table(
      apartments.slice(0, 10).map((apartment) => ({
        rooms: apartment.rooms,
        areaSqm: apartment.areaSqm,
        rentEur: apartment.rentEur,
        location: apartment.location,
      }))
    );
  } finally {
    await browser.close();
    await db.closePool();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
