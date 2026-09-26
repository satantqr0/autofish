import type { BrowserBridgeAgent, BrowserBridgeOverview } from "@/lib/types";

export const MINIMUM_BROWSER_BRIDGE_VERSION = "0.5.0";

export type BrowserBridgeOperation = BrowserBridgeAgent["capabilities"][number];

type BrowserBridgeSafety = BrowserBridgeOverview["safety"];

type ParsedSemver = {
  core: [number, number, number];
  prerelease: string[];
};

function parseSemver(value: string): ParsedSemver | null {
  const match = value.trim().match(
    /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/,
  );
  if (!match) return null;

  const core = match.slice(1, 4).map(Number) as [number, number, number];
  if (core.some((part) => !Number.isSafeInteger(part))) return null;

  return {
    core,
    prerelease: match[4]?.split(".") ?? [],
  };
}

function comparePrerelease(left: string[], right: string[]) {
  if (left.length === 0 && right.length === 0) return 0;
  if (left.length === 0) return 1;
  if (right.length === 0) return -1;

  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const leftPart = left[index];
    const rightPart = right[index];
    if (leftPart === undefined) return -1;
    if (rightPart === undefined) return 1;
    if (leftPart === rightPart) continue;

    const leftNumeric = /^\d+$/.test(leftPart);
    const rightNumeric = /^\d+$/.test(rightPart);
    if (leftNumeric && rightNumeric) return Number(leftPart) > Number(rightPart) ? 1 : -1;
    if (leftNumeric !== rightNumeric) return leftNumeric ? -1 : 1;
    return leftPart > rightPart ? 1 : -1;
  }
  return 0;
}

export function isBridgeVersionCompatible(
  current: string | null | undefined,
  minimum = MINIMUM_BROWSER_BRIDGE_VERSION,
) {
  if (!current) return false;
  const currentVersion = parseSemver(current);
  const minimumVersion = parseSemver(minimum);
  if (!currentVersion || !minimumVersion) return false;

  for (let index = 0; index < currentVersion.core.length; index += 1) {
    if (currentVersion.core[index] === minimumVersion.core[index]) continue;
    return currentVersion.core[index] > minimumVersion.core[index];
  }
  return comparePrerelease(currentVersion.prerelease, minimumVersion.prerelease) >= 0;
}

export function supportsBridgeOperation(
  agent: BrowserBridgeAgent | null | undefined,
  operation: BrowserBridgeOperation,
  minimum = MINIMUM_BROWSER_BRIDGE_VERSION,
) {
  return Boolean(
    agent
    && agent.status === "ONLINE"
    && isBridgeVersionCompatible(agent.version, minimum)
    && agent.capabilities.includes(operation),
  );
}

export function isManualPublishReady(
  agent: BrowserBridgeAgent | null | undefined,
  safety: BrowserBridgeSafety | null | undefined,
) {
  return Boolean(
    safety?.publish_submission_enabled
    && supportsBridgeOperation(agent, "PUBLISH_PRODUCT"),
  );
}

export function isCustomerServiceSubmissionReady(
  agent: BrowserBridgeAgent | null | undefined,
  safety: BrowserBridgeSafety | null | undefined,
) {
  return Boolean(
    safety?.customer_service_submission_enabled
    && supportsBridgeOperation(agent, "SEND_REPLY"),
  );
}
