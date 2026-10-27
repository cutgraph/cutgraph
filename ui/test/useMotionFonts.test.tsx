// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useMotionFonts } from "../src/preview/useMotionFonts";

function withFonts(load: (face: string) => Promise<FontFace[]>) {
  Object.defineProperty(document, "fonts", { configurable: true, value: { load: vi.fn(load) } });
}

afterEach(() => {
  Reflect.deleteProperty(document, "fonts");
  vi.restoreAllMocks();
});

describe("useMotionFonts", () => {
  it("is ready once every scene face has loaded", async () => {
    withFonts(async () => [{} as FontFace]);
    const { result } = renderHook(() => useMotionFonts());
    expect(result.current).toBe(false);
    await waitFor(() => expect(result.current).toBe(true));
  });

  it("still lets the canvas paint when a face fails to load", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    withFonts(async () => { throw new Error("network"); });
    const { result } = renderHook(() => useMotionFonts());
    await waitFor(() => expect(result.current).toBe(true));
    expect(error).toHaveBeenCalledWith("Could not load scene fonts", expect.any(Error));
  });

  it("reports faces that no @font-face declares, then paints", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    withFonts(async (face) => (face.startsWith("900") ? [] : [{} as FontFace]));
    const { result } = renderHook(() => useMotionFonts());
    await waitFor(() => expect(result.current).toBe(true));
    expect(error).toHaveBeenCalledWith("Scene fonts are not declared", ['900 16px "Inter"']);
  });
});
