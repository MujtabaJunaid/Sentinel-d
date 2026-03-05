"use strict";

/**
 * day6-integration-gate.js — End-of-Day Integration Gate Test
 *
 * Simulates the full flow from GHAS webhook to downstream routing.
 * Run with Dev A at EOD — both engineers must see PASS before Day 7.
 *
 * Usage:
 *   node scripts/day6-integration-gate.js [--mock]
 *
 * With --mock flag: runs entirely locally with mocked Azure services.
 * Without --mock: requires Azure infrastructure to be deployed.
 *
 * Env vars (for live mode):
 *   WEBHOOK_URL           — Azure Function webhook endpoint
 *   SERVICE_BUS_NAMESPACE — Service Bus namespace
 *   GITHUB_TOKEN          — GitHub API token
 *   GITHUB_OWNER          — Repository owner
 *   GITHUB_REPO           — Repository name
 */

const path = require("path");
const { v4: uuidv4 } = require("uuid");

const MOCK_MODE = process.argv.includes("--mock");

// ── Test data ───────────────────────────────────────────────────────────────

function makeWebhookPayload(overrides = {}) {
  return {
    event_id: uuidv4(),
    cve_id: "CVE-2021-44228",
    severity: "CRITICAL",
    affected_package: "org.apache.logging.log4j:log4j-core",
    current_version: "2.14.0",
    fix_version_range: ">=2.15.0",
    file_path: "pom.xml",
    line_range: [42, 42],
    repo: "test-org/test-repo",
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

// ── Step runners ────────────────────────────────────────────────────────────

const results = [];

function logStep(step, status, detail) {
  const icon = status === "PASS" ? "✅" : status === "FAIL" ? "❌" : "⚠️";
  console.log(`  ${icon} Step ${step}: ${detail}`);
  results.push({ step, status, detail });
}

/**
 * Step 1: POST a mock GHAS webhook payload and validate acceptance.
 */
async function step1_postWebhook(payload) {
  console.log("\n📡 Step 1: POST GHAS webhook payload");

  if (MOCK_MODE) {
    // Use the webhook handler directly
    const { validate } = require("../azure-functions/webhook-receiver/src/functions/webhook-receiver");

    if (!validate) {
      logStep(1, "FAIL", "Schema validator not loaded");
      return false;
    }

    const valid = validate(payload);
    if (!valid) {
      logStep(1, "FAIL", `Schema validation failed: ${JSON.stringify(validate.errors)}`);
      return false;
    }

    logStep(1, "PASS", `Payload validated — event_id: ${payload.event_id}`);
    return true;
  }

  // Live mode: POST to Azure Function
  const webhookUrl = process.env.WEBHOOK_URL;
  if (!webhookUrl) {
    logStep(1, "FAIL", "WEBHOOK_URL not set");
    return false;
  }

  try {
    const res = await fetch(webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.status === 202) {
      logStep(1, "PASS", `Webhook accepted (202) — event_id: ${payload.event_id}`);
      return true;
    } else {
      const body = await res.text();
      logStep(1, "FAIL", `Webhook rejected (${res.status}): ${body}`);
      return false;
    }
  } catch (err) {
    logStep(1, "FAIL", `Webhook POST failed: ${err.message}`);
    return false;
  }
}

/**
 * Step 2: Run SRE Agent classification (mock pipeline).
 */
async function step2_classifyEvent(payload) {
  console.log("\n🔍 Step 2: SRE Agent classification");

  if (MOCK_MODE) {
    // Simulate classification using the Python classifier directly
    const { execSync } = require("child_process");
    const fs = require("fs");
    const os = require("os");

    try {
      // Write Python script to temp file to avoid shell escaping issues
      const scriptPath = path.join(os.tmpdir(), `classify-${payload.event_id}.py`);
      const eventPath = path.join(os.tmpdir(), `event-${payload.event_id}.json`);

      fs.writeFileSync(eventPath, JSON.stringify(payload));

      const script = `
import json, sys
sys.path.insert(0, '${path.resolve(__dirname, "../sre-agent")}')
from classifier import classify

with open('${eventPath}') as f:
    event = json.load(f)

telemetry_active = {"call_count": 150, "last_called": "2026-03-04T10:00:00Z"}
telemetry_dormant = {"call_count": 0, "last_called": None}

result_active = classify(telemetry_active, event, "traces | where message contains 'log4j'")
result_dormant = classify(telemetry_dormant, event, "traces | where message contains 'log4j'")

print(json.dumps({"active": result_active, "dormant": result_dormant}))
`;
      fs.writeFileSync(scriptPath, script);

      const output = execSync(`python3 ${scriptPath}`, {
        encoding: "utf8",
        timeout: 10_000,
      });

      // Clean up temp files
      fs.unlinkSync(scriptPath);
      fs.unlinkSync(eventPath);

      const classifications = JSON.parse(output.trim());

      if (classifications.active.status !== "ACTIVE") {
        logStep(2, "FAIL", "Expected ACTIVE classification for high call_count");
        return null;
      }
      if (classifications.dormant.status !== "DORMANT") {
        logStep(2, "FAIL", "Expected DORMANT classification for zero call_count");
        return null;
      }

      logStep(
        2,
        "PASS",
        `Classified correctly — ACTIVE (confidence: ${classifications.active.confidence}), ` +
          `DORMANT (confidence: ${classifications.dormant.confidence})`
      );

      return classifications;
    } catch (err) {
      logStep(2, "FAIL", `Classification failed: ${err.message}`);
      return null;
    }
  }

  logStep(2, "SKIP", "Live classification requires deployed SRE Agent");
  return null;
}

/**
 * Step 3: Verify ACTIVE event would be published to nlp-pipeline-input topic.
 */
async function step3_verifyActiveRouting(classification, payload) {
  console.log("\n📤 Step 3: Verify ACTIVE → nlp-pipeline-input topic routing");

  if (MOCK_MODE) {
    // Verify the router module handles ACTIVE correctly (without real Service Bus)
    const { execSync } = require("child_process");
    const fs = require("fs");
    const os = require("os");

    try {
      const scriptPath = path.join(os.tmpdir(), `route-active-${payload.event_id}.py`);
      const dataPath = path.join(os.tmpdir(), `route-data-${payload.event_id}.json`);

      fs.writeFileSync(dataPath, JSON.stringify({ classification, payload }));

      const routerScript = `
import json, sys, asyncio
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, '${path.resolve(__dirname, "../sre-agent")}')
from router import _route_active

with open('${dataPath}') as f:
    data = json.load(f)

async def test():
    mock_sender = AsyncMock()
    mock_sender.__aenter__ = AsyncMock(return_value=mock_sender)
    mock_sender.__aexit__ = AsyncMock(return_value=None)
    mock_client = AsyncMock()
    mock_client.get_topic_sender = MagicMock(return_value=mock_sender)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_cred = AsyncMock()

    with patch('router.DefaultAzureCredential', return_value=mock_cred), \\
         patch('router.ServiceBusClient', return_value=mock_client), \\
         patch('router.SB_NAMESPACE', 'test-ns'):
        result = await _route_active(data['classification'], data['payload'])
        print(json.dumps(result))

asyncio.run(test())
`;
      fs.writeFileSync(scriptPath, routerScript);

      const output = execSync(`python3 ${scriptPath}`, {
        encoding: "utf8",
        timeout: 10_000,
      });

      fs.unlinkSync(scriptPath);
      fs.unlinkSync(dataPath);

      const result = JSON.parse(output.trim());

      if (result.destination.includes("nlp-pipeline-input")) {
        logStep(3, "PASS", `ACTIVE routed to ${result.destination}`);
        return true;
      } else {
        logStep(3, "FAIL", `Wrong destination: ${result.destination}`);
        return false;
      }
    } catch (err) {
      logStep(3, "FAIL", `Active routing test failed: ${err.message}`);
      return false;
    }
  }

  logStep(3, "SKIP", "Live topic verification requires deployed Service Bus");
  return false;
}

/**
 * Step 4: Verify DORMANT event would create a GitHub Decision Gate issue.
 */
async function step4_verifyDormantRouting(classification, payload) {
  console.log("\n🎫 Step 4: Verify DORMANT → GitHub Decision Gate issue");

  if (MOCK_MODE) {
    // Verify the create-decision-issue module can build the issue body
    try {
      const { buildIssueBody } = require("../safety-governor/create-decision-issue");

      const body = buildIssueBody(payload, classification, {
        lookup_status: "NO_MATCH",
      });

      const hasMetadata = body.includes("sentinel-metadata");
      const hasCveId = body.includes(payload.cve_id);
      const hasLabels = body.includes("sentinel/fix-now") && body.includes("sentinel/defer");

      if (hasMetadata && hasCveId && hasLabels) {
        logStep(4, "PASS", "Decision issue body built correctly with metadata + labels");
        return true;
      } else {
        logStep(4, "FAIL", `Issue body missing fields: metadata=${hasMetadata}, cve=${hasCveId}, labels=${hasLabels}`);
        return false;
      }
    } catch (err) {
      logStep(4, "FAIL", `Decision issue build failed: ${err.message}`);
      return false;
    }
  }

  logStep(4, "SKIP", "Live issue verification requires deployed GitHub integration");
  return false;
}

/**
 * Step 5: Verify Foundry client skeleton returns a valid mock patch.
 */
async function step5_verifyFoundryClient(payload) {
  console.log("\n🔧 Step 5: Verify Foundry client skeleton (mock mode)");

  try {
    const { generatePatch } = require("../patch-generator/foundry-client");

    const mockContext = {
      event_id: payload.event_id,
      fix_strategy: "VERSION_BUMP",
      breaking_changes: [],
      community_intent_class: "security-fix",
      intent_confidence: 0.92,
      nvd_context: { cvss_score: 10.0 },
      migration_steps: ["Bump log4j-core to >=2.15.0"],
      historical_match_status: "NO_MATCH",
      historical_patch_available: false,
      solutions_to_avoid: [],
      historical_record_id: null,
      pipeline_version: "3.0.0",
    };

    const patch = await generatePatch(mockContext);

    if (patch.status === "PATCH_GENERATED" && patch.diff && patch.event_id === payload.event_id) {
      logStep(5, "PASS", `Foundry client returned PATCH_GENERATED (model: ${patch.model_id})`);
      return true;
    } else {
      logStep(5, "FAIL", `Unexpected patch status: ${patch.status}`);
      return false;
    }
  } catch (err) {
    logStep(5, "FAIL", `Foundry client failed: ${err.message}`);
    return false;
  }
}

// ── Main ────────────────────────────────────────────────────────────────────

async function main() {
  console.log("═══════════════════════════════════════════════════════════");
  console.log("  Sentinel-D — Day 6 Integration Gate Test");
  console.log(`  Mode: ${MOCK_MODE ? "MOCK (local)" : "LIVE (Azure)"}`);
  console.log(`  Time: ${new Date().toISOString()}`);
  console.log("═══════════════════════════════════════════════════════════");

  const activePayload = makeWebhookPayload();
  const dormantPayload = makeWebhookPayload({
    event_id: uuidv4(),
    cve_id: "CVE-2023-12345",
    severity: "MEDIUM",
  });

  // Step 1: Webhook validation
  const step1ok = await step1_postWebhook(activePayload);

  // Step 2: SRE Agent classification
  const classifications = await step2_classifyEvent(activePayload);

  // Step 3: ACTIVE routing
  let step3ok = false;
  if (classifications?.active) {
    step3ok = await step3_verifyActiveRouting(classifications.active, activePayload);
  } else {
    logStep(3, "SKIP", "No active classification to route");
  }

  // Step 4: DORMANT routing
  let step4ok = false;
  if (classifications?.dormant) {
    step4ok = await step4_verifyDormantRouting(classifications.dormant, dormantPayload);
  } else {
    logStep(4, "SKIP", "No dormant classification to route");
  }

  // Step 5: Foundry client
  const step5ok = await step5_verifyFoundryClient(activePayload);

  // Summary
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("  RESULTS SUMMARY");
  console.log("═══════════════════════════════════════════════════════════");

  const passed = results.filter((r) => r.status === "PASS").length;
  const failed = results.filter((r) => r.status === "FAIL").length;
  const skipped = results.filter((r) => r.status === "SKIP").length;

  for (const r of results) {
    const icon = r.status === "PASS" ? "✅" : r.status === "FAIL" ? "❌" : "⚠️";
    console.log(`  ${icon} ${r.detail}`);
  }

  console.log(`\n  Total: ${passed} PASS, ${failed} FAIL, ${skipped} SKIP`);

  if (failed > 0) {
    console.log("\n  ❌ INTEGRATION GATE: FAIL — resolve issues before Day 7");
    process.exit(1);
  } else {
    console.log("\n  ✅ INTEGRATION GATE: PASS — ready for Day 7");
    process.exit(0);
  }
}

main().catch((err) => {
  console.error("Integration gate crashed:", err);
  process.exit(1);
});
