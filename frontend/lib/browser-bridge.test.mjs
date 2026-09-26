import assert from "node:assert/strict";
import test from "node:test";

import {
  isBridgeVersionCompatible,
  isCustomerServiceSubmissionReady,
  isManualPublishReady,
  supportsBridgeOperation,
} from "./browser-bridge.ts";

test("accepts compatible stable browser bridge versions", () => {
  assert.equal(isBridgeVersionCompatible("0.5.0"), true);
  assert.equal(isBridgeVersionCompatible("0.5.1"), true);
  assert.equal(isBridgeVersionCompatible("1.0.0"), true);
});

test("rejects old, prerelease, and malformed browser bridge versions", () => {
  assert.equal(isBridgeVersionCompatible("0.4.9"), false);
  assert.equal(isBridgeVersionCompatible("0.5.0-beta.1"), false);
  assert.equal(isBridgeVersionCompatible("unknown"), false);
});

test("requires online status, compatible version, and the requested capability", () => {
  const agent = {
    bridge_id: "local-bridge",
    name: "Local Bridge",
    version: "0.5.1",
    status: "ONLINE",
    capabilities: ["PREFILL_PRODUCT"],
    current_url: null,
    last_error: null,
    last_seen_at: "2026-08-26T00:00:00Z",
  };

  assert.equal(supportsBridgeOperation(agent, "PREFILL_PRODUCT"), true);
  assert.equal(supportsBridgeOperation(agent, "PUBLISH_PRODUCT"), false);
  assert.equal(supportsBridgeOperation({ ...agent, status: "OFFLINE" }, "PREFILL_PRODUCT"), false);
  assert.equal(supportsBridgeOperation({ ...agent, version: "0.4.9" }, "PREFILL_PRODUCT"), false);
});

test("customer-service permission never enables product publishing", () => {
  const agent = {
    bridge_id: "local-bridge",
    name: "Local Bridge",
    version: "0.5.1",
    status: "ONLINE",
    capabilities: ["PUBLISH_PRODUCT", "SEND_REPLY"],
    current_url: null,
    last_error: null,
    last_seen_at: "2026-08-26T00:00:00Z",
  };
  const customerServiceOnly = {
    publish_submission_enabled: false,
    customer_service_submission_enabled: true,
  };

  assert.equal(isManualPublishReady(agent, customerServiceOnly), false);
  assert.equal(isCustomerServiceSubmissionReady(agent, customerServiceOnly), true);
  assert.equal(isManualPublishReady(agent, {
    ...customerServiceOnly,
    publish_submission_enabled: true,
  }), true);
});
