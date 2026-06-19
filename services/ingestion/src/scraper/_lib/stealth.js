// Shared browser-stealth + fingerprint helpers for every Node scraper.
//
// Why this module exists
// ----------------------
// All scrapers used to hand-roll the same three things, badly:
//   1. A single HARDCODED Chrome/124 user-agent string (April 2024) reused on
//      every request of every run — no rotation, and by mid-2026 a ~2-year-old
//      browser version is itself a bot signal.
//   2. Manual `evaluateOnNewDocument` patches that set `navigator.plugins` to
//      `[1,2,3,4,5]`. Real `navigator.plugins` is a `PluginArray` of `Plugin`
//      objects, so that patch CREATED a fingerprint anomaly instead of hiding
//      one, and it never touched the `Runtime.enable` CDP leak that DataDome /
//      Cloudflare actively probe.
//   3. A user-agent string with no matching `sec-ch-ua` client hints — the UA
//      claimed Chrome 124 while the high-entropy client hints said something
//      else, a classic mismatch tell.
//
// This module centralises:
//   - `makeStealthPuppeteer(vanilla)` — wraps a scraper's own `puppeteer`
//     instance with puppeteer-extra + the stealth plugin (handles webdriver,
//     plugins, the CDP leak, WebGL/codec tells, etc. — the things hand patches
//     can't).
//   - A pool of CURRENT desktop-Chrome user agents, rotated ONCE PER RUN (not
//     per request — a real browser does not change its UA mid-session).
//   - `applyStealthToPage()` — sets the chosen UA together with a CONSISTENT
//     `userAgentMetadata` block, so the UA string and the client hints
//     (`sec-ch-ua`, platform, full-version list) always agree.
//
// Deps (`puppeteer-extra`, `puppeteer-extra-plugin-stealth`) live in this
// package's package.json only. Scrapers keep their own `require('puppeteer')`
// as the engine and pass it to `makeStealthPuppeteer` — `addExtra` wraps the
// instance the scraper already downloaded, so no second chromium download.

const { addExtra } = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');

const DEFAULT_TIMEOUT_MS = 30_000;

// Keep these bumped roughly in step with stable Chrome. The stealth plugin
// spoofs a Chromium runtime, so the pool is Chrome-only by design — a Firefox
// UA on a Chromium browser is a guaranteed mismatch. Update the builds list
// every few Chrome releases; stale entries here are the thing this module
// exists to prevent.
const CHROME_BUILDS = [
  { major: 137, full: '137.0.7151.69' },
  { major: 136, full: '136.0.7103.114' },
  { major: 135, full: '135.0.7049.96' },
];

const PLATFORMS = [
  {
    uaToken: 'Macintosh; Intel Mac OS X 10_15_7',
    platform: 'macOS',
    platformVersion: '15.5.0',
    architecture: 'x86',
  },
  {
    uaToken: 'Windows NT 10.0; Win64; x64',
    platform: 'Windows',
    platformVersion: '15.0.0', // Windows 11 reports 13+/15 via UA-CH
    architecture: 'x86',
  },
  {
    uaToken: 'X11; Linux x86_64',
    platform: 'Linux',
    platformVersion: '',
    architecture: 'x86',
  },
];

function uaStringFor(build, platform) {
  return (
    `Mozilla/5.0 (${platform.uaToken}) AppleWebKit/537.36 ` +
    `(KHTML, like Gecko) Chrome/${build.full} Safari/537.36`
  );
}

// Build a Protocol.Emulation.UserAgentMetadata that AGREES with `ua`. Passing
// this as the 2nd arg to `page.setUserAgent` is what keeps the high-entropy
// client hints (navigator.userAgentData + the sec-ch-ua-* request headers)
// consistent with the UA string — the part the old code never did.
function metadataFor(build, platform) {
  const major = String(build.major);
  // GREASE brand — cosmetic, present in real Chrome's brand list.
  const grease = { brand: 'Not/A)Brand', version: '24' };
  return {
    brands: [
      { brand: 'Chromium', version: major },
      { brand: 'Google Chrome', version: major },
      grease,
    ],
    fullVersion: build.full,
    fullVersionList: [
      { brand: 'Chromium', version: build.full },
      { brand: 'Google Chrome', version: build.full },
      { brand: grease.brand, version: `${grease.version}.0.0.0` },
    ],
    platform: platform.platform,
    platformVersion: platform.platformVersion,
    architecture: platform.architecture,
    model: '',
    mobile: false,
  };
}

// The full rotation pool: every (build × platform) combination.
const USER_AGENT_PROFILES = CHROME_BUILDS.flatMap((build) =>
  PLATFORMS.map((platform) => ({
    userAgent: uaStringFor(build, platform),
    metadata: metadataFor(build, platform),
  }))
);

// A current default for banners / back-compat with the old DEFAULT_USER_AGENT
// export. Not the rotation source — that's `pickProfile`.
const DEFAULT_USER_AGENT = USER_AGENT_PROFILES[0].userAgent;

function pickProfile() {
  const i = Math.floor(Math.random() * USER_AGENT_PROFILES.length);
  return USER_AGENT_PROFILES[i];
}

// Resolve which (UA + metadata) profile to apply:
//   - an explicit `profile` (to reuse one UA across several tabs in a session);
//   - else a forced UA string (USER_AGENT=...): reuse the pool profile whose UA
//     matches so client hints stay coherent, or synthesise from the first entry
//     for a fully custom UA;
//   - else rotate.
function resolveProfile({ profile = null, userAgent = null } = {}) {
  if (profile) return profile;
  if (userAgent) {
    const match = USER_AGENT_PROFILES.find((p) => p.userAgent === userAgent);
    if (match) return match;
    return { userAgent, metadata: USER_AGENT_PROFILES[0].metadata };
  }
  return pickProfile();
}

// Wrap a scraper's own puppeteer with the stealth plugin. Call once per process
// and reuse the returned instance for `.launch(...)`.
function makeStealthPuppeteer(vanillaPuppeteer) {
  const extra = addExtra(vanillaPuppeteer);
  extra.use(StealthPlugin());
  return extra;
}

// Replaces the old per-scraper `preparePage`. Sets timeout + a coherent
// UA/client-hint pair + Accept-Language. Deliberately does NOT re-add the old
// manual navigator patches — the stealth plugin owns those now.
// Returns the chosen profile ({ userAgent, metadata }) so a caller that opens
// several tabs in one browser can pass it back via `profile:` to keep the whole
// session on a single UA.
async function applyStealthToPage(
  page,
  { userAgent = null, profile = null, acceptLanguage = 'de-DE,de;q=0.9,en-US;q=0.7,en;q=0.6', timeoutMs = DEFAULT_TIMEOUT_MS } = {}
) {
  const chosen = resolveProfile({ profile, userAgent });
  page.setDefaultTimeout(timeoutMs);
  // 2-arg form: UA string + metadata → consistent navigator.userAgentData and
  // sec-ch-ua-* headers.
  await page.setUserAgent(chosen.userAgent, chosen.metadata);
  await page.setExtraHTTPHeaders({ 'Accept-Language': acceptLanguage });
  // Keep navigator.languages in step with the Accept-Language header — the
  // stealth plugin defaults it to en-US, which mismatches a `de` header and is
  // itself a tell. Derive it from the same string so they can't drift.
  const languages = acceptLanguage
    .split(',')
    .map((part) => part.split(';')[0].trim())
    .filter(Boolean);
  await page.evaluateOnNewDocument((langs) => {
    Object.defineProperty(navigator, 'languages', { get: () => langs });
    Object.defineProperty(navigator, 'language', { get: () => langs[0] });
  }, languages);
  return chosen;
}

// Generic challenge detector for sites with NO passive captcha widget to
// whitelist (e.g. kleinanzeigen). Covers Cloudflare interstitials + DataDome
// (kleinanzeigen's vendor — its block page loads geo.captcha-delivery.com) +
// any visibly-rendered captcha iframe. Sites that DO embed a passive badge
// (housinganywhere, wg-gesucht) keep their own tuned detector.
async function detectChallenge(page) {
  return page.evaluate(() => {
    const title = (document.title || '').toLowerCase();
    if (title.includes('just a moment') || title.includes('attention required')) {
      return 'cloudflare_challenge';
    }
    if (document.querySelector('#challenge-running, #cf-please-wait, .cf-browser-verification')) {
      return 'cloudflare_challenge';
    }
    // DataDome: a full-page interstitial served from captcha-delivery.com.
    if (
      document.querySelector('iframe[src*="captcha-delivery.com"], iframe[src*="geo.captcha-delivery"]') ||
      /datadome/i.test(document.documentElement.outerHTML.slice(0, 4000))
    ) {
      return 'datadome';
    }
    const captchaFrames = [
      ...document.querySelectorAll('iframe[src*="hcaptcha"], iframe[src*="recaptcha"]'),
    ];
    for (const frame of captchaFrames) {
      const rect = frame.getBoundingClientRect();
      const style = window.getComputedStyle(frame);
      if (rect.width > 100 && rect.height > 100 && style.display !== 'none' && style.visibility !== 'hidden') {
        return 'captcha';
      }
    }
    return null;
  });
}

module.exports = {
  DEFAULT_USER_AGENT,
  DEFAULT_TIMEOUT_MS,
  USER_AGENT_PROFILES,
  pickProfile,
  makeStealthPuppeteer,
  applyStealthToPage,
  detectChallenge,
};
