export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const DEFAULT_API_TIMEOUT_MS = 30_000;

export type ApiFetchInit = RequestInit & {
  timeoutMs?: number;
};

export async function apiFetch<T>(path: string, init?: ApiFetchInit): Promise<T> {
  const {
    signal: callerSignal,
    timeoutMs: requestedTimeout = DEFAULT_API_TIMEOUT_MS,
    ...requestInit
  } = init ?? {};
  const timeoutMs = Number.isFinite(requestedTimeout) && requestedTimeout > 0
    ? requestedTimeout
    : DEFAULT_API_TIMEOUT_MS;
  const controller = new AbortController();
  let timedOut = false;

  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) {
    abortFromCaller();
  } else {
    callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }

  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException("Request timed out", "TimeoutError"));
  }, timeoutMs);

  try {
    const response = await fetch(path, {
      ...requestInit,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...requestInit.headers,
      },
      signal: controller.signal,
    });
    if (response.status === 401 && typeof window !== "undefined") {
      // apiFetch is framework-independent and cannot use Next's useRouter hook.
      // A full-document replace reliably clears protected UI state and avoids a back-button redirect loop.
      window.location.replace("/login?expired=1");
      throw new ApiError(401, "登录已失效");
    }
    if (!response.ok) {
      let message = `请求失败 (${response.status})`;
      try {
        const body = (await response.json()) as {
          detail?: string | { message?: string; status?: string; error_code?: string };
        };
        if (typeof body.detail === "string") {
          message = body.detail;
        } else if (body.detail?.message) {
          message = `${body.detail.message}${body.detail.status ? ` · ${body.detail.status}` : ""}`;
        }
      } catch {
        // Keep the status-based fallback when the response is not JSON.
      }
      throw new ApiError(response.status, message);
    }
    return (await response.json()) as T;
  } catch (cause) {
    if (timedOut) {
      throw new ApiError(408, `请求超时（${Math.ceil(timeoutMs / 1000)} 秒），请检查网络或稍后重试`);
    }
    throw cause;
  } finally {
    clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }
}
