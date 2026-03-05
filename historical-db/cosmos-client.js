"use strict";

/**
 * cosmos-client.js
 *
 * Unified Cosmos DB client for the Historical DB write path.
 * Called by the Safety Governor after every resolution.
 *
 * Env vars required:
 *   COSMOS_ENDPOINT        — Cosmos DB account endpoint URL
 *   COSMOS_DB_NAME         — Database name (default: "sentinel")
 *   COSMOS_CONTAINER_NAME  — Container name (default: "historical_records")
 */

const { CosmosClient } = require("@azure/cosmos");
const { DefaultAzureCredential } = require("@azure/identity");
const { writeResolutionRecord } = require("./write-client");

const COSMOS_ENDPOINT = process.env.COSMOS_ENDPOINT || process.env.COSMOS_DB_ENDPOINT;
const DATABASE_NAME = process.env.COSMOS_DB_NAME || process.env.COSMOS_DB_DATABASE || "sentinel";
const CONTAINER_NAME =
  process.env.COSMOS_CONTAINER_NAME || process.env.COSMOS_DB_CONTAINER || "historical_records";

/**
 * Returns an authenticated Cosmos DB container handle.
 * @returns {import('@azure/cosmos').Container}
 */
function getContainer() {
  const credential = new DefaultAzureCredential();
  const client = new CosmosClient({ endpoint: COSMOS_ENDPOINT, aadCredentials: credential });
  return client.database(DATABASE_NAME).container(CONTAINER_NAME);
}

/**
 * Upsert a remediation record into Cosmos DB.
 * Validates against historical_db_record.json schema (delegated to write-client).
 * @param {object} record - Must conform to shared/schemas/historical_db_record.json
 * @returns {Promise<{ id: string }>}
 */
async function writeRecord(record) {
  return writeResolutionRecord(record);
}

/**
 * Exact lookup of a historical record by cve_id.
 * Uses a parameterised query against the partition key.
 * @param {string} cveId - e.g. "CVE-2021-44228"
 * @returns {Promise<object|null>} The first matching record, or null if not found
 */
async function getRecord(cveId) {
  const container = getContainer();
  const querySpec = {
    query: "SELECT * FROM c WHERE c.cve_id = @cveId",
    parameters: [{ name: "@cveId", value: cveId }],
  };

  const { resources } = await container.items
    .query(querySpec)
    .fetchAll();

  return resources.length > 0 ? resources[0] : null;
}

/**
 * Delete a record by id and partition key (cve_id).
 * Used only in test cleanup — not part of the production path.
 * @param {string} id - Document id
 * @param {string} cveId - Partition key value
 * @returns {Promise<void>}
 */
async function deleteRecord(id, cveId) {
  const container = getContainer();
  await container.item(id, cveId).delete();
}

module.exports = { writeRecord, getRecord, deleteRecord };
