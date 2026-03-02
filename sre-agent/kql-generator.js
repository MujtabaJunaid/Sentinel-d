const { DefaultAzureCredential } = require("@azure/identity");

const FOUNDRY_ENDPOINT = process.env.FOUNDRY_ENDPOINT;

/**
 * Generate a KQL query that counts calls to a given file path and package
 * in the last 30 days, restricted to the `traces` table.
 * @param {string} filePath - The file path from the vulnerability alert
 * @param {string} packageName - The affected package name
 * @returns {string} A KQL query string
 */
async function generateKQL(filePath, packageName) {
  if (!FOUNDRY_ENDPOINT) {
    // Fallback: generate a deterministic KQL query without calling AI
    return buildFallbackKQL(filePath, packageName);
  }

  const credential = new DefaultAzureCredential();
  const tokenResponse = await credential.getToken(
    "https://cognitiveservices.azure.com/.default"
  );

  const prompt = buildPrompt(filePath, packageName);

  const response = await fetch(FOUNDRY_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${tokenResponse.token}`,
    },
    body: JSON.stringify({
      messages: [
        {
          role: "system",
          content:
            "You are a KQL expert. Output ONLY a valid KQL query with no markdown, no explanation, no code fences.",
        },
        { role: "user", content: prompt },
      ],
      max_tokens: 128,
      temperature: 0,
    }),
  });

  if (!response.ok) {
    throw new Error(`Foundry API error: ${response.status} ${response.statusText}`);
  }

  const data = await response.json();
  const kql = data.choices[0].message.content.trim();
  return kql;
}

/**
 * Build the prompt for KQL generation.
 * @param {string} filePath - Target file path
 * @param {string} packageName - Target package name
 * @returns {string} Prompt text
 */
function buildPrompt(filePath, packageName) {
  return `Write a KQL query for Azure Application Insights that:
1. Uses ONLY the traces table
2. Counts how many times code in file "${filePath}" or package "${packageName}" was called
3. Filters to the last 30 days using: where timestamp > ago(30d)
4. Returns two columns: call_count (count of matching traces) and last_called (max timestamp)
5. Use where clause to filter by message or customDimensions containing the file path or package name`;
}

/**
 * Build a deterministic fallback KQL query when no AI endpoint is available.
 * @param {string} filePath - Target file path
 * @param {string} packageName - Target package name
 * @returns {string} KQL query string
 */
function buildFallbackKQL(filePath, packageName) {
  return `traces
| where timestamp > ago(30d)
| where message contains "${filePath}" or message contains "${packageName}" or customDimensions contains "${filePath}" or customDimensions contains "${packageName}"
| summarize call_count = count(), last_called = max(timestamp)`;
}

module.exports = { generateKQL, buildPrompt, buildFallbackKQL };
