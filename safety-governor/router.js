"use strict";

/**
 * router.js — Safety Governor Four-Tier Router
 *
 * Routes events based on composite confidence score with override conditions.
 * Dev A owns computeScore() — this module consumes the score and decides action.
 */

/**
 * @typedef {Object} RouteResult
 * @property {"HIGH"|"MEDIUM"|"LOW"|"BLOCKED"} tier
 * @property {"AUTO_PR"|"REVIEW_PR"|"GITHUB_ISSUE_ESCALATE"|"ARCHIVE"} action
 * @property {string|null} overrideReason
 */

/**
 * Route an event based on composite score and override conditions.
 *
 * Tier thresholds:
 *   HIGH    (S >= 0.85)  → AUTO_PR
 *   MEDIUM  (0.70 <= S < 0.85) → REVIEW_PR
 *   LOW     (0.55 <= S < 0.70) → GITHUB_ISSUE_ESCALATE
 *   BLOCKED (S < 0.55)  → ARCHIVE
 *
 * Override conditions (can only downgrade, never upgrade):
 *   → BLOCKED: CANNOT_PATCH, tests_failed === -1 or -2
 *   → LOW:     touches_auth_crypto === true
 *   → MEDIUM:  visual_regression === true OR fix_strategy === 'FULL_REFACTOR'
 *
 * @param {number} compositeScore - Composite confidence score (0–1)
 * @param {object} validationBundle - validation_bundle.json
 * @param {object} candidatePatch - candidate_patch.json
 * @returns {RouteResult}
 */
function route(compositeScore, validationBundle, candidatePatch) {
  // Start with score-based tier
  let tier;
  let action;

  if (compositeScore >= 0.85) {
    tier = "HIGH";
    action = "AUTO_PR";
  } else if (compositeScore >= 0.70) {
    tier = "MEDIUM";
    action = "REVIEW_PR";
  } else if (compositeScore >= 0.55) {
    tier = "LOW";
    action = "GITHUB_ISSUE_ESCALATE";
  } else {
    tier = "BLOCKED";
    action = "ARCHIVE";
  }

  let overrideReason = null;

  // BLOCKED overrides (highest priority — check first)
  if (candidatePatch.status === "CANNOT_PATCH") {
    tier = "BLOCKED";
    action = "ARCHIVE";
    overrideReason = "Patch generator returned CANNOT_PATCH";
  } else if (validationBundle.tests_failed === -1) {
    tier = "BLOCKED";
    action = "ARCHIVE";
    overrideReason = "Infrastructure failure: sandbox workflow timed out";
  } else if (validationBundle.tests_failed === -2) {
    tier = "BLOCKED";
    action = "ARCHIVE";
    overrideReason = "Patch apply failure: git apply failed in sandbox";
  }

  // LOW override (only if not already BLOCKED)
  if (tier !== "BLOCKED" && candidatePatch.touches_auth_crypto === true) {
    if (tier === "HIGH" || tier === "MEDIUM") {
      tier = "LOW";
      action = "GITHUB_ISSUE_ESCALATE";
      overrideReason = "Patch touches auth/crypto code — forced to LOW for human review";
    }
  }

  // MEDIUM override (only if currently HIGH)
  if (tier === "HIGH") {
    if (validationBundle.visual_regression === true) {
      tier = "MEDIUM";
      action = "REVIEW_PR";
      overrideReason = "Visual regression detected — forced to MEDIUM for human review";
    } else if (candidatePatch.fix_strategy === "FULL_REFACTOR") {
      tier = "MEDIUM";
      action = "REVIEW_PR";
      overrideReason = "Fix strategy is FULL_REFACTOR — forced to MEDIUM for human review";
    }
  }

  return { tier, action, overrideReason };
}

module.exports = { route };
