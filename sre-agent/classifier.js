/**
 * Three-way classifier for SRE Agent telemetry results.
 * Produces a telemetry_classification conforming to the shared schema.
 */

/**
 * Classify a telemetry result as ACTIVE, DORMANT, or DEFERRED.
 * @param {{ callCount: number, lastCalled: string|null, error?: string }} telemetryResult
 * @param {object} event - The original webhook_payload event
 * @param {string} kqlQuery - The KQL query that was used
 * @returns {object} telemetry_classification per the schema
 */
function classify(telemetryResult, event, kqlQuery) {
  const status = telemetryResult.callCount > 0 ? "ACTIVE" : "DORMANT";
  const blastRadius = computeBlastRadius(event.severity);
  const confidence = computeConfidence(telemetryResult);

  return {
    event_id: event.event_id,
    status,
    call_count_30d: telemetryResult.callCount,
    last_called: telemetryResult.lastCalled || null,
    blast_radius: blastRadius,
    kql_query_used: kqlQuery,
    confidence,
  };
}

/**
 * Map severity to blast_radius.
 * @param {string} severity - CRITICAL, HIGH, MEDIUM, or LOW
 * @returns {string} HIGH, MEDIUM, or LOW
 */
function computeBlastRadius(severity) {
  switch (severity) {
    case "CRITICAL":
    case "HIGH":
      return "HIGH";
    case "MEDIUM":
      return "MEDIUM";
    case "LOW":
      return "LOW";
    default:
      return "UNKNOWN";
  }
}

/**
 * Compute confidence based on the telemetry result quality.
 * @param {{ callCount: number, lastCalled: string|null, error?: string }} telemetryResult
 * @returns {number} Confidence between 0 and 1
 */
function computeConfidence(telemetryResult) {
  if (telemetryResult.error) return 0.3;
  if (telemetryResult.callCount > 100) return 0.95;
  if (telemetryResult.callCount > 0) return 0.85;
  return 0.7;
}

module.exports = { classify, computeBlastRadius, computeConfidence };
