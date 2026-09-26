import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import {
  apiFetch,
  ApiError,
  DEFAULT_API_TIMEOUT_MS,
} from "./api.ts";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("uses a finite default timeout and parses successful JSON", async () => {
  assert.equal(DEFAULT_API_TIMEOUT_MS, 30_000);
  globalThis.fetch = async (_path, init) => {
    assert.equal(init.signal.aborted, false);
    return new Response(JSON.stringify({ ok: true }), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    });
  };

  assert.deepEqual(await apiFetch("/ok"), { ok: true });
});

test("aborts a stalled request at the configured timeout", async () => {
  globalThis.fetch = async (_path, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
  });

  await assert.rejects(
    apiFetch("/slow", { timeoutMs: 5 }),
    (error) => error instanceof ApiError && error.status === 408 && error.message.includes("请求超时"),
  );
});

test("forwards caller cancellation without misreporting it as a timeout", async () => {
  const caller = new AbortController();
  const reason = new DOMException("Caller cancelled", "AbortError");
  globalThis.fetch = async (_path, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
  });

  const request = apiFetch("/cancelled", { signal: caller.signal, timeoutMs: 1_000 });
  caller.abort(reason);
  await assert.rejects(request, (error) => error === reason);
});
