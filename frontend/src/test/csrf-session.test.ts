import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Call = { path: string; method: string; body?: string; key: string | null };

// Model the backend's shared HttpOnly cookie: every token GET replaces it.
// Each POST must match the token belonging to that cookie, not merely any token.
function browserSession() {
  let session = 0;
  let currentToken = "";
  const calls: Call[] = [];
  const accepted: Call[] = [];
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    const headers = new Headers(init?.headers);
    const call = { path, method, body: init?.body as string | undefined, key: headers.get("Idempotency-Key") };
    calls.push(call);
    if (path.endsWith("/csrf-token")) {
      currentToken = `synthetic-token-${++session}`;
      return Response.json({ csrf_token: currentToken });
    }
    if (headers.get("X-CSRF-Token") !== currentToken) {
      return Response.json({ detail: "csrf_session_invalid" }, { status: 403 });
    }
    accepted.push(call);
    return Response.json({ status: "ready", canonical_symbol: "2408.TW", operation_id: null });
  });
  return { calls, accepted, fetchMock, rotate: () => { currentToken = "another-tab-token"; } };
}

describe("shared local research session", () => {
  beforeEach(() => { vi.restoreAllMocks(); vi.resetModules(); });
  afterEach(() => vi.useRealTimers());

  it("updates, saves, returns to the stock and switches stocks without replacing the shared session", async () => {
    const browser = browserSession();
    const { bootstrapSymbol } = await import("../api/phase20Client");
    const { researchMutation } = await import("../api/researchClient");
    await bootstrapSymbol("2408.TW");
    await researchMutation("/api/v2/research/journal/2408.TW", { note: "保存測試" }, "save-once");
    await expect(bootstrapSymbol("2408.TW")).resolves.toMatchObject({ status: "ready" });
    await bootstrapSymbol("2330.TW");
    expect(browser.calls.filter(c => c.method === "GET")).toHaveLength(1);
    expect(browser.accepted.filter(c => c.path.includes("/journal/"))).toHaveLength(1);
    expect(browser.accepted.filter(c => c.path.endsWith("/bootstrap")).map(c => JSON.parse(c.body!).canonical_symbol))
      .toEqual(["2408.TW", "2408.TW", "2330.TW"]);
  });

  it("coalesces simultaneous first update and save token requests", async () => {
    const browser = browserSession();
    const { bootstrapSymbol } = await import("../api/phase20Client");
    const { researchMutation } = await import("../api/researchClient");
    await Promise.all([
      bootstrapSymbol("2408.TW"),
      researchMutation("/api/v2/research/journal/2408.TW", { note: "test" }, "save-once"),
    ]);
    expect(browser.calls.filter(c => c.method === "GET")).toHaveLength(1);
    expect(browser.accepted).toHaveLength(2);
  });

  it("recovers an update after another tab replaces the cookie and shares the new session with saving", async () => {
    const browser = browserSession();
    const { bootstrapSymbol } = await import("../api/phase20Client");
    const { researchMutation } = await import("../api/researchClient");
    await bootstrapSymbol("2408.TW");
    browser.rotate();
    await bootstrapSymbol("2408.TW", true);
    await researchMutation("/api/v2/research/journal/2408.TW", {}, "fixed-key");
    const updates = browser.calls.filter(c => c.path.endsWith("/bootstrap"));
    expect(updates).toHaveLength(3);
    expect(updates[1]!.body).toBe(updates[2]!.body);
    expect(browser.calls.filter(c => c.method === "GET")).toHaveLength(2);
    expect(browser.accepted).toHaveLength(3);
  });

  it.each(["csrf_session_expired", "csrf_session_invalid"])("retries an explicitly rejected update only once: %s", async detail => {
    const mock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ csrf_token: "old" }))
      .mockResolvedValueOnce(Response.json({ detail }, { status: 403 }))
      .mockResolvedValueOnce(Response.json({ csrf_token: "new" }))
      .mockResolvedValueOnce(Response.json({ detail }, { status: 403 }));
    const { bootstrapSymbol } = await import("../api/phase20Client");
    await expect(bootstrapSymbol("2408.TW", true)).rejects.toThrow("操作驗證仍未通過");
    const posts = mock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(2);
    expect(posts[0]![1]?.body).toBe(posts[1]![1]?.body);
    expect(mock).toHaveBeenCalledTimes(4);
  });

  it.each([403, 409, 500])("does not retry unrelated errors or uncertain responses (%s)", async status => {
    const mock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ csrf_token: "token" }))
      .mockResolvedValueOnce(Response.json({ detail: "research_origin_not_allowed" }, { status }));
    const { bootstrapSymbol } = await import("../api/phase20Client");
    await expect(bootstrapSymbol("2408.TW")).rejects.toThrow("research_origin_not_allowed");
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it("does not repeat an update when its response was lost", async () => {
    const mock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ csrf_token: "token" }))
      .mockRejectedValueOnce(new TypeError("network response lost"));
    const { bootstrapSymbol } = await import("../api/phase20Client");
    await expect(bootstrapSymbol("2408.TW")).rejects.toThrow("network response lost");
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it.each(["journal/2408.TW", "assumptions/2408.TW/eps/draft", "assumptions/2408.TW/eps/id/approve", "assumptions/2408.TW/eps/id/revoke"])(
    "requires explicit retry for %s and preserves the payload and idempotency key", async path => {
      const browser = browserSession();
      const { bootstrapSymbol } = await import("../api/phase20Client");
      const { researchMutation, researchErrorMessage } = await import("../api/researchClient");
      await bootstrapSymbol("2408.TW");
      browser.rotate();
      const payload = { rationale: "使用者確認", note: "原筆記" };
      await expect(researchMutation(`/api/v2/research/${path}`, payload, "same-key")).rejects.toThrow("csrf_refresh_required");
      expect(browser.accepted.filter(c => c.path.endsWith(path))).toHaveLength(0);
      expect(researchErrorMessage(new Error("csrf_refresh_required"), "failed")).toContain("本次操作尚未執行");
      await researchMutation(`/api/v2/research/${path}`, payload, "same-key");
      const posts = browser.calls.filter(c => c.path.endsWith(path));
      expect(posts).toHaveLength(2);
      expect(posts[0]!.body).toBe(posts[1]!.body);
      expect(posts.map(c => c.key)).toEqual(["same-key", "same-key"]);
      expect(browser.accepted.filter(c => c.path.endsWith(path))).toHaveLength(1);
    },
  );

  it("does not replay saving when its response was lost", async () => {
    const mock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ csrf_token: "token" }))
      .mockRejectedValueOnce(new TypeError("network response lost"));
    const { researchMutation } = await import("../api/researchClient");
    await expect(researchMutation("/api/v2/research/journal/2408.TW", { note: "kept" }, "fixed-key"))
      .rejects.toThrow("network response lost");
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it("cancels the old stock's wait without cancelling shared issuance or posting for the old stock", async () => {
    let release!: (response: Response) => void;
    let issuanceSignal: AbortSignal | undefined | null;
    const mock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (String(input).endsWith("/csrf-token")) {
        issuanceSignal = init?.signal;
        return new Promise(resolve => { release = resolve; });
      }
      return Response.json({ status: "ready" });
    });
    const { bootstrapSymbol } = await import("../api/phase20Client");
    const controller = new AbortController();
    const old = bootstrapSymbol("2330.TW", false, controller.signal);
    const oldResult = expect(old).rejects.toMatchObject({ name: "AbortError" });
    const current = bootstrapSymbol("2408.TW");
    controller.abort();
    await oldResult;
    expect(issuanceSignal?.aborted).toBe(false);
    release(Response.json({ csrf_token: "shared" }));
    await current;
    const posts = mock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0]![1]!.body as string).canonical_symbol).toBe("2408.TW");
  });

  it("does not invalidate a renewed token when an older rejection arrives late", async () => {
    const browser = browserSession();
    const { getLocalCsrfToken, invalidateLocalCsrfToken } = await import("../api/localCsrfSession");
    const old = await getLocalCsrfToken("/api/v2/data-operations/csrf-token");
    invalidateLocalCsrfToken(old);
    const renewed = await getLocalCsrfToken("/api/v2/data-operations/csrf-token");
    invalidateLocalCsrfToken(old);
    expect(await getLocalCsrfToken("/api/v2/data-operations/csrf-token")).toBe(renewed);
    expect(browser.calls).toHaveLength(2);
  });

  it("does not repeat cancellation or change its selected operation", async () => {
    const browser = browserSession();
    const { getCsrfToken, cancelOperation } = await import("../api/dataOperationsClient");
    await getCsrfToken();
    browser.rotate();
    await expect(cancelOperation("owned-operation")).rejects.toThrow("本次取消未執行");
    const posts = browser.calls.filter(c => c.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0]!.body!)).toEqual({ expected_operation_id: "owned-operation" });
    expect(browser.accepted).toHaveLength(0);
  });

  it.each([{}, { csrf_token: "" }, { csrf_token: 123 }])("rejects malformed issuance before any mutation: %j", async payload => {
    const mock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(payload));
    const { bootstrapSymbol } = await import("../api/phase20Client");
    await expect(bootstrapSymbol("2408.TW")).rejects.toThrow("尚未送出變更");
    expect(mock).toHaveBeenCalledTimes(1);
  });

  it("releases failed session initialization so a later user retry can proceed", async () => {
    const mock = vi.spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(Response.json({ csrf_token: "new" }))
      .mockResolvedValueOnce(Response.json({ status: "ready" }));
    const { bootstrapSymbol } = await import("../api/phase20Client");
    await expect(bootstrapSymbol("2408.TW")).rejects.toThrow("offline");
    await expect(bootstrapSymbol("2408.TW")).resolves.toMatchObject({ status: "ready" });
    expect(mock).toHaveBeenCalledTimes(3);
  });

  it("bounds a stalled shared token request and lets the next attempt acquire a fresh session", async () => {
    vi.useFakeTimers();
    const mock = vi.spyOn(globalThis, "fetch").mockImplementationOnce(async (_input, init) =>
      new Promise((_resolve, reject) => { init?.signal?.addEventListener("abort", () => reject(init.signal!.reason), { once: true }); }),
    ).mockResolvedValueOnce(Response.json({ csrf_token: "new" }));
    const { getLocalCsrfToken } = await import("../api/localCsrfSession");
    const result = expect(getLocalCsrfToken("/api/v2/data-operations/csrf-token")).rejects.toThrow("操作驗證逾時");
    await vi.advanceTimersByTimeAsync(15001);
    await result;
    expect(await getLocalCsrfToken("/api/v2/data-operations/csrf-token")).toBe("new");
    expect(mock).toHaveBeenCalledTimes(2);
  });
});
