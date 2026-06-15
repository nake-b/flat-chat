const fs = require('node:fs/promises');
const path = require('node:path');
const puppeteer = require('puppeteer');

const START_URL =
  process.env.START_URL ||
  'https://www.immowelt.de/classified-search?distributionTypes=Rent&estateTypes=Apartment&locations=AD08DE8634';
const LISTING_SOURCE = 'immowelt';
const OUTPUT_FILE = process.env.OUTPUT_FILE || path.join(__dirname, 'immowelt.json');
const MAX_PAGES = Number.parseInt(process.env.MAX_PAGES || '1', 10);
const MAX_LISTINGS = process.env.MAX_LISTINGS ? Number.parseInt(process.env.MAX_LISTINGS, 10) : null;
const DETAIL_DELAY_MS = Number.parseInt(process.env.DETAIL_DELAY_MS || '750', 10);
const SHELL_LINK_PATTERNS = [
  /\/$/,
  /\/anbieten\//i,
  /\/immobilienpreise\//i,
  /\/projekte\//i,
  /\/ratgeber\//i,
  /\/umzug\//i,
  /\/finanzieren\//i,
  /\/gewerbe\//i,
  /#/,
];

function printBanner() {
  console.log('');
  console.log('Immowelt scraper');
  console.log('================');
  console.log(`Source: ${START_URL}`);
  console.log(`Target pages: ${MAX_PAGES}`);
  console.log(`Listing cap: ${MAX_LISTINGS || 'none'}`);
  console.log(`Output: ${OUTPUT_FILE}`);
  console.log('');
}

function progressBar(current, total, width = 28) {
  const safeTotal = Math.max(total, 1);
  const ratio = Math.min(current / safeTotal, 1);
  const filled = Math.round(ratio * width);
  return `${'#'.repeat(filled)}${'-'.repeat(width - filled)}`;
}

function renderProgress({ completed, total, status }) {
  const bar = progressBar(completed, total);
  const percent = Math.round((Math.min(completed, total) / Math.max(total, 1)) * 100);
  const line = `[${bar}] ${percent}% | ${completed}/${total} | ${status}`;

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

function normalizeUrl(url) {
  if (!url) return null;

  try {
    const parsed = new URL(url, START_URL);
    parsed.hash = '';
    return parsed.toString();
  } catch {
    return null;
  }
}

function externalIdFromUrl(url) {
  if (!url) return null;

  const patterns = [
    /\/expose\/(?<id>[a-z0-9-]+)/i,
    /\/classified\/(?<id>[a-z0-9-]+)/i,
    /[?&](?:id|objectId|advertisementId)=(?<id>[a-z0-9-]+)/i,
  ];

  for (const pattern of patterns) {
    const match = url.match(pattern);
    if (match?.groups?.id) return match.groups.id;
  }

  return null;
}

function isLikelyListingUrl(url) {
  if (!url) return false;

  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }

  if (!/\.?immowelt\.de$/i.test(parsed.hostname)) return false;
  if (SHELL_LINK_PATTERNS.some((pattern) => pattern.test(parsed.pathname))) return false;

  return (
    /\/expose\/[a-z0-9-]+/i.test(parsed.pathname) ||
    /\/classified\/[a-z0-9-]+/i.test(parsed.pathname) ||
    /\/immobilien\/.+/i.test(parsed.pathname) ||
    /\/wohnung-mieten\/.+/i.test(parsed.pathname) ||
    /[?&](?:id|objectId|advertisementId)=[a-z0-9-]+/i.test(parsed.search)
  );
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function clickIfPresent(page, selectors) {
  for (const selector of selectors) {
    const element = await page.$(selector);
    if (element) {
      await element.click().catch(() => {});
      await sleep(500);
      return true;
    }
  }

  return false;
}

async function acceptConsent(page) {
  await clickIfPresent(page, [
    '#onetrust-accept-btn-handler',
    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
    'button[data-testid*="accept"]',
    'button[id*="accept"]',
    'button[aria-label*="Akzeptieren"]',
    'button[aria-label*="accept"]',
  ]);

  await page.evaluate(() => {
    const labels = [/alle akzeptieren/i, /akzeptieren/i, /accept all/i, /^accept$/i, /zustimmen/i];
    const buttons = [...document.querySelectorAll('button')];
    const button = buttons.find((candidate) => {
      const text = (candidate.innerText || candidate.textContent || '').replace(/\s+/g, ' ').trim();
      return labels.some((label) => label.test(text));
    });
    button?.click();
  }).catch(() => {});
}

async function waitForSearchPage(page) {
  const deadline = Date.now() + 30_000;

  while (Date.now() < deadline) {
    const state = await page.evaluate(() => {
      const bodyText = document.body?.innerText || '';
      const html = document.documentElement?.outerHTML || '';
      const hasListingText = /€|EUR|m²|Zimmer|Wohnung|Apartment/i.test(bodyText);
      const hasLinks = [...document.querySelectorAll('a[href]')].some((anchor) =>
        /expose|classified|immobilien|wohnung|mieten/i.test(anchor.href)
      );
      const isBlocked = /DataDome|captcha-delivery|CAPTCHA|geo\.captcha|datadome/i.test(html);

      return {
        hasListings: hasListingText || hasLinks,
        isBlocked,
        title: document.title || null,
        bodyPreview: bodyText.replace(/\s+/g, ' ').trim().slice(0, 300),
      };
    });

    if (state.isBlocked) {
      throw new Error(
        `Immowelt returned an anti-bot/CAPTCHA page instead of search results. Title: ${state.title || 'unknown'}`
      );
    }

    if (state.hasListings) return;

    await sleep(500);
  }

  const state = await page.evaluate(() => ({
    title: document.title || null,
    bodyPreview: (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 300),
    htmlPreview: (document.documentElement?.outerHTML || '').slice(0, 500),
  }));

  throw new Error(
    `Timed out waiting for Immowelt search results. Title: ${state.title || 'unknown'}; body: ${
      state.bodyPreview || '[empty]'
    }; html: ${state.htmlPreview}`
  );
}

async function collectJsonResponses(page, responses) {
  page.on('response', async (response) => {
    const url = response.url();
    const contentType = response.headers()['content-type'] || '';
    const status = response.status();

    if (status >= 400 || !/json/i.test(contentType)) return;
    if (!/immowelt|aviv|estate|classified|search|listing|graphql/i.test(url)) return;

    try {
      const payload = await response.json();
      const payloadText = JSON.stringify(payload);
      if (!/Kaltmiete|Wohnung|Apartment|Zimmer|estate|classified|listing|realEstate/i.test(payloadText)) return;

      responses.push({
        url,
        status,
        content_type: contentType,
        payload,
      });
    } catch {
      // Ignore non-JSON or already-consumed bodies.
    }
  });
}

function collectObjects(value, predicate, results = []) {
  if (!value) return results;

  if (Array.isArray(value)) {
    for (const item of value) collectObjects(item, predicate, results);
    return results;
  }

  if (typeof value === 'object') {
    if (predicate(value)) results.push(value);
    for (const item of Object.values(value)) collectObjects(item, predicate, results);
  }

  return results;
}

function listingsFromJsonResponses(responses, searchPageNumber) {
  const rows = [];

  for (const response of responses) {
    const objects = collectObjects(response.payload, (item) => {
      const text = JSON.stringify(item);
      return (
        /Kaltmiete|Wohnung zur Miete|Zimmer|m²|Apartment/i.test(text) &&
        /url|href|link|canonical|id|expose|classified|estate/i.test(text)
      );
    });

    for (const item of objects) {
      const values = Object.values(item).filter((value) => typeof value === 'string');
      const href = values.map(normalizeUrl).find(isLikelyListingUrl) || null;
      const id =
        item.id ||
        item.advertisementId ||
        item.classifiedId ||
        item.estateId ||
        item.objectId ||
        externalIdFromUrl(href);

      if (!href && !id) continue;

      rows.push({
        listing_source: LISTING_SOURCE,
        search_page: searchPageNumber,
        card_index: rows.length,
        href,
        anchor_text: item.title || item.headline || item.name || null,
        card_text: JSON.stringify(item).slice(0, 5000),
        card_html: null,
        attributes: {},
        anchor_attributes: {},
        images: values.filter((value) => /^https?:\/\/.+\.(?:jpg|jpeg|png|webp)(?:[?#].*)?$/i.test(value)),
        meta_nodes: [],
        response_url: response.url,
        response_payload: item,
      });
    }
  }

  return rows;
}

async function scrapeSearchCards(page, searchPageNumber) {
  return page.evaluate(
    ({ listingSource, searchPage }) => {
      const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
      const absoluteUrl = (value) => {
        try {
          return new URL(value, location.href).toString();
        } catch {
          return null;
        }
      };
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
      };
      const attrs = (element) =>
        [...element.attributes].reduce((result, attr) => {
          if (
            attr.name.startsWith('data-') ||
            ['id', 'class', 'aria-label', 'title', 'itemtype', 'itemprop'].includes(attr.name)
          ) {
            result[attr.name] = attr.value;
          }
          return result;
        }, {});
      const nearestCard = (anchor) =>
        anchor.closest('article, li, section, [data-testid], [data-test], [class*="card"], [class*="result"], [class*="listing"]') ||
        anchor;
      const linkLooksRelevant = (anchor) => {
        const text = clean(anchor.innerText || anchor.getAttribute('aria-label') || anchor.getAttribute('title'));
        const url = new URL(anchor.href, location.href);
        const isShellLink =
          /\/anbieten\/|\/immobilienpreise\/|\/projekte\/|\/ratgeber\/|\/umzug\/|\/finanzieren\/|\/gewerbe\//i.test(
            url.pathname
          ) || url.hash;
        const isListingPath =
          /\/expose\/[a-z0-9-]+|\/classified\/[a-z0-9-]+|\/immobilien\/.+|\/wohnung-mieten\/.+/i.test(url.pathname) ||
          /[?&](id|objectId|advertisementId)=[a-z0-9-]+/i.test(url.search);

        return !isShellLink && isListingPath && /€|EUR|m²|m2|Zimmer|Wohnung|Apartment|Berlin/i.test(text + ' ' + anchor.href);
      };
      const cardLooksRelevant = (card) => /€|EUR|m²|m2|Zimmer|Wohnung|Apartment|Berlin/i.test(clean(card.innerText));
      const imageUrls = (card) =>
        [...card.querySelectorAll('img')]
          .map((image) => image.currentSrc || image.src || image.getAttribute('data-src'))
          .filter(Boolean)
          .map(absoluteUrl)
          .filter(Boolean);
      const metaNodes = (card) =>
        [...card.querySelectorAll('[data-testid], [data-test], [itemprop], [aria-label], dt, dd, li, p, span, h2, h3')]
          .map((node) => ({
            tag: node.tagName.toLowerCase(),
            text: clean(node.innerText || node.textContent).slice(0, 500),
            attributes: attrs(node),
          }))
          .filter((item) => item.text || Object.keys(item.attributes).length)
          .slice(0, 80);

      const seen = new Set();

      return [...document.querySelectorAll('a[href]')]
        .filter(visible)
        .filter(linkLooksRelevant)
        .map((anchor) => {
          const href = absoluteUrl(anchor.href);
          if (!href || seen.has(href)) return null;
          seen.add(href);

          const card = nearestCard(anchor);
          if (!cardLooksRelevant(card)) return null;

          return {
            listing_source: listingSource,
            search_page: searchPage,
            card_index: seen.size - 1,
            href,
            anchor_text: clean(anchor.innerText || anchor.getAttribute('aria-label') || anchor.getAttribute('title')),
            card_text: clean(card.innerText),
            card_html: card.outerHTML.slice(0, 25_000),
            attributes: attrs(card),
            anchor_attributes: attrs(anchor),
            images: imageUrls(card),
            meta_nodes: metaNodes(card),
          };
        })
        .filter(Boolean);
    },
    { listingSource: LISTING_SOURCE, searchPage: searchPageNumber }
  );
}

async function scrapeDetailPage(page, listingUrl) {
  const detail = await page.evaluate(() => {
    const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
    const absoluteUrl = (value) => {
      try {
        return new URL(value, location.href).toString();
      } catch {
        return null;
      }
    };
    const attrs = (element) =>
      [...element.attributes].reduce((result, attr) => {
        if (
          attr.name.startsWith('data-') ||
          ['id', 'class', 'aria-label', 'title', 'itemtype', 'itemprop', 'name', 'property', 'content'].includes(attr.name)
        ) {
          result[attr.name] = attr.value;
        }
        return result;
      }, {});
    const parseJson = (text) => {
      try {
        return JSON.parse(text);
      } catch {
        return null;
      }
    };
    const textBlocks = (selector) =>
      [...document.querySelectorAll(selector)]
        .map((node) => ({
          tag: node.tagName.toLowerCase(),
          text: clean(node.innerText || node.textContent),
          attributes: attrs(node),
        }))
        .filter((item) => item.text || Object.keys(item.attributes).length);

    const jsonLd = [...document.querySelectorAll('script[type="application/ld+json"]')]
      .map((script) => parseJson(script.textContent))
      .filter(Boolean);
    const nextData = parseJson(document.querySelector('#__NEXT_DATA__')?.textContent || '');
    const jsonScripts = [...document.scripts]
      .map((script) => ({
        id: script.id || null,
        type: script.type || null,
        src: script.src || null,
        json: parseJson(script.textContent),
      }))
      .filter((script) => script.json)
      .slice(0, 20);

    return {
      url: location.href,
      title: document.title || null,
      canonical_url: document.querySelector('link[rel="canonical"]')?.href || null,
      meta: [...document.querySelectorAll('meta[name], meta[property]')]
        .map((meta) => attrs(meta))
        .filter((item) => item.content)
        .slice(0, 80),
      headings: textBlocks('h1, h2, h3').slice(0, 80),
      facts: textBlocks('[data-testid], [data-test], [itemprop], dl, table, ul, ol').slice(0, 120),
      description_candidates: textBlocks('article, section, [class*="description"], [data-testid*="description"]').slice(0, 40),
      body_text: clean(document.body.innerText).slice(0, 60_000),
      images: [...document.querySelectorAll('img')]
        .map((image) => image.currentSrc || image.src || image.getAttribute('data-src'))
        .filter(Boolean)
        .map(absoluteUrl)
        .filter(Boolean),
      embedded_json: {
        json_ld: jsonLd,
        next_data: nextData,
        json_scripts: jsonScripts,
      },
    };
  });

  return {
    requested_url: listingUrl,
    ...detail,
  };
}

async function clickNextSearchPage(page) {
  const previousUrl = page.url();
  const previousText = await page.evaluate(() => document.body.innerText.slice(0, 1500));

  const clicked = await page.evaluate(() => {
    const visible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const candidates = [...document.querySelectorAll('a[href], button')]
      .filter(visible)
      .filter((element) => {
        const label = [
          element.innerText,
          element.textContent,
          element.getAttribute('aria-label'),
          element.getAttribute('title'),
          element.getAttribute('rel'),
        ]
          .filter(Boolean)
          .join(' ')
          .replace(/\s+/g, ' ')
          .trim();
        return /weiter|nächste|next|vorwärts|›|>/i.test(label);
      });
    const next = candidates.find((element) => !element.disabled && element.getAttribute('aria-disabled') !== 'true');
    next?.click();
    return Boolean(next);
  });

  if (!clicked) {
    const nextUrl = new URL(page.url());
    const currentPage = Number.parseInt(nextUrl.searchParams.get('page') || nextUrl.searchParams.get('p') || '1', 10);
    nextUrl.searchParams.set('page', String(currentPage + 1));

    if (nextUrl.toString() === previousUrl) return false;

    await page.goto(nextUrl.toString(), { waitUntil: 'domcontentloaded' });
  } else {
    await page.waitForFunction(
      (oldUrl, oldText) => location.href !== oldUrl || document.body.innerText.slice(0, 1500) !== oldText,
      { timeout: 30_000 },
      previousUrl,
      previousText
    ).catch(() => {});
  }

  await sleep(1000);
  return page.url() !== previousUrl || (await page.evaluate((oldText) => document.body.innerText.slice(0, 1500) !== oldText, previousText));
}

function toBronzeRow(card, detail, status, scrapedAt) {
      const listingUrl = normalizeUrl(detail?.canonical_url || detail?.url || card.href);
  const finalUrl = normalizeUrl(detail?.url || card.href);

  return {
    listing_source: LISTING_SOURCE,
    source_url: START_URL,
    listing_url: listingUrl,
    external_id: externalIdFromUrl(listingUrl) || externalIdFromUrl(finalUrl),
    scraped_at: scrapedAt,
    scrape_metadata: {
      search_page: card.search_page,
      card_index: card.card_index,
      final_url: finalUrl,
      detail_scrape_status: status,
      extraction_notes: [
        'Search cards are extracted from visible listing-like anchors and nearest listing containers.',
        'Detail payload captures visible page text, structured DOM candidates, images, and parseable embedded JSON.',
      ],
    },
    raw_payload: {
      card,
      detail,
    },
  };
}

async function main() {
  printBanner();

  const launchOptions = {
    headless: process.env.HEADLESS !== 'false',
    defaultViewport: { width: 1365, height: 900 },
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-crash-reporter',
      '--disable-crashpad',
      '--disable-breakpad',
    ],
  };

  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  const browser = await puppeteer.launch(launchOptions);

  try {
    const searchPage = await browser.newPage();
    const searchJsonResponses = [];
    await collectJsonResponses(searchPage, searchJsonResponses);
    searchPage.setDefaultTimeout(30_000);
    await searchPage.setUserAgent(
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'
    );

    console.log(`Opening ${START_URL}`);
    const initialResponse = await searchPage.goto(START_URL, { waitUntil: 'domcontentloaded' });
    if (initialResponse && initialResponse.status() >= 400) {
      await sleep(1000);
      const html = await searchPage.evaluate(() => document.documentElement?.outerHTML || '');
      if (/DataDome|captcha-delivery|CAPTCHA|geo\.captcha|datadome/i.test(html)) {
        throw new Error(`Immowelt returned ${initialResponse.status()} with anti-bot/CAPTCHA content.`);
      }
      throw new Error(`Immowelt search page returned HTTP ${initialResponse.status()}.`);
    }
    await acceptConsent(searchPage);
    await waitForSearchPage(searchPage);

    const cardsByUrl = new Map();

    for (let pageNumber = 1; pageNumber <= MAX_PAGES; pageNumber += 1) {
      renderProgress({
        completed: pageNumber - 1,
        total: MAX_PAGES,
        status: `reading search page ${pageNumber}`,
      });

      const cards = [
        ...(await scrapeSearchCards(searchPage, pageNumber)),
        ...listingsFromJsonResponses(searchJsonResponses, pageNumber),
      ];

      for (const card of cards) {
        const normalized = normalizeUrl(card.href) || `immowelt:${card.response_payload?.id || card.card_text}`;
        if (!normalized || cardsByUrl.has(normalized)) continue;
        cardsByUrl.set(normalized, { ...card, href: normalizeUrl(card.href) });
        if (MAX_LISTINGS && cardsByUrl.size >= MAX_LISTINGS) break;
      }

      renderProgress({
        completed: pageNumber,
        total: MAX_PAGES,
        status: `search page complete, ${cardsByUrl.size} unique listings`,
      });

      if ((MAX_LISTINGS && cardsByUrl.size >= MAX_LISTINGS) || pageNumber >= MAX_PAGES) break;

      const moved = await clickNextSearchPage(searchPage);
      if (!moved) break;
      await waitForSearchPage(searchPage).catch(() => {});
    }

    finishProgressLine();

    const cards = [...cardsByUrl.values()];
    const rows = [];
    const detailPage = await browser.newPage();
    detailPage.setDefaultTimeout(30_000);
    await detailPage.setUserAgent(
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'
    );

    for (let index = 0; index < cards.length; index += 1) {
      const card = cards[index];
      renderProgress({
        completed: index,
        total: cards.length,
        status: `reading detail ${index + 1}/${cards.length}`,
      });

      const scrapedAt = new Date().toISOString();
      let detail = null;
      let status = card.href ? 'ok' : 'card_only';

      try {
        if (card.href) {
          await detailPage.goto(card.href, { waitUntil: 'domcontentloaded' });
          await acceptConsent(detailPage);
          await sleep(DETAIL_DELAY_MS);
          detail = await scrapeDetailPage(detailPage, card.href);
        } else {
          detail = {
            requested_url: null,
            note: 'No detail URL was available in the rendered card or captured JSON response.',
          };
        }
      } catch (error) {
        status = 'error';
        detail = {
          requested_url: card.href,
          error_name: error.name,
          error_message: error.message,
        };
      }

      rows.push(toBronzeRow(card, detail, status, scrapedAt));
    }

    renderProgress({
      completed: cards.length,
      total: cards.length,
      status: 'writing output',
    });
    finishProgressLine();

    await fs.writeFile(OUTPUT_FILE, `${JSON.stringify(rows, null, 2)}\n`);

    console.log('');
    console.log('Scrape complete');
    console.log(`Listings saved: ${rows.length}`);
    console.log(`File: ${OUTPUT_FILE}`);
    console.log('');
    console.table(
      rows.slice(0, 10).map((row) => ({
        external_id: row.external_id,
        listing_url: row.listing_url,
        status: row.scrape_metadata.detail_scrape_status,
      }))
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
