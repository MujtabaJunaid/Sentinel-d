"use strict";

const path = require("path");
const { withRetry, isRetryable, DEFAULT_RETRY_CODES } = require(
  path.resolve(__dirname, "../retry")
);

describe("isRetryable()", () => {
  test("returns true for 429 status code", () => {
    const err = new Error("Too many requests");
    err.statusCode = 429;
    expect(isRetryable(err, DEFAULT_RETRY_CODES)).toBe(true);
  });

  test("returns true for 503 status code", () => {
    const err = new Error("Service unavailable");
    err.statusCode = 503;
    expect(isRetryable(err, DEFAULT_RETRY_CODES)).toBe(true);
  });

  test("returns true for 504 status code", () => {
    const err = new Error("Gateway timeout");
    err.statusCode = 504;
    expect(isRetryable(err, DEFAULT_RETRY_CODES)).toBe(true);
  });

  test("returns true for message containing 'too many requests'", () => {
    const err = new Error("Server returned Too Many Requests");
    expect(isRetryable(err, DEFAULT_RETRY_CODES)).toBe(true);
  });

  test("returns true for ECONNRESET", () => {
    const err = new Error("read ECONNRESET");
    expect(isRetryable(err, DEFAULT_RETRY_CODES)).toBe(true);
  });

  test("returns false for 400 bad request", () => {
    const err = new Error("Bad request");
    err.statusCode = 400;
    expect(isRetryable(err, DEFAULT_RETRY_CODES)).toBe(false);
  });

  test("returns false for 401 unauthorized", () => {
    const err = new Error("Unauthorized");
    err.statusCode = 401;
    expect(isRetryable(err, DEFAULT_RETRY_CODES)).toBe(false);
  });

  test("returns false for generic error without status", () => {
    const err = new Error("Something broke");
    expect(isRetryable(err, DEFAULT_RETRY_CODES)).toBe(false);
  });
});

describe("withRetry()", () => {
  test("returns result on first success", async () => {
    const fn = jest.fn().mockResolvedValue("ok");
    const result = await withRetry(fn, { label: "test" });
    expect(result).toBe("ok");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  test("retries on retryable error then succeeds", async () => {
    const err = new Error("Service unavailable");
    err.statusCode = 503;

    const fn = jest
      .fn()
      .mockRejectedValueOnce(err)
      .mockResolvedValue("recovered");

    const result = await withRetry(fn, {
      baseDelayMs: 10,
      label: "test-retry",
    });
    expect(result).toBe("recovered");
    expect(fn).toHaveBeenCalledTimes(2);
  });

  test("throws after maxAttempts exhausted", async () => {
    const err = new Error("Service unavailable");
    err.statusCode = 503;

    const fn = jest.fn().mockRejectedValue(err);

    await expect(
      withRetry(fn, { maxAttempts: 3, baseDelayMs: 10, label: "test-max" })
    ).rejects.toThrow("Service unavailable");
    expect(fn).toHaveBeenCalledTimes(3);
  });

  test("does not retry on non-retryable error", async () => {
    const err = new Error("Bad request");
    err.statusCode = 400;

    const fn = jest.fn().mockRejectedValue(err);

    await expect(
      withRetry(fn, { maxAttempts: 3, baseDelayMs: 10, label: "test-no-retry" })
    ).rejects.toThrow("Bad request");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  test("respects custom retryOn codes", async () => {
    const err = new Error("Conflict");
    err.statusCode = 409;

    const fn = jest
      .fn()
      .mockRejectedValueOnce(err)
      .mockResolvedValue("ok");

    const result = await withRetry(fn, {
      retryOn: [409],
      baseDelayMs: 10,
      label: "test-custom-codes",
    });
    expect(result).toBe("ok");
    expect(fn).toHaveBeenCalledTimes(2);
  });

  test("delay increases exponentially", async () => {
    const err = new Error("Service unavailable");
    err.statusCode = 503;

    const delays = [];
    const originalSetTimeout = global.setTimeout;
    jest.spyOn(global, "setTimeout").mockImplementation((fn, delay) => {
      delays.push(delay);
      return originalSetTimeout(fn, 1);
    });

    const failFn = jest
      .fn()
      .mockRejectedValueOnce(err)
      .mockRejectedValueOnce(err)
      .mockResolvedValue("ok");

    await withRetry(failFn, {
      maxAttempts: 3,
      baseDelayMs: 100,
      label: "test-backoff",
    });

    expect(delays[0]).toBeGreaterThanOrEqual(100);
    expect(delays[0]).toBeLessThan(300);
    expect(delays[1]).toBeGreaterThanOrEqual(200);
    expect(delays[1]).toBeLessThan(400);

    global.setTimeout.mockRestore();
  });

  test("delay is capped at maxDelayMs", async () => {
    const err = new Error("Service unavailable");
    err.statusCode = 503;

    const delays = [];
    const originalSetTimeout = global.setTimeout;
    jest.spyOn(global, "setTimeout").mockImplementation((fn, delay) => {
      delays.push(delay);
      return originalSetTimeout(fn, 1);
    });

    const failFn = jest
      .fn()
      .mockRejectedValueOnce(err)
      .mockResolvedValue("ok");

    await withRetry(failFn, {
      maxAttempts: 2,
      baseDelayMs: 50000,
      maxDelayMs: 100,
      label: "test-cap",
    });

    expect(delays[0]).toBeLessThanOrEqual(200);

    global.setTimeout.mockRestore();
  });

  test("retries on 429 Too Many Requests", async () => {
    const err = new Error("Rate limited");
    err.statusCode = 429;

    const fn = jest
      .fn()
      .mockRejectedValueOnce(err)
      .mockResolvedValue("success");

    const result = await withRetry(fn, { baseDelayMs: 10, label: "test-429" });
    expect(result).toBe("success");
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
