"use strict";

jest.mock("@octokit/rest");

const { processDeadLetters } = require("../src/functions/dead-letter-handler");
const { Octokit } = require("@octokit/rest");

function makeMessage(overrides = {}) {
  return {
    messageId: "msg-001",
    body: JSON.stringify({
      event_id: "evt-dlq-001",
      cve_id: "CVE-2024-5555",
      severity: "HIGH",
    }),
    deliveryCount: 1,
    deadLetterReason: "MaxDeliveryCountExceeded",
    deadLetterErrorDescription: "Processing failed",
    enqueuedTimeUtc: "2026-03-05T10:00:00Z",
    ...overrides,
  };
}

describe("processDeadLetters()", () => {
  test("processes messages and returns count", async () => {
    const result = await processDeadLetters(
      [makeMessage(), makeMessage({ messageId: "msg-002" })],
      { githubToken: null }
    );
    expect(result.processed).toBe(2);
    expect(result.issuesCreated).toBe(0);
  });

  test("does not create issue for deliveryCount <= 3", async () => {
    const result = await processDeadLetters(
      [makeMessage({ deliveryCount: 3 })],
      { githubToken: "test-token", githubOwner: "test", githubRepo: "repo" }
    );
    expect(result.issuesCreated).toBe(0);
  });

  test("creates GitHub Issue for deliveryCount > 3", async () => {
    const mockCreate = jest.fn().mockResolvedValue({ data: { number: 1 } });
    Octokit.mockImplementation(() => ({
      rest: { issues: { create: mockCreate } },
    }));

    const result = await processDeadLetters(
      [makeMessage({ deliveryCount: 5 })],
      { githubToken: "test-token", githubOwner: "test", githubRepo: "repo" }
    );

    expect(result.issuesCreated).toBe(1);
    expect(mockCreate).toHaveBeenCalledTimes(1);

    const issueBody = mockCreate.mock.calls[0][0].body;
    expect(issueBody).toContain("CVE-2024-5555");
    expect(issueBody).toContain("5 times");
    expect(mockCreate.mock.calls[0][0].labels).toContain("sentinel/infrastructure-failure");
  });

  test("handles non-JSON message body gracefully", async () => {
    const msg = makeMessage({ body: "plain text body", deliveryCount: 5 });
    const mockCreate = jest.fn().mockResolvedValue({ data: { number: 1 } });
    Octokit.mockImplementation(() => ({
      rest: { issues: { create: mockCreate } },
    }));

    const result = await processDeadLetters([msg], {
      githubToken: "test-token",
      githubOwner: "test",
      githubRepo: "repo",
    });

    expect(result.processed).toBe(1);
    expect(result.issuesCreated).toBe(1);
  });

  test("returns zero when given empty array", async () => {
    const result = await processDeadLetters([]);
    expect(result.processed).toBe(0);
    expect(result.issuesCreated).toBe(0);
  });

  test("continues processing if GitHub Issue creation fails", async () => {
    Octokit.mockImplementation(() => ({
      rest: {
        issues: {
          create: jest.fn().mockRejectedValue(new Error("API error")),
        },
      },
    }));

    const result = await processDeadLetters(
      [
        makeMessage({ deliveryCount: 5, messageId: "msg-a" }),
        makeMessage({ deliveryCount: 5, messageId: "msg-b" }),
      ],
      { githubToken: "test-token", githubOwner: "test", githubRepo: "repo" }
    );

    expect(result.processed).toBe(2);
    expect(result.issuesCreated).toBe(0);
  });
});
