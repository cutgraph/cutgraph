# Cutgraph Studio

Canonical domain vocabulary, checked against the source. Architecture evidence lives in [Architecture](architecture.md).

## Language

### Workspaces and videos

| Term | Meaning | Aliases to avoid |
|------|---------|------------------|
| **Workspace** | A named group of Videos with a shared Library and a Workspace brand. | project (for the group), folder |
| **Video** | One edit containing media, words, Scenes, Shots and Cues. | project, project document |
| **Unsorted** | Where the videos that belong to no workspace are listed. | inbox, uncategorized |
| **Archived workspace** | A Workspace hidden from ordinary selection, with its Videos, Library and Characters kept. | hidden, trashed |
| **Library** | A workspace's shared media, imported once and usable by every video in it. | media bin |
| **Asset** | One file in a library: a clip, a photo or a sound. | media item |
| **Brand kit** | The built-in Workspace of shared house media and Characters available to Videos. | brand workspace, house project |
| **Workspace brand** | A Workspace's headline name and accent colour. | project brand, label |

### Story

| Term | Meaning | Aliases to avoid |
|------|---------|------------------|
| **Story** | The view where the script is the timeline: a video's words, scenes and cues, read top to bottom. | editor (for this view) |
| **Lens** | One way of looking at the same story: Script, Board, Timeline or Source. | tab, mode |
| **Arc** | A strip of a Video's Scenes, coloured by Role. | outline |
| **Scene** | A titled stretch of a video that starts on a word, with a role and a shot. | section, beat |
| **Role** | A scene's part in the story, such as Problem, Example, Payoff or Wrap. | beat type |
| **Transcript** | The timed words heard in a speech source. | captions, words file |
| **Speech source** | The clip whose transcript a video reads as its words. | main clip |
| **Word anchor** | A tie between a transcript word and a Clip or Cue. | time pin |
| **Clip** | A window of media placed on the timeline. | segment |
| **Cut** | Removing words from a video and closing the gap they leave. | trim, delete |
| **Filler** | An um, an uh, a false start or a repeat that can be cut. | disfluency |
| **Cue** | A B-roll Clip, graphic, music or sound associated with a word. | insert, element |
| **Cue word** | The transcript word on which a Shot's Cameo, duo Interaction or Bubble lands. | cue (for the word), trigger word |
| **Caption page** | One pill of captions burned into the picture, a few words at a time. | subtitle line |
| **Revision** | A Video's document version number. | version |

### Shots and staging

| Term | Meaning | Aliases to avoid |
|------|---------|------------------|
| **Shot** | How a scene is pictured: its mode, its characters and their acting, and its staging. | recipe |
| **Shot mode** | Footage, Animation (a full-frame animated picture over the footage's sound) or Cameo. | scene type |
| **Cameo** | A Character over footage, associated with a Cue word. | — |
| **Take** | One look tried while directing a shot, numbered in the Shot view for that sitting. | version, variant |
| **Critic** | The Shot view's check of a Shot's acting and headline. | review |
| **Direct it** | A plain-language direction for a proposed Shot. | prompt |
| **Staging** | The layout and camera choices that frame an animated shot. | stage (for these choices) |
| **Layout** | How an animated shot is composed: Stage, Phone screen, Title card or Sign-off. | template |
| **Stage** | The default Layout for Characters on a backdrop. | staging (for this layout) |
| **Camera** | A bounded centre push-in or pull-out, a pan move, or none (Still). | zoom |
| **Badge** | A short label on a Title card. | chip, tag |
| **Mark** | Optional pixel text beside the resting performer in a Sign-off layout. | logo |
| **Eyebrow** | The small line over an animated headline; by default the workspace brand's name. | kicker, overline |

### Cast

| Term | Meaning | Aliases to avoid |
|------|---------|------------------|
| **Character pack** | A collection of Rigs and their shared drawing metadata, available to an installation's Cast. | rig bundle, character plugin |
| **Cast** | The characters a video can use: the Brand kit's plus its own workspace's. | characters list |
| **Character** | A named member of a cast, drawn by a rig in one of its palettes, with its art and approval. | profile (outside code), model |
| **Rig** | The drawing a character is drawn with: its parts, expressions, poses and moves. | character (for the drawing), model |
| **Brand rig** | A rig that is house art and can be used in every workspace; a client rig stays in its client's workspace. | brand character (for the rig) |
| **Draft** | A Character look or Expression awaiting approval. | pending art |
| **Expression** | A face: one option for each part of a rig's face, built in or custom. | emotion, mood |
| **Performer** | A character as drawn in one shot: the lead, a co-star or an ensemble member. | actor |
| **Co-star** | The second character in a duo shot. | partner, second character |
| **Ensemble** | Up to three more characters lined up in one shot. | group shot |
| **Interaction** | What a duo does together: face each other, fist bump, high five or point. | gesture |
| **Bubble** | A think, say, exclaim ("!"), question ("?") or exclaims (two simultaneous neutral "!!") balloon over a performer. | speech bubble (for all five) |

### Script videos

| Term | Meaning | Aliases to avoid |
|------|---------|------------------|
| **Script video** | A video made from a written script over a silent animated picture, rather than from footage. | animation project |
| **Script** | The written words of a Script video, with sections corresponding to Scenes. | draft (for a script), screenplay |
| **Narration** | The voice reading a Script video's Script, with estimated timing before a Recording is applied. | voiceover, VO |
| **Recording** | One reading of the narration, recorded in Studio or dropped in as a file. | take (for a recording) |

### Co-pilot

| Term | Meaning | Aliases to avoid |
|------|---------|------------------|
| **Co-pilot** | The assistant working inside Studio on the open Video, with edits proposed for acceptance or rejection. | co-editor, assistant |
| **Ask bar** | The bar under the story where you ask the co-pilot for something. | chat, chat box |
| **Co-pilot context** | The open Video's content and editing choices as read by the Co-pilot. | story (for assistant context) |
| **Editorial decision** | A recorded outcome of editing a Video, with editorial intent and an optional reason: accepted, rejected, modified or manual. | preference (for a single decision) |
| **Preference summary** | A compact selection of Editorial decisions describing prior editing choices for a Video or channel. | full history, transcript |
| **Proposal** | An edit suggested by the Co-pilot and awaiting acceptance or rejection. | suggestion (for the stored edit) |

### Export

| Term | Meaning | Aliases to avoid |
|------|---------|------------------|
| **Export** | The finished Video files in chosen Formats, with captions when enabled. | render, compile (for the outcome) |
| **Format** | An Export's picture shape and size: landscape, vertical, square or landscape 4K. | aspect, preset |
| **Render document** | A copy of a video retargeted at another format for its export. | derived project |
| **Final render job** | The background Job for one final Export, requiring approval before execution. | render (bare) |
| **Scene render** | One animated scene drawn ahead of an export and kept for reuse until it changes. | scene cache entry |
| **Job** | Background work with a status, such as transcribing or rendering. | task |

## Ambiguities

- **One word, many concepts** — `project` denotes both **Workspace** and **Video**. Canonical split: use **Workspace** for a group of videos with its library and brand and **Video** for one edit; never use `project` outside code identifiers and link values. (`ui/src/v3/router.ts:5; pipeline/project.py:156`)
- **One word, many concepts** — `brand` denotes **Brand kit**, **Workspace brand** and **Brand rig**. Canonical split: use **Brand kit** for the built-in house workspace, **Workspace brand** for a workspace's headline name and accent and **Brand rig** for house-owned drawing; never use bare `brand` in docs (the app's short "Brand" labels sit where the screen says which). (`pipeline/library.py:44; pipeline/library.py:275; motion-core/src/types.ts:87`)
- **One word, many concepts** — `draft` denotes both **Draft** and **Script**. Canonical split: use **Draft** for unapproved art and **Script** for the written words of a Script video; never use `draft` as a noun for a script (the "Draft it" button is a verb and stays). (`pipeline/cast.py:156; ui/src/v3/newscript/api.ts:32`)
- **One word, many concepts** — `take` denotes both **Take** and **Recording**. Canonical split: use **Take** for a look tried in the Shot view and **Recording** for a reading of the narration; never use `take` for a recording. (`ui/src/v3/story/shot/takes.ts:11; pipeline/script_video.py:224`)
- **One word, many concepts** — `cue` denotes both **Cue** and **Cue word**. Canonical split: use **Cue** for a word-anchored insert and **Cue word** for the word a shot lands on; never use bare `cue` for the word. (`ui/src/v3/story/drawer/insert.ts:173; pipeline/project.py:152`)
- **One word, many concepts** — `stage` denotes both **Staging** and **Stage**. Canonical split: use **Staging** for the layout and camera choices and **Stage** for the default layout; never use `stage` for the choices. (`pipeline/project.py:746; pipeline/project.py:740`)
- **One word, many concepts** — `character` denotes both **Character** and **Rig**. Canonical split: use **Character** for a cast member and **Rig** for the drawing it is drawn with; never use `character` for a rig. (`pipeline/cast.py:394; motion-core/src/types.ts:83`)
- **Many words, one concept** — `co-pilot`, `co-editor`, `assistant` denote **Co-pilot**. Canonical: **Co-pilot**. (`pipeline/copilot.py:743`)
- **Many words, one concept** — `export`, `render`, `compile` denote **Export**. Canonical: **Export**; render names its parts (**Final render job**, **Scene render**, **Render document**). (`ui/src/v3/story/ExportSheet.tsx:15; pipeline/jobs.py:601; pipeline/scene_render.py:178`)
- **Many words, one concept** — `character`, `profile` denote **Character**. Canonical: **Character**. (`pipeline/cast.py:396`)

- **One word, many concepts** — `story` denotes both **Story** and **Co-pilot context**. Canonical split: use **Story** for the editing view and **Co-pilot context** for the assistant's reading; never use `story` for the reading context. (`ui/src/v3/story/StoryView.tsx:26`; `pipeline/copilot.py:81`)

## Candidates

None.
