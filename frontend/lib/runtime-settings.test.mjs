import assert from "node:assert/strict";
import test from "node:test";

import { runtimeDraft, sameRuntimeDraft } from "./runtime-settings.ts";

const runtime = {
  enabled: false,
  primary_provider: "qwen",
  fallback_provider: "deepseek",
  monthly_budget_cny: "100.00",
  temperature: "0.200",
  max_output_tokens: 1200,
  request_timeout_seconds: 30,
  task_routing: {
    customer_service: "qwen",
    risk_summary: "human",
  },
  updated_at: "2026-08-26T00:00:00Z",
};

test("creates an independent editable draft without server metadata", () => {
  const draft = runtimeDraft(runtime);
  assert.equal("updated_at" in draft, false);
  assert.notEqual(draft.task_routing, runtime.task_routing);
  assert.equal(sameRuntimeDraft(draft, runtimeDraft(runtime)), true);
});

test("detects unsaved switches, budgets, timeouts, and routes", () => {
  const draft = runtimeDraft(runtime);
  assert.equal(sameRuntimeDraft(draft, { ...draft, enabled: true }), false);
  assert.equal(sameRuntimeDraft(draft, { ...draft, monthly_budget_cny: "200.00" }), false);
  assert.equal(sameRuntimeDraft(draft, { ...draft, request_timeout_seconds: 60 }), false);
  assert.equal(sameRuntimeDraft(draft, {
    ...draft,
    task_routing: { ...draft.task_routing, customer_service: "deepseek" },
  }), false);
});

test("compares task routing independently of object key order", () => {
  const draft = runtimeDraft(runtime);
  assert.equal(sameRuntimeDraft(draft, {
    ...draft,
    task_routing: {
      risk_summary: "human",
      customer_service: "qwen",
    },
  }), true);
});
