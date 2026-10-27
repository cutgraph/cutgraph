import { afterEach, expect, it, vi } from "vitest";
import { api } from "../src/api";

afterEach(() => vi.unstubAllGlobals());

it("loads provider presence without verification for Settings", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ providers: [] })));
  vi.stubGlobal("fetch", fetchMock);
  await api.getChatConfig(false);
  expect(fetchMock).toHaveBeenCalledExactlyOnceWith("/api/chat/config?verify=0");
});

it("preserves the authentication refusal code for chat callers", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    code: "authentication_required", error: "Connect your key in Settings to ask for edits.",
  }), { status: 409, statusText: "Conflict" })));
  await expect(api.sendChat("video", "hello")).rejects.toMatchObject({
    code: "authentication_required", detail: "Connect your key in Settings to ask for edits.",
    message: "409 Conflict: Connect your key in Settings to ask for edits.",
  });
});

it("rejects a name conflict without treating it as a revision conflict", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    code: "name_conflict", error: "That name already exists.",
  }), { status: 409 })));
  await expect(api.patchProject("video", [], 0)).rejects.toThrow("That name already exists.");
});

it("returns the authoritative revision for a revision conflict", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    currentRevision: 3,
  }), { status: 409 })));
  await expect(api.patchProject("video", [], 0)).resolves.toEqual({ ok: false, currentRevision: 3 });
});

it("rejects a scenes name conflict without treating it as a revision conflict", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    code: "name_conflict", error: "That name already exists.",
  }), { status: 409 })));
  await expect(api.autoScenes("video", 0)).rejects.toThrow("That name already exists.");
});

it("returns the authoritative revision for a scenes revision conflict", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    currentRevision: 3,
  }), { status: 409 })));
  await expect(api.autoScenes("video", 0)).resolves.toEqual({ ok: false, currentRevision: 3 });
});
