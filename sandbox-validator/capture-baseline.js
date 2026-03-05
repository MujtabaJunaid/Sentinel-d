"use strict";

/**
 * capture-baseline.js
 *
 * Captures full-page screenshots of three demo app routes before any patch
 * is applied. These "before" baselines are compared against post-patch
 * screenshots by the SSIM module.
 *
 * Usage:
 *   BASE_URL=http://localhost:3000 node capture-baseline.js
 *
 * Env vars:
 *   BASE_URL          — Base URL of the running demo app (default: http://localhost:3000)
 *   SCREENSHOT_PREFIX — Filename prefix: 'baseline' (default) or 'postpatch'
 *   BASELINES_DIR     — Output directory (default: ./baselines)
 */

const puppeteer = require("puppeteer");
const path = require("path");
const fs = require("fs");

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";
const SCREENSHOT_PREFIX = process.env.SCREENSHOT_PREFIX || "baseline";
const BASELINES_DIR = process.env.BASELINES_DIR
  ? path.resolve(process.env.BASELINES_DIR)
  : path.join(__dirname, "baselines");

/** Routes to capture. Slug is used as the filename component. */
const ROUTES = [
  { path: "/", slug: "home" },
  { path: "/api/status", slug: "api-status" },
  { path: "/dashboard", slug: "dashboard" },
];

const VIEWPORT = { width: 1280, height: 720 };

/**
 * Capture a single full-page screenshot of the given URL.
 * @param {import('puppeteer').Browser} browser
 * @param {string} url
 * @param {string} outputPath
 * @returns {Promise<void>}
 */
async function captureScreenshot(browser, url, outputPath) {
  const page = await browser.newPage();
  try {
    await page.setViewport(VIEWPORT);
    await page.goto(url, { waitUntil: "networkidle0", timeout: 30000 });
    await page.screenshot({ path: outputPath, fullPage: true });
  } finally {
    await page.close();
  }
}

/**
 * Main: capture all three routes and write PNGs to BASELINES_DIR.
 * @returns {Promise<{ route: string; file: string }[]>} Captured file paths
 */
async function captureBaselines() {
  fs.mkdirSync(BASELINES_DIR, { recursive: true });

  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  const results = [];

  try {
    for (const route of ROUTES) {
      const url = `${BASE_URL}${route.path}`;
      const filename = `${route.slug}-${SCREENSHOT_PREFIX}.png`;
      const outputPath = path.join(BASELINES_DIR, filename);

      console.log(`Capturing ${url} → ${outputPath}`);
      await captureScreenshot(browser, url, outputPath);
      console.log(`  ✓ Saved ${filename}`);

      results.push({ route: route.path, file: outputPath });
    }
  } finally {
    await browser.close();
  }

  const summary = {
    prefix: SCREENSHOT_PREFIX,
    base_url: BASE_URL,
    captured_at: new Date().toISOString(),
    screenshots: results.map((r) => ({
      route: r.route,
      file: path.relative(process.cwd(), r.file),
    })),
  };

  const summaryPath = path.join(BASELINES_DIR, `${SCREENSHOT_PREFIX}-manifest.json`);
  fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
  console.log(`\nManifest written to ${summaryPath}`);

  return results;
}

// Run when executed directly
if (require.main === module) {
  captureBaselines().catch((err) => {
    console.error("Baseline capture failed:", err.message);
    process.exit(1);
  });
}

module.exports = { captureBaselines, captureScreenshot, ROUTES, VIEWPORT };
