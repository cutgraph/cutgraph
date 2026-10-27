export { LENS } from './lens.ts';
export { LENS_EYES, LENS_MOUTHS, LENS_EXPRESSIONS, LENS_POSES, LENS_MOVES } from './rig.ts';
export { CHARACTERS, PACK_RIGS, PACK_ID, validatePackManifest, selectPackManifests, registerPack, DEFAULT_CHARACTER, characterOf, isSpecOf, performer, drawCharacter } from './characters.ts';
export type { Performer, PackRig, PackManifest, HeadCrop } from './characters.ts';
export { FORMATS, designScale, BACKDROPS, DIAGRAMS, EYEBROW, ACCENT, layout, layoutFor, headlineRuns, headlineFoot, drawShot } from './scene.ts';
export { INTERACTIONS, costarPerformer, costarTime, duoBeat, duoLayout, drawDuoShot, overCostar } from './duo.ts';
export type { DuoBeat, DuoLayout, DuoSlot, Lean } from './duo.ts';
export { BUBBLES, bubbleBeat, bubbleLayout, crownOf, drawBubble, enterFromRight, entranceS, pixelOf, widthOf } from './acting.ts';
export type { BubbleBeat, BubbleLayout, Entrance, Spot } from './acting.ts';
export { ENSEMBLE_MAX, drawEnsembleShot, ensembleLayout, memberPerformer, memberSlots, memberTime } from './ensemble.ts';
export type { EnsembleLayout } from './ensemble.ts';
export { GRAPHICS, drawGraphic, drawCaptions } from './overlays.ts';
export {
  LAYOUTS, CAMERAS, BADGE_TONES, MARK, MARK_CHARS, BADGES_MAX, MARK_MAX, SCREEN_MORE_MAX, CAMERA_S, ZOOM,
  stagingOf, staged, cameraAt, focusOf, beginCamera, endCamera, screenLayout, phonesOf, titleLayout, signoffLayout, drawMark, drawLayout,
  HEADLINE_SPOTS, LABEL_HEADLINE, LABEL_BADGES, stageLabels, drawStageLabels,
} from './staging.ts';
export type {
  StoredStaging, Staging, CameraState, CameraMove, Focus, PhoneSlot, ScreenLayout, SignoffLayout, CostarHeadline, StageBadges, StageLabels,
} from './staging.ts';
export { PAN_S, panAt, panDistance, panShift, panExtras } from './pan.ts';
export type { PanState } from './pan.ts';
export {
  BOARD_KINDS, BOARD_W, BOARD_H, BOARD_LABELS_MAX, BOARD_LABEL_MAX, isBoard, boardState, boardRect, besideRegion, paintBoard,
  drawBoard, drawStageBoard, LABEL_FS, labelTop,
} from './ideas.ts';
export type { BoardPlace, BoardRect, BoardRegion, BoardSpot, BoardState, Box, BoxState, StageBoard } from './ideas.ts';
export { LEAD, TAIL, bare, wordDur, timeline, sceneAt, wordAt } from './timing.ts';
export { DEFAULT_FONT, clamp, prog, ease } from './draw.ts';
export { SHOT_MODES, ROLE_LOOKS, CAMEO_IN, shotMode, resolveShot, shotTiming, drawSceneFrame } from './project.ts';
export type { ShotMode, ProjectShot, ProjectCostar } from './project.ts';
export type * from './types.ts';
export {
  POSTER_FORMATS, POSTER_T, POSTER_BACKDROP, POSTER_WORDS, POSTER_CROP, POSTER_OUTLINE,
  resolvePoster, posterLayout, posterTitle, posterLabels, drawPosterBackdrop, drawPoster,
} from './poster.ts';
export type { PosterFormat, ProjectPoster, ProjectPosterMember, Poster, PosterLook, PosterSlot, PosterTitleBox, PosterLayout, PosterTitle, PosterLabel } from './poster.ts';
