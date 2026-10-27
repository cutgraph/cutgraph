import { describe, it, expect } from "vitest";
import { landProxy, resolveMedia, sourceSlug } from "../src/preview/resolveMedia";

// The player resolves proxy ?? source. The stem/basename
// distinction is load-bearing: the server's slug guard rejects a dotted source slug, so the source
// branch must strip the extension while the proxy branch keeps the full filename (served by name).
describe("resolveMedia — proxy ?? source", () => {
  it("encodes source and proxy filenames as single URL segments", () => {
    expect(resolveMedia({ source: "data/source/take?#%.mp4" })).toBe("/media/source/take%3F%23%25");
    expect(resolveMedia({ source: "data/source/take.mp4", proxy: "outputs/proxies/take?#%.mp4" })).toBe("/media/proxy/take%3F%23%25.mp4");
    landProxy("take?#%");
    expect(resolveMedia({ source: "data/source/take?#%.mp4" })).toBe("/media/source/take%3F%23%25?proxy");
  });
  it("prefers the proxy, served by full basename", () => {
    expect(resolveMedia({ source: "media/A001.mp4", proxy: "proxies/A001_540.mp4" })).toBe(
      "/media/proxy/A001_540.mp4",
    );
  });

  it("falls back to the source as a bare slug (extension stripped — the server rejects a dotted slug)", () => {
    expect(resolveMedia({ source: "media/A001.mp4", proxy: null })).toBe("/media/source/A001");
    expect(resolveMedia({ source: "media/A001.mp4" })).toBe("/media/source/A001");
  });

  it("strips a Windows-style path and the extension for the source slug", () => {
    expect(resolveMedia({ source: "C:\\clips\\B002.MOV", proxy: null })).toBe("/media/source/B002");
  });

  it("keeps the proxy's own extension and directory-stripped name", () => {
    expect(resolveMedia({ source: "media/A001.mp4", proxy: "sub/dir/A001_proxy.mp4" })).toBe(
      "/media/proxy/A001_proxy.mp4",
    );
  });

  it("serves a project-scoped imported asset without treating it as a source slug", () => {
    expect(resolveMedia({ source: "data/project-media/launch/Intro_Card.png", proxy: null })).toBe(
      "/media/project-asset/launch/Intro_Card.png",
    );
  });

  it("returns null when neither source nor proxy is present", () => {
    expect(resolveMedia({ source: "", proxy: null })).toBe(null);
    expect(resolveMedia({ source: "" })).toBe(null);
  });

  it("treats an empty-string proxy as absent (falls through to source)", () => {
    expect(resolveMedia({ source: "media/A001.mp4", proxy: "" })).toBe("/media/source/A001");
  });

  it("uses the non-generating source route when Compare disables lazy source generation", () => {
    expect(resolveMedia({ source: "data/source/interview.mp4", proxy: null }, "source", false)).toBe("/media/source-raw/data/source/interview.mp4");
  });

  it("gates Compare media URLs on the server preflight manifest", () => {
    expect(resolveMedia({ source: "data/source/interview.mp4", proxy: "data/proxy/interview.mp4" }, "proxy", false, { id: "clip", side: "before", sourceAvailable: true, proxyAvailable: false, sourceUrl: "/media/source-raw/data/source/interview.mp4" })).toBe("/media/source-raw/data/source/interview.mp4");
    expect(resolveMedia({ source: "data/source/interview.mp4", proxy: "data/proxy/interview.mp4" }, "source", false, { id: "clip", side: "before", sourceAvailable: false, proxyAvailable: true })).toBeNull();
    expect(resolveMedia({ source: "external/interview.mp4", proxy: null }, "source", false, { id: "clip", side: "before", sourceAvailable: true, proxyAvailable: false })).toBeNull();
  });

  it("keeps Before and After availability separate when a stable clip id changes media", () => {
    const before = { id: "clip", side: "before" as const, sourceAvailable: true, proxyAvailable: false, sourceUrl: "/media/source-raw/data/source/take.mp4" };
    const after = { id: "clip", side: "after" as const, sourceAvailable: true, proxyAvailable: false, sourceUrl: "/media/source-raw/data/source/take.mov" };
    expect(resolveMedia({ source: "data/source/take.mp4" }, "source", false, before)).toBe(before.sourceUrl);
    expect(resolveMedia({ source: "data/source/take.mov" }, "source", false, after)).toBe(after.sourceUrl);
  });
});

describe("sources that play through /media/source", () => {
  it("names the slug of a source with no proxy, and nothing else", () => {
    expect(sourceSlug({ source: "data/source/demo-talk.mp4", proxy: null })).toBe("demo-talk");
    expect(sourceSlug({ source: "data/source/demo-talk.mp4", proxy: "outputs/proxies/demo-talk_540.mp4" })).toBeNull();
    expect(sourceSlug({ source: "data/project-media/launch/Intro_Card.png" })).toBeNull();
    expect(sourceSlug({ source: "workspaces/garden-1a2b3c/library/0123456789abcdef.mp4" })).toBeNull();
    expect(sourceSlug({ source: "" })).toBeNull();
  });

  it("gives a source a new URL once its proxy lands, so a <video> reloads onto it", () => {
    expect(resolveMedia({ source: "data/source/landing.mp4" })).toBe("/media/source/landing");
    landProxy("landing");
    expect(resolveMedia({ source: "data/source/landing.mp4" })).toBe("/media/source/landing?proxy");
    expect(resolveMedia({ source: "data/source/landing.mp4" }, "source", false)).toBe("/media/source-raw/data/source/landing.mp4");
  });
});
