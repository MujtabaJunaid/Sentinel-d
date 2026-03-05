"use strict";

/**
 * escalate.js — Safety Governor Escalation (LOW tier)
 *
 * Creates a GitHub Issue with full diagnostic bundle for LOW confidence patches.
 * Optionally fires a PagerDuty alert if PAGERDUTY_ROUTING_KEY is configured.
 */

const { Octokit } = require("@octokit/rest");
require("dotenv").config();

/**
 * Build the escalation issue body with full diagnostic information.
 * @param {object} event - Original webhook payload
 * @param {number} compositeScore
 * @param {object} validationBundle
 * @param {object} candidatePatch
 * @returns {string}
 */
function buildEscalationBody(event, compositeScore, validationBundle, candidatePatch) {
  const failedTestInfo =
    validationBundle.tests_failed > 0
      ? `${validationBundle.tests_failed} test(s) failed`
      : "No test failures";

  return `## 🚨 Sentinel-D — Low Confidence Patch Escalation

This patch was generated but scored below the auto-approve threshold.
**Human review is required.**

### Event Details

| Field | Value |
|-------|-------|
| **Event ID** | \`${event.event_id}\` |
| **CVE ID** | \`${event.cve_id}\` |
| **Severity** | \`${event.severity}\` |
| **Package** | \`${event.affected_package}\` @ \`${event.current_version}\` |
| **File** | \`${event.file_path}\` |
| **Repository** | \`${event.repo}\` |

### Score Breakdown

| Signal | Value |
|--------|-------|
| **Composite Score** | ${compositeScore.toFixed(2)} |
| **LLM Confidence** | ${candidatePatch.llm_confidence?.toFixed(2) || "N/A"} |
| **Patch Source** | ${candidatePatch.source} |
| **Patch Status** | ${candidatePatch.status} |

### Sandbox Results

| Metric | Value |
|--------|-------|
| **Tests Passed** | ${validationBundle.tests_passed} |
| **Tests Failed** | ${validationBundle.tests_failed} (${failedTestInfo}) |
| **Coverage Before** | ${(validationBundle.coverage_before * 100).toFixed(1)}% |
| **Coverage After** | ${(validationBundle.coverage_after * 100).toFixed(1)}% |
| **Visual Diff** | ${(validationBundle.visual_diff_pct * 100).toFixed(2)}% |
| **Visual Regression** | ${validationBundle.visual_regression ? "⚠️ Yes" : "✅ No"} |

### Links

- 📋 [Sandbox Test Log](${validationBundle.test_log_url || "#"})
- 🧠 [LLM Reasoning Chain](${candidatePatch.reasoning_chain || "#"})
${validationBundle.screenshot_diff_url ? `- 🖼️ [SSIM Diff Image](${validationBundle.screenshot_diff_url})` : ""}

---

**Action required:** Review the patch, test results, and reasoning chain.
Apply \`sentinel/fix-now\` to approve, \`sentinel/defer\` to postpone, or \`sentinel/wont-fix\` to accept risk.`;
}

/**
 * Fire a PagerDuty alert for escalated events.
 * @param {object} event
 * @param {number} compositeScore
 * @returns {Promise<boolean>} true if alert was sent
 */
async function firePagerDutyAlert(event, compositeScore) {
  const PAGERDUTY_ROUTING_KEY = process.env.PAGERDUTY_ROUTING_KEY;
  if (!PAGERDUTY_ROUTING_KEY) return false;

  try {
    const res = await fetch("https://events.pagerduty.com/v2/enqueue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        routing_key: PAGERDUTY_ROUTING_KEY,
        event_action: "trigger",
        payload: {
          summary: `[Sentinel-D] Low confidence patch for ${event.cve_id} (score: ${compositeScore.toFixed(2)})`,
          severity: event.severity === "CRITICAL" ? "critical" : "warning",
          source: "sentinel-d-safety-governor",
          component: event.affected_package,
          custom_details: {
            event_id: event.event_id,
            cve_id: event.cve_id,
            composite_score: compositeScore,
            repo: event.repo,
          },
        },
      }),
    });

    return res.ok;
  } catch (err) {
    console.error(`PagerDuty alert failed: ${err.message}`);
    return false;
  }
}

/**
 * Create a GitHub Issue for LOW tier escalation.
 *
 * @param {object} event - Original webhook payload
 * @param {number} compositeScore
 * @param {object} validationBundle
 * @param {object} candidatePatch
 * @returns {Promise<{issueNumber: number, issueUrl: string, pagerdutyAlerted: boolean}>}
 */
async function createEscalationIssue(event, compositeScore, validationBundle, candidatePatch) {
  const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
  const GITHUB_OWNER = process.env.GITHUB_OWNER;
  const GITHUB_REPO = process.env.GITHUB_REPO;
  const ONCALL_GITHUB_LOGIN = process.env.ONCALL_GITHUB_LOGIN;

  if (!GITHUB_TOKEN) {
    throw new Error("GITHUB_TOKEN environment variable is required");
  }
  if (!GITHUB_OWNER || !GITHUB_REPO) {
    throw new Error("GITHUB_OWNER and GITHUB_REPO environment variables are required");
  }

  const octokit = new Octokit({ auth: GITHUB_TOKEN });

  const title = `[Sentinel-D ESCALATE] ${event.cve_id} — Low confidence patch requires review`;
  const body = buildEscalationBody(event, compositeScore, validationBundle, candidatePatch);

  const issueParams = {
    owner: GITHUB_OWNER,
    repo: GITHUB_REPO,
    title,
    body,
    labels: ["sentinel/escalate"],
  };

  if (ONCALL_GITHUB_LOGIN) {
    issueParams.assignees = [ONCALL_GITHUB_LOGIN];
  }

  const { data: issue } = await octokit.rest.issues.create(issueParams);

  // Fire PagerDuty alert if configured
  const pagerdutyAlerted = await firePagerDutyAlert(event, compositeScore);

  return {
    issueNumber: issue.number,
    issueUrl: issue.html_url,
    pagerdutyAlerted,
  };
}

module.exports = { createEscalationIssue, buildEscalationBody, firePagerDutyAlert };
