"use strict";

/**
 * validate.js — Sandbox Validator Orchestrator
 *
 * Main entry point. Given a candidate_patch.json, it:
 *  1. Reads the patch diff and event_id
 *  2. Triggers the sandbox-validator.yml GitHub Actions workflow
 *  3. Polls for workflow completion (max 15 min, every 30s)
 *  4. Reads test_results.json from workflow artifacts
 *  5. Triggers post-patch screenshot capture
 *  6. Calls Python SSIM module
 *  7. Assembles and emits validation_bundle.json
 *
 * Env vars:
 *   GITHUB_TOKEN      — GitHub API token
 *   GITHUB_OWNER      — Repository owner
 *   GITHUB_REPO       — Repository name
 *   BASELINES_DIR     — Path to baseline screenshots (default: ./baselines)
 *   CONTAINER_ID      — Container App session ID (set by workflow)
 */

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
const GITHUB_OWNER = process.env.GITHUB_OWNER;
const GITHUB_REPO = process.env.GITHUB_REPO;
const BASELINES_DIR = process.env.BASELINES_DIR || path.join(__dirname, "baselines");

const POLL_INTERVAL_MS = 30_000;
const MAX_POLL_TIME_MS = 15 * 60 * 1000;
const WORKFLOW_FILE = "sandbox-validator.yml";

/**
 * Trigger a GitHub Actions workflow dispatch.
 * @param {string} eventId
 * @param {string} diff
 * @returns {Promise<void>}
 */
async function triggerWorkflow(eventId, diff) {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`;

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({
      ref: "main",
      inputs: {
        event_id: eventId,
        patch_diff: diff || "",
      },
    }),
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Failed to trigger workflow: ${res.status} ${body}`);
  }
}

/**
 * Poll for the most recent workflow run matching our event_id.
 * @param {string} eventId
 * @returns {Promise<object>} The completed workflow run object.
 */
async function pollWorkflowCompletion(eventId) {
  const startTime = Date.now();

  while (Date.now() - startTime < MAX_POLL_TIME_MS) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));

    const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/runs?per_page=5`;
    const res = await fetch(url, {
      headers: {
        Authorization: `Bearer ${GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
      },
    });

    if (!res.ok) continue;

    const data = await res.json();
    const run = data.workflow_runs?.find(
      (r) => r.status === "completed" && r.name?.includes(eventId)
    );

    if (run) return run;
  }

  return null; // Timeout
}

/**
 * Download test_results.json artifact from a completed workflow run.
 * @param {number} runId
 * @returns {Promise<object|null>} Parsed test results or null.
 */
async function downloadTestResults(runId) {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${runId}/artifacts`;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
    },
  });

  if (!res.ok) return null;

  const data = await res.json();
  const artifact = data.artifacts?.find((a) => a.name === "test-results");

  if (!artifact) return null;

  // Download the artifact
  const dlUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/artifacts/${artifact.id}/zip`;
  const dlRes = await fetch(dlUrl, {
    headers: {
      Authorization: `Bearer ${GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
    },
  });

  if (!dlRes.ok) return null;

  // Write zip to /tmp, extract test_results.json
  const zipPath = `/tmp/test-results-${runId}.zip`;
  const buffer = await dlRes.arrayBuffer();
  fs.writeFileSync(zipPath, Buffer.from(buffer));

  try {
    execSync(`cd /tmp && unzip -o ${zipPath} test_results.json`, { stdio: "pipe" });
    const results = JSON.parse(fs.readFileSync("/tmp/test_results.json", "utf8"));
    return results;
  } catch {
    return null;
  }
}

/**
 * Run the Python SSIM module to compare baseline vs current screenshots.
 * @param {string} eventId
 * @returns {object} SSIM results aggregated across all routes.
 */
function runSSIM(eventId) {
  const { ROUTES } = require("./capture-baseline");
  const baselinesDir = process.env.BASELINES_DIR || BASELINES_DIR;
  let worstScore = 1.0;
  let maxDiffPct = 0.0;
  let anyRegression = false;
  let diffImagePath = null;

  for (const route of ROUTES) {
    const baselinePath = path.join(baselinesDir, `${route.slug}-baseline.png`);
    const currentPath = `/tmp/current-${route.slug}-${eventId}.png`;

    if (!fs.existsSync(baselinePath) || !fs.existsSync(currentPath)) {
      console.log(`Skipping SSIM for ${route.slug}: missing screenshot(s)`);
      continue;
    }

    const ssimScript = path.join(__dirname, "ssim.py");
    const cmd = `python3 -c "
import json, sys
sys.path.insert(0, '${__dirname}')
from ssim import compute_ssim
result = compute_ssim('${baselinePath}', '${currentPath}', '${eventId}-${route.slug}')
print(json.dumps(result))
"`;

    try {
      const output = execSync(cmd, { encoding: "utf8", timeout: 60_000 });
      const result = JSON.parse(output.trim());

      if (result.ssim_score < worstScore) {
        worstScore = result.ssim_score;
      }
      if (result.visual_diff_pct > maxDiffPct) {
        maxDiffPct = result.visual_diff_pct;
        diffImagePath = result.diff_image_path;
      }
      if (result.visual_regression) {
        anyRegression = true;
      }
    } catch (err) {
      console.error(`SSIM failed for ${route.slug}: ${err.message}`);
    }
  }

  return {
    ssim_score: worstScore,
    visual_diff_pct: maxDiffPct,
    visual_regression: anyRegression,
    diff_image_path: diffImagePath,
  };
}

/**
 * Main orchestrator: validate a candidate patch end-to-end.
 * @param {object} candidatePatch - Parsed candidate_patch.json.
 * @returns {Promise<object>} validation_bundle.json conformant object.
 */
async function validate(candidatePatch) {
  const { event_id, diff, status } = candidatePatch;
  const containerId = process.env.CONTAINER_ID || `sandbox-${event_id}`;

  // CANNOT_PATCH: short-circuit with failure sentinel
  if (status === "CANNOT_PATCH") {
    return buildBundle(event_id, {
      tests_passed: 0,
      tests_failed: -2,
      coverage_before: 0,
      coverage_after: 0,
      visual_diff_pct: 0,
      visual_regression: false,
      container_id: containerId,
      test_log_url: "",
      screenshot_diff_url: null,
    });
  }

  let testResults = null;
  let workflowRun = null;

  try {
    // Step 1: Trigger sandbox workflow
    console.log(`Triggering sandbox workflow for event ${event_id}...`);
    await triggerWorkflow(event_id, diff);

    // Step 2: Poll for completion
    console.log("Polling for workflow completion (max 15 min)...");
    workflowRun = await pollWorkflowCompletion(event_id);

    if (!workflowRun) {
      // Workflow timeout
      console.error("Workflow timed out");
      return buildBundle(event_id, {
        tests_passed: 0,
        tests_failed: -1,
        coverage_before: 0,
        coverage_after: 0,
        visual_diff_pct: 0,
        visual_regression: false,
        container_id: containerId,
        test_log_url: "",
        screenshot_diff_url: null,
      });
    }

    // Step 3: Download test results
    console.log(`Workflow completed: ${workflowRun.conclusion}`);
    testResults = await downloadTestResults(workflowRun.id);
  } catch (err) {
    console.error(`Sandbox execution failed: ${err.message}`);
    return buildBundle(event_id, {
      tests_passed: 0,
      tests_failed: -2,
      coverage_before: 0,
      coverage_after: 0,
      visual_diff_pct: 0,
      visual_regression: false,
      container_id: containerId,
      test_log_url: workflowRun?.html_url || "",
      screenshot_diff_url: null,
    });
  }

  // Step 4: Run SSIM comparison
  console.log("Running SSIM visual regression analysis...");
  const ssimResults = runSSIM(event_id);

  // Step 5: Assemble validation bundle
  const testsPassed = testResults?.tests_passed ?? 0;
  const testsFailed = testResults?.tests_failed ?? 0;
  const coverageBefore = testResults?.coverage_before ?? 0;
  const coverageAfter = testResults?.coverage_after ?? 0;

  return buildBundle(event_id, {
    tests_passed: testsPassed,
    tests_failed: testsFailed,
    coverage_before: coverageBefore,
    coverage_after: coverageAfter,
    visual_diff_pct: ssimResults.visual_diff_pct,
    visual_regression: ssimResults.visual_regression,
    container_id: containerId,
    test_log_url: workflowRun?.html_url || "",
    screenshot_diff_url: ssimResults.diff_image_path || null,
  });
}

/**
 * Build a validation_bundle.json conformant object.
 * @param {string} eventId
 * @param {object} fields
 * @returns {object}
 */
function buildBundle(eventId, fields) {
  return {
    event_id: eventId,
    tests_passed: fields.tests_passed,
    tests_failed: fields.tests_failed,
    coverage_before: fields.coverage_before,
    coverage_after: fields.coverage_after,
    visual_diff_pct: fields.visual_diff_pct,
    visual_regression: fields.visual_regression,
    container_id: fields.container_id,
    test_log_url: fields.test_log_url,
    screenshot_diff_url: fields.screenshot_diff_url ?? null,
  };
}

// CLI entry point
if (require.main === module) {
  const inputPath = process.argv[2];
  if (!inputPath) {
    console.error("Usage: node validate.js <candidate_patch.json>");
    process.exit(1);
  }

  const candidatePatch = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  validate(candidatePatch)
    .then((bundle) => {
      const outputPath = `/tmp/validation_bundle_${candidatePatch.event_id}.json`;
      fs.writeFileSync(outputPath, JSON.stringify(bundle, null, 2));
      console.log(`\nValidation bundle written to ${outputPath}`);
      console.log(JSON.stringify(bundle, null, 2));
    })
    .catch((err) => {
      console.error("Validation failed:", err.message);
      process.exit(1);
    });
}

module.exports = { validate, buildBundle, runSSIM, triggerWorkflow, pollWorkflowCompletion };
