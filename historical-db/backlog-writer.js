const { TableClient } = require("@azure/data-tables");
const { DefaultAzureCredential } = require("@azure/identity");

require("dotenv").config();

const TABLE_STORAGE_CONN = process.env.TABLE_STORAGE_CONN_STRING;
const TABLE_STORAGE_ACCOUNT = process.env.TABLE_STORAGE_ACCOUNT;
const TABLE_NAME = "deferredbacklog";

/**
 * Write a deferred event to Azure Table Storage for later re-scan.
 * Partition key: "deferred", Row key: eventId.
 * @param {string} eventId - The event ID from the webhook payload
 * @param {string} cveId - The CVE identifier
 * @param {string} deferralTimestamp - ISO 8601 timestamp of deferral
 * @param {string} annotation - Human annotation for the deferral
 */
async function writeDeferred(eventId, cveId, deferralTimestamp, annotation) {
  let tableClient;

  if (TABLE_STORAGE_CONN) {
    tableClient = TableClient.fromConnectionString(TABLE_STORAGE_CONN, TABLE_NAME);
  } else if (TABLE_STORAGE_ACCOUNT) {
    const credential = new DefaultAzureCredential();
    tableClient = new TableClient(
      `https://${TABLE_STORAGE_ACCOUNT}.table.core.windows.net`,
      TABLE_NAME,
      credential
    );
  } else {
    throw new Error("Missing TABLE_STORAGE_CONN_STRING or TABLE_STORAGE_ACCOUNT");
  }

  const entity = {
    partitionKey: "deferred",
    rowKey: eventId,
    cveId,
    deferralTimestamp,
    annotation,
    createdAt: new Date().toISOString(),
  };

  await tableClient.upsertEntity(entity, "Replace");

  console.log(
    JSON.stringify({
      message: "Deferred backlog entry written",
      eventId,
      cveId,
    })
  );
}

module.exports = { writeDeferred };
