---
status: accepted
date: 2026-09-24
amended: 2026-10-01
superseded-by:
decision-drivers:
  - Animated scenes must look the same in preview and export.
  - Preview timing belongs to the product and must support exact frame seeks and media cuts.
  - Export must run locally without a headless browser or an external compositor.
---

# motion-core draws scenes in preview and export; the frame player drives preview timing

## Decision

motion-core is the shared drawing engine. Preview calls `drawSceneFrame` on a browser
canvas; export calls the same function on an `@napi-rs/canvas` surface in Node and
streams raw frames to ffmpeg. No external compositor or player package ships.

The product owns preview timing in `ui/src/player/`. `FramePlayer` hosts `content`
with `contentProps`, `frameCount`, `fps`, `width` and `height`. `FrameClock` advances
from elapsed time. `FrameWindow` places children in half-open frame windows, and `useFrame`
reads the local frame. `Layer`, `FrameImage`, `ClipVideo` and `ClipAudio` provide
layout and media presentation. Media buffering and sound policy remain part of
the player; scene drawing remains in motion-core.

The public barrel also exports `usePlaybackPolicy` and the types `FramePlayerProps`,
`FrameWindowProps`, `LayerProps`, `FrameImageProps`, `ClipMediaProps`,
`FramePlayerHandle`, `FrameListener` and `FrameEventName`.

`FramePlayerHandle` exposes `seek`, `readFrame`, `play`, `pause`, `isRunning`,
sound controls and `listen`/`unlisten`. Its events are `frame`, `seek`, `rate`,
`play`, `pause`, `end` and `sound`. Story owns the transport controls and synchronizes
the player with the Video document's playhead through `usePlayerSync`.

## Consequences

- A scene drawing change reaches preview and export through the shared engine.
  Font and rendering parity checks still cover differences between the hosts.
- Export requires Node beside Python. Scene cache keys include drawing code and
  render inputs, so drawing changes invalidate cached scenes.
- Player names describe product concepts. Preview callers use the product API,
  and a surface guard checks its documented exports and members.
- Browser media events remain native to HTML media elements; they are separate
  from the frame player's events.

## Evidence

- `ui/src/player/FramePlayer.tsx`: `FramePlayer` hosts the content and scales it to the viewport.
- `ui/src/player/clock.ts`: `FrameClock`, `FramePlayerHandle` and `FrameEventName` own playback timing and events.
- `ui/src/player/context.tsx`: `FrameWindow` and `useFrame` provide local frame windows.
- `ui/src/player/media.tsx`: `ClipVideo` and `ClipAudio` follow the clock and enforce media windows.
- `ui/src/v3/story/PlayerPanel.tsx`: `PlayerPanel` supplies `ProjectComposition` and the Story transport.
- `ui/src/preview/usePlayerSync.ts`: `usePlayerSync` preserves feedback guards and playhead publication.
- `ui/src/preview/SceneLayer.tsx`: `paintScene` calls `drawSceneFrame` for preview.
- `ui/vite.config.ts`: `alias` resolves motion-core directly to its TypeScript source.
- `motion-core/bin/render-scene.ts`: `renderScene` draws frames in Node and pipes them to ffmpeg.
- `pipeline/scene_render.py`: `render_scene`, `motion_core_version` and `cache_key` run and cache scene exports.
- `ui/test/playerSurface.test.ts`: guards documented exports, props, handle members and events.
