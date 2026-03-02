const { CosmosClient } = require("@azure/cosmos");
const { DefaultAzureCredential } = require("@azure/identity");
const Ajv = require("ajv");
const addFormats = require("ajv-formats");
const path = require("path");
const fs = require("fs");

require("dotenv").config();

const COSMOS_ENDPOINT = process.env.COSMOS_DB_ENDPOINT;
const DATABASE_NAME = process.env.COSMOS_DB_DATABASE || "sentinel";
const CONTAINER_NAME = process.env.COSMOS_DB_CONTAINER || "historical_records";

// Load and compile schema
let validate;
let schemaLoadError;

try {
  const localSchema = path.resolve(__dirname, "schemas/historical_db_record.json");
  const repoSchema = path.resolve(__dirname, "../shared/schemas/historical_db_record.json");
  const schemaPath = fs.existsSync(localSchema) ? localSchema : repoSchema;
  const schema = JSON.parse(fs.readFileSync(schemaPath, "utf-8"));
  const ajv = new Ajv({ allErrors: true });
  addFormats(ajv);
  validate = ajv.compile(schema);
} catch (err) {
  schemaLoadError = `Schema initialization failed: ${err.message}`;
}

/**
 * Write a resolution record to Cosmos DB after Safety Governor resolution.
 * Validates against historical_db_record.json schema before writing.
 * Uses upsert to handle conflicts (same id).
 * @param {object} record - The historical DB record to write
 * @returns {{ id: string }} The written document ID
 */
async function writeResolutionRecord(record) {
  if (schemaLoadError) {
    throw new Error(schemaLoadError);
  }

  const valid = validate(record);
  if (!valid) {
    const errors = validate.errors.map(
      (e) => `${e.instancePath || "/"}: ${e.message}`
    );
    throw new Error(`Schema validation failed: ${errors.join("; ")}`);
  }

  const credential = new DefaultAzureCredential();
  const client = new CosmosClient({ endpoint: COSMOS_ENDPOINT, aadCredentials: credential });
  const container = client.database(DATABASE_NAME).container(CONTAINER_NAME);

  const { resource } = await container.items.upsert(record);

  console.log(
    JSON.stringify({
      message: "Historical DB record written",
      documentId: resource.id,
      cveId: record.cve_id,
      outcome: record.patch_outcome,
    })
  );

  return { id: resource.id };
}

module.exports = { writeResolutionRecord };
