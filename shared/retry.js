"use strict";

/**
 * retry.js — Shared retry utility with exponential backoff.
 *
 * Wraps any async function with automatic retry on transient failures.
 * Retries on HTTP 429, 503, 504 by default.
 */

/** @type {number[]} Default HTTP status codes to retry on */
const DEFAULT_RETRY_CODES = [429, 503, 504];

/**
 * Determine if an error is retryable based on HTTP status code.
 * @param {Error} err
 * @param {number[]} retryCodes
 * @returns {boolean}
 */
function isRetryable(err, retryCodes) {
  const status =
    err.statusCode || err.status || err.code || err.response?.status;
  if (typeof status === "number" && retryCodes.includes(status)) return true;

  // Azure SDK errors often have a `code` string
  const message = (err.message || "").toLowerCase();
  if (message.includes("too many requests")) return true;
  if (message.includes("service unavailable")) return true;
  if (message.includes("gateway timeout")) return true;
  if (message.includes("econnreset") || message.includes("etimedout")) return true;

  return false;
}

/**
 * Execute an async function with exponential backoff retry.
 *
 * @param {() => Promise<*>} fn - Async function to execute
 * @param {object} [options]
 * @param {number} [options.maxAttempts=3] - Maximum number of attempts
 * @param {number} [options.baseDelayMs=1000] - Base delay in ms (doubled each retry)
 * @param {number} [options.maxDelayMs=30000] - Maximum delay cap in ms
 * @param {number[]} [options.retryOn=[429,503,504]] - HTTP status codes to retry
 * @param {string} [options.label="operation"] - Label for log messages
 * @returns {Promise<*>} Result of fn()
 */
async function withRetry(fn, options = {}) {
  const {
    maxAttempts = 3,
    baseDelayMs = 1000,
    maxDelayMs = 30000,
    retryOn = DEFAULT_RETRY_CODES,
    label = "operation",
  } = options;

  let lastError;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;

      if (attempt >= maxAttempts || !isRetryable(err, retryOn)) {
        throw err;
      }

      // Exponential backoff with jitter
      const delay = Math.min(
        baseDelayMs * Math.pow(2, attempt - 1) + Math.random() * 100,
        maxDelayMs
      );

      console.log(
        JSON.stringify({
          message: `Retry ${attempt}/${maxAttempts} for ${label}`,
          delay: Math.round(delay),
          error: err.message,
        })
      );

      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }

  throw lastError;
}

module.exports = { withRetry, isRetryable, DEFAULT_RETRY_CODES };
