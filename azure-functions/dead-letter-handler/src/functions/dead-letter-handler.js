"use strict";

/**
 * dead-letter-handler.js — Azure Function Timer Trigger
 *
 * Runs every 15 minutes. Reads all messages from the Service Bus dead-letter queue.
 * - Logs each message to App Insights structured log
 * - Creates a GitHub Issue if a message has been dead-lettered >3 times (systemic failure)
 * - Does NOT automatically retry — dead-lettered messages require human inspection
 */

const { app } = require("@azure/functions");
const { ServiceBusClient } = require("@azure/service-bus");
const { DefaultAzureCredential } = require("@azure/identity");
const { Octokit } = require("@octokit/rest");

/**
 * Process a batch of dead-letter messages.
 *
 * @param {object[]} messages - Dead-letter messages
 * @param {object} options - Environment config
 * @returns {Promise<{processed: number, issuesCreated: number}>}
 */
async function processDeadLetters(messages, options = {}) {
  const {
    githubToken = process.env.GITHUB_TOKEN,
    githubOwner = process.env.GITHUB_OWNER,
    githubRepo = process.env.GITHUB_REPO,
  } = options;

  let issuesCreated = 0;

  for (const msg of messages) {
    const body = typeof msg.body === "string"
      ? msg.body
      : JSON.stringify(msg.body);

    const deliveryCount = msg.deliveryCount || 0;
    const deadLetterReason = msg.deadLetterReason || "Unknown";
    const deadLetterDescription = msg.deadLetterErrorDescription || "";
    const enqueuedTime = msg.enqueuedTimeUtc || msg._rawAmqpMessage?.messageAnnotations?.["x-opt-enqueued-time"];

    // Structured log for App Insights
    console.log(
      JSON.stringify({
        message: "Dead-letter message processed",
        messageId: msg.messageId,
        deliveryCount,
        deadLetterReason,
        deadLetterDescription,
        enqueuedTime,
        bodyPreview: body.substring(0, 500),
        severity: deliveryCount > 3 ? "ERROR" : "WARNING",
      })
    );

    // Create GitHub Issue for systemic failures (>3 delivery attempts)
    if (deliveryCount > 3 && githubToken && githubOwner && githubRepo) {
      try {
        const octokit = new Octokit({ auth: githubToken });

        let eventId = "unknown";
        let cveId = "unknown";
        try {
          const parsed = JSON.parse(body);
          eventId = parsed.event_id || eventId;
          cveId = parsed.cve_id || cveId;
        } catch {
          // Body might not be JSON
        }

        await octokit.rest.issues.create({
          owner: githubOwner,
          repo: githubRepo,
          title: `[Sentinel-D DLQ] Systemic failure: ${cveId} (${deliveryCount} attempts)`,
          body: `## 🚨 Dead-Letter Queue — Systemic Failure

A message has failed processing **${deliveryCount} times** and was moved to the dead-letter queue.

### Message Details

| Field | Value |
|-------|-------|
| **Message ID** | \`${msg.messageId || "N/A"}\` |
| **Event ID** | \`${eventId}\` |
| **CVE ID** | \`${cveId}\` |
| **Delivery Count** | ${deliveryCount} |
| **Dead-Letter Reason** | ${deadLetterReason} |
| **Error Description** | ${deadLetterDescription} |
| **Enqueued Time** | ${enqueuedTime || "N/A"} |

### Message Body (preview)

\`\`\`json
${body.substring(0, 1000)}
\`\`\`

---

**Action required:** Inspect the message, fix the root cause, and resubmit if appropriate.
This message will NOT be automatically retried.`,
          labels: ["sentinel/infrastructure-failure"],
        });

        issuesCreated++;
      } catch (err) {
        console.error(
          JSON.stringify({
            message: "Failed to create DLQ GitHub Issue",
            error: err.message,
            messageId: msg.messageId,
          })
        );
      }
    }
  }

  return { processed: messages.length, issuesCreated };
}

/**
 * Receive messages from the dead-letter sub-queue.
 *
 * @param {object} [options]
 * @returns {Promise<object[]>}
 */
async function receiveDeadLetters(options = {}) {
  const namespace = process.env.SERVICE_BUS_NAMESPACE;
  const queueName = process.env.SERVICE_BUS_QUEUE_NAME || "vulnerability-events";

  if (!namespace) {
    throw new Error("SERVICE_BUS_NAMESPACE environment variable is required");
  }

  const credential = new DefaultAzureCredential();
  const client = new ServiceBusClient(
    `${namespace}.servicebus.windows.net`,
    credential
  );

  try {
    const receiver = client.createReceiver(queueName, {
      subQueueType: "deadLetter",
    });

    const messages = await receiver.receiveMessages(50, {
      maxWaitTimeInMs: 5000,
    });

    // Complete all received messages (remove from DLQ after processing)
    for (const msg of messages) {
      await receiver.completeMessage(msg);
    }

    await receiver.close();
    return messages;
  } finally {
    await client.close();
  }
}

// Register the Azure Function timer trigger
app.timer("dead-letter-handler", {
  schedule: "0 */15 * * * *",
  handler: async (timer, context) => {
    context.log("Dead-letter handler triggered at:", timer.scheduleStatus);

    try {
      const messages = await receiveDeadLetters();

      if (messages.length === 0) {
        context.log("No dead-letter messages found");
        return;
      }

      context.log(`Processing ${messages.length} dead-letter message(s)`);
      const result = await processDeadLetters(messages);
      context.log(
        `Processed ${result.processed} messages, created ${result.issuesCreated} issues`
      );
    } catch (err) {
      context.error("Dead-letter handler failed:", err.message);
      throw err;
    }
  },
});

module.exports = { processDeadLetters, receiveDeadLetters };
