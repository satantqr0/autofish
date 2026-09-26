import type { AIRuntimeSettings } from "@/lib/types";

export type AIRuntimeDraft = Omit<AIRuntimeSettings, "updated_at">;

export function runtimeDraft(runtime: AIRuntimeSettings): AIRuntimeDraft {
  return {
    enabled: runtime.enabled,
    primary_provider: runtime.primary_provider,
    fallback_provider: runtime.fallback_provider,
    monthly_budget_cny: runtime.monthly_budget_cny,
    temperature: runtime.temperature,
    max_output_tokens: runtime.max_output_tokens,
    request_timeout_seconds: runtime.request_timeout_seconds,
    task_routing: { ...runtime.task_routing },
  };
}

function sameRouting(
  left: AIRuntimeDraft["task_routing"],
  right: AIRuntimeDraft["task_routing"],
) {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key) => left[key] === right[key]);
}

export function sameRuntimeDraft(left: AIRuntimeDraft, right: AIRuntimeDraft) {
  return left.enabled === right.enabled
    && left.primary_provider === right.primary_provider
    && left.fallback_provider === right.fallback_provider
    && left.monthly_budget_cny === right.monthly_budget_cny
    && left.temperature === right.temperature
    && left.max_output_tokens === right.max_output_tokens
    && left.request_timeout_seconds === right.request_timeout_seconds
    && sameRouting(left.task_routing, right.task_routing);
}
