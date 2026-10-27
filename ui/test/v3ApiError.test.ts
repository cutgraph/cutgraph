import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiErrorText, request } from "../src/v3/api";
import { errorText } from "../src/v3/common";
import { useNewScript } from "../src/v3/newscript/store";

afterEach(() => {
  vi.unstubAllGlobals();
  useNewScript.setState({ idea: "", drafting: false, draftError: null });
});

describe("v3 refusal text", () => {
  it.each([
    { status: 503, error: "Drafting is unavailable. Check your connection.", reason: undefined },
    { status: 409, error: "This installation cannot update.", reason: "Use the release notes to update manually." },
  ])("keeps diagnostics and displays the server text for $status", async ({ status, error, reason }) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error, reason }), { status })));
    const failure = await request("/api/action").catch((err: unknown) => err);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status, detail: error, message: `${status} ${error}` });
    expect(apiErrorText(failure)).toBe(reason ?? error);
    expect(errorText(failure)).toBe(reason ?? error);
  });

  it("shows a draft refusal without the status in the script store", async () => {
    const message = "Drafting is unavailable. Check your connection.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: message }), { status: 503 })));
    useNewScript.getState().setIdea("A short cooking lesson");
    await useNewScript.getState().draftIt();
    expect(useNewScript.getState().draftError).toBe(message);
    expect(useNewScript.getState().drafting).toBe(false);
  });

  it("preserves plain errors and uses unprefixed status text for a non-JSON refusal", async () => {
    expect(apiErrorText(new Error("Connection interrupted."))).toBe("Connection interrupted.");
    expect(errorText(new ApiError(0, "Network unavailable."))).toBe("Network unavailable.");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Unavailable", { status: 503, statusText: "Service Unavailable" })));
    const failure = await request("/api/action").catch((err: unknown) => err);
    expect(apiErrorText(failure)).toBe("Service Unavailable");
    expect(failure).toMatchObject({ message: "503 Service Unavailable" });
  });
});
