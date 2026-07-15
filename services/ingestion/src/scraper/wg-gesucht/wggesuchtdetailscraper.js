// Phase 2: visit each pending card from iron_cards and write the detail-tier
// record straight into raw_listings (bronze). Once a listing is captured the
// matching iron row is flipped via detail_scraped_at = now() so the next run
// resumes naturally on whatever remains.

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

const ORIGIN = 'https://www.wg-gesucht.de';
const LISTING_SOURCE = 'wg-gesucht';
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

function randomDelay() {
  const lo = Math.min(MIN_DELAY_MS, MAX_DELAY_MS);
  const hi = Math.max(MIN_DELAY_MS, MAX_DELAY_MS);
  return lo + Math.floor(Math.random() * (hi - lo + 1));
}

function printBanner(targets) {
  console.log('');
  console.log('wg-gesucht.de scraper (detail pages)');
  console.log('====================================');
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

// Browser-side scrape — runs entirely in the page context.
// Returns a plain object; everything that fails returns null/[] without throwing.
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

      const findSectionByTitle = (titles) => {
        const headings = [...document.querySelectorAll('h2.section_panel_title, h2.headline-detailed-view-panel-title')];
        const heading = headings.find((h) => {
          const text = clean(h.textContent).toLowerCase();
          return titles.some((t) => text.includes(t.toLowerCase()));
        });
        if (!heading) return null;
        return heading.closest('div.panel, div.section_panel, section') || heading.parentElement;
      };

      const labelValueMap = (section) => {
        const map = {};
        if (!section) return map;
        section.querySelectorAll('.row, .col-sm-6, .col-xs-12').forEach((row) => {
          const labelEl = row.querySelector('.section_panel_detail');
          const valueEl = row.querySelector('.section_panel_value');
          if (!labelEl || !valueEl) return;
          const label = clean(labelEl.textContent);
          const value = clean(valueEl.textContent);
          if (label) map[label] = value;
        });
        return map;
      };

      const findValue = (map, ...patterns) => {
        for (const pattern of patterns) {
          const re = new RegExp(pattern, 'i');
          for (const [label, value] of Object.entries(map)) {
            if (re.test(label)) return value;
          }
        }
        return null;
      };

      const result = {
        externalId: expectedIdArg,
        canonicalUrl: canonicalUrlArg,
        url: window.location.href,
      };

      // ---- Title ----------------------------------------------------------
      result.title =
        clean(document.querySelector('h1.detailed-view-title span')?.textContent) ||
        clean(document.querySelector('h1.headline-detailed-view-title')?.textContent) ||
        clean(document.querySelector('h1')?.textContent) ||
        null;

      // ---- Address --------------------------------------------------------
      result.address = (() => {
        const section = findSectionByTitle(['Address', 'Adresse']);
        if (!section) return null;
        const detail = section.querySelector('.section_panel_detail');
        if (!detail) return null;
        const raw = clean(detail.textContent);
        // The address tends to live as two lines separated by a <br/>:
        // line 1 = street (and house number, if disclosed)
        // line 2 = "<postal> Berlin <district>"
        const html = detail.innerHTML.replace(/<br\s*\/?>/gi, '\n');
        const tmp = document.createElement('div');
        tmp.innerHTML = html;
        const lines = (tmp.innerText || tmp.textContent || '')
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean);
        const street = lines[0] || null;
        const cityLine = lines[1] || '';
        const m = cityLine.match(/^(\d{5})\s+([A-Za-zÄÖÜäöüß.\- ]+?)(?:\s+(.+))?$/);
        return {
          street,
          postalCode: m ? m[1] : null,
          city: m ? m[2].trim() : null,
          district: m ? (m[3] || null) : null,
          raw,
        };
      })();

      // ---- Price breakdown -----------------------------------------------
      // The "Kaltmiete" / "Cold rent" line on the German live page is labeled
      // simply "Miete:" — match it via an explicit exact-prefix pattern that
      // doesn't bleed into "Warmmiete" / "Gesamtmiete".
      result.price = (() => {
        const section = findSectionByTitle(['Costs', 'Kosten', 'Price', 'Preis']);
        const raw = labelValueMap(section);
        return {
          kaltmieteEur: parseGermanNumber(
            findValue(raw, 'Kaltmiete', 'Base rent', 'Cold rent', 'Net rent', '^\\s*Miete\\s*:?\\s*$')
          ),
          nebenkostenEur: parseGermanNumber(findValue(raw, 'Nebenkosten', 'Utilities', 'Additional')),
          heizkostenEur: parseGermanNumber(findValue(raw, 'Heizkosten', 'Heating')),
          sonstigeEur: parseGermanNumber(findValue(raw, 'Sonstige', 'Miscellaneous', 'Other costs')),
          warmmieteEur: parseGermanNumber(findValue(raw, 'Warmmiete', 'Total rent', 'Final rent', 'Gesamtmiete')),
          kautionEur: parseGermanNumber(findValue(raw, 'Kaution', 'Deposit')),
          ablseEur: parseGermanNumber(findValue(raw, 'Ablöse', 'Existing equipment', 'Abstand')),
          provisionEur: parseGermanNumber(findValue(raw, 'Provision', 'Commission')),
          raw,
        };
      })();

      // ---- Key facts (size, rooms, final rent) ---------------------------
      const keyFacts = {};
      document.querySelectorAll('.key_fact_detail').forEach((el) => {
        const label = clean(el.textContent);
        const valueEl =
          el.parentElement?.querySelector('.key_fact_value') ||
          el.nextElementSibling;
        const value = clean(valueEl?.textContent);
        if (label) keyFacts[label] = value;
      });
      result.keyFacts = keyFacts;
      result.areaSqm = (() => {
        for (const [label, value] of Object.entries(keyFacts)) {
          if (/size|größe|wohnfl/i.test(label)) return parseGermanNumber(value);
        }
        return null;
      })();
      result.rooms = (() => {
        for (const [label, value] of Object.entries(keyFacts)) {
          if (/rooms|zimmer/i.test(label)) return parseGermanNumber(value);
        }
        return null;
      })();
      // If the price section didn't yield a Warmmiete (common on the German
      // page where the breakdown shows "Miete + Nebenkosten + Sonstige" but
      // no total), pull the total from the key-facts strip ("Gesamtmiete" /
      // "Final rent").
      if (result.price.warmmieteEur == null) {
        for (const [label, value] of Object.entries(keyFacts)) {
          if (/gesamtmiete|final rent|total rent/i.test(label)) {
            result.price.warmmieteEur = parseGermanNumber(value);
            break;
          }
        }
      }

      // ---- Availability --------------------------------------------------
      result.availability = (() => {
        const section = findSectionByTitle(['Availability', 'Verfügbarkeit', 'Frei ab', 'Date']);
        const raw = labelValueMap(section);
        return {
          from: findValue(raw, 'Frei ab', 'From', 'Available from', 'Move'),
          until: findValue(raw, 'Frei bis', 'Until', 'Available until'),
          minStayMonths: parseGermanNumber(findValue(raw, 'Mindestmietdauer', 'min')),
          maxStayMonths: parseGermanNumber(findValue(raw, 'Maximale Mietdauer', 'max')),
          raw,
        };
      })();

      // ---- Descriptions (tabbed freitext) --------------------------------
      // `data-text` carries a CSS selector ("#freitext_0") not a bare id,
      // so resolve it via querySelector.
      result.descriptions = (() => {
        const tabs = [...document.querySelectorAll('.section_panel_tab[data-text]')];
        if (tabs.length > 0) {
          return tabs
            .map((tab) => {
              const tabName = clean(tab.querySelector('h2, h3')?.textContent) || clean(tab.textContent);
              const targetSelector = tab.getAttribute('data-text');
              let target = null;
              try {
                target = targetSelector ? document.querySelector(targetSelector) : null;
              } catch {
                target = null;
              }
              const text = target ? clean(target.innerText || target.textContent) : null;
              return { tab: tabName || null, text: text || null };
            })
            .filter((d) => d.text);
        }
        const blocks = [
          ...document.querySelectorAll('#ad_description_text, .section_freetext, [id^="freitext_"]'),
        ];
        return blocks
          .map((block) => ({ tab: null, text: clean(block.innerText || block.textContent) }))
          .filter((d) => d.text);
      })();

      // ---- Amenities (icon grid in "Further details") --------------------
      result.amenities = [...document.querySelectorAll('.utility_icons .text-center')]
        .map((node) => {
          const iconEl = node.querySelector('span[class*="mdi-"]');
          const iconClass = iconEl
            ? [...iconEl.classList].find((c) => c.startsWith('mdi-') && c !== 'mdi') || null
            : null;
          const label = clean(node.innerText || node.textContent);
          return { icon: iconClass, label };
        })
        .filter((a) => a.label);

      // ---- Images: prefer the inline JS gallery payload ------------------
      result.images = (() => {
        try {
          const gallery = window.image_gallery;
          if (gallery && Array.isArray(gallery.images)) {
            return gallery.images.map((img) => ({
              large: img.large || null,
              sized: img.sized || null,
              thumb: img.thumb || null,
              position: img.position ?? null,
            }));
          }
        } catch {
          // fall through
        }
        return [...document.querySelectorAll('#gallery_slides .sp-slide img.sp-image, .sp-slide img')]
          .map((img) => ({
            large: img.getAttribute('data-large') || img.getAttribute('data-src') || img.src || null,
            sized: img.getAttribute('data-medium') || null,
            thumb: img.getAttribute('data-small') || null,
            position: null,
          }))
          .filter((i) => i.large || i.sized || i.thumb);
      })();

      // ---- Geo: regex-extract from the inline map_config script payload --
      // wg-gesucht initialises the map via `map_config = { markers: [{lat,lng,...}], ... }`
      // inside a non-window-scoped <script>, so `window.map_config` is undefined.
      // Parse the lat/lng pair directly out of the page source.
      result.geo = (() => {
        const html = document.documentElement.outerHTML;
        const m = html.match(/"lat"\s*:\s*(-?\d+\.\d+)\s*,\s*"lng"\s*:\s*(-?\d+\.\d+)/);
        if (m) return { lat: Number(m[1]), lng: Number(m[2]) };
        return null;
      })();

      // ---- Lister info ---------------------------------------------------
      // PRIVACY: only the derived lister *type* (private/agency) is kept. The
      // poster's name is read locally ONLY to classify the type via the agency
      // heuristic below, then discarded — it is never returned/stored. The
      // "member since" date, online-status, and verified flag (all of which
      // fingerprint the individual poster) are not collected. See
      // services/ingestion/src/pii.py.
      result.lister = (() => {
        const candidates = [
          ...document.querySelectorAll('.user_profile_info, .panel.rhs_contact_information, .contact_box_sticky'),
        ];
        const visible = candidates.find((n) => n.offsetParent !== null);
        const node = visible || candidates[0] || null;
        if (!node) return null;

        // Local-only — used for the agency heuristic, then dropped.
        const name =
          clean(node.querySelector('.text-bold, .user_name, .user_profile_link, a[href*="/user/"]')?.textContent) ||
          null;

        // Heuristic: company suffix in the name, a premium/company badge,
        // or text mentioning "Wohnungsverwaltung"/"Hausverwaltung" → agency.
        const nameLooksAgency = name && /\b(GmbH|AG|UG|KG|OHG|Ltd|Inc|Wohnungsverwaltung|Hausverwaltung|Immobilien|Verwaltung|Vermietung|Rentals?)\b/i.test(name);
        const badgeLooksAgency = !!node.querySelector(
          'img[alt*="premium" i], img[alt*="company" i], .company_logo, [class*="agency" i]'
        );
        const type = nameLooksAgency || badgeLooksAgency ? 'agency' : 'private';

        return { type };
      })();

      // ---- Tags / chips --------------------------------------------------
      result.tags = (() => {
        const found = new Set();
        const selectors = [
          '.detail-categories li',
          '.noprint .label',
          '.label.label-info',
          '.tag',
          '.badge',
        ];
        for (const sel of selectors) {
          document.querySelectorAll(sel).forEach((n) => {
            const text = clean(n.innerText || n.textContent);
            if (text && text.length < 80) found.add(text);
          });
        }
        return [...found];
      })();

      // ---- Ad ID sanity check --------------------------------------------
      const adIdEl = document.querySelector('[data-ad_id]');
      result.scrapedAdId = adIdEl ? adIdEl.getAttribute('data-ad_id') : null;

      // ---- Catch-all: every other section's label/value rows -------------
      result.extra = (() => {
        const skip = new Set(
          ['Address', 'Adresse', 'Costs', 'Kosten', 'Price', 'Preis', 'Availability', 'Verfügbarkeit', 'Frei ab', 'Date'].map(
            (s) => s.toLowerCase()
          )
        );
        const panels = {};
        document.querySelectorAll('h2.section_panel_title, h2.headline-detailed-view-panel-title').forEach((h) => {
          const title = clean(h.textContent);
          if (!title) return;
          if ([...skip].some((s) => title.toLowerCase().includes(s))) return;
          const parent = h.closest('div.panel, div.section_panel, section') || h.parentElement;
          const map = labelValueMap(parent);
          if (Object.keys(map).length > 0) panels[title] = map;
        });
        return panels;
      })();

      return result;
    },
    expectedId,
    canonicalUrl
  );
}

async function buildOutputRow(card, detail, scrapedAt) {
  return {
    listing_source: LISTING_SOURCE,
    id: card.id,
    scrapeUrl: detail?.url || detail?.canonicalUrl || card.canonicalUrl || card.url,
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
      '--lang=de-DE,de',
    ],
  };
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  console.log('Launching browser...');
  const browser = await puppeteer.launch(launchOptions);

  const stats = { ok: 0, errors: 0 };

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
        await page.waitForSelector('h1.detailed-view-title, h1.headline-detailed-view-title, #basic_ad_details', {
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
        detail = await scrapeDetail(page, String(target.id), target.url);
      } catch (error) {
        console.warn(`  scrape failed: ${error.message}`);
        await dumpDebugArtifacts(page, DEBUG_DIR, `detail-${target.id}-scrape-err`);
        stats.errors += 1;
        continue;
      }

      const row = await buildOutputRow(target.card, detail, new Date().toISOString());
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
        `  ok — title="${(detail.title || '').slice(0, 60)}" street="${detail.address?.street || ''}" ` +
          `kalt=${detail.price?.kaltmieteEur ?? '–'} warm=${detail.price?.warmmieteEur ?? '–'} ` +
          `area=${detail.areaSqm ?? '–'} imgs=${detail.images?.length ?? 0}`
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
  console.log(`OK:                ${stats.ok}`);
  console.log(`Errors skipped:    ${stats.errors}`);
  console.log(`Output:            raw_listings table (source=${LISTING_SOURCE})`);
  console.log('');
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
