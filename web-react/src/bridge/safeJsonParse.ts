/**
 * Parse a bridge response as JSON, returning `fallback` instead of throwing
 * when the response is empty/missing or malformed. QWebChannel calls can
 * come back empty (e.g. a slot invocation that didn't match any registered
 * overload resolves with no result) — JSON.parse("") throws "Unexpected end
 * of JSON input", which otherwise propagates as an unhandled rejection out
 * of every bridge call site.
 */
export function safeJsonParse<T>(json: string | null | undefined, fallback: T): T {
  if (!json) return fallback;
  try {
    return JSON.parse(json) as T;
  } catch (e) {
    console.error('[bridge] Failed to parse bridge response as JSON:', e, json);
    return fallback;
  }
}
