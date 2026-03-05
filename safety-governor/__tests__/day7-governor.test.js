"use strict";

const { route } = require("../router");
const { buildPRBody } = require("../pr-generator");
const { buildEscalationBody, firePagerDutyAlert } = require("../escalate");
const { writeAuditRecord, getTableClient } = require("../audit-log");
const { govern, tierToOutcome, buildHistoricalRecord } = require("../governor");

// ─── Fixtures ──────────────────────────────────────────────────────────────────

function makeBundle(overrides = {}) {
  return {
    event_id: "evt-001",
    tests_passed: 42,
    tests_failed: 0,
    coverage_before: 0.85,
    coverage_after: 0.87,
    visual_diff_pct: 0.001,
    visual_regression: false,
    container_id: "capp-abc",
    test_log_url: "https://example.com/logs/evt-001",
    ...overrides,
  };
}

function makePatch(overrides = {}) {
  return {
    event_id: "evt-001",
    status: "SUCCESS",
    source: "FOUNDRY",
    files_modified: ["pom.xml"],
    lines_changed: 3,
    touches_auth_crypto: false,
    llm_confidence: 0.92,
    model_id: "gpt-4o",
    diff: "--- a/pom.xml\n+++ b/pom.xml\n@@ -1 +1 @@\n-old\n+new",
    reasoning_chain: "https://example.com/chain/evt-001",
    ...overrides,
  };
}

function makeEvent(overrides = {}) {
  return {
    event_id: "evt-001",
    cve_id: "CVE-2024-1234",
    severity: "HIGH",
    affected_package: "spring-boot",
    current_version: "2.7.0",
    file_path: "pom.xml",
    repo: "org/demo-app",
    ...overrides,
  };
}

// ─── Router Tests ──────────────────────────────────────────────────────────────

describe("router.route()", () => {
  test("HIGH tier (score >= 0.85) → AUTO_PR", () => {
    const result = route(0.90, makeBundle(), makePatch());
    expect(result.tier).toBe("HIGH");
    expect(result.action).toBe("AUTO_PR");
    expect(result.overrideReason).toBeNull();
  });

  test("HIGH tier at boundary (0.85) → AUTO_PR", () => {
    const result = route(0.85, makeBundle(), makePatch());
    expect(result.tier).toBe("HIGH");
    expect(result.action).toBe("AUTO_PR");
  });

  test("MEDIUM tier (0.70 <= score < 0.85) → REVIEW_PR", () => {
    const result = route(0.75, makeBundle(), makePatch());
    expect(result.tier).toBe("MEDIUM");
    expect(result.action).toBe("REVIEW_PR");
    expect(result.overrideReason).toBeNull();
  });

  test("MEDIUM tier at boundary (0.70) → REVIEW_PR", () => {
    const result = route(0.70, makeBundle(), makePatch());
    expect(result.tier).toBe("MEDIUM");
    expect(result.action).toBe("REVIEW_PR");
  });

  test("LOW tier (0.55 <= score < 0.70) → GITHUB_ISSUE_ESCALATE", () => {
    const result = route(0.60, makeBundle(), makePatch());
    expect(result.tier).toBe("LOW");
    expect(result.action).toBe("GITHUB_ISSUE_ESCALATE");
    expect(result.overrideReason).toBeNull();
  });

  test("LOW tier at boundary (0.55) → GITHUB_ISSUE_ESCALATE", () => {
    const result = route(0.55, makeBundle(), makePatch());
    expect(result.tier).toBe("LOW");
    expect(result.action).toBe("GITHUB_ISSUE_ESCALATE");
  });

  test("BLOCKED tier (score < 0.55) → ARCHIVE", () => {
    const result = route(0.40, makeBundle(), makePatch());
    expect(result.tier).toBe("BLOCKED");
    expect(result.action).toBe("ARCHIVE");
    expect(result.overrideReason).toBeNull();
  });

  test("BLOCKED tier at zero → ARCHIVE", () => {
    const result = route(0.0, makeBundle(), makePatch());
    expect(result.tier).toBe("BLOCKED");
    expect(result.action).toBe("ARCHIVE");
  });
});

// ─── Override Tests ────────────────────────────────────────────────────────────

describe("router override conditions", () => {
  test("CANNOT_PATCH overrides HIGH → BLOCKED", () => {
    const result = route(0.95, makeBundle(), makePatch({ status: "CANNOT_PATCH" }));
    expect(result.tier).toBe("BLOCKED");
    expect(result.action).toBe("ARCHIVE");
    expect(result.overrideReason).toMatch(/CANNOT_PATCH/);
  });

  test("tests_failed === -1 overrides MEDIUM → BLOCKED", () => {
    const result = route(0.75, makeBundle({ tests_failed: -1 }), makePatch());
    expect(result.tier).toBe("BLOCKED");
    expect(result.action).toBe("ARCHIVE");
    expect(result.overrideReason).toMatch(/timed out/);
  });

  test("tests_failed === -2 overrides to BLOCKED", () => {
    const result = route(0.90, makeBundle({ tests_failed: -2 }), makePatch());
    expect(result.tier).toBe("BLOCKED");
    expect(result.overrideReason).toMatch(/git apply/i);
  });

  test("touches_auth_crypto overrides HIGH → LOW", () => {
    const result = route(0.90, makeBundle(), makePatch({ touches_auth_crypto: true }));
    expect(result.tier).toBe("LOW");
    expect(result.action).toBe("GITHUB_ISSUE_ESCALATE");
    expect(result.overrideReason).toMatch(/auth\/crypto/);
  });

  test("touches_auth_crypto does not downgrade BLOCKED", () => {
    const result = route(0.40, makeBundle(), makePatch({ touches_auth_crypto: true }));
    expect(result.tier).toBe("BLOCKED");
    expect(result.action).toBe("ARCHIVE");
  });

  test("visual_regression overrides HIGH → MEDIUM", () => {
    const result = route(0.90, makeBundle({ visual_regression: true }), makePatch());
    expect(result.tier).toBe("MEDIUM");
    expect(result.action).toBe("REVIEW_PR");
    expect(result.overrideReason).toMatch(/[Vv]isual regression/);
  });

  test("FULL_REFACTOR overrides HIGH → MEDIUM", () => {
    const result = route(0.90, makeBundle(), makePatch({ fix_strategy: "FULL_REFACTOR" }));
    expect(result.tier).toBe("MEDIUM");
    expect(result.action).toBe("REVIEW_PR");
    expect(result.overrideReason).toMatch(/FULL_REFACTOR/);
  });
});

// ─── PR Body Tests ─────────────────────────────────────────────────────────────

describe("buildPRBody()", () => {
  test("HIGH tier body includes auto-approve badge", () => {
    const body = buildPRBody(makePatch(), makeBundle(), 0.92, "HIGH");
    expect(body).toContain("brightgreen");
    expect(body).toContain("Sentinel-D");
    expect(body).toContain("CVE Summary");
    expect(body).not.toContain("Human review required");
  });

  test("MEDIUM tier body includes review warning", () => {
    const body = buildPRBody(makePatch(), makeBundle(), 0.75, "MEDIUM");
    expect(body).toContain("Human review required");
    expect(body).toContain("yellow");
  });

  test("body contains all required sections", () => {
    const body = buildPRBody(
      makePatch({ cve_id: "CVE-2024-9999", affected_package: "lodash" }),
      makeBundle(),
      0.88,
      "HIGH"
    );
    expect(body).toContain("CVE-2024-9999");
    expect(body).toContain("lodash");
    expect(body).toContain("FOUNDRY");
    expect(body).toContain("Sandbox Results");
    expect(body).toContain("Confidence Breakdown");
    expect(body).toContain("Generated by Sentinel-D v3.0");
  });
});

// ─── Escalation Body Tests ─────────────────────────────────────────────────────

describe("buildEscalationBody()", () => {
  test("escalation body includes all diagnostic sections", () => {
    const body = buildEscalationBody(makeEvent(), 0.58, makeBundle(), makePatch());
    expect(body).toContain("CVE-2024-1234");
    expect(body).toContain("spring-boot");
    expect(body).toContain("0.58");
    expect(body).toContain("Score Breakdown");
    expect(body).toContain("Sandbox Results");
    expect(body).toContain("fix-now");
    expect(body).toContain("defer");
    expect(body).toContain("wont-fix");
  });
});

// ─── Audit Log Tests ───────────────────────────────────────────────────────────

describe("writeAuditRecord()", () => {
  test("creates entity with correct partition/row keys", async () => {
    const mockCreateEntity = jest.fn().mockResolvedValue({});
    jest.spyOn(require("../audit-log"), "getTableClient").mockReturnValue({
      createEntity: mockCreateEntity,
    });

    const result = await writeAuditRecord({
      event: makeEvent(),
      tier: "HIGH",
      compositeScore: 0.92,
      action: "AUTO_PR",
      prUrl: "https://github.com/org/repo/pull/1",
      issueUrl: null,
      validationBundle: makeBundle(),
      candidatePatch: makePatch(),
      overrideReason: null,
    });

    expect(result.partitionKey).toMatch(/^\d{4}-\d{2}$/);
    expect(result.rowKey).toBe("evt-001");

    const entity = mockCreateEntity.mock.calls[0][0];
    expect(entity.eventId).toBe("evt-001");
    expect(entity.cveId).toBe("CVE-2024-1234");
    expect(entity.tier).toBe("HIGH");
    expect(entity.actionTaken).toBe("AUTO_PR");
    expect(entity.humanOverride).toBe(false);
    expect(entity.pipelineVersion).toBeTruthy();
    expect(entity.compositeScore).toBe(0.92);
    expect(entity.testsPassed).toBe(42);
    expect(entity.prUrl).toBe("https://github.com/org/repo/pull/1");
  });

  test("includes all five validation signals", async () => {
    const mockCreateEntity = jest.fn().mockResolvedValue({});
    jest.spyOn(require("../audit-log"), "getTableClient").mockReturnValue({
      createEntity: mockCreateEntity,
    });

    await writeAuditRecord({
      event: makeEvent(),
      tier: "LOW",
      compositeScore: 0.58,
      action: "GITHUB_ISSUE_ESCALATE",
      prUrl: null,
      issueUrl: "https://github.com/org/repo/issues/5",
      validationBundle: makeBundle({ tests_failed: 2, visual_regression: true }),
      candidatePatch: makePatch({ touches_auth_crypto: true }),
      overrideReason: "auth/crypto forced LOW",
    });

    const entity = mockCreateEntity.mock.calls[0][0];
    expect(entity.testsFailed).toBe(2);
    expect(entity.visualRegression).toBe(true);
    expect(entity.touchesAuthCrypto).toBe(true);
    expect(entity.overrideReason).toBe("auth/crypto forced LOW");
    expect(entity.issueUrl).toBe("https://github.com/org/repo/issues/5");
  });
});

// ─── tierToOutcome Tests ───────────────────────────────────────────────────────

describe("tierToOutcome()", () => {
  test("maps HIGH → SUCCESS", () => expect(tierToOutcome("HIGH")).toBe("SUCCESS"));
  test("maps MEDIUM → PARTIAL", () => expect(tierToOutcome("MEDIUM")).toBe("PARTIAL"));
  test("maps LOW → FAILED", () => expect(tierToOutcome("LOW")).toBe("FAILED"));
  test("maps BLOCKED → FAILED", () => expect(tierToOutcome("BLOCKED")).toBe("FAILED"));
});

// ─── Historical Record Tests ───────────────────────────────────────────────────

describe("buildHistoricalRecord()", () => {
  test("builds record with all required fields", () => {
    const record = buildHistoricalRecord(
      makeEvent(),
      makePatch(),
      "HIGH",
      null
    );

    expect(record.id).toBe("evt-001");
    expect(record.cve_id).toBe("CVE-2024-1234");
    expect(record.repo).toBe("org/demo-app");
    expect(record.patch_outcome).toBe("SUCCESS");
    expect(record.resolved_by).toBe("sentinel-d-safety-governor");
    expect(record.patch_diff).toContain("pom.xml");
    expect(record.solutions_tried).toHaveLength(1);
    expect(record.solutions_tried[0].source).toBe("FOUNDRY");
    expect(record.failure_reason).toBeNull();
    expect(record.pipeline_version).toBeTruthy();
  });

  test("LOW/BLOCKED tier sets FAILED outcome", () => {
    const record = buildHistoricalRecord(
      makeEvent(),
      makePatch(),
      "LOW",
      "Low confidence"
    );
    expect(record.patch_outcome).toBe("FAILED");
    expect(record.failure_reason).toBe("Low confidence");
  });
});

// ─── Governor Orchestrator Tests ───────────────────────────────────────────────

describe("govern()", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  test("HIGH score → AUTO_PR flow", async () => {
    const mockCreatePR = jest
      .spyOn(require("../pr-generator"), "createPR")
      .mockResolvedValue({ prNumber: 42, prUrl: "https://github.com/org/repo/pull/42" });

    const mockAuditWrite = jest
      .spyOn(require("../audit-log"), "writeAuditRecord")
      .mockResolvedValue({ partitionKey: "2025-01", rowKey: "evt-001" });

    const mockWriteRecord = jest
      .spyOn(require("../../historical-db/write-client"), "writeResolutionRecord")
      .mockResolvedValue({});

    const result = await govern({
      event: makeEvent(),
      compositeScore: 0.92,
      validationBundle: makeBundle(),
      candidatePatch: makePatch(),
    });

    expect(result.tier).toBe("HIGH");
    expect(result.action).toBe("AUTO_PR");
    expect(result.prNumber).toBe(42);
    expect(result.patchOutcome).toBe("SUCCESS");
    expect(mockCreatePR).toHaveBeenCalledTimes(1);
    expect(mockAuditWrite).toHaveBeenCalledTimes(1);
    expect(mockWriteRecord).toHaveBeenCalledTimes(1);
    expect(mockWriteRecord.mock.calls[0][0].patch_outcome).toBe("SUCCESS");
  });

  test("LOW score → GITHUB_ISSUE_ESCALATE flow", async () => {
    const mockEscalate = jest
      .spyOn(require("../escalate"), "createEscalationIssue")
      .mockResolvedValue({
        issueNumber: 10,
        issueUrl: "https://github.com/org/repo/issues/10",
        pagerdutyAlerted: false,
      });

    jest.spyOn(require("../audit-log"), "writeAuditRecord")
      .mockResolvedValue({ partitionKey: "2025-01", rowKey: "evt-001" });

    const mockWriteRecord = jest
      .spyOn(require("../../historical-db/write-client"), "writeResolutionRecord")
      .mockResolvedValue({});

    const result = await govern({
      event: makeEvent(),
      compositeScore: 0.60,
      validationBundle: makeBundle(),
      candidatePatch: makePatch(),
    });

    expect(result.tier).toBe("LOW");
    expect(result.action).toBe("GITHUB_ISSUE_ESCALATE");
    expect(result.issueNumber).toBe(10);
    expect(result.patchOutcome).toBe("FAILED");
    expect(mockEscalate).toHaveBeenCalledTimes(1);
    expect(mockWriteRecord).toHaveBeenCalledTimes(1);
  });

  test("BLOCKED score → ARCHIVE flow (no PR, no issue)", async () => {
    jest.spyOn(require("../audit-log"), "writeAuditRecord")
      .mockResolvedValue({ partitionKey: "2025-01", rowKey: "evt-001" });

    const mockWriteRecord = jest
      .spyOn(require("../../historical-db/write-client"), "writeResolutionRecord")
      .mockResolvedValue({});

    const result = await govern({
      event: makeEvent(),
      compositeScore: 0.30,
      validationBundle: makeBundle(),
      candidatePatch: makePatch(),
    });

    expect(result.tier).toBe("BLOCKED");
    expect(result.action).toBe("ARCHIVE");
    expect(result.prUrl).toBeNull();
    expect(result.issueUrl).toBeNull();
    expect(result.patchOutcome).toBe("FAILED");
    expect(mockWriteRecord).toHaveBeenCalledTimes(1);
  });
});
