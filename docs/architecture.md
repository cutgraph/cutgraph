# Architecture

This map follows the current source runtime. A Workspace groups Videos and a
shared asset library; `Project` is the code type for one Video document.
See the [glossary](glossary.md) for product vocabulary.

## Runtime and routes

`scripts/studio_app.py` is the loopback launcher. It configures the listening
port, starts uvicorn with one worker and serves the app from `studio/app.py`.
The app assembles `studio/routes/`, applies browser security middleware and owns
startup and shutdown. `studio/routes/editor.py` serves the built React UI.
`studio/helpers.py` holds bounded body reads, media serving and document commit
helpers; domain behavior belongs in `pipeline/`.

`studio/compute.py` owns the single-slot compute worker and background proxy
tasks. `pipeline/jobs.py` persists job records and approval state, so interrupted
work can be recovered at startup. `studio/state.py` holds process-scoped queues,
locks, the agent host and event hub. `studio/routes/events.py` streams SSE revision
notifications; chat and job routes stream their progress through the same hub.
Shutdown releases workers, background tasks and assistant sessions.

## Video document and schema

`pipeline/project.py` defines and validates the Video document: tracks, clips,
scenes, shots, captions, frame rate and revision. `pipeline/edits.py` implements
document editing rules. Writes through `studio/routes/projects.py` and
`studio/routes/project_edits.py` use the commit helpers and check the base
revision; stale writes receive a conflict instead of overwriting newer work.

`scripts/export_schema.py` generates `schema/project.schema.json` from the Python
model. Run `python scripts/export_schema.py`, then `npm --prefix ui run codegen`
after model changes. That generates `ui/src/types/project.ts`; do not edit it
by hand. `schema/graphics.registry.json` supplies the shared graphics registry.
`schema/encoder-ladder.json` is hand-authored cross-language encoding policy;
Python and Node read it at runtime, with a parity fixture binding both consumers.
The Node renderer therefore needs the sibling `schema/` directory as well as
motion-core; the portable bundle includes both.

## Story and stores

`ui/src/v3/router.ts` selects Home, Workspace, Library, Settings and Story.
`ui/src/v3/story/StoryView.tsx` assembles Story's lenses, player, drawer, Ask bar
and export sheet. `ui/src/v3/story/storyStore.ts` owns view state.
`ui/src/store/useProjectStore.ts` owns the Video document, playhead and undo
history; local mutations use one labelled undo step. `ui/src/persist.ts`
serializes autosaves and handles revision conflicts.
`ui/src/api.ts` is the browser's Studio client. Event notifications trigger
adoption of newer server state without discarding concurrent local edits.

## Preview and export

`ui/src/preview/ProjectComposition.tsx` composes footage, scenes and captions.
`ui/src/preview/SceneLayer.tsx` draws animated scenes with the shared
`motion-core/src/` engine. `motion-core/bin/render-scene.ts` uses that same
engine with Node canvas to pipe frames to ffmpeg for export, without a browser.

`pipeline/scene_render.py` derives render requests and caches scene movies by
content under the state home's scene cache. The cache identity includes drawing
code and render inputs, and eviction retains the renders needed by the active
export. `pipeline/compile.py` composites footage, scene layers, graphics, audio
and captions into the final ffmpeg export using the shared H.264 ladder. Builds
with libx264 retain NVENC → libx264 for export, proxies and opaque scenes, and
libx264 alone for script stills and frame-rate conforming. Builds without
libx264 try NVENC → QSV → AMF → Media Foundation in all roles. Only device or
initialization failures advance to another encoder; other errors stop the job.
`pipeline/runtime_tools.py` resolves ffmpeg, ffprobe and bundled fonts.

Character packs in repository-root `packs/<pack-id>/` add rigs without engine edits. `pack.json`
supplies Python cast metadata; the UI and Node register matching `index.ts` drawing modules through
motion-core. Packs load in directory-name order and invalid or clashing packs are skipped whole with
a warning. Python can select a directory with `CUTGRAPH_PACKS_DIR`; the UI discovers packs when built.
Clashes are decided from all valid manifests before registration; every pack declaring a shared or built-in id is skipped.
Scene and poster cache identities include every regular file under the packs directory outside test folders, including shared helpers and rejected packs.

Python export and TypeScript preview share frame/time, scene and caption rules.
`pipeline/timebase.py`, `pipeline/caption_pages.py`, `pipeline/staging.py` and
their TypeScript counterparts are checked by parity fixtures in `tests/` and
`motion-core/test/`. Preserve both sides when changing rendering behavior.
The drawing engine decision is recorded in
[this ADR](adr/0003-motion-core-only-drawing-engine.md).

## Assistant boundary

`scripts/agent_host.py` hosts the optional Claude Agent SDK. It exposes explicitly
selected editor and compute tools, removes built-in tools, filters known secret
environment variables and isolates assistant configuration.
`pipeline/copilot.py` and `pipeline/proposals.py` create pending edit proposals.
Accept through `studio/routes/chat.py` applies a proposal through the document
revision gate. Plan mode exposes read-only tools. Deterministic media work stays
in Python and Node; compute tools use `scripts/mcp_gpu.py` as a loopback bridge
to the local job registry. `pipeline/agent_credentials.py` resolves and verifies
the user's API key; `pipeline/secret_store.py` protects stored keys.
See [Security model](security-model.md) and [Privacy](privacy.md).

The assistant's read-only `read_preferences` tool returns compact summaries from
`pipeline/decisions.py`: decision outcomes, editorial intent, rationale and
timestamps. Video scope reads that Video; channel scope can include other Videos
on the same channel. These tool results may be sent to the assistant provider,
including in Plan mode. They contain no raw footage or full transcripts, but
model-written intent titles and rationales can quote transcript words.

## State layout

`runtime.py` selects the state home, and `config.py` derives mutable paths.
The checkout holds code, built UI, schemas and bundled fonts. The state home
contains the following directories (these are runtime locations, not source paths):

| State location | Contents |
| --- | --- |
| data/source | Imported source media |
| data/project-media | Video-specific imported media |
| workspaces | Workspace metadata and shared libraries |
| outputs/projects | Video documents |
| outputs/transcripts | Word transcripts and text/subtitle sidecars |
| outputs/proposals, outputs/notes, outputs/chat | Assistant proposals, notes and conversation logs |
| outputs/decisions | Editorial decision history used by assistant preference summaries |
| outputs/jobs, outputs/proxies, outputs/scenes | Durable jobs and media caches |
| outputs/final, outputs/preview | Exports and preview segments |
| runtime | Protected keys, assistant configuration and temporary runtime files |
| models/torchaudio | Verified English alignment checkpoint |

Hugging Face's speech snapshot uses its separate cache. State does not sync
between installations. See [Getting started](getting-started.md#local-state)
for platform defaults and overrides. Tests select throwaway state before
application imports; manual verification uses copied fixtures and free ports.
