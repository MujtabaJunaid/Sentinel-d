"use strict";

/**
 * stress-test.js — Sends 10 simultaneous GHAS webhook payloads.
 *
 * Usage:
 *   WEBHOOK_URL=https://... node scripts/stress-test.js
 *   node scripts/stress-test.js --mock   (dry-run without real endpoints)
 *
 * Sends 10 unique events, polls Service Bus for receipt, checks DLQ after 2 min.
 */

require("dotenv").config();

const WEBHOOK_URL = process.env.WEBHOOK_URL || "http://localhost:7071/api/webhook";
const MOCK_MODE = process.argv.includes("--mock");
const EVENT_COUNT = 10;

/**
 * Generate a unique webhook payload.
 * @param {number} index
 * @returns {object}
 */
function generatePayload(index) {
  const padded = String(index).padStart(3, "0");
  return {
    event_id: `stress-test-${Date.now()}-${padded}`,
    cve_id: `CVE-2024-${1000 + index}`,
    severity: index % 3 === 0 ? "CRITICAL" : index % 3 === 1 ? "HIGH" : "MEDIUM",
    affected_package: `test-package-${index}`,
    current_version: "1.0.0",
    fixed_version: "1.1.0",
    file_path: `src/module-${index}/index.js`,
    repo: "sentinel-d/stress-test",
    alert_url: `https://github.com/sentinel-d/stress-test/security/advisories/GHSA-${padded}`,
    description: `Stress test vulnerability ${index}`,
  };
}

/**
 * Send a single webhook payload and measure response time.
 * @param {object} payload
 * @returns {Promise<{eventId: string, status: number, elapsedMs: number, error?: string}>}
 */
async function sendWebhook(payload) {
  const start = Date.now();

  if (MOCK_MODE) {
    // Simulate network delay
    await new Promise((r) => setTimeout(r, 50 + Math.random() * 100));
    return {
      eventId: payload.event_id,
      status: 202,
      elapsedMs: Date.now() - start,
    };
  }

  try {
    const res = await fetch(WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    return {
      eventId: payload.event_id,
      status: res.status,
      elapsedMs: Date.now() - start,
    };
  } catch (err) {
    return {
      eventId: payload.event_id,
      status: -1,
      elapsedMs: Date.now() - start,
      error: err.message,
    };
  }
}

/**
 * Poll Service Bus to verify message receipt (mock implementation).
 * In production, this would use the Azure Service Bus SDK to peek messages.
 * @param {string[]} eventIds
 * @returns {Promise<{received: number, missing: string[]}>}
 */
async function pollServiceBus(eventIds) {
  if (MOCK_MODE) {
    // Simulate all messages received
    return { received: eventIds.length, missing: [] };
  }

  const { ServiceBusClient } = require("@azure/service-bus");
  const { DefaultAzureCredential } = require("@azure/identity");

  const namespace = process.env.SERVICE_BUS_NAMESPACE;
  if (!namespace) {
    console.warn("⚠️  SERVICE_BUS_NAMESPACE not set — skipping Service Bus poll");
    return { received: 0, missing: eventIds };
  }

  const credential = new DefaultAzureCredential();
  const client = new ServiceBusClient(`${namespace}.servicebus.windows.net`, credential);
  const queueName = process.env.SERVICE_BUS_QUEUE_NAME || "vulnerability-events";

  try {
    const receiver = client.createReceiver(queueName, { receiveMode: "peekLock" });
    const messages = await receiver.peekMessages(50);
    const receivedIds = new Set();

    for (const msg of messages) {
      try {
        const body = typeof msg.body === "string" ? JSON.parse(msg.body) : msg.body;
        if (body.event_id) receivedIds.add(body.event_id);
      } catch {
        // skip unparseable messages
      }
    }

    await receiver.close();

    const missing = eventIds.filter((id) => !receivedIds.has(id));
    return { received: receivedIds.size, missing };
  } finally {
    await client.close();
  }
}

/**
 * Check dead-letter queue for any messages (should be empty).
 * @returns {Promise<{deadLettered: number}>}
 */
async function checkDeadLetterQueue() {
  if (MOCK_MODE) {
    return { deadLettered: 0 };
  }

  const { ServiceBusClient } = require("@azure/service-bus");
  const { DefaultAzureCredential } = require("@azure/identity");

  const namespace = process.env.SERVICE_BUS_NAMESPACE;
  if (!namespace) {
    console.warn("⚠️  SERVICE_BUS_NAMESPACE not set — skipping DLQ check");
    return { deadLettered: -1 };
  }

  const credential = new DefaultAzureCredential();
  const client = new ServiceBusClient(`${namespace}.servicebus.windows.net`, credential);
  const queueName = process.env.SERVICE_BUS_QUEUE_NAME || "vulnerability-events";

  try {
    const receiver = client.createReceiver(queueName, { subQueueType: "deadLetter" });
    const messages = await receiver.peekMessages(50);
    await receiver.close();
    return { deadLettered: messages.length };
  } finally {
    await client.close();
  }
}

async function main() {
  console.log("═══════════════════════════════════════════════════════");
  console.log(`  Sentinel-D Stress Test — ${EVENT_COUNT} Simultaneous Events`);
  console.log(`  Mode: ${MOCK_MODE ? "MOCK (dry-run)" : "LIVE"}`);
  console.log(`  Target: ${WEBHOOK_URL}`);
  console.log("═══════════════════════════════════════════════════════\n");

  // Step 1: Generate and send all payloads simultaneously
  console.log(`⏳ Sending ${EVENT_COUNT} webhooks simultaneously...`);
  const payloads = Array.from({ length: EVENT_COUNT }, (_, i) => generatePayload(i));
  const startTime = Date.now();
  const results = await Promise.all(payloads.map(sendWebhook));
  const totalTime = Date.now() - startTime;

  // Step 2: Report send results
  const succeeded = results.filter((r) => r.status >= 200 && r.status < 300);
  const failed = results.filter((r) => r.status < 200 || r.status >= 300);

  console.log(`\n📊 Send Results:`);
  console.log(`  ✅ Accepted: ${succeeded.length}/${EVENT_COUNT}`);
  console.log(`  ❌ Failed:   ${failed.length}/${EVENT_COUNT}`);
  console.log(`  ⏱️  Total time: ${totalTime}ms`);
  console.log(`  ⏱️  Avg per msg: ${(totalTime / EVENT_COUNT).toFixed(0)}ms`);

  for (const r of results) {
    const icon = r.status >= 200 && r.status < 300 ? "✅" : "❌";
    console.log(`  ${icon} ${r.eventId} — ${r.status} (${r.elapsedMs}ms)${r.error ? ` [${r.error}]` : ""}`);
  }

  // Step 3: Wait and poll Service Bus
  if (!MOCK_MODE) {
    console.log("\n⏳ Waiting 30s for message processing...");
    await new Promise((r) => setTimeout(r, 30000));
  }

  const eventIds = payloads.map((p) => p.event_id);
  const busResult = await pollServiceBus(eventIds);
  console.log(`\n📬 Service Bus Poll:`);
  console.log(`  Received: ${busResult.received}/${EVENT_COUNT}`);
  if (busResult.missing.length > 0) {
    console.log(`  Missing: ${busResult.missing.join(", ")}`);
  }

  // Step 4: Check DLQ
  if (!MOCK_MODE) {
    console.log("\n⏳ Waiting 2 minutes before DLQ check...");
    await new Promise((r) => setTimeout(r, 120000));
  }

  const dlqResult = await checkDeadLetterQueue();
  console.log(`\n💀 Dead-Letter Queue:`);
  console.log(`  Dead-lettered: ${dlqResult.deadLettered}`);

  // Step 5: Summary
  console.log("\n═══════════════════════════════════════════════════════");
  const allPassed =
    succeeded.length === EVENT_COUNT &&
    busResult.received === EVENT_COUNT &&
    dlqResult.deadLettered === 0;

  if (allPassed) {
    console.log("  ✅ STRESS TEST PASSED");
  } else {
    console.log("  ❌ STRESS TEST FAILED");
    if (succeeded.length < EVENT_COUNT) console.log(`     - ${failed.length} sends failed`);
    if (busResult.missing.length > 0) console.log(`     - ${busResult.missing.length} messages not received`);
    if (dlqResult.deadLettered > 0) console.log(`     - ${dlqResult.deadLettered} messages dead-lettered`);
  }
  console.log("═══════════════════════════════════════════════════════");

  return allPassed ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error("Stress test failed:", err);
    process.exit(1);
  });
