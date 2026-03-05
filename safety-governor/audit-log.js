"use strict";

/**
 * audit-log.js — Append-Only Audit Log
 *
 * Writes Safety Governor decisions to Azure Table Storage.
 * Partition key: YYYY-MM (month bucket), Row key: eventId.
 *
 * IMPORTANT: This table is append-only. No updates, no deletes, ever.
 */

const { TableClient } = require("@azure/data-tables");
const { DefaultAzureCredential } = require("@azure/identity");
require("dotenv").config();

const TABLE_NAME = "auditlog";

/**
 * Get a TableClient for the audit log table.
 * @returns {import("@azure/data-tables").TableClient}
 */
function getTableClient() {
  const TABLE_STORAGE_CONN = process.env.TABLE_STORAGE_CONN_STRING;
  const TABLE_STORAGE_ACCOUNT = process.env.TABLE_STORAGE_ACCOUNT;

  if (TABLE_STORAGE_CONN) {
    return TableClient.fromConnectionString(TABLE_STORAGE_CONN, TABLE_NAME);
  }
  if (TABLE_STORAGE_ACCOUNT) {
    const credential = new DefaultAzureCredential();
    return new TableClient(
      `https://${TABLE_STORAGE_ACCOUNT}.table.core.windows.net`,
      TABLE_NAME,
      credential
    );
  }
  throw new Error("Missing TABLE_STORAGE_CONN_STRING or TABLE_STORAGE_ACCOUNT");
}

/**
 * Write an audit record for a Safety Governor decision.
 * Append-only — this function only creates, never updates.
 *
 * @param {object} params
 * @param {object} params.event - Original webhook payload
 * @param {string} params.tier - HIGH/MEDIUM/LOW/BLOCKED
 * @param {number} params.compositeScore
 * @param {string} params.action - AUTO_PR/REVIEW_PR/GITHUB_ISSUE_ESCALATE/ARCHIVE
 * @param {string|null} params.prUrl
 * @param {string|null} params.issueUrl
 * @param {object} params.validationBundle
 * @param {object} params.candidatePatch
 * @param {string|null} params.overrideReason
 * @returns {Promise<{partitionKey: string, rowKey: string}>}
 */
async function writeAuditRecord({
  event,
  tier,
  compositeScore,
  action,
  prUrl,
  issueUrl,
  validationBundle,
  candidatePatch,
  overrideReason,
}) {
  const now = new Date();
  const partitionKey = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
  const rowKey = event.event_id;

  const entity = {
    partitionKey,
    rowKey,

    // Event context
    eventId: event.event_id,
    cveId: event.cve_id,
    repo: event.repo,
    timestamp: now.toISOString(),

    // Decision
    compositeScore,
    tier,
    actionTaken: action,
    overrideReason: overrideReason || "",

    // Validation signals
    testsPassed: validationBundle.tests_passed,
    testsFailed: validationBundle.tests_failed,
    coverageBefore: validationBundle.coverage_before,
    coverageAfter: validationBundle.coverage_after,
    visualDiffPct: validationBundle.visual_diff_pct,
    visualRegression: validationBundle.visual_regression,

    // Patch signals
    llmConfidence: candidatePatch.llm_confidence,
    patchSource: candidatePatch.source,
    patchStatus: candidatePatch.status,
    touchesAuthCrypto: candidatePatch.touches_auth_crypto,

    // URLs
    prUrl: prUrl || "",
    issueUrl: issueUrl || "",
    reasoningChainUrl: candidatePatch.reasoning_chain || "",
    sandboxLogUrl: validationBundle.test_log_url || "",

    // Metadata
    humanOverride: false,
    pipelineVersion: process.env.PIPELINE_VERSION || "3.0.0",
  };

  const tableClient = module.exports.getTableClient();
  await tableClient.createEntity(entity);

  console.log(
    JSON.stringify({
      message: "Audit record written",
      partitionKey,
      rowKey,
      tier,
      action,
    })
  );

  return { partitionKey, rowKey };
}

module.exports = { writeAuditRecord, getTableClient };
