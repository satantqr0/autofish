import assert from "node:assert/strict";
import test from "node:test";

import {
  isPlatformActionPreviewCurrent,
  platformActionFingerprint,
} from "./platform-action-preview.ts";

const form = {
  actionType: "UPDATE_PRICE",
  price: "89.00",
  targetId: "42",
};

const action = {
  id: 17,
  action_type: "UPDATE_PRICE",
  target_id: 42,
  status: "PREVIEWED",
  preview: { executable: true },
};

const boundPreview = {
  action,
  fingerprint: platformActionFingerprint(form),
};

test("accepts only the preview bound to the current form", () => {
  assert.equal(isPlatformActionPreviewCurrent(boundPreview, form), true);
});

test("invalidates the preview when action, target, or price changes", () => {
  assert.equal(isPlatformActionPreviewCurrent(boundPreview, { ...form, actionType: "REMOVE_LISTING" }), false);
  assert.equal(isPlatformActionPreviewCurrent(boundPreview, { ...form, targetId: "43" }), false);
  assert.equal(isPlatformActionPreviewCurrent(boundPreview, { ...form, price: "88.00" }), false);
});

test("rejects blocked and already executed actions", () => {
  assert.equal(isPlatformActionPreviewCurrent({ ...boundPreview, action: { ...action, status: "BLOCKED" } }, form), false);
  assert.equal(isPlatformActionPreviewCurrent({ ...boundPreview, action: { ...action, status: "SUCCEEDED" } }, form), false);
  assert.equal(isPlatformActionPreviewCurrent({ ...boundPreview, action: { ...action, preview: { executable: false } } }, form), false);
});
