# Characters

`src/characters.ts` is the drawing registry. Lens (`lens`, `src/lens.ts`) is the default character.
An unregistered id draws Lens with a small "Missing character" marker. Hosts keep the stored id,
including when another profile or shot field changes; unavailable profiles remain visible in Cast.

A `Character` declares its owner, palettes, expression parts, built-in expressions, poses, moves,
rows, scale, aspect, hand anchors and crown. Scenes use `drawCharacter`, never a rig-specific API.
A custom expression is exactly one known option per part; invalid palettes or specs are ignored.

Duo layouts use scale and aspect to fit performers, and hand anchors to align interactions.
Ensembles add up to three more performers after the lead and co-star. Every performer has its own
clock. Bubbles and entrances use the registry metadata and work with every registered rig.
Camera, diagram, title, screen, signoff and poster layouts share the same drawing engine.

Built-in rigs live in `CHARACTERS` and `pipeline/cast.py`'s `BUILTIN_RIGS`. Installations can add rigs
through character packs. Test every expression, pose and move, deterministic canvas reuse, hand
anchors, crown and the house style.

The house style uses a fixed pixel grid, whole-pixel scaling, a one-pixel dark outline, cel shading
lit from above, capsule limbs, readable faces and breathing idle motion. Landing squash preserves
volume; follow-through trails the body. Draw with feet anchored at `(cx, by)` and a ground shadow.
Cache offscreen canvases per environment, clear them every paint, and compute motion only from
`(move, t)` so preview and export remain identical.

## Shared motion and defaults

Pack rigs use the same `resolveShot`, `drawSceneFrame` and `resolvePoster` paths as built-ins.
Hosts resolve each cast profile to its rig id, palette and custom expression before drawing;
co-stars and ensemble members are resolved separately. Unsupported poses and moves fall back
to options that the registered rig declares. Custom expression specs still use the rig's parts.

A sign-off defaults to `rest`, `neutral` and `idle` when the rig provides them, otherwise its
first declared options. Explicit looks remain selectable. The performer uses the selected pose
beside optional pixel lettering supplied by `mark`.
Posters prefer a resting lead and pointing or waving members with neutral expressions, falling
back to each rig's declared options. Their default backdrop is `work`.

The single `exclaim` keeps its default accent colours. Paired `exclaims` use two equal neutral marks on one
clock. Title and member badges use `ink`, `paper` or `accent`; unknown tones resolve to `ink`.
Sign-off lettering uses the default accent ramp; its eyebrow uses the workspace accent.
The `grid`, `chain`, `radial`, `tree` and `list` boards size their geometry from one to six
entries, with independent scene-local timing and placement fitted to current performers and acting marks.
Omitted labels draw three blank nodes; blank entries draw empty boxes.

Push-in and pull-out use bounded centre framing and scene-local easing. All
registered rigs follow that camera transform. `rigDraw` uses `pixelImage` from `src/draw.ts`
to sample zoomed sprites on the design grid before export enlargement. Custom sprite drawers
can call that helper with their own canvas, destination rectangle and shared `MotionEnv`.
Direct `ctx.drawImage` calls remain valid, but do not opt into this design-grid sampling.
Text and vector drawing retain finer export edges; camera zoom does not guarantee whole rig cells.

Packs supply drawing and cast metadata. Store explicit looks, marks and current diagram ids
in shots to stage a particular sequence. The drawing API supports custom compositions.
See [STAGING.md](STAGING.md) and
[POSTER.md](POSTER.md) for the shared layouts.

## Adding a character pack

Create `packs/<pack-id>/` at the repository root. The directory id, rig ids, palettes, part keys,
part options, expressions, poses and moves must all match
`^[a-z0-9][a-z0-9-]{0,39}$`. A pack contains both `pack.json` and `index.ts`; a directory missing
either file is silently ignored. No engine edits are needed.

`pack.json` is the metadata source of truth for Python and the UI:

```json
{
  "characters": [{
    "id": "sample-rig",
    "name": "Sample",
    "owner": "client",
    "palettes": ["plain"],
    "parts": {"eyes": ["calm", "wide"]},
    "expressions": ["neutral"],
    "poses": ["rest"],
    "moves": ["idle"],
    "labels": {"titles": {"eyes": "Eyes"}, "options": {"eyes": {"calm": "Calm"}}},
    "head": {"rows": 40, "foot": 40, "span": 20, "cols": 22, "centre": 12}
  }]
}
```

Every entry requires `id`, a non-empty `name`, `owner` (`"brand"` or `"client"`), a non-empty
`palettes` list, `parts` (each part's ordered options, calm/neutral first), a non-empty `expressions`
list, and the `poses` and `moves` id lists. Ownership has the same workspace scoping as built-in rigs. Rig ids
must be unique across built-ins and all packs. `labels` and `head` are optional: without them the
composer derives readable names from ids and paints a whole-character tile. Head crop numbers
describe the rig's height (`rows`), feet row (`foot`), crop height/width (`span`, `cols`) and centre row
(`centre`); height and crop dimensions must be positive and all numbers finite.

`index.ts` exports `const characters: readonly Character[]`, importing `Character` from
`../../motion-core/src/index.ts`. Export exactly one drawing rig per manifest entry, with no extras.
Each rig must agree on `id`, `name`, `owner`, ordered `palettes`, `poses` and `moves`, and `parts`
including option order. Its expression keys must equal the manifest's expression ids as a set.
Every Character must have at least one built-in expression. Use explicit `.ts` import extensions
so the same modules run in Node and the browser.

Packs load in sorted directory-name order. All hosts validate the manifests of complete pack
directories before deciding clashes. Invalid manifests declare no ids. If an id appears in more
than one valid pack manifest or equals a built-in id, every pack declaring it is skipped whole
with a warning. Python logs warnings, Node writes to stderr and the UI uses `console.warn`.
Node and the browser import only packs that survive this manifest pre-pass.

Packs are **trusted local source code**, like any file in the repository: they are imported and
executed by the browser and the Node renderers. A syntax error that breaks the browser bundler
at build time is the only loud failure. Malformed JSON, invalid metadata, clashes and import
failures (including top-level throws) are skipped with a warning at runtime on every host that
evaluates them.

A module that fails to import or disagrees with its manifest is skipped by the TypeScript hosts
with a warning. Python cannot evaluate TypeScript, so it still lists
that rig from a valid, non-clashing manifest. Preview and export draw the missing-character marker
when the drawing rig is unavailable. This remaining host divergence is accepted.

The UI discovers packs at build time; rebuild it after changing packs. Python reads the repository
`packs/` directory at import, or `CUTGRAPH_PACKS_DIR` when set before startup. The Node scene and
poster CLIs load only when given `--packs <dir>`; Python supplies that option when the directory
exists. Scene and poster cache identities include every regular file under the packs directory,
including shared helpers without manifests, named `packs/<relative path>` in the hash. This includes runtime data and
assets as well as manifests and TypeScript files. Files under any `test/` folder are excluded.
Keep pack tests there; the browser typecheck includes only top-level `packs/*/*.ts` files.
A pack may import only from `motion-core/src` and files inside the packs directory, because only
those sources are covered by render cache versions.
