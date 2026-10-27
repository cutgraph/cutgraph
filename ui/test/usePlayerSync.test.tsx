// @vitest-environment jsdom
import { forwardRef, memo, useEffect, useImperativeHandle, type ComponentType } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

type FrameEvent = "frame" | "seek";
type PlayerListener = (event: { detail: { frame: number } }) => void;

const mockPlayer = vi.hoisted(() => {
  const listeners = new Map<string, Set<PlayerListener>>();
  return {
    currentFrame: 0,
    playing: true,
    renders: 0,
    compositionMounts: 0,
    compositionUnmounts: 0,
    listeners,
    seek: vi.fn((frame: number) => {
      mockPlayer.currentFrame = frame;
    }),
    emit(event: FrameEvent, frame: number, frameAfterEvent = frame): void {
      mockPlayer.currentFrame = frame;
      for (const listener of listeners.get(event) ?? []) listener({ detail: { frame } });
      mockPlayer.currentFrame = frameAfterEvent;
    },
    reset(): void {
      mockPlayer.currentFrame = 0;
      mockPlayer.playing = true;
      mockPlayer.renders = 0;
      mockPlayer.compositionMounts = 0;
      mockPlayer.compositionUnmounts = 0;
      mockPlayer.seek.mockClear();
      listeners.clear();
    },
  };
});

vi.mock("../src/player", () => ({
  FramePlayer: memo(forwardRef(({ content: Composition, contentProps }: {
    content: ComponentType<Record<string, unknown>>;
    contentProps: Record<string, unknown>;
  }, ref) => {
    mockPlayer.renders += 1;
    useImperativeHandle(ref, () => ({
      isSoundBlocked: () => false,
      enableSound: () => {},
      readFrame: () => mockPlayer.currentFrame,
      seek: mockPlayer.seek,
      listen: (event: string, listener: PlayerListener) => {
        const eventListeners = mockPlayer.listeners.get(event) ?? new Set();
        eventListeners.add(listener);
        mockPlayer.listeners.set(event, eventListeners);
      },
      unlisten: (event: string, listener: PlayerListener) => {
        mockPlayer.listeners.get(event)?.delete(listener);
      },
      isRunning: () => mockPlayer.playing,
      play: () => {
        mockPlayer.playing = true;
        for (const listener of mockPlayer.listeners.get("play") ?? []) {
          listener({ detail: { frame: mockPlayer.currentFrame } });
        }
      },
      pause: () => {
        mockPlayer.playing = false;
        for (const listener of mockPlayer.listeners.get("pause") ?? []) {
          listener({ detail: { frame: mockPlayer.currentFrame } });
        }
      },
    }));
    return <Composition {...contentProps} />;
  })),
}));

vi.mock("../src/preview/ProjectComposition", () => ({
  ProjectComposition: () => {
    useEffect(() => {
      mockPlayer.compositionMounts += 1;
      return () => { mockPlayer.compositionUnmounts += 1; };
    }, []);
    return <div data-testid="composition" />;
  },
}));
vi.mock("../src/v3/story/useStoryModel", () => {
  const model = { words: [], spans: [], media: null };
  return { useStoryModel: () => model };
});
vi.mock("../src/v3/story/shot/screenLibrary", () => {
  const screens: unknown[] = [];
  return { useScreenAssets: () => screens };
});
vi.mock("../src/v3/library/castStore", () => {
  const state = { cast: { characters: [], expressions: [] } };
  return { useVideoCast: () => state };
});

import PlayerPanel from "../src/v3/story/PlayerPanel";
import { adoptSaved, setPlayhead, useProjectStore } from "../src/store/useProjectStore";
import { resetStory } from "../src/v3/story/storyStore";
import type { Project } from "../src/types/project";

const project: Project = {
  schemaVersion: 1,
  name: "playback-sync",
  revision: 1,
  fps: 30,
  width: 1920,
  height: 1080,
  tracks: [{
    id: "speech",
    kind: "speech",
    clips: [{
      id: "source",
      media: { source: "data/source/playback-sync.mp4", proxy: null },
      srcInF: 0,
      srcOutF: 300,
      tlStartF: 0,
      gainDb: 0,
    }],
  }],
};

beforeEach(() => {
  mockPlayer.reset();
  act(() => adoptSaved(project, 1));
});

afterEach(() => {
  cleanup();
  useProjectStore.setState({ project: null, revision: 0, selection: null, playheadF: 0, previewSec: 0 });
  resetStory();
});

describe("usePlayerSync through PlayerPanel", () => {
  it("does not seek backwards when playback advances before its store update renders", () => {
    render(<PlayerPanel />);

    act(() => mockPlayer.emit("frame", 1, 2));

    expect(useProjectStore.getState().playheadF).toBe(1);
    expect(mockPlayer.seek).not.toHaveBeenCalled();
  });

  it("still seeks exactly once for an external playhead change", () => {
    render(<PlayerPanel />);
    mockPlayer.currentFrame = 2;

    act(() => setPlayhead(90));

    expect(mockPlayer.seek).toHaveBeenCalledOnce();
    expect(mockPlayer.seek).toHaveBeenCalledWith(90);
  });

  it("limits playback publications so the editor does not rerender for every video frame", () => {
    render(<PlayerPanel />);

    act(() => {
      mockPlayer.emit("frame", 1);
      mockPlayer.emit("frame", 2);
      mockPlayer.emit("frame", 3);
    });

    expect(useProjectStore.getState().playheadF).toBe(1);
    act(() => mockPlayer.emit("frame", 4));
    expect(useProjectStore.getState().playheadF).toBe(4);
  });

  it("keeps the player composition mounted while the transport clock updates", () => {
    render(<PlayerPanel />);
    const composition = screen.getByTestId("composition");
    expect(mockPlayer.renders).toBe(1);
    expect(mockPlayer.compositionMounts).toBe(1);

    act(() => mockPlayer.emit("frame", 30));

    expect(screen.getByText("0:01 / 0:10")).toBeTruthy();
    expect(mockPlayer.renders).toBe(1);
    expect(screen.getByTestId("composition")).toBe(composition);
    expect(mockPlayer.compositionMounts).toBe(1);
    expect(mockPlayer.compositionUnmounts).toBe(0);
  });
});
