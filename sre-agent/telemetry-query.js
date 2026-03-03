const { DefaultAzureCredential } = require("@azure/identity");
const { LogsQueryClient } = require("@azure/monitor-query");

/**
 * Execute a KQL query against Azure Application Insights and return structured results.
 * Never throws — returns an error field on failure.
 * @param {string} kqlQuery - The validated KQL query to execute
 * @param {string} workspaceId - The Log Analytics workspace ID
 * @returns {{ callCount: number, lastCalled: string|null, error?: string }}
 */
async function queryTelemetry(kqlQuery, workspaceId) {
  try {
    if (!workspaceId) {
      return { callCount: 0, lastCalled: null, error: "Missing workspaceId" };
    }

    const credential = new DefaultAzureCredential();
    const client = new LogsQueryClient(credential);

    const result = await client.queryWorkspace(
      workspaceId,
      kqlQuery,
      { duration: "P30D" }
    );

    if (result.status === "Success" || result.status === "PartialFailure") {
      const tables = result.tables;
      if (tables.length > 0 && tables[0].rows.length > 0) {
        const row = tables[0].rows[0];
        const columns = tables[0].columnDescriptors.map((c) => c.name);

        const countIdx = columns.indexOf("call_count");
        const lastCalledIdx = columns.indexOf("last_called");

        const callCount = countIdx >= 0 ? Number(row[countIdx]) : 0;
        const lastCalled =
          lastCalledIdx >= 0 && row[lastCalledIdx]
            ? new Date(row[lastCalledIdx]).toISOString()
            : null;

        return { callCount, lastCalled };
      }
      return { callCount: 0, lastCalled: null };
    }

    return {
      callCount: 0,
      lastCalled: null,
      error: `Query returned status: ${result.status}`,
    };
  } catch (err) {
    return {
      callCount: 0,
      lastCalled: null,
      error: err.message,
    };
  }
}

module.exports = { queryTelemetry };
