// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { api } from "../src/api";
import type { Project } from "../src/types/project";
import StockAttribution from "../src/v3/StockAttribution";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(["javascript:alert(1)", "data:text/html,unsafe", "http://example.invalid", "//example.invalid", "/local"])(
  "renders unsafe attribution URLs as text: %s", (url) => {
    const { container } = render(<StockAttribution kind="video" attribution={{
      provider: "pexels", author: "Contributor", authorUrl: url, sourceUrl: url, license: "Example",
    }} />);
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(container.textContent).toContain("Contributor");
  },
);

it("preserves HTTPS attribution links", () => {
  const { container } = render(<StockAttribution kind="photo" attribution={{
    provider: "pexels", author: "Contributor", authorUrl: "https://example.invalid/author",
    sourceUrl: "https://example.invalid/source", license: "Example",
  }} />);
  expect(container.querySelectorAll("a")).toHaveLength(2);
});

it("encodes all request-derived path segments", async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(
    new Response(JSON.stringify({ project: {}, revision: 1 })),
  ));
  vi.stubGlobal("fetch", fetcher);
  const value = "video/other?#% space";
  const encoded = encodeURIComponent(value);
  const doc = {} as Project;
  const calls: [() => unknown, string][] = [
    [() => api.cancelJob(value), `/api/jobs/${encoded}/cancel`],
    [() => api.retryJob(value), `/api/jobs/${encoded}/retry`],
    [() => api.getTranscript(value), `/api/transcript/${encoded}`],
    [() => api.transcription(value), `/api/project/${encoded}/transcription`],
    [() => api.rippleCut(value, doc, []), `/api/verbs/ripple_cut/${encoded}`],
    [() => api.rippleRestore(value, doc, []), `/api/verbs/ripple_restore/${encoded}`],
    [() => api.getPeaks(value), `/api/peaks/${encoded}`],
    [() => api.getProject(value), `/api/project/${encoded}`],
    [() => api.saveProject(value, doc, 0), `/api/project/${encoded}`],
    [() => api.patchProject(value, [], 0), `/api/project/${encoded}`],
    [() => api.autoScenes(value, 0), `/api/project/${encoded}/scenes/auto?apply=1`],
    [() => api.proxyStatus(value), `/api/project/${encoded}/proxy`],
    [() => api.sendChat(value, "hello"), `/api/chat/${encoded}`],
    [() => api.getChatLog(value), `/api/chat/${encoded}/log`],
    [() => api.acceptProposal(value, value), `/api/proposal/${encoded}/${encoded}/accept`],
    [() => api.setProposalStatus(value, value, "rejected"), `/api/proposal/${encoded}/${encoded}/status`],
    [() => api.getProposals(value), `/api/chat/${encoded}/proposals`],
    [() => api.approveJob(value), `/api/jobs/${encoded}/approve`],
    [() => api.getComputeJob(value), `/api/compute/jobs/${encoded}`],
  ];
  for (const [call, expected] of calls) {
    await call();
    expect(fetcher.mock.lastCall?.[0]).toBe(expected);
  }
  expect(api.thumb(value)).toBe(`/media/thumb/${encoded}`);
});
