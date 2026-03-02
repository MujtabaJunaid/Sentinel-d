/**
 * KQL query validator.
 * Ensures generated KQL only uses permitted tables and does not contain blocked operators.
 */

const PERMITTED_TABLES = ["traces", "requests", "exceptions", "dependencies"];
const BLOCKED_OPERATORS = [
  "externaldata",
  "http_request",
  "invoke",
  "evaluate",
  "plugins",
];

/**
 * Validate a KQL query string against the allowlist and blocklist.
 * @param {string} kqlString - The KQL query to validate
 * @returns {{ valid: boolean, reason?: string }}
 */
function validateKQL(kqlString) {
  if (!kqlString || typeof kqlString !== "string") {
    return { valid: false, reason: "KQL query is empty or not a string" };
  }

  const normalized = kqlString.toLowerCase();

  // Check for blocked operators
  for (const op of BLOCKED_OPERATORS) {
    // Match operator as a whole word to avoid false positives
    const regex = new RegExp(`\\b${op}\\b`, "i");
    if (regex.test(normalized)) {
      return { valid: false, reason: `Blocked operator detected: ${op}` };
    }
  }

  // Extract table references — KQL tables appear at the start of a line
  // or after a pipe join/union. We look for identifiers that precede a pipe
  // or newline and are used as table sources.
  const tablePattern = /(?:^|\|)\s*(?:union\s+)?([a-z_][a-z0-9_]*)\s*(?:\||$|\n|\r|\/\/)/gim;
  const foundTables = new Set();

  // Also check the very first word (the primary table)
  const firstTableMatch = normalized.match(/^\s*([a-z_][a-z0-9_]*)\s*/);
  if (firstTableMatch) {
    foundTables.add(firstTableMatch[1]);
  }

  // Check for union statements referencing additional tables (with or without parens)
  const unionPattern = /\bunion\s+(?:kind\s*=\s*\w+\s+)?\(?\s*([a-z_][a-z0-9_]*(?:\s*,\s*[a-z_][a-z0-9_]*)*)/gi;
  let unionMatch;
  while ((unionMatch = unionPattern.exec(kqlString)) !== null) {
    const tables = unionMatch[1].split(",").map((t) => t.trim().toLowerCase());
    tables.forEach((t) => foundTables.add(t));
  }

  // Check for join statements
  const joinPattern = /\bjoin\s+(?:kind\s*=\s*\w+\s+)?\(?\s*([a-z_][a-z0-9_]*)/gi;
  let joinMatch;
  while ((joinMatch = joinPattern.exec(kqlString)) !== null) {
    foundTables.add(joinMatch[1].toLowerCase());
  }

  // Validate all found tables are in the permitted list
  // Filter out KQL operators/keywords that might be mistaken for tables
  const kqlKeywords = new Set([
    "where", "summarize", "project", "extend", "order", "sort", "top",
    "take", "limit", "count", "distinct", "render", "let", "datatable",
    "print", "range", "search", "find", "parse", "mv-expand", "mvexpand",
    "by", "asc", "desc", "on", "kind", "ago", "now", "bin", "startofday",
    "contains", "has", "or", "and", "not", "in", "between", "true", "false",
  ]);

  for (const table of foundTables) {
    if (kqlKeywords.has(table)) continue;
    if (!PERMITTED_TABLES.includes(table)) {
      return {
        valid: false,
        reason: `Non-permitted table referenced: ${table}`,
      };
    }
  }

  return { valid: true };
}

module.exports = { validateKQL, PERMITTED_TABLES, BLOCKED_OPERATORS };
