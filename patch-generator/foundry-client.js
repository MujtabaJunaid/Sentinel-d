"use strict";

/**
 * foundry-client.js — Patch Generator Foundry API Client (Skeleton)
 *
 * Infrastructure wrapper that calls the Microsoft Foundry API endpoint.
 * The real four-section prompt logic is Dev A's domain — this is just
 * the HTTP client so the infrastructure pipe is connected end-to-end.
 *
 * Accepts structured_context.json, returns candidate_patch.json.
 *
 * Env vars:
 *   FOUNDRY_ENDPOINT  — Foundry API base URL
 *   FOUNDRY_API_KEY   — API key for authentication
 *   APP_INSIGHTS_CONN — App Insights connection string (for response time logging)
 */

require("dotenv").config();

const { withRetry } = require("../shared/retry");

const FOUNDRY_ENDPOINT = process.env.FOUNDRY_ENDPOINT;
const FOUNDRY_API_KEY = process.env.FOUNDRY_API_KEY;

/**
 * Call the Foundry API to generate a candidate patch.
 *
 * @param {object} structuredContext - structured_context.json from NLP Pipeline
 * @returns {Promise<object>} candidate_patch.json conformant object
 */
async function generatePatch(structuredContext) {
  if (!structuredContext || !structuredContext.event_id) {
    throw new Error("structured_context with event_id is required");
  }

  const startTime = Date.now();

  // If Foundry endpoint is configured, call the real API
  const endpoint = process.env.FOUNDRY_ENDPOINT;
  const apiKey = process.env.FOUNDRY_API_KEY;

  if (endpoint && apiKey) {
    try {
      const response = await withRetry(
        () =>
          fetch(`${endpoint}/v1/patches/generate`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${apiKey}`,
            },
            body: JSON.stringify({
              context: structuredContext,
              prompt_version: "skeleton-v1",
            }),
          }).then((res) => {
            if (res.status === 429 || res.status === 503 || res.status === 504) {
              const err = new Error(`Foundry API error: ${res.status}`);
              err.statusCode = res.status;
              throw err;
            }
            return res;
          }),
        { label: "foundry-api", maxAttempts: 3 }
      );

      const elapsedMs = Date.now() - startTime;
      logResponseTime(structuredContext.event_id, elapsedMs);

      if (!response.ok) {
        const errorText = await response.text();
        console.error(
          `Foundry API error (${response.status}): ${errorText}`
        );
        return buildCannotPatch(
          structuredContext.event_id,
          `Foundry API error: ${response.status}`
        );
      }

      const result = await response.json();
      return mapFoundryResponse(structuredContext.event_id, result);
    } catch (err) {
      const elapsedMs = Date.now() - startTime;
      logResponseTime(structuredContext.event_id, elapsedMs, err.message);

      console.error(`Foundry API call failed: ${err.message}`);
      return buildCannotPatch(
        structuredContext.event_id,
        `Foundry API unreachable: ${err.message}`
      );
    }
  }

  // Mock mode: return a placeholder candidate patch
  const elapsedMs = Date.now() - startTime;
  logResponseTime(structuredContext.event_id, elapsedMs, null, true);

  return buildMockPatch(structuredContext);
}

/**
 * Map a Foundry API response to candidate_patch.json schema.
 * @param {string} eventId
 * @param {object} foundryResponse
 * @returns {object}
 */
function mapFoundryResponse(eventId, foundryResponse) {
  return {
    event_id: eventId,
    status: foundryResponse.diff ? "PATCH_GENERATED" : "CANNOT_PATCH",
    source: "FOUNDRY",
    diff: foundryResponse.diff || null,
    files_modified: foundryResponse.files_modified || [],
    lines_changed: foundryResponse.lines_changed || 0,
    touches_auth_crypto: foundryResponse.touches_auth_crypto || false,
    llm_confidence: foundryResponse.confidence || 0,
    reasoning_chain: foundryResponse.reasoning_chain || null,
    model_id: foundryResponse.model_id || "foundry-unknown",
    cannot_patch_reason: foundryResponse.cannot_patch_reason || null,
  };
}

/**
 * Build a mock candidate_patch.json for development/testing.
 * @param {object} structuredContext
 * @returns {object}
 */
function buildMockPatch(structuredContext) {
  return {
    event_id: structuredContext.event_id,
    status: "PATCH_GENERATED",
    source: "FOUNDRY",
    diff: [
      "--- a/pom.xml",
      "+++ b/pom.xml",
      "@@ -42,1 +42,1 @@",
      "-    <version>2.14.0</version>",
      "+    <version>2.17.1</version>",
    ].join("\n"),
    files_modified: ["pom.xml"],
    lines_changed: 1,
    touches_auth_crypto: false,
    llm_confidence: 0.85,
    reasoning_chain: "Mock: version bump from vulnerable to fixed version",
    model_id: "mock-foundry-skeleton-v1",
    cannot_patch_reason: null,
  };
}

/**
 * Build a CANNOT_PATCH response.
 * @param {string} eventId
 * @param {string} reason
 * @returns {object}
 */
function buildCannotPatch(eventId, reason) {
  return {
    event_id: eventId,
    status: "CANNOT_PATCH",
    source: "FOUNDRY",
    diff: null,
    files_modified: [],
    lines_changed: 0,
    touches_auth_crypto: false,
    llm_confidence: 0,
    reasoning_chain: null,
    model_id: "foundry-error",
    cannot_patch_reason: reason,
  };
}

/**
 * Log Foundry API response time (to console; App Insights wiring is a Day 8 task).
 * @param {string} eventId
 * @param {number} elapsedMs
 * @param {string} [error]
 * @param {boolean} [isMock]
 */
function logResponseTime(eventId, elapsedMs, error, isMock) {
  const logEntry = {
    metric: "foundry_api_response_time",
    event_id: eventId,
    elapsed_ms: elapsedMs,
    success: !error,
    mock: !!isMock,
  };
  if (error) logEntry.error = error;

  // Structured log for App Insights consumption
  console.log(JSON.stringify(logEntry));
}

// CLI entry point
if (require.main === module) {
  const fs = require("fs");
  const inputPath = process.argv[2];

  if (!inputPath) {
    console.error("Usage: node foundry-client.js <structured_context.json>");
    process.exit(1);
  }

  const ctx = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  generatePatch(ctx)
    .then((patch) => {
      const outputPath = `/tmp/candidate_patch_${ctx.event_id}.json`;
      fs.writeFileSync(outputPath, JSON.stringify(patch, null, 2));
      console.log(`Candidate patch written to ${outputPath}`);
    })
    .catch((err) => {
      console.error("Patch generation failed:", err.message);
      process.exit(1);
    });
}

module.exports = {
  generatePatch,
  buildMockPatch,
  buildCannotPatch,
  mapFoundryResponse,
};
