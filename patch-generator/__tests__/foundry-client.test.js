"use strict";

/**
 * foundry-client.test.js — Tests for the Patch Generator Foundry API Client
 */

const {
  generatePatch,
  buildMockPatch,
  buildCannotPatch,
  mapFoundryResponse,
} = require("../foundry-client");

// ── Shared fixtures ─────────────────────────────────────────────────────────

const MOCK_STRUCTURED_CONTEXT = {
  event_id: "550e8400-e29b-41d4-a716-446655440001",
  fix_strategy: "VERSION_BUMP",
  breaking_changes: [],
  community_intent_class: "security-fix",
  intent_confidence: 0.92,
  nvd_context: { cvss_score: 10.0 },
  migration_steps: ["Bump log4j-core to >=2.15.0"],
  historical_match_status: "NO_MATCH",
  historical_patch_available: false,
  solutions_to_avoid: [],
  historical_record_id: null,
  pipeline_version: "3.0.0",
};

const CANDIDATE_PATCH_REQUIRED_KEYS = [
  "event_id",
  "status",
  "source",
  "files_modified",
  "lines_changed",
  "touches_auth_crypto",
  "llm_confidence",
  "model_id",
];

// ── Tests ───────────────────────────────────────────────────────────────────

describe("generatePatch — mock mode (no Foundry endpoint)", () => {
  const originalEndpoint = process.env.FOUNDRY_ENDPOINT;
  const originalKey = process.env.FOUNDRY_API_KEY;

  beforeAll(() => {
    delete process.env.FOUNDRY_ENDPOINT;
    delete process.env.FOUNDRY_API_KEY;
  });

  afterAll(() => {
    if (originalEndpoint) process.env.FOUNDRY_ENDPOINT = originalEndpoint;
    if (originalKey) process.env.FOUNDRY_API_KEY = originalKey;
  });

  test("returns PATCH_GENERATED with mock diff", async () => {
    const result = await generatePatch(MOCK_STRUCTURED_CONTEXT);

    expect(result.status).toBe("PATCH_GENERATED");
    expect(result.source).toBe("FOUNDRY");
    expect(result.event_id).toBe(MOCK_STRUCTURED_CONTEXT.event_id);
    expect(result.diff).toBeTruthy();
    expect(result.model_id).toContain("mock");
  });

  test("includes all required candidate_patch.json keys", async () => {
    const result = await generatePatch(MOCK_STRUCTURED_CONTEXT);

    for (const key of CANDIDATE_PATCH_REQUIRED_KEYS) {
      expect(result).toHaveProperty(key);
    }
  });

  test("throws on missing structured_context", async () => {
    await expect(generatePatch(null)).rejects.toThrow("structured_context");
    await expect(generatePatch({})).rejects.toThrow("event_id");
  });
});

describe("generatePatch — with Foundry endpoint", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    delete process.env.FOUNDRY_ENDPOINT;
    delete process.env.FOUNDRY_API_KEY;
  });

  test("calls Foundry API and maps response", async () => {
    process.env.FOUNDRY_ENDPOINT = "https://foundry.example.com";
    process.env.FOUNDRY_API_KEY = "test-key";

    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        diff: "--- a/pom.xml\n+++ b/pom.xml\n-old\n+new",
        files_modified: ["pom.xml"],
        lines_changed: 1,
        confidence: 0.9,
        model_id: "foundry-gpt4",
        reasoning_chain: "Fixed version bump",
      }),
    });

    const result = await generatePatch(MOCK_STRUCTURED_CONTEXT);

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(result.status).toBe("PATCH_GENERATED");
    expect(result.source).toBe("FOUNDRY");
    expect(result.llm_confidence).toBe(0.9);
    expect(result.model_id).toBe("foundry-gpt4");
  });

  test("returns CANNOT_PATCH on API error", async () => {
    process.env.FOUNDRY_ENDPOINT = "https://foundry.example.com";
    process.env.FOUNDRY_API_KEY = "test-key";

    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 500,
      text: async () => "Internal Server Error",
    });

    const result = await generatePatch(MOCK_STRUCTURED_CONTEXT);

    expect(result.status).toBe("CANNOT_PATCH");
    expect(result.cannot_patch_reason).toContain("500");
  });

  test("returns CANNOT_PATCH on network failure", async () => {
    process.env.FOUNDRY_ENDPOINT = "https://foundry.example.com";
    process.env.FOUNDRY_API_KEY = "test-key";

    global.fetch = jest.fn().mockRejectedValue(new Error("ECONNREFUSED"));

    const result = await generatePatch(MOCK_STRUCTURED_CONTEXT);

    expect(result.status).toBe("CANNOT_PATCH");
    expect(result.cannot_patch_reason).toContain("ECONNREFUSED");
  });
});

describe("buildMockPatch", () => {
  test("returns a valid candidate patch shape", () => {
    const result = buildMockPatch(MOCK_STRUCTURED_CONTEXT);

    expect(result.event_id).toBe(MOCK_STRUCTURED_CONTEXT.event_id);
    expect(result.status).toBe("PATCH_GENERATED");
    expect(result.diff).toContain("pom.xml");
    expect(result.llm_confidence).toBeGreaterThan(0);
  });
});

describe("buildCannotPatch", () => {
  test("returns CANNOT_PATCH with reason", () => {
    const result = buildCannotPatch("test-id", "No fix available");

    expect(result.status).toBe("CANNOT_PATCH");
    expect(result.cannot_patch_reason).toBe("No fix available");
    expect(result.diff).toBeNull();
    expect(result.llm_confidence).toBe(0);
  });
});

describe("mapFoundryResponse", () => {
  test("maps a complete Foundry response", () => {
    const foundryResp = {
      diff: "some diff",
      files_modified: ["file.js"],
      lines_changed: 5,
      confidence: 0.88,
      model_id: "foundry-model",
      touches_auth_crypto: true,
      reasoning_chain: "chain",
    };

    const result = mapFoundryResponse("evt-1", foundryResp);

    expect(result.event_id).toBe("evt-1");
    expect(result.status).toBe("PATCH_GENERATED");
    expect(result.touches_auth_crypto).toBe(true);
    expect(result.llm_confidence).toBe(0.88);
  });

  test("returns CANNOT_PATCH when no diff in response", () => {
    const result = mapFoundryResponse("evt-2", {});

    expect(result.status).toBe("CANNOT_PATCH");
    expect(result.diff).toBeNull();
  });
});
