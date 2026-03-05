"use strict";

/**
 * governor.js — Safety Governor Orchestrator
 *
 * Wires: score → route → action (PR/issue/archive) → audit log → Historical DB write.
 * Dev A owns computeScore() — for now we accept it as a parameter.
 */

const path = require("path");
const router = require("./router");
const prGenerator = require("./pr-generator");
const escalation = require("./escalate");
const auditLog = require("./audit-log");
const historicalDb = require(path.resolve(
  __dirname,
  "../historical-db/write-client"
));

require("dotenv").config();

/**
 * Map a routing tier to a patch outcome for the Historical DB record.
 * @param {string} tier
 * @returns {string}
 */
function tierToOutcome(tier) {
  switch (tier) {
    case "HIGH":
      return "SUCCESS";
    case "MEDIUM":
      return "PARTIAL";
    default:
      return "FAILED";
  }
}

/**
 * Build a Historical DB record from the governor decision.
 * @param {object} event - webhook payload
 * @param {object} candidatePatch
 * @param {string} tier
 * @param {string|null} failureReason
 * @returns {object} historical_db_record.json conformant object
 */
function buildHistoricalRecord(event, candidatePatch, tier, failureReason) {
  return {
    id: event.event_id,
    cve_id: event.cve_id,
    repo: event.repo,
    affected_package: event.affected_package,
    affected_version_range: event.current_version,
    language: "java", // Inferred from context; Dev A may override
    framework: "unknown",
    fix_strategy_used: candidatePatch.source === "RAG_REPLAY" ? "RAG_REPLAY" : "LLM_GENERATED",
    patch_outcome: tierToOutcome(tier),
    resolved_at: new Date().toISOString(),
    resolved_by: "sentinel-d-safety-governor",
    human_override: false,
    cve_description_embedding: [], // Dev A populates this via NLP pipeline
    patch_diff: candidatePatch.diff || "",
    solutions_tried: [
      {
        source: candidatePatch.source,
        model_id: candidatePatch.model_id,
        confidence: candidatePatch.llm_confidence,
      },
    ],
    failure_reason: failureReason,
    pipeline_version: process.env.PIPELINE_VERSION || "3.0.0",
  };
}

/**
 * Run the full Safety Governor flow.
 *
 * @param {object} params
 * @param {object} params.event - Original webhook payload
 * @param {number} params.compositeScore - From Dev A's computeScore()
 * @param {object} params.validationBundle - From Sandbox Validator
 * @param {object} params.candidatePatch - From Patch Generator
 * @param {object} [params.structuredContext] - From NLP Pipeline (optional for routing)
 * @returns {Promise<object>} Full decision result
 */
async function govern({
  event,
  compositeScore,
  validationBundle,
  candidatePatch,
  structuredContext,
}) {
  // Step 1: Route based on score + overrides
  const { tier, action, overrideReason } = router.route(
    compositeScore,
    validationBundle,
    candidatePatch
  );

  console.log(
    JSON.stringify({
      message: "Safety Governor routing decision",
      eventId: event.event_id,
      tier,
      action,
      compositeScore,
      overrideReason,
    })
  );

  let prUrl = null;
  let issueUrl = null;
  let prNumber = null;
  let issueNumber = null;
  let pagerdutyAlerted = false;
  let failureReason = null;

  // Step 2: Execute action
  switch (action) {
    case "AUTO_PR":
    case "REVIEW_PR": {
      const prResult = await prGenerator.createPR(
        { ...candidatePatch, cve_id: event.cve_id, severity: event.severity, affected_package: event.affected_package },
        validationBundle,
        compositeScore,
        tier
      );
      prUrl = prResult.prUrl;
      prNumber = prResult.prNumber;
      break;
    }
    case "GITHUB_ISSUE_ESCALATE": {
      const issueResult = await escalation.createEscalationIssue(
        event,
        compositeScore,
        validationBundle,
        candidatePatch
      );
      issueUrl = issueResult.issueUrl;
      issueNumber = issueResult.issueNumber;
      pagerdutyAlerted = issueResult.pagerdutyAlerted;
      failureReason = `Low confidence (${compositeScore.toFixed(2)}) — escalated for human review`;
      break;
    }
    case "ARCHIVE":
      failureReason =
        overrideReason || `Blocked: composite score ${compositeScore.toFixed(2)} below threshold`;
      break;
  }

  // Step 3: Write audit log (always, for every decision)
  await auditLog.writeAuditRecord({
    event,
    tier,
    compositeScore,
    action,
    prUrl,
    issueUrl,
    validationBundle,
    candidatePatch,
    overrideReason,
  });

  // Step 4: Write Historical DB record (always, after every resolution)
  const historicalRecord = buildHistoricalRecord(
    event,
    candidatePatch,
    tier,
    failureReason
  );
  await historicalDb.writeResolutionRecord(historicalRecord);

  return {
    eventId: event.event_id,
    tier,
    action,
    overrideReason,
    compositeScore,
    prUrl,
    prNumber,
    issueUrl,
    issueNumber,
    pagerdutyAlerted,
    patchOutcome: tierToOutcome(tier),
  };
}

module.exports = { govern, tierToOutcome, buildHistoricalRecord };
