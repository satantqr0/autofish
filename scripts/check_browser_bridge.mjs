#!/usr/bin/env node

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const [manifestText, background, content] = await Promise.all([
  readFile(resolve(root, "browser-bridge/manifest.json"), "utf8"),
  readFile(resolve(root, "browser-bridge/background.js"), "utf8"),
  readFile(resolve(root, "browser-bridge/content.js"), "utf8"),
]);
const manifest = JSON.parse(manifestText);
const declaredVersion = background.match(/const BRIDGE_VERSION = "([^"]+)";/)?.[1];

assert.equal(manifest.manifest_version, 3);
assert.equal(manifest.version, declaredVersion, "manifest 与心跳版本必须一致");
assert.ok(manifest.permissions.includes("debugger"), "可信点击需要 debugger 权限");
assert.match(background, /PREPARE_TRUSTED_COMMIT/);
assert.match(background, /OBSERVE_PUBLISH_RESULT/);
assert.match(content, /READY_FOR_TRUSTED_CLICK/);
assert.match(content, /categorySearchTerms/);
assert.match(content, /selectedCategoryMatches/);
assert.match(content, /ant-select-selection-item/);
assert.match(content, /findLabeledField/);
assert.match(content, /else if \(payload\.category\) missingFields\.push\("category"\)/);
assert.match(content, /metricPageFingerprint/);
assert.match(content, /maximizeMetricPageSize/);
assert.match(content, /target\.size <= currentSize/);
assert.match(content, /分页重复商品的指标不一致/);
assert.match(content, /metrics_duplicate_rows_skipped/);
assert.doesNotMatch(content, /\bbutton\.click\s*\(/, "禁止回退到非可信 DOM 发布点击");

const debuggerMethods = [...background.matchAll(/chrome\.debugger\.sendCommand\([^,]+,\s*"([^"]+)"/g)]
  .map((match) => match[1]);
assert.ok(debuggerMethods.length >= 3, "必须发送完整鼠标移动、按下和释放事件");
assert.deepEqual(
  [...new Set(debuggerMethods)],
  ["Input.dispatchMouseEvent"],
  "调试通道只允许 Input.dispatchMouseEvent",
);

console.log(`AutoFish Bridge ${manifest.version} 静态安全契约通过`);
