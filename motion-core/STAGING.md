# Staging reference

Preview and export use the same renderer. Stored fields are validated by the
Python document model and resolved against the video's cast. Composition adapts
to landscape, portrait and square frames.

## Layouts

| Layout | Behavior |
| --- | --- |
| Default stage | Places a lead, optional co-star and up to three ensemble members beside a diagram. |
| `screen` | Fits a headline and one to three phone frames, each with a separate library image. |
| `title` | Drops badges into place, then raises the headline beneath them. |
| `signoff` | Places a resting performer beside optional pixel text and a headline. |

`hideHeadline` suppresses the stage headline. `headlineAt: costar` places it over
the co-star. `eyebrow` supplies a secondary line; an empty string hides it.
Badges carry text with `ink`, `paper` or `accent` tones. Ink and paper use
neutral ramps; accent derives from the shot's accent. Badges fit their region
and stack when the frame is narrow.

Screen images are cover-fitted and top-aligned. A missing image draws a generated
placeholder; an image still loading leaves the screen dark. `screen.image`
identifies the front phone's decoded image; `screen.more` supplies the others.
Side phones sit behind the front phone, smaller and tilted outward, with staggered
entrances. Landscape places the title beside them; other formats place it above.

The sign-off's `mark` accepts text. Glyphs rise from the ground individually,
0.09 seconds apart.
The performer rests beside the optional mark using its declared dimensions. The mark
derives its ramp from the shot accent, as the eyebrow does, using the resolved
Workspace accent or renderer default when needed. The performer's
look remains separately selectable.

## Cameras

| Camera | Behavior |
| --- | --- |
| Absent | Fixed stage composition. |
| `pan` | Translates the world by whole pixels, easing to its settled composition, with backdrop parallax. |
| `push-in` | Zooms about the frame centre to at most 1.18 during the final min(0.8 s, scene duration). |
| `pull-out` | Starts at an independent 1.12 crop and returns to the full frame during the first min(0.8 s, scene duration). |

Timing uses scene-local seconds and scene duration. A short scene clamps the
pan's settling time. Zooms work independently in each scene.
Cast movement and camera movement are independent fields. `vision` selects a
bright radial backdrop through the shot's backdrop field.
All layouts use continuous centre framing. Character rigs retain nearest-neighbour
sampling, with crisp device-pixel edges and feet following the same camera
transform as the rig grid. Camera zoom does not promise whole rig-cell sizes.
Zoomed rigs sample the same design pixel grid in preview and export before
nearest-neighbour enlargement, so a 4K export is the 1080p rig enlarged exactly.
Bubbles, acting marks and sign-off letters stay nearest-neighbour but are sampled
at device resolution, like text and vector elements, which retain finer export edges.
Without a scene length, as in picker tiles, a push-in keeps the full frame.

## Diagrams and board

Classic diagram ids select `scatter`, `blank`, `brief`, `route`, `checks` or `glow` drawings.
`none` draws no diagram. Independent presets each have their own geometry and
scene-local reveal timing. Select the diagram that suits the current content:

| Board kind | Behavior |
| --- | --- |
| `chain` | Connects entries along rows, reversing direction on alternate rows. Labels follow the path. |
| `grid` | Sizes rows and columns to the entry count. Labels follow row order. |
| `list` | Spaces entries evenly from top to bottom. |
| `radial` | Places the first entry at the centre and the remaining entries around it, clockwise from the top. |
| `tree` | Builds levels from the entry count, with at most two children per parent. Labels follow level order. |

`boardLabels` supplies one to six labels, at most twelve characters each. The
entry count determines the node count and geometry; omitted labels draw three
blank nodes. Blank entries draw empty boxes. Labels appear with their nodes
and fit inside the board. Placement accounts for current performers and acting marks.
Each preset draws within its own scene. The classic `brief` drawing remains
separately selectable.

## Acting and groups

| Mark | Behavior |
| --- | --- |
| `say` | Speech bubble with animated strokes. |
| `think` | Thought bubble with cycling dots. |
| `exclaim` | Single exclamation with overshoot and shake. |
| `question` | Question centred above the performer's crown. |
| `exclaims` | Equal neutral exclamations centred above the crown, appearing and shaking together. |

Marks start at the cue word, or first word when no cue is stored. An entering
performer's mark waits for arrival. Each performer can carry its own mark.
Bubble cells scale with the rig and stay inside the frame.

`costar` adds a second performer. `interaction` controls facing, pointing or hand
contact. `ensemble` adds background performers with separate rigs, palettes,
looks and moves. The stage fits their declared dimensions and reserves caption
space. Member badges and a co-star headline occupy separate regions.
Unavailable rigs keep their ids and draw the renderer's placeholder.

## Host images and verification

The host resolves library asset ids in the video's Workspace first, then the
shared library. Only images fill screens. Preview decodes URLs through its image
environment; export decodes local files before rendering. Content-addressed
image filenames participate in scene cache keys.

The staging parity fixture covers one, two and three phones and missing assets.
The museum film fixture covers a six-beat travel example; the staging fixture
covers the sign-off. Focused tests cover
independent diagram geometry and labels, cameras, badge tones, groups and marks.
`node scripts/staging-sheet.ts [out.png] [image]` writes separate feature examples
in landscape and portrait. `node scripts/acting-sheet.ts [out-dir]` writes
independent acting rows.

## Platform goldens

Canvas rasterisation differs by platform, so pixel recordings are kept per
platform as `test/<name>.golden.<platform>.json`. `node scripts/update-goldens.ts`
writes recordings for the current platform only. A pixel test loads the file
for `process.platform`. If that file is missing, Windows fails at load with
regeneration instructions, while other platforms skip the test with a visible
`Missing platform golden` reason.

Linux recordings come from the `Motion-core Linux goldens` workflow
(`.github/workflows/motion-goldens.yml`). It runs on `ubuntu-latest` with
read-only access and never writes back:

1. Dispatch it on the branch to record:
   `gh workflow run motion-goldens.yml --ref <branch>`. Private copies also
   need the `HOSTED_CI` repository variable set to `true`.
2. Once the run passes, download its artifact into an empty directory:
   `gh run download <run-id> --name motion-core-linux-goldens --dir <empty-dir>`.
3. Review it. The run has already checked that the recordings cover the same
   goldens as Windows and that every motion-core test passes against them.
   Compare the file hashes with the run's summary. Then compare
   `contact-sheet/` with a local `node scripts/contact-sheet.ts <dir>`. The scenes
   must match, though anti-aliasing may differ.
4. Copy only the `*.golden.linux.json` files into `test/`, then commit them on
   the task branch. The Linux CI leg then runs every pixel check instead of
   skipping it.

Record again whenever an intentional rendering change regenerates the Windows
recordings.
