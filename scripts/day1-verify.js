#!/usr/bin/env node
/**
 * Day 1 End-of-Day Verification Script
 *
 * 1. POSTs a valid webhook_payload to the Azure Function
 * 2. Reads the next message from the Service Bus queue
 * 3. Asserts the message body matches the sent payload
 * 4. Prints PASS or FAIL with details
 *
 * Usage:
 *   FUNCTION_URL=https://<app>.azurewebsites.net/api/webhook-receiver \
 *   FUNCTION_KEY=<key> \
 *   SERVICE_BUS_NAMESPACE=<namespace> \
 *   node scripts/day1-verify.js
 */
const { ServiceBusClient } = require("@azure/service-bus");
const { DefaultAzureCredential } = require("@azure/identity");
const { randomUUID } = require("crypto");

const FUNCTION_URL = process.env.FUNCTION_URL;
const FUNCTION_KEY = process.env.FUNCTION_KEY || "";
const SB_NAMESPACE = process.env.SERVICE_BUS_NAMESPACE;
const SB_QUEUE = process.env.SERVICE_BUS_QUEUE_NAME || "vulnerability-events";

if (!FUNCTION_URL || !SB_NAMESPACE) {
  console.error(
    "ERROR: Set FUNCTION_URL and SERVICE_BUS_NAMESPACE environment variables"
  );
  process.exit(1);
}

const testPayload = {
  event_id: randomUUID(),
  cve_id: "CVE-2024-9999",
  severity: "CRITICAL",
  affected_package: "express",
  current_version: "4.17.1",
  fix_version_range: ">=4.18.0",
  file_path: "src/server.js",
  line_range: [10, 25],
  repo: "contoso/webapp",
  timestamp: new Date().toISOString(),
};

async function main() {
  console.log("=== Sentinel-D Day 1 Verification ===\n");
  console.log(`Function URL: ${FUNCTION_URL}`);
  console.log(`Service Bus:  ${SB_NAMESPACE} / ${SB_QUEUE}`);
  console.log(`Event ID:     ${testPayload.event_id}\n`);

  // Step 1: POST valid payload to Function
  console.log("--- Step 1: POST valid payload ---");
  const url = FUNCTION_KEY
    ? `${FUNCTION_URL}?code=${FUNCTION_KEY}`
    : FUNCTION_URL;

  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(testPayload),
  });

  const responseText = await response.text();
  let responseBody;
  try {
    responseBody = JSON.parse(responseText);
  } catch {
    console.log(`HTTP ${response.status} (non-JSON): ${responseText.slice(0, 500)}`);
    console.log("\n❌ FAIL — Response is not valid JSON. Check FUNCTION_KEY or deployment status.");
    process.exit(1);
  }
  console.log(`HTTP ${response.status}: ${JSON.stringify(responseBody)}`);

  if (response.status !== 202) {
    console.log("\n❌ FAIL — Expected HTTP 202, got", response.status);
    process.exit(1);
  }
  console.log("✅ HTTP 202 received\n");

  // Step 2: Read message from Service Bus queue
  console.log("--- Step 2: Read message from Service Bus ---");
  const credential = new DefaultAzureCredential();
  const sbClient = new ServiceBusClient(
    `${SB_NAMESPACE}.servicebus.windows.net`,
    credential
  );
  const receiver = sbClient.createReceiver(SB_QUEUE);

  try {
    let matched = null;
    const staleCount = { n: 0 };

    // Read messages in batches, completing stale ones until we find ours
    while (!matched) {
      const messages = await receiver.receiveMessages(10, {
        maxWaitTimeInMs: 15000,
      });

      if (messages.length === 0) break;

      for (const msg of messages) {
        if (msg.body && msg.body.event_id === testPayload.event_id) {
          matched = msg;
        } else {
          staleCount.n++;
          await receiver.completeMessage(msg);
        }
      }
    }

    if (staleCount.n > 0) {
      console.log(`(drained ${staleCount.n} stale message(s) from previous runs)`);
    }

    if (!matched) {
      console.log("❌ FAIL — No matching message found in queue after 15 seconds");
      process.exit(1);
    }

    const msgBody = matched.body;
    console.log(`Message received: event_id=${msgBody.event_id}`);

    // Step 3: Assert match
    console.log("\n--- Step 3: Assert payload match ---");
    if (msgBody.cve_id !== testPayload.cve_id) {
      console.log(
        `❌ FAIL — cve_id mismatch: sent=${testPayload.cve_id} received=${msgBody.cve_id}`
      );
      process.exit(1);
    }

    // Complete the message (remove from queue)
    await receiver.completeMessage(matched);

    console.log("✅ Payload matches\n");
    console.log("=== ✅ PASS — Day 1 verification complete ===");
  } finally {
    await receiver.close();
    await sbClient.close();
  }
}

main().catch((err) => {
  console.error("❌ FAIL —", err.message);
  process.exit(1);
});
