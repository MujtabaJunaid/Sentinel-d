const { classify, computeBlastRadius, computeConfidence } = require("../classifier");

describe("Three-Way Classifier", () => {
  const baseEvent = {
    event_id: "550e8400-e29b-41d4-a716-446655440000",
    cve_id: "CVE-2024-1234",
    severity: "HIGH",
    affected_package: "express",
    current_version: "4.17.1",
    fix_version_range: ">=4.18.0",
    file_path: "src/app.js",
    line_range: [10, 20],
    repo: "org/repo",
    timestamp: "2024-01-01T00:00:00Z",
  };

  const kqlQuery = "traces | where timestamp > ago(30d) | summarize count()";

  describe("classify", () => {
    test("returns ACTIVE when callCount > 0", () => {
      const telemetry = { callCount: 42, lastCalled: "2024-01-15T10:00:00Z" };
      const result = classify(telemetry, baseEvent, kqlQuery);

      expect(result.status).toBe("ACTIVE");
      expect(result.event_id).toBe(baseEvent.event_id);
      expect(result.call_count_30d).toBe(42);
      expect(result.last_called).toBe("2024-01-15T10:00:00Z");
      expect(result.kql_query_used).toBe(kqlQuery);
    });

    test("returns DORMANT when callCount === 0", () => {
      const telemetry = { callCount: 0, lastCalled: null };
      const result = classify(telemetry, baseEvent, kqlQuery);

      expect(result.status).toBe("DORMANT");
      expect(result.call_count_30d).toBe(0);
      expect(result.last_called).toBeNull();
    });

    test("includes blast_radius based on severity", () => {
      const telemetry = { callCount: 1, lastCalled: "2024-01-15T10:00:00Z" };
      const result = classify(telemetry, baseEvent, kqlQuery);

      expect(result.blast_radius).toBe("HIGH");
    });
  });

  describe("computeBlastRadius", () => {
    test("CRITICAL severity → HIGH", () => {
      expect(computeBlastRadius("CRITICAL")).toBe("HIGH");
    });

    test("HIGH severity → HIGH", () => {
      expect(computeBlastRadius("HIGH")).toBe("HIGH");
    });

    test("MEDIUM severity → MEDIUM", () => {
      expect(computeBlastRadius("MEDIUM")).toBe("MEDIUM");
    });

    test("LOW severity → LOW", () => {
      expect(computeBlastRadius("LOW")).toBe("LOW");
    });

    test("unknown severity → UNKNOWN", () => {
      expect(computeBlastRadius("UNKNOWN_VALUE")).toBe("UNKNOWN");
    });
  });

  describe("computeConfidence", () => {
    test("returns 0.3 on error", () => {
      expect(computeConfidence({ callCount: 0, lastCalled: null, error: "fail" })).toBe(0.3);
    });

    test("returns 0.95 for high call count", () => {
      expect(computeConfidence({ callCount: 200, lastCalled: "2024-01-01T00:00:00Z" })).toBe(0.95);
    });

    test("returns 0.85 for positive call count", () => {
      expect(computeConfidence({ callCount: 5, lastCalled: "2024-01-01T00:00:00Z" })).toBe(0.85);
    });

    test("returns 0.7 for zero call count", () => {
      expect(computeConfidence({ callCount: 0, lastCalled: null })).toBe(0.7);
    });
  });
});
