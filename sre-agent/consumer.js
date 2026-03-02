const { ServiceBusClient } = require("@azure/service-bus");
const { DefaultAzureCredential } = require("@azure/identity");
const { generateKQL } = require("./kql-generator");
const { validateKQL } = require("./kql-validator");
const { queryTelemetry } = require("./telemetry-query");
const { classify } = require("./classifier");

require("dotenv").config();

const SB_NAMESPACE = process.env.SERVICE_BUS_NAMESPACE;
const SB_QUEUE = process.env.SERVICE_BUS_QUEUE_NAME || "vulnerability-events";
const WORKSPACE_ID = process.env.APP_INSIGHTS_WORKSPACE_ID;

// Lock renewal interval: 4 minutes (lock duration is 5 min)
const LOCK_RENEWAL_MS = 4 * 60 * 1000;

/**
 * Process a single webhook payload message through the SRE Agent pipeline.
 * Generates KQL, validates it, queries telemetry, and classifies the result.
 * @param {object} event - Parsed webhook_payload from Service Bus message body
 * @returns {object} telemetry_classification conforming to the schema
 */
async function processEvent(event) {
  const kqlQuery = await generateKQL(event.file_path, event.affected_package);

  const validation = validateKQL(kqlQuery);
  if (!validation.valid) {
    throw new Error(`KQL validation failed: ${validation.reason}`);
  }

  const telemetryResult = await queryTelemetry(kqlQuery, WORKSPACE_ID);
  return classify(telemetryResult, event, kqlQuery);
}

/**
 * Start the Service Bus consumer.
 * Subscribes to the vulnerability-events queue, processes each message
 * through the SRE Agent pipeline, and completes or abandons accordingly.
 */
async function startConsumer() {
  const credential = new DefaultAzureCredential();
  const client = new ServiceBusClient(
    `${SB_NAMESPACE}.servicebus.windows.net`,
    credential
  );
  const receiver = client.createReceiver(SB_QUEUE);

  console.log(`SRE Agent consumer listening on queue: ${SB_QUEUE}`);

  const messageHandler = async (message) => {
    let renewalTimer;
    try {
      // Renew lock every 4 minutes for long-running processing
      renewalTimer = setInterval(async () => {
        try {
          await receiver.renewMessageLock(message);
        } catch (err) {
          console.error(`Lock renewal failed for ${message.messageId}:`, err.message);
        }
      }, LOCK_RENEWAL_MS);

      const event = message.body;
      const classification = await processEvent(event);

      console.log(
        `Classified event_id=${event.event_id} as ${classification.status}`
      );

      await receiver.completeMessage(message);
      return classification;
    } catch (err) {
      console.error(
        `Processing failed for message ${message.messageId}:`,
        err.message
      );
      await receiver.abandonMessage(message);
      return null;
    } finally {
      if (renewalTimer) clearInterval(renewalTimer);
    }
  };

  const errorHandler = async (err) => {
    console.error("Service Bus receiver error:", err.message);
  };

  receiver.subscribe({
    processMessage: messageHandler,
    processError: errorHandler,
  });

  // Graceful shutdown
  const shutdown = async () => {
    console.log("Shutting down SRE Agent consumer...");
    await receiver.close();
    await client.close();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

// Run if executed directly
if (require.main === module) {
  startConsumer().catch((err) => {
    console.error("Consumer startup failed:", err.message);
    process.exit(1);
  });
}

module.exports = { processEvent, startConsumer };
