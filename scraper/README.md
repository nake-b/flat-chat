# Puppeteer apartment scraper

This example opens the inberlinwohnen apartment finder, waits for the rendered listing data, extracts the current page of apartments, and writes `wohninberlin.json`.

Run it with:

```bash
npm run start
```

On this machine, use the system Chromium script because Puppeteer's downloaded browser is missing native libraries:

```bash
npm run start:chromium
MAX_PAGES=2 npm run start:chromium
```

Useful options:

```bash
MAX_PAGES=3 npm run start
HEADLESS=false npm run start
PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium-browser npm run start
```

What to study in `wohninberlinscraper.js`:

- `puppeteer.launch()` starts the browser.
- `page.goto()` loads the target page.
- `page.waitForFunction()` waits until apartment text exists in the rendered page.
- `page.$$eval()` runs scraping code inside the browser and returns plain data to Node.
- `clickNextPage()` shows how to click a real page control and wait for new data.
