// @vitest-environment jsdom
import { forwardRef, useImperativeHandle } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Project } from "../src/types/project";

type PlayerListener = (event: { detail: unknown }) => void;

const mockPlayer = vi.hoisted(() => {
  const listeners = new Map<string, Set<PlayerListener>>();
  return {
    currentFrame: 0,
    playing: false,
    soundBlocked: false,
    enableSound: vi.fn(() => {
      mockPlayer.soundBlocked = false;
      mockPlayer.emit("sound", { soundBlocked: false });
    }),
    listeners,
    seek: vi.fn((frame: number) => {
      mockPlayer.currentFrame = frame;
    }),
    emit(event: string, detail?: unknown): void {
      for (const listener of mockPlayer.listeners.get(event) ?? []) listener({ detail });
    },
    reset(): void {
      mockPlayer.currentFrame = 0;
      mockPlayer.playing = false;
      mockPlayer.soundBlocked = false;
      mockPlayer.enableSound.mockClear();
      mockPlayer.seek.mockClear();
      listeners.clear();
    },
  };
});

vi.mock("../src/player", () => ({
  FramePlayer: forwardRef((_props, ref) => {
    useImperativeHandle(ref, () => ({
      isSoundBlocked: () => mockPlayer.soundBlocked,
      enableSound: mockPlayer.enableSound,
      readFrame: () => mockPlayer.currentFrame,
      seek: mockPlayer.seek,
      listen: (event: string, listener: PlayerListener) => {
        const set = mockPlayer.listeners.get(event) ?? new Set();
        set.add(listener);
        mockPlayer.listeners.set(event, set);
      },
      unlisten: (event: string, listener: PlayerListener) => {
        mockPlayer.listeners.get(event)?.delete(listener);
      },
      isRunning: () => mockPlayer.playing,
      play: () => {
        mockPlayer.playing = true;
        mockPlayer.emit("play");
      },
      pause: () => {
        mockPlayer.playing = false;
        mockPlayer.emit("pause");
      },
    }));
    return <div data-testid="mock-player" />;
  }),
}));
vi.mock("../src/preview/ProjectComposition", () => ({ ProjectComposition: () => null }));

const saveProject = vi.fn();
const transcription = vi.fn();
const getTranscript = vi.fn();
vi.mock("../src/api", () => ({
  api: {
    saveProject: (...args: unknown[]) => saveProject(...args),
    transcription: (...args: unknown[]) => transcription(...args),
    getTranscript: (...args: unknown[]) => getTranscript(...args),
  },
}));

import PlayerPanel from "../src/v3/story/PlayerPanel";
import { adoptSaved, setPlayhead, useProjectStore } from "../src/store/useProjectStore";
import { resetStory, useStory } from "../src/v3/story/storyStore";

const project: Project = {
  schemaVersion: 1,
  name: "player-panel",
  revision: 1,
  fps: 30,
  width: 1920,
  height: 1080,
  tracks: [{
    id: "speech",
    kind: "speech",
    clips: [{
      id: "a",
      media: { source: "data/source/player-panel.mp4", proxy: null },
      srcInF: 0,
      srcOutF: 300,
      tlStartF: 0,
      gainDb: 0,
    }],
  }],
};

beforeEach(() => {
  mockPlayer.reset();
  saveProject.mockReset().mockResolvedValue({ ok: true, revision: 2 });
  transcription.mockReset().mockResolvedValue({ state: "not_started", job: null });
  getTranscript.mockReset();
  act(() => adoptSaved(project, 1));
});

afterEach(() => {
  cleanup();
  useProjectStore.setState({ project: null, revision: 0, playheadF: 0, previewSec: 0 });
  resetStory();
});

describe("PlayerPanel", () => {
  it("offers an external sound control when playback is muted by browser policy", async () => {
    render(<PlayerPanel />);
    expect(screen.queryByRole("button", { name: "Enable sound" })).toBeNull();
    act(() => {
      mockPlayer.soundBlocked = true;
      mockPlayer.emit("sound", { soundBlocked: true });
    });
    await userEvent.click(screen.getByRole("button", { name: "Enable sound" }));
    expect(mockPlayer.enableSound).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Enable sound" })).toBeNull();
  });

  it("renders the transport with a play button and m:ss time readout", () => {
    render(<PlayerPanel />);
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    expect(screen.getByText("0:00 / 0:10")).toBeTruthy();
  });

  it("follows a setPlayhead call from outside the player", () => {
    render(<PlayerPanel />);
    act(() => setPlayhead(90));
    expect(mockPlayer.seek).toHaveBeenCalledWith(90);
  });

  it("toggles project.captions.enabled through applyLocal and persists the write", async () => {
    render(<PlayerPanel />);
    expect(useProjectStore.getState().project?.captions?.enabled).not.toBe(true);

    await userEvent.click(screen.getByTitle("Captions"));

    expect(useProjectStore.getState().project?.captions).toEqual({ enabled: true });
    await waitFor(() => expect(saveProject).toHaveBeenCalledWith("player-panel", expect.objectContaining({ captions: { enabled: true } }), 1));

    await userEvent.click(screen.getByTitle("Captions"));
    expect(useProjectStore.getState().project?.captions).toEqual({ enabled: false });
  });

  it("mirrors play/pause/end into storyStore.playing", async () => {
    render(<PlayerPanel />);
    expect(useStory.getState().playing).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(useStory.getState().playing).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(useStory.getState().playing).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(useStory.getState().playing).toBe(true);
    act(() => mockPlayer.emit("end"));
    expect(useStory.getState().playing).toBe(false);
  });

  it("cycles the format chip 16:9 → 9:16 → 1:1 → 16:9, one undo step per click setting width/height/crop together", async () => {
    render(<PlayerPanel />);
    const before = useProjectStore.temporal.getState().pastStates.length;
    const chip = screen.getByTitle("Format");
    expect(chip.textContent).toBe("16:9");

    await userEvent.click(chip);
    let updated = useProjectStore.getState().project;
    expect([updated?.width, updated?.height, updated?.crop]).toEqual([1080, 1920, { mode: "center" }]);
    expect(screen.getByTitle("Format").textContent).toBe("9:16");
    expect(useProjectStore.temporal.getState().pastStates.length).toBe(before + 1);
    await waitFor(() => expect(saveProject).toHaveBeenCalled());

    await userEvent.click(screen.getByTitle("Format"));
    updated = useProjectStore.getState().project;
    expect([updated?.width, updated?.height, screen.getByTitle("Format").textContent]).toEqual([1080, 1080, "1:1"]);
    await userEvent.click(screen.getByTitle("Format"));
    updated = useProjectStore.getState().project;
    expect([updated?.width, updated?.height, updated?.crop, screen.getByTitle("Format").textContent]).toEqual([1920, 1080, null, "16:9"]);
    expect(useProjectStore.temporal.getState().pastStates.length).toBe(before + 3);
  });
});
