import { beforeEach, describe, expect, it, vi } from "vitest";

describe("research CSRF routing", () => {
  beforeEach(() => { vi.restoreAllMocks(); vi.resetModules(); });

  it.each(["assumptions/2330.TW/eps/draft", "assumptions/2330.TW/eps/draft-id/approve", "journal/2330.TW"])(
    "uses the installed session for %s while legacy writes stay disabled", async path => {
      const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        if (String(input) === "/api/v2/research/csrf-token") return new Response("", { status: 503 });
        if (String(input) === "/api/v2/data-operations/csrf-token") return Response.json({ csrf_token: "installed-token" });
        expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("installed-token");
        expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("same-request");
        expect(init?.credentials).toBe("same-origin");
        return Response.json({ status: "accepted" });
      });
      const { researchMutation } = await import("../api/researchClient");
      await expect(researchMutation(`/api/v2/research/${path}`, {}, "same-request")).resolves.toEqual({ status: "accepted" });
      expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v2/data-operations/csrf-token");
      expect(fetchMock).toHaveBeenCalledTimes(2);
    },
  );

  it("keeps the legacy queue endpoint and does not bypass a disabled workflow", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 503 }));
    const { researchMutation } = await import("../api/researchClient");
    await expect(researchMutation("/api/v2/research/queue", {})).rejects.toThrow("尚未送出變更");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v2/research/csrf-token");
  });

  it("shows an actionable error and sends no write when installed session creation fails", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 503 }));
    const { researchMutation } = await import("../api/researchClient");
    await expect(researchMutation("/api/v2/research/assumptions/2330.TW/eps/id/approve", {})).rejects.toThrow("錯誤代碼 503");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes an expired installed session without silently replaying approval", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ csrf_token: "old" }))
      .mockResolvedValueOnce(Response.json({ detail: "csrf_session_expired" }, { status: 403 }))
      .mockResolvedValueOnce(Response.json({ csrf_token: "new" }))
      .mockResolvedValueOnce(Response.json({ status: "approved" }));
    const { researchMutation } = await import("../api/researchClient");
    const path = "/api/v2/research/assumptions/2330.TW/eps/id/approve";
    await expect(researchMutation(path, {}, "fixed-key")).rejects.toThrow("csrf_refresh_required");
    expect(fetchMock.mock.calls[2]?.[0]).toBe("/api/v2/data-operations/csrf-token");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    await researchMutation(path, {}, "fixed-key");
    expect(new Headers(fetchMock.mock.calls[3]?.[1]?.headers).get("Idempotency-Key")).toBe("fixed-key");
  });
});
