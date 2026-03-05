"use strict";

/**
 * cosmos-write.test.js
 *
 * Integration test for the Historical DB write path.
 * Requires a live Azure Cosmos DB account.
 *
 * Env vars required (set in CI or .env):
 *   COSMOS_ENDPOINT        — Cosmos DB account endpoint URL
 *   COSMOS_DB_NAME         — Database name
 *   COSMOS_CONTAINER_NAME  — Container name
 *
 * Run: cd historical-db && npm test
 */

const { writeRecord, getRecord, deleteRecord } = require("../cosmos-client");

// Skip when Azure credentials / endpoint are not configured
const SKIP = !process.env.COSMOS_ENDPOINT && !process.env.COSMOS_DB_ENDPOINT;

const describeOrSkip = SKIP ? describe.skip : describe;

/** Minimal valid record conforming to historical_db_record.json schema */
const TEST_RECORD = {
  id: "test-cve-2021-44228-integration",
  cve_id: "CVE-2021-44228",
  affected_package: "log4j-core",
  affected_version_range: ">=2.0-beta9 <2.15.0",
  cve_description_embedding: [0.1, 0.2, 0.3],
  fix_strategy_used: "version_bump",
  patch_diff: "--- a/pom.xml\n+++ b/pom.xml\n@@ -1 +1 @@\n-2.14.1\n+2.15.0",
  patch_outcome: "SUCCESS",
  failure_reason: null,
  solutions_tried: [{ strategy: "version_bump", attempted_at: "2026-03-05T00:00:00Z" }],
  repo: "sentinel-d/demo-app",
  language: "java",
  framework: "spring-boot",
  resolved_at: "2026-03-05T16:00:00Z",
  resolved_by: "sentinel-d-pipeline",
  human_override: false,
  pipeline_version: "1.0.0",
};

describeOrSkip("Cosmos DB write path — integration", () => {
  afterAll(async () => {
    // Cleanup: remove the test record so re-runs are idempotent
    try {
      await deleteRecord(TEST_RECORD.id, TEST_RECORD.cve_id);
    } catch {
      // Ignore if already deleted or not found
    }
  });

  test("writeRecord upserts a SUCCESS record for CVE-2021-44228", async () => {
    const result = await writeRecord(TEST_RECORD);
    expect(result).toHaveProperty("id", TEST_RECORD.id);
  });

  test("getRecord returns the written record by cve_id", async () => {
    const record = await getRecord("CVE-2021-44228");
    expect(record).not.toBeNull();
    expect(record.id).toBe(TEST_RECORD.id);
    expect(record.cve_id).toBe("CVE-2021-44228");
    expect(record.patch_outcome).toBe("SUCCESS");
    expect(record.affected_package).toBe("log4j-core");
    expect(record.human_override).toBe(false);
    expect(record.pipeline_version).toBe("1.0.0");
  });

  test("getRecord returns null for an unknown cve_id", async () => {
    const record = await getRecord("CVE-9999-00000");
    expect(record).toBeNull();
  });
});

// Unit test: schema validation rejects a bad record (no Azure needed)
describe("Cosmos DB write path — schema validation", () => {
  test("writeRecord rejects a record missing required fields", async () => {
    const bad = { id: "bad", cve_id: "CVE-0000-0000" }; // missing most required fields
    await expect(writeRecord(bad)).rejects.toThrow(/Schema validation failed/);
  });
});
