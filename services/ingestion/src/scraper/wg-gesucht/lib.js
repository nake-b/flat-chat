// Shared Puppeteer helpers for wg-gesucht scrapers (cards + detail).
// Browser-side helpers (e.g. parseGermanNumber) live inline in each scraper's
// evaluate blocks; this file holds only Node-side utilities.

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

// wg-gesucht uses ConsentManager.net (#cmpbox).
async function acceptConsent(page) {
  const selectors = [
    // Prefer "Speichern" (save current selection) — it dismisses the
    // consentmanager.net box without triggering the full accept-all vendor
    // load that was confusing the scraper. Try it before any accept button.
    '.cmpboxbtnsave',
    '#cmpbntsavetxt',
    '#cmpwelcomebtnyes',
    '.cmpboxbtnyes',
    '.cmpboxbtn[role="button"]',
    '#cmpbntyestxt',
    'button[aria-label*="accept" i]',
    'button[aria-label*="Akzeptieren"]',
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
          /speichern|alle akzeptieren|akzeptieren|einverstanden|zustimmen|accept all|i agree|save/i.test(
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
      // ignore inaccessible frames
    }
  }

  return false;
}

// Thin wrapper over the shared stealth helper, fixing wg-gesucht's German
// Accept-Language. Pass `userAgent: null` (the new default) to rotate a current
// Chrome UA per run; an explicit USER_AGENT env override still wins. The old
// manual navigator patches are gone — the stealth plugin owns those now.
async function preparePage(page, { userAgent = null, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  return stealth.applyStealthToPage(page, {
    userAgent,
    acceptLanguage: 'de-DE,de;q=0.9,en-US;q=0.7,en;q=0.6',
    timeoutMs,
  });
}

async function dumpDebugArtifacts(page, baseDir, label) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const htmlPath = path.join(baseDir, `wggesucht-debug-${label}-${stamp}.html`);
  const pngPath = path.join(baseDir, `wggesucht-debug-${label}-${stamp}.png`);
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
// wg-gesucht embeds a hidden 0x0 invisible-mode reCAPTCHA on every page for form
// protection, so we only flag captcha iframes that are visibly rendered.
async function detectChallenge(page) {
  return page.evaluate(() => {
    const title = (document.title || '').toLowerCase();
    if (title.includes('just a moment') || title.includes('attention required')) return 'cloudflare_challenge';
    if (document.querySelector('#challenge-running, #cf-please-wait, .cf-browser-verification')) {
      return 'cloudflare_challenge';
    }
    const captchaFrames = [...document.querySelectorAll('iframe[src*="hcaptcha"], iframe[src*="recaptcha"]')];
    for (const frame of captchaFrames) {
      const rect = frame.getBoundingClientRect();
      const style = window.getComputedStyle(frame);
      if (
        rect.width > 50 &&
        rect.height > 50 &&
        style.display !== 'none' &&
        style.visibility !== 'hidden'
      ) {
        return 'captcha';
      }
    }
    return null;
  });
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
};
