import type {
  DataOperationsStatusResponse,
  SyncTriggerResponse,
  EnableSymbolResponse,
} from "./types";

let cachedCsrfToken: string | null = null;

export async function getCsrfToken(): Promise<string> {
  if (cachedCsrfToken) {
    return cachedCsrfToken;
  }
  const res = await fetch("/api/v2/data-operations/csrf-token", {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    // fallback
    const fallbackRes = await fetch("/api/v2/research/csrf-token", {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (!fallbackRes.ok) {
      throw new Error(`Failed to fetch CSRF token: ${res.status}`);
    }
    const fallbackData = await fallbackRes.json();
    cachedCsrfToken = fallbackData.csrf_token;
    return cachedCsrfToken!;
  }
  const data = await res.json();
  cachedCsrfToken = data.csrf_token;
  return cachedCsrfToken!;
}

export async function getDataOperationsStatus(): Promise<DataOperationsStatusResponse> {
  const res = await fetch("/api/v2/data-operations/status", {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch data operations status: ${res.status}`);
  }
  return res.json();
}

export async function getOperationDetails(operationId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`/api/v2/data-operations/operations/${encodeURIComponent(operationId)}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch operation details: ${res.status}`);
  }
  return res.json();
}

export async function triggerSync(
  targetSymbols?: string[],
  deadlineSeconds?: number
): Promise<SyncTriggerResponse> {
  const token = await getCsrfToken();
  const effectiveDeadline = Math.min(deadlineSeconds || 90.0, 90.0);
  const res = await fetch("/api/v2/data-operations/sync", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": token,
    },
    body: JSON.stringify({
      target_symbols: targetSymbols || null,
      deadline_seconds: effectiveDeadline,
    }),
  });
  if (res.status === 403) {
    cachedCsrfToken = null;
    const retryToken = await getCsrfToken();
    const retryRes = await fetch("/api/v2/data-operations/sync", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": retryToken,
      },
      body: JSON.stringify({
        target_symbols: targetSymbols || null,
        deadline_seconds: effectiveDeadline,
      }),
    });
    if (!retryRes.ok) {
      throw new Error(`Sync failed: ${retryRes.status}`);
    }
    return retryRes.json();
  }
  if (!res.ok) {
    throw new Error(`Sync failed: ${res.status}`);
  }
  return res.json();
}

export async function enableSymbol(symbol: string): Promise<EnableSymbolResponse> {
  const token = await getCsrfToken();
  const clean = symbol.trim().toUpperCase();
  const res = await fetch(`/api/v2/data-operations/symbols/${encodeURIComponent(clean)}/enable`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": token,
    },
    body: JSON.stringify({}),
  });
  if (res.status === 403) {
    cachedCsrfToken = null;
    const retryToken = await getCsrfToken();
    const retryRes = await fetch(`/api/v2/data-operations/symbols/${encodeURIComponent(clean)}/enable`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": retryToken,
      },
      body: JSON.stringify({}),
    });
    if (!retryRes.ok) {
      throw new Error(`Enable symbol failed: ${retryRes.status}`);
    }
    return retryRes.json();
  }
  if (!res.ok) {
    throw new Error(`Enable symbol failed: ${res.status}`);
  }
  return res.json();
}

export async function cancelOperation(): Promise<{ operation_id: string; status: string }> {
  const token = await getCsrfToken();
  const res = await fetch("/api/v2/data-operations/cancel", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": token,
    },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    throw new Error(`Cancel failed: ${res.status}`);
  }
  return res.json();
}
