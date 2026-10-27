import { join, resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const playerRoot = resolve(__dirname, "../src/player");
const config = ts.readConfigFile(resolve(__dirname, "../tsconfig.app.json"), ts.sys.readFile);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, resolve(__dirname, ".."));
const program = ts.createProgram(parsed.fileNames, parsed.options);
const checker = program.getTypeChecker();

function exportsOf(file: string): ts.Symbol[] {
  const source = program.getSourceFile(file)!;
  return checker.getExportsOfModule(checker.getSymbolAtLocation(source)!);
}

function membersOf(file: string, name: string): string[] {
  const exported = exportsOf(join(playerRoot, file)).find((item) => item.name === name)!;
  return checker.getDeclaredTypeOfSymbol(exported).getProperties().map((member) => member.name).sort();
}

describe("product player surface", () => {
  it("exports exactly the documented product API, including types", () => {
    expect(exportsOf(join(playerRoot, "index.ts")).map((item) => item.name).sort()).toEqual([
      "ClipAudio", "ClipMediaProps", "ClipVideo", "FrameEventName", "FrameImage", "FrameImageProps",
      "FrameListener", "FramePlayer", "FramePlayerHandle", "FramePlayerProps", "FrameWindow",
      "FrameWindowProps", "Layer", "LayerProps", "useFrame", "usePlaybackPolicy",
    ].sort());
  });

  it("exposes the documented content, playback and frame-window properties", () => {
    expect(membersOf("FramePlayer.tsx", "FramePlayerProps")).toEqual([
      "clickToggles", "content", "contentProps", "fps", "frameCount", "fullscreenOnDoubleClick",
      "height", "keyboardPlayback", "speed", "style", "width",
    ].sort());
    expect(membersOf("context.tsx", "FrameWindowProps")).toEqual([
      "children", "frameCount", "label", "preloadFrames", "startFrame",
    ].sort());
    expect(membersOf("clock.ts", "FramePlayerHandle")).toEqual([
      "enableSound", "isRunning", "isSoundBlocked", "listen", "pause", "play", "readFrame", "seek", "unlisten",
    ].sort());
  });

  it("uses product events for playback, seek, speed and sound policy", () => {
    const symbol = exportsOf(join(playerRoot, "clock.ts")).find((item) => item.name === "FrameEventName")!;
    const events = checker.getDeclaredTypeOfSymbol(symbol);
    expect(events.isUnion()).toBe(true);
    if (!events.isUnion()) throw new Error("FrameEventName must enumerate its events");
    const names = events.types.map((type) => type.isStringLiteral() ? type.value : checker.typeToString(type));
    expect(names.sort()).toEqual(["end", "frame", "pause", "play", "rate", "seek", "sound"]);
  });
});
