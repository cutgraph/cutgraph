import { expect, it } from "vitest";
import { LENS_EXPRESSIONS, FORMATS, layout } from "motion-core";

it("resolves motion-core through the vite alias", () => {
  expect(Object.keys(LENS_EXPRESSIONS)).toContain("happy");
  expect(layout("9:16")).toMatchObject({ W: FORMATS["9:16"][0], H: FORMATS["9:16"][1] });
});
