import { expect, it } from "vitest";
import { FramePlayer } from "../src/player";

function Composition({ title, count }: { title: string; count: number }) {
  return <span>{title}: {count}</span>;
}

const settings = { frameCount: 10, fps: 10, width: 800, height: 400 };

it("preserves required composition props on the memoized player", () => {
  const direct = <FramePlayer {...settings} content={Composition} contentProps={{ title: "Sample", count: 2 }} />;
  // @ts-expect-error Required composition props must retain their declared types.
  const invalid = <FramePlayer {...settings} content={Composition} contentProps={{ title: "Sample", count: "two" }} />;
  // @ts-expect-error Required composition props cannot be omitted.
  const missing = <FramePlayer {...settings} content={Composition} contentProps={{ title: "Sample" }} />;
  expect(direct.type).toBe(FramePlayer);
  expect(invalid.type).toBe(FramePlayer);
  expect(missing.type).toBe(FramePlayer);
});
