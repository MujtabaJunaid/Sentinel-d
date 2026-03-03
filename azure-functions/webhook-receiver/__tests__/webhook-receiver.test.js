const path = require("path");
const fs = require("fs");
const Ajv = require("ajv");
const addFormats = require("ajv-formats");

// Load schema directly for test isolation
const schemaPath = path.resolve(
  __dirname,
  "../../../shared/schemas/webhook_payload.json"
);
const webhookSchema = JSON.parse(fs.readFileSync(schemaPath, "utf-8"));
const ajv = new Ajv({ allErrors: true });
addFormats(ajv);
const validate = ajv.compile(webhookSchema);

// Mock Service Bus before requiring the handler
jest.mock("@azure/service-bus", () => ({
  ServiceBusClient: jest.fn().mockImplementation(() => ({
    createSender: () => ({
      sendMessages: jest.fn().mockResolvedValue(undefined),
      close: jest.fn().mockResolvedValue(undefined),
    }),
    close: jest.fn().mockResolvedValue(undefined),
  })),
}));
jest.mock("@azure/identity", () => ({
  DefaultAzureCredential: jest.fn(),
}));

process.env.SERVICE_BUS_NAMESPACE = "test-namespace";

const { handler } = require("../src/functions/webhook-receiver");

// Valid test payload
const validPayload = {
  event_id: "550e8400-e29b-41d4-a716-446655440000",
  cve_id: "CVE-2024-1234",
  severity: "HIGH",
  affected_package: "lodash",
  current_version: "4.17.20",
  fix_version_range: ">=4.17.21",
  file_path: "src/utils/helpers.js",
  line_range: [42, 55],
  repo: "contoso/webapp",
  timestamp: "2026-03-01T12:00:00Z",
};

// Helper to build a mock Azure Functions request
function mockRequest({ headers = {}, body = null } = {}) {
  const headerMap = new Map(Object.entries(headers));
  return {
    headers: { get: (key) => headerMap.get(key) || null },
    json: body instanceof Error
      ? () => Promise.reject(body)
      : () => Promise.resolve(body),
  };
}

// Mock invocation context
const mockContext = { log: jest.fn(), error: jest.fn() };

describe("webhook_payload.json schema validation", () => {
  test("accepts a valid payload", () => {
    const result = validate(validPayload);
    expect(result).toBe(true);
    expect(validate.errors).toBeNull();
  });

  test("rejects payload missing required field (event_id)", () => {
    const { event_id, ...incomplete } = validPayload;
    const result = validate(incomplete);
    expect(result).toBe(false);
    expect(validate.errors.some((e) => e.params.missingProperty === "event_id")).toBe(true);
  });

  test("rejects invalid severity enum", () => {
    const bad = { ...validPayload, severity: "EXTREME" };
    const result = validate(bad);
    expect(result).toBe(false);
  });

  test("rejects line_range with wrong item count", () => {
    const bad = { ...validPayload, line_range: [1] };
    const result = validate(bad);
    expect(result).toBe(false);
  });

  test("rejects line_range with non-integer items", () => {
    const bad = { ...validPayload, line_range: ["a", "b"] };
    const result = validate(bad);
    expect(result).toBe(false);
  });

  test("rejects additional properties", () => {
    const bad = { ...validPayload, extra_field: "nope" };
    const result = validate(bad);
    expect(result).toBe(false);
  });

  test("rejects invalid timestamp format", () => {
    const bad = { ...validPayload, timestamp: "not-a-date" };
    const result = validate(bad);
    expect(result).toBe(false);
  });

  test("rejects empty object", () => {
    const result = validate({});
    expect(result).toBe(false);
    expect(validate.errors.length).toBeGreaterThan(0);
  });
});

describe("webhook-receiver HTTP handler", () => {
  beforeEach(() => jest.clearAllMocks());

  test("returns 202 for a valid payload", async () => {
    const req = mockRequest({
      headers: { "content-type": "application/json" },
      body: validPayload,
    });
    const res = await handler(req, mockContext);
    expect(res.status).toBe(202);
    expect(res.jsonBody.status).toBe("accepted");
    expect(res.jsonBody.event_id).toBe(validPayload.event_id);
  });

  test("returns 400 for invalid Content-Type", async () => {
    const req = mockRequest({
      headers: { "content-type": "text/plain" },
      body: validPayload,
    });
    const res = await handler(req, mockContext);
    expect(res.status).toBe(400);
    expect(res.jsonBody.error).toBe("Invalid Content-Type");
  });

  test("returns 400 for invalid JSON body", async () => {
    const req = mockRequest({
      headers: { "content-type": "application/json" },
      body: new Error("parse error"),
    });
    const res = await handler(req, mockContext);
    expect(res.status).toBe(400);
    expect(res.jsonBody.error).toBe("Invalid JSON");
  });

  test("returns 400 when schema validation fails", async () => {
    const req = mockRequest({
      headers: { "content-type": "application/json" },
      body: { event_id: "not-a-uuid", severity: "EXTREME" },
    });
    const res = await handler(req, mockContext);
    expect(res.status).toBe(400);
    expect(res.jsonBody.error).toBe("Schema validation failed");
    expect(Array.isArray(res.jsonBody.detail)).toBe(true);
  });

  test("returns 400 with missing Content-Type header", async () => {
    const req = mockRequest({ headers: {}, body: validPayload });
    const res = await handler(req, mockContext);
    expect(res.status).toBe(400);
    expect(res.jsonBody.error).toBe("Invalid Content-Type");
  });
});
