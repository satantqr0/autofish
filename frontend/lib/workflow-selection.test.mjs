import assert from "node:assert/strict";
import test from "node:test";

import { resolveFilteredActiveId } from "./workflow-selection.ts";

const filtered = [{ id: "after-sales" }, { id: "refund" }];

test("keeps an active workflow only while it remains visible", () => {
  assert.equal(resolveFilteredActiveId(filtered, "refund"), "refund");
});

test("selects the first filtered workflow when the old selection is hidden", () => {
  assert.equal(resolveFilteredActiveId(filtered, "sourcing"), "after-sales");
});

test("uses an empty sentinel when no workflow matches", () => {
  assert.equal(resolveFilteredActiveId([], "sourcing"), "");
});
