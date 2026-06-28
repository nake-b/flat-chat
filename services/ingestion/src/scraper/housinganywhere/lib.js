// Shared Puppeteer helpers for housinganywhere scrapers (cards + detail).
// Browser-side helpers live inline in each scraper's evaluate blocks; this
// file holds only Node-side utilities.

const fs = require('node:fs/promises');
const path = require('node:path');
const stealth = require('scraper-lib/stealth');

// Re-exported from the shared stealth module so existing imports keep working.
const { DEFAULT_USER_AGENT, DEFAULT_TIMEOUT_MS } = stealth;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function clickIfPresent(page, selectors) {
  for (const selector of selectors) {
    const element = await page.$(selector);
    if (element) {
      try {
        await element.click({ delay: 30 });
        return true;
      } catch {
        // try next selector
      }
    }
  }
  return false;
}

// housinganywhere.com uses OneTrust (domainKey present on every page).
// Non-fatal if the banner never shows — listings render behind it.
async function acceptConsent(page) {
  const selectors = [
    '#onetrust-accept-btn-handler',
    'button#onetrust-accept-btn-handler',
    '#accept-recommended-btn-handler',
    'button[aria-label*="accept" i]',
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
          /accept all|allow all|i agree/i.test((node.innerText || node.textContent || '').trim())
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
      // ignore inaccessible frames
    }
  }

  return false;
}

// Thin wrapper over the shared stealth helper, fixing housinganywhere's
// English-first Accept-Language. Pass `userAgent: null` (the new default) to
// rotate a current Chrome UA per run; an explicit USER_AGENT env override still
// wins. The old manual navigator patches are gone — the stealth plugin owns
// those now.
async function preparePage(page, { userAgent = null, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  return stealth.applyStealthToPage(page, {
    userAgent,
    acceptLanguage: 'en-US,en;q=0.9,de;q=0.7',
    timeoutMs,
  });
}

async function dumpDebugArtifacts(page, baseDir, label) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const htmlPath = path.join(baseDir, `housinganywhere-debug-${label}-${stamp}.html`);
  const pngPath = path.join(baseDir, `housinganywhere-debug-${label}-${stamp}.png`);
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

// Cloudflare interstitials replace the listing markup; bail early if we hit one.
// housinganywhere embeds a passive reCAPTCHA Enterprise anchor widget (the
// 256x60 "protected by reCAPTCHA" badge guarding the contact form) on normal
// listing pages, so a small visible captcha iframe is NOT a challenge — only
// flag the large challenge overlays (recaptcha bframe / hcaptcha challenge /
// DataDome interstitial are all >280px squares).
async function detectChallenge(page) {
  return page.evaluate(() => {
    const title = (document.title || '').toLowerCase();
    if (title.includes('just a moment') || title.includes('attention required')) return 'cloudflare_challenge';
    if (document.querySelector('#challenge-running, #cf-please-wait, .cf-browser-verification')) {
      return 'cloudflare_challenge';
    }
    const captchaFrames = [...document.querySelectorAll('iframe[src*="hcaptcha"], iframe[src*="recaptcha"], iframe[src*="captcha-delivery"]')];
    for (const frame of captchaFrames) {
      const src = frame.getAttribute('src') || '';
      if (src.includes('/anchor')) continue; // passive badge/checkbox widget, not a challenge
      const rect = frame.getBoundingClientRect();
      const style = window.getComputedStyle(frame);
      if (
        rect.width > 250 &&
        rect.height > 250 &&
        style.display !== 'none' &&
        style.visibility !== 'hidden'
      ) {
        return 'captcha';
      }
    }
    return null;
  });
}

// Individual units live at /room/ut{ID}/...; multi-unit student-accommodation
// complexes live at /s/{City}--{Country}/{category}/{slug}-{ID}. We only scrape
// individual units, so a non-match here doubles as the complex filter.
function extractRoomId(href) {
  if (!href) return null;
  const m = href.match(/\/room\/ut(\d+)\b/);
  return m ? m[1] : null;
}

// The search page server-renders only the first ~8 cards; the rest hydrate
// client-side via Algolia. Scroll to the bottom and wait until the card count
// is non-zero and stable across consecutive polls. The stability rule (rather
// than an absolute threshold) also makes static file:// replays terminate.
async function waitForCardsSettled(page, { timeoutMs = 20_000, pollMs = 750, stablePolls = 2 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastCount = -1;
  let stable = 0;
  while (Date.now() < deadline) {
    const count = await page.evaluate(() => {
      window.scrollTo(0, document.body.scrollHeight);
      return document.querySelectorAll('a[data-test-locator="ListingCard/Anchor"]').length;
    });
    if (count > 0 && count === lastCount) {
      stable += 1;
      if (stable >= stablePolls) return count;
    } else {
      stable = 0;
    }
    lastCount = count;
    await sleep(pollMs);
  }
  return Math.max(lastCount, 0);
}

module.exports = {
  DEFAULT_USER_AGENT,
  DEFAULT_TIMEOUT_MS,
  sleep,
  clickIfPresent,
  acceptConsent,
  preparePage,
  dumpDebugArtifacts,
  detectChallenge,
  extractRoomId,
  waitForCardsSettled,
};
