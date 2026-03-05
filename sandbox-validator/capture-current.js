"use strict";

/**
 * capture-current.js
 *
 * Captures post-patch screenshots of the demo app routes.
 * Same structure as capture-baseline.js but saves to /tmp/current-[route]-[event_id].png.
 * Called from the GitHub Actions workflow AFTER the test suite runs.
 *
 * Usage:
 *   BASE_URL=http://localhost:3000 EVENT_ID=abc-123 node capture-current.js
 *
 * Env vars:
 *   BASE_URL  — Base URL of the running demo app (default: http://localhost:3000)
 *   EVENT_ID  — Event ID from the candidate patch (required)
 */

const puppeteer = require("puppeteer");
const path = require("path");
const fs = require("fs");

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";
const EVENT_ID = process.env.EVENT_ID;
const OUTPUT_DIR = "/tmp";

const { ROUTES, VIEWPORT } = require("./capture-baseline");

/**
 * Capture post-patch screenshots for all routes.
 * @param {string} eventId - Event ID for filename uniqueness.
 * @param {string} [baseUrl] - Base URL override.
 * @returns {Promise<{ route: string; file: string }[]>} Captured file paths.
 */
async function captureCurrentScreenshots(eventId, baseUrl) {
  if (!eventId) {
    throw new Error("EVENT_ID is required for post-patch screenshot capture");
  }

  const url = baseUrl || BASE_URL;

  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  const results = [];

  try {
    for (const route of ROUTES) {
      const fullUrl = `${url}${route.path}`;
      const filename = `current-${route.slug}-${eventId}.png`;
      const outputPath = path.join(OUTPUT_DIR, filename);

      const page = await browser.newPage();
      try {
        await page.setViewport(VIEWPORT);
        await page.goto(fullUrl, { waitUntil: "networkidle0", timeout: 30000 });
        await page.screenshot({ path: outputPath, fullPage: true });
      } finally {
        await page.close();
      }

      console.log(`Captured ${fullUrl} → ${outputPath}`);
      results.push({ route: route.path, file: outputPath });
    }
  } finally {
    await browser.close();
  }

  // Write manifest
  const manifest = {
    prefix: "current",
    event_id: eventId,
    base_url: url,
    captured_at: new Date().toISOString(),
    screenshots: results.map((r) => ({
      route: r.route,
      file: r.file,
    })),
  };

  const manifestPath = path.join(OUTPUT_DIR, `current-manifest-${eventId}.json`);
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  console.log(`Manifest written to ${manifestPath}`);

  return results;
}

// Run when executed directly
if (require.main === module) {
  if (!EVENT_ID) {
    console.error("Error: EVENT_ID environment variable is required");
    process.exit(1);
  }
  captureCurrentScreenshots(EVENT_ID).catch((err) => {
    console.error("Post-patch capture failed:", err.message);
    process.exit(1);
  });
}

module.exports = { captureCurrentScreenshots };
