type CsrfEndpoint = "/api/v2/data-operations/csrf-token" | "/api/v2/research/csrf-token";

let cachedToken: string | null = null;
let pendingToken: Promise<string> | null = null;

export function isCsrfRejection(status: number, detail: unknown): boolean {
  return status === 403 && (detail === "csrf_session_invalid" || detail === "csrf_session_expired");
}

export function invalidateLocalCsrfToken(rejectedToken: string): void {
  // A late rejection of an older request must not discard an already renewed session.
  if (cachedToken === rejectedToken) cachedToken = null;
}

function waitForToken(pending: Promise<string>, signal?: AbortSignal): Promise<string> {
  if (!signal) return pending;
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(signal.reason);
    signal.addEventListener("abort", onAbort, { once: true });
    pending.then(token => {
      signal.removeEventListener("abort", onAbort);
      if (signal.aborted) reject(signal.reason);
      else resolve(token);
    }, error => {
      signal.removeEventListener("abort", onAbort);
      reject(error);
    });
  });
}

async function issueToken(endpoint: CsrfEndpoint, fallback?: CsrfEndpoint): Promise<string> {
  // Token issuance changes one shared HttpOnly cookie. Coalesce callers and do not
  // abort issuance when one stock page unmounts: other callers still need its result.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  const options: RequestInit = {
    method: "GET", headers: { Accept: "application/json" }, credentials: "same-origin",
    cache: "no-store", signal: controller.signal,
  };
  try {
    let response = await fetch(endpoint, options);
    if (!response.ok && fallback) {
      controller.signal.throwIfAborted();
      response = await fetch(fallback, options);
    }
    if (!response.ok) throw new Error(`無法取得操作驗證，尚未送出變更。請稍後再試；若仍失敗，請回報錯誤代碼 ${response.status}。`);
    const payload = await response.json() as { csrf_token?: unknown };
    controller.signal.throwIfAborted();
    if (typeof payload?.csrf_token !== "string" || !payload.csrf_token.trim()) {
      throw new Error("操作驗證回應不完整，尚未送出變更。請稍後再試。");
    }
    return payload.csrf_token;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("取得操作驗證逾時，尚未送出變更。請稍後再試。");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export function getLocalCsrfToken(endpoint: CsrfEndpoint, signal?: AbortSignal, fallback?: CsrfEndpoint): Promise<string> {
  signal?.throwIfAborted();
  if (cachedToken) return Promise.resolve(cachedToken);
  if (!pendingToken) {
    pendingToken = issueToken(endpoint, fallback).then(token => {
      cachedToken = token;
      return token;
    }).finally(() => { pendingToken = null; });
  }
  return waitForToken(pendingToken, signal);
}
