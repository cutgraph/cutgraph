# Poster reference

`src/poster.ts` draws deterministic landscape and portrait covers. Browser
preview and Node export call `drawPoster` with the same fonts and cast data.

## Stored fields

A video's `thumbnail` stores a headline, optional eyebrow and badges, a lead's
character, pose and expression, an optional ensemble and a backdrop. Each member
can carry a badge. A still has no camera, move or acting mark. The Python
document model validates fields using the shot rules.

Absent fields use the video's title, its first animated scene's character or
cast default, and up to three other cast profiles. Store `ensemble: []` to show
only the lead. The renderer resolves unsupported looks against each rig's
available poses and expressions. Tests cover defaults and fallbacks separately
from sample content. The lead defaults to rest and members to pointing or waving,
with neutral expressions. Explicit poses and expressions remain selectable.
The default `work` backdrop is a subdued grid.

## Composition

| Element | Behavior |
| --- | --- |
| Lead | Bottom-right, facing into the frame, cropped below the waist at whole-pixel scale. |
| Ensemble | A band behind the lead, below the title, fitted using rig rows and aspect ratios. |
| Headline | High-contrast uppercase lettering, fitted in up to three lines. |
| Eyebrow | Optional secondary line above the headline. |
| Badges | Beneath the title; member badges sit above their respective heads. |
| Backdrop | Scene backdrop at rest, with a vignette. |
| Cutouts | White outlines around solid performer silhouettes; small specks are excluded. |

`posterLayout` computes regions from frame and rig metadata. Landscape places
the title at the upper left; portrait centres it above the performers. Three
words per line is the preferred maximum. Longer titles reduce size to fit.
Members overlap within the allowed band and shrink when necessary. Every
performer uses a fixed sample time and idle motion.

## Rendering and verification

The Export sheet, Thumbnail editor and Video cards use the same preview canvas.
The saved document's thumbnail endpoint invokes `bin/render-poster.ts` and
caches the PNG by request and renderer content hash. No video encoder is needed.

The thumbnail parity fixture holds preview and export resolution to the same
results. Unit tests cover custom faces, unavailable rigs, labels, title fitting,
composition and deterministic pixels.
`node scripts/poster-sheet.ts [out-dir]` writes one Lens performer with a
Museum Visit headline at both output sizes and small card preview sizes.
