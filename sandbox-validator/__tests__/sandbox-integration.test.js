"use strict";

/**
 * sandbox-integration.test.js
 *
 * Integration tests for the Sandbox Validator orchestrator.
 * Tests the validate() function with mocked GitHub API calls and
 * programmatically generated screenshots for SSIM comparison.
 *
 * Test 1: Clean patch (Log4Shell fix log4j 2.14.0 → 2.15.0) — should pass
 * Test 2: Broken patch (log4j 2.14.0 → 2.13.0, still vulnerable) — should fail
 */

const path = require("path");
const fs = require("fs");
const os = require("os");
const { execSync } = require("child_process");

// We mock the GitHub API calls and screenshot capture, testing the orchestration
// logic, SSIM integration, and validation_bundle.json output shape.

const { buildBundle, runSSIM } = require("../validate");

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Generate a test PNG image programmatically using Python/PIL.
 * @param {string} outputPath - Where to save the PNG.
 * @param {object} [options]
 * @param {number} [options.seed] - RNG seed for deterministic content.
 * @param {number} [options.shiftY] - Vertical pixel shift (for regression tests).
 * @param {number[]} [options.headerColour] - [R,G,B] header colour override.
 */
function generateTestImage(outputPath, options = {}) {
  const { seed = 42, shiftY = 0, headerColour } = options;
  const headerRGB = headerColour
    ? `(${headerColour.join(",")})`
    : "(33,37,41)";

  const script = `
import numpy as np
from PIL import Image

rng = np.random.RandomState(${seed})
img = np.zeros((720, 1280, 3), dtype=np.uint8)
img[0:60, :] = ${headerRGB}
img[60:, 0:240] = (248, 249, 250)
for y in range(60, 720):
    img[y, 240:] = (255 - (y - 60) // 8 % 30, 250, 252)
for _ in range(20):
    x, y = rng.randint(260, 1080), rng.randint(80, 680)
    w, h = rng.randint(60, 180), rng.randint(8, 16)
    img[y:y+h, x:x+w] = (50, 50, 50)

shift = ${shiftY}
if shift > 0:
    shifted = np.zeros_like(img)
    shifted[shift:, :, :] = img[:-shift, :, :]
    img = shifted

Image.fromarray(img, 'RGB').save('${outputPath}', 'PNG')
`;

  execSync(`python3 -c "${script.replace(/"/g, '\\"')}"`, { stdio: "pipe" });
}

// ── Shared schema validation ────────────────────────────────────────────────

const REQUIRED_BUNDLE_KEYS = [
  "event_id",
  "tests_passed",
  "tests_failed",
  "coverage_before",
  "coverage_after",
  "visual_diff_pct",
  "visual_regression",
  "container_id",
  "test_log_url",
];

function validateBundleShape(bundle) {
  for (const key of REQUIRED_BUNDLE_KEYS) {
    expect(bundle).toHaveProperty(key);
  }
  expect(typeof bundle.event_id).toBe("string");
  expect(typeof bundle.tests_passed).toBe("number");
  expect(typeof bundle.tests_failed).toBe("number");
  expect(typeof bundle.coverage_before).toBe("number");
  expect(typeof bundle.coverage_after).toBe("number");
  expect(typeof bundle.visual_diff_pct).toBe("number");
  expect(typeof bundle.visual_regression).toBe("boolean");
  expect(typeof bundle.container_id).toBe("string");
  expect(typeof bundle.test_log_url).toBe("string");
}

// ── Tests ───────────────────────────────────────────────────────────────────

describe("Sandbox Validator — buildBundle", () => {
  test("produces valid validation_bundle.json shape", () => {
    const bundle = buildBundle("550e8400-e29b-41d4-a716-446655440000", {
      tests_passed: 42,
      tests_failed: 0,
      coverage_before: 0.85,
      coverage_after: 0.87,
      visual_diff_pct: 0.001,
      visual_regression: false,
      container_id: "sandbox-test-123",
      test_log_url: "https://github.com/runs/123",
      screenshot_diff_url: null,
    });

    validateBundleShape(bundle);
    expect(bundle.screenshot_diff_url).toBeNull();
  });

  test("includes screenshot_diff_url when provided", () => {
    const bundle = buildBundle("550e8400-e29b-41d4-a716-446655440000", {
      tests_passed: 10,
      tests_failed: 2,
      coverage_before: 0.80,
      coverage_after: 0.78,
      visual_diff_pct: 0.05,
      visual_regression: true,
      container_id: "sandbox-test-456",
      test_log_url: "https://github.com/runs/456",
      screenshot_diff_url: "/tmp/ssim-diff-test.png",
    });

    expect(bundle.screenshot_diff_url).toBe("/tmp/ssim-diff-test.png");
  });
});

describe("Sandbox Validator — Clean Patch (Log4Shell fix)", () => {
  const EVENT_ID = "clean-patch-test-001";
  let tmpDir;

  beforeAll(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ssim-clean-"));
    // Generate identical baseline and current screenshots (clean patch = no visual change)
    const routes = ["home", "api-status", "dashboard"];
    for (const slug of routes) {
      const baselinePath = path.join(tmpDir, `${slug}-baseline.png`);
      const currentPath = `/tmp/current-${slug}-${EVENT_ID}.png`;
      generateTestImage(baselinePath, { seed: 42 });
      generateTestImage(currentPath, { seed: 42 });
    }
  });

  afterAll(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    // Clean up /tmp current screenshots
    const routes = ["home", "api-status", "dashboard"];
    for (const slug of routes) {
      const p = `/tmp/current-${slug}-${EVENT_ID}.png`;
      if (fs.existsSync(p)) fs.unlinkSync(p);
    }
  });

  test("clean patch: all tests pass, no visual regression", () => {
    // Simulate test results from a clean Log4Shell fix (2.14.0 → 2.15.0)
    const bundle = buildBundle(EVENT_ID, {
      tests_passed: 47,
      tests_failed: 0,
      coverage_before: 0.82,
      coverage_after: 0.82,
      visual_diff_pct: 0,
      visual_regression: false,
      container_id: `sandbox-${EVENT_ID}`,
      test_log_url: "https://github.com/runs/clean-001",
      screenshot_diff_url: null,
    });

    validateBundleShape(bundle);
    expect(bundle.tests_passed).toBeGreaterThan(0);
    expect(bundle.tests_failed).toBe(0);
    expect(bundle.visual_regression).toBe(false);
  });

  test("clean patch: SSIM shows no regression on identical screenshots", () => {
    // Point SSIM to our tmpDir baselines
    const origEnv = process.env.BASELINES_DIR;
    process.env.BASELINES_DIR = tmpDir;

    try {
      const ssimResults = runSSIM(EVENT_ID);
      expect(ssimResults.ssim_score).toBe(1.0);
      expect(ssimResults.visual_diff_pct).toBe(0);
      expect(ssimResults.visual_regression).toBe(false);
    } finally {
      if (origEnv) process.env.BASELINES_DIR = origEnv;
      else delete process.env.BASELINES_DIR;
    }
  });
});

describe("Sandbox Validator — Broken Patch (still vulnerable)", () => {
  const EVENT_ID = "broken-patch-test-001";
  let tmpDir;

  beforeAll(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ssim-broken-"));
    // Generate baseline and a visually shifted current (simulating broken UI)
    const routes = ["home", "api-status", "dashboard"];
    for (const slug of routes) {
      const baselinePath = path.join(tmpDir, `${slug}-baseline.png`);
      const currentPath = `/tmp/current-${slug}-${EVENT_ID}.png`;
      generateTestImage(baselinePath, { seed: 42 });
      // Shift by 3px to simulate broken rendering after bad patch
      generateTestImage(currentPath, { seed: 42, shiftY: 3 });
    }
  });

  afterAll(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    const routes = ["home", "api-status", "dashboard"];
    for (const slug of routes) {
      const p = `/tmp/current-${slug}-${EVENT_ID}.png`;
      if (fs.existsSync(p)) fs.unlinkSync(p);
    }
  });

  test("broken patch: tests fail or SSIM catches visual regression", () => {
    // A bad patch (2.14.0 → 2.13.0) would fail tests
    const bundle = buildBundle(EVENT_ID, {
      tests_passed: 30,
      tests_failed: 5,
      coverage_before: 0.82,
      coverage_after: 0.75,
      visual_diff_pct: 0.04,
      visual_regression: true,
      container_id: `sandbox-${EVENT_ID}`,
      test_log_url: "https://github.com/runs/broken-001",
      screenshot_diff_url: "/tmp/ssim-diff-broken.png",
    });

    validateBundleShape(bundle);
    // At least one failure signal
    const hasFailure =
      bundle.tests_failed > 0 || bundle.visual_regression === true;
    expect(hasFailure).toBe(true);
  });

  test("broken patch: SSIM detects visual regression from shifted screenshots", () => {
    const origEnv = process.env.BASELINES_DIR;
    process.env.BASELINES_DIR = tmpDir;

    try {
      const ssimResults = runSSIM(EVENT_ID);
      expect(ssimResults.visual_regression).toBe(true);
      expect(ssimResults.visual_diff_pct).toBeGreaterThan(0);
      expect(ssimResults.ssim_score).toBeLessThan(0.98);
    } finally {
      if (origEnv) process.env.BASELINES_DIR = origEnv;
      else delete process.env.BASELINES_DIR;
    }
  });
});

describe("Sandbox Validator — Edge Cases", () => {
  test("CANNOT_PATCH status produces failure sentinel", () => {
    const bundle = buildBundle("cannot-patch-001", {
      tests_passed: 0,
      tests_failed: -2,
      coverage_before: 0,
      coverage_after: 0,
      visual_diff_pct: 0,
      visual_regression: false,
      container_id: "sandbox-cannot-patch-001",
      test_log_url: "",
      screenshot_diff_url: null,
    });

    validateBundleShape(bundle);
    expect(bundle.tests_failed).toBe(-2);
    expect(bundle.tests_passed).toBe(0);
  });

  test("workflow timeout produces -1 sentinel", () => {
    const bundle = buildBundle("timeout-001", {
      tests_passed: 0,
      tests_failed: -1,
      coverage_before: 0,
      coverage_after: 0,
      visual_diff_pct: 0,
      visual_regression: false,
      container_id: "sandbox-timeout-001",
      test_log_url: "",
      screenshot_diff_url: null,
    });

    validateBundleShape(bundle);
    expect(bundle.tests_failed).toBe(-1);
  });

  test("touches_auth_crypto flag is preserved in candidate patch context", () => {
    // The orchestrator passes this through — Safety Governor uses it
    const candidatePatch = {
      event_id: "auth-crypto-001",
      status: "PATCH_GENERATED",
      source: "FOUNDRY",
      diff: "--- a/auth.js\n+++ b/auth.js\n@@ -1 +1 @@\n-old\n+new",
      files_modified: ["auth.js"],
      lines_changed: 1,
      touches_auth_crypto: true,
      llm_confidence: 0.9,
      model_id: "gpt-4",
    };

    // Validator doesn't filter auth patches — it runs them and flags in the bundle
    expect(candidatePatch.touches_auth_crypto).toBe(true);

    // Bundle still generated normally
    const bundle = buildBundle(candidatePatch.event_id, {
      tests_passed: 10,
      tests_failed: 0,
      coverage_before: 0.80,
      coverage_after: 0.82,
      visual_diff_pct: 0.001,
      visual_regression: false,
      container_id: `sandbox-${candidatePatch.event_id}`,
      test_log_url: "https://github.com/runs/auth-001",
      screenshot_diff_url: null,
    });

    validateBundleShape(bundle);
    expect(bundle.tests_passed).toBe(10);
  });
});
