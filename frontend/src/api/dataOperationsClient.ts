import type {
  DataOperationsStatusResponse,
  SyncTriggerResponse,
  EnableSymbolResponse,
} from "./types";
import { getLocalCsrfToken, invalidateLocalCsrfToken, isCsrfRejection } from "./localCsrfSession";

export async function getCsrfToken(signal?: AbortSignal): Promise<string> {
  return getLocalCsrfToken("/api/v2/data-operations/csrf-token", signal, "/api/v2/research/csrf-token");
}

export async function postDataOperation<T>(path: string, payload: unknown, errorPrefix: string, signal?: AbortSignal): Promise<T> {
  const body = JSON.stringify(payload);
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = await getCsrfToken(signal);
    signal?.throwIfAborted();
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body, signal,
    });
    if (response.ok) return await response.json() as T;
    const error = await response.json().catch(() => ({})) as { detail?: unknown };
    if (isCsrfRejection(response.status, error.detail)) {
      invalidateLocalCsrfToken(token);
      // These exact middleware errors precede execution. Never replay a network
      // failure, an ambiguous write response, or an unrelated authorization error.
      if (attempt === 0) continue;
      throw new Error("操作驗證仍未通過，本次操作未執行。請稍後再試或重新整理頁面。");
    }
    throw new Error(typeof error.detail === "string" ? error.detail : `${errorPrefix}:${response.status}`);
  }
  throw new Error("操作驗證未完成，請稍後再試。");
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

export async function getOperationDetails(operationId: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
  const res = await fetch(`/api/v2/data-operations/operations/${encodeURIComponent(operationId)}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch operation details: ${res.status}`);
  }
  return res.json();
}

export async function triggerSync(
  targetSymbols?: string[],
  deadlineSeconds?: number,
  signal?: AbortSignal
): Promise<SyncTriggerResponse> {
  const effectiveDeadline = Math.min(deadlineSeconds || 90.0, 90.0);
  return postDataOperation("/api/v2/data-operations/sync", {
    target_symbols: targetSymbols || null, deadline_seconds: effectiveDeadline,
  }, "Sync failed", signal);
}

export async function enableSymbol(symbol: string): Promise<EnableSymbolResponse> {
  const clean = symbol.trim().toUpperCase();
  return postDataOperation(`/api/v2/data-operations/symbols/${encodeURIComponent(clean)}/enable`, {}, "Enable symbol failed");
}

export async function cancelOperation(expectedOperationId?: string): Promise<{ operation_id: string; status: string }> {
  const token = await getCsrfToken();
  const response = await fetch("/api/v2/data-operations/cancel", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: JSON.stringify(expectedOperationId ? { expected_operation_id: expectedOperationId } : {}),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({})) as { detail?: unknown };
    if (isCsrfRejection(response.status, error.detail)) {
      invalidateLocalCsrfToken(token);
      // Do not automatically repeat cancellation against a possibly changed active job.
      throw new Error("操作驗證已失效，本次取消未執行。請確認作業狀態後再試。");
    }
    throw new Error(`Cancel failed: ${response.status}`);
  }
  return response.json();
}
