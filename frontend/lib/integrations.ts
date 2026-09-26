import { apiFetch } from "@/lib/api";
import type { IntegrationStatus } from "@/lib/types";

const CACHE_TTL_MS = 15_000;

let cached: { expiresAt: number; value: IntegrationStatus } | null = null;
let inFlight: Promise<IntegrationStatus> | null = null;

export function fetchIntegrationStatus(force = false): Promise<IntegrationStatus> {
  const now = Date.now();
  if (!force && cached && cached.expiresAt > now) return Promise.resolve(cached.value);
  if (!force && inFlight) return inFlight;

  const request = apiFetch<IntegrationStatus>("/api/v1/integrations/status")
    .then((value) => {
      cached = { value, expiresAt: Date.now() + CACHE_TTL_MS };
      return value;
    })
    .finally(() => {
      if (inFlight === request) inFlight = null;
    });
  inFlight = request;
  return request;
}
