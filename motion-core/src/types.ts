export type Format = '16:9' | '9:16' | '1:1';

export type Ctx = CanvasRenderingContext2D;

// A rig is painted at its own grid size into an offscreen canvas the host supplies, so the same code runs
// against a DOM canvas in the browser and @napi-rs/canvas in Node.
export type RigCanvas = CanvasImageSource & { getContext(contextId: '2d'): Ctx | null };

export interface FontEnv {
  fontFamily?: string;
}

/** Reuse one env across frames: the rig canvas it creates is cached per env object. */
export interface MotionEnv extends FontEnv {
  createCanvas(width: number, height: number): RigCanvas;
  /** A decoded image the host resolved (`Shot.screen.image`), or null while it is not ready; see STAGING.md. */
  image?(src: string): CanvasImageSource | null;
}

export type ExpressionId =
  | 'neutral' | 'happy' | 'excited' | 'surprised' | 'curious' | 'focused' | 'sleepy' | 'idea' | 'confused' | 'determined' | 'proud';
export type PoseId = 'rest' | 'point' | 'hold' | 'cheer' | 'shrug' | 'think' | 'wave' | 'hips' | 'flex';
export type MoveId = 'idle' | 'enter' | 'hop' | 'walk' | 'nod' | 'think' | 'wave' | 'celebrate' | 'sway';
export type BackdropId = 'chaos' | 'work' | 'route' | 'proof' | 'result' | 'black' | 'vision';
export type DiagramId = 'scatter' | 'blank' | 'brief' | 'route' | 'checks' | 'glow' | 'none' | BoardKind;
/** Independent content-sized board layouts (ideas.ts), each resolved within its own scene. */
export type BoardKind = 'grid' | 'chain' | 'radial' | 'tree' | 'list';
export type GraphicId = 'lower-third' | 'title' | 'callout' | 'steps' | 'character-cameo';
/** How a duo shot's two characters play off each other (duo.ts). */
export type Interaction = 'none' | 'face' | 'fistbump' | 'highfive' | 'point';
/** A balloon over a performer's head (acting.ts): thought, speech, emphasis, a question or
 * simultaneous neutral exclamation marks. */
export type BubbleId = 'think' | 'say' | 'exclaim' | 'question' | 'exclaims';
/** How an animated shot's frame is composed (staging.ts): `stage` is the character shot. */
export type StageLayout = 'stage' | 'screen' | 'title' | 'signoff';
/** A bounded centre framing zoom (staging.ts), or a tracking pan that carries the stage
 * in from the left (pan.ts). Push and pull have independent endpoints. */
export type Camera = 'push-in' | 'pull-out' | 'pan';
/** Where a stage shot's headline goes instead of its own place (staging.ts): over the co-star. */
export type HeadlineSpot = 'costar';
export type BadgeTone = 'ink' | 'paper' | 'accent';

/** A `title` card's badge. */
export interface Badge {
  text: string;
  tone: BadgeTone;
}

/** One phone's picture: a library image the host resolved, or a generated placeholder app screen. */
export interface PhoneImage {
  /** The library asset id, as stored. */
  asset?: string;
  /** Where the host found it (a URL in the browser, a file in Node); `env.image` decodes it. */
  image?: string;
}

/** A `screen` shot's phones: phone 1 (the hero, in front) and up to SCREEN_MORE_MAX more beside it. */
export interface PhoneScreen extends PhoneImage {
  more?: PhoneImage[];
}

/** One option for each part of a character's expression spec space, e.g. Lens's {eyes, mouth}. */
export type PartSpec = Readonly<Record<string, string>>;

/** What a character draws: ids from its own sets. Unknown ids fall back to the character's defaults. */
export interface CharacterState {
  /** One of the character's `palettes`; its first when absent. */
  palette?: string;
  pose?: string;
  expression?: string;
  move?: string;
  t?: number;
  /** Used when `expression` is not one of the character's built-ins. */
  custom?: PartSpec;
  /** Screen pixels the character is raised off the ground (a hop into a high five); its shadow stays down. */
  lift?: number;
}

/**
 * A rig in the character registry (characters.ts). `parts` is its expression spec space (each part and
 * its options), `expressions` its built-in combinations of them. See motion-core/CHARACTERS.md.
 */
export interface Character {
  readonly id: string;
  readonly name: string;
  /** Which workspace scope owns the rig: a client's rig stays out of anything shared beyond that client. */
  readonly owner: 'brand' | 'client';
  /** Colour variants the rig draws; the first is the default. */
  readonly palettes: readonly string[];
  readonly parts: Readonly<Record<string, readonly string[]>>;
  readonly expressions: Readonly<Record<string, PartSpec>>;
  readonly poses: readonly string[];
  readonly moves: readonly string[];
  /** Height beside another character in a duo shot, relative to the default rig's (1, the default). */
  readonly scale?: number;
  /** Width over height at rest, for spacing a duo apart; 0.62 when absent. */
  readonly aspect?: number;
  /** Rig pixel rows over its height: the rig is drawn at a whole-pixel scale of floor(h / rows). */
  readonly rows?: number;
  /**
   * Duo shots: where the front hand of each pose is, in rig pixels from the feet anchor, [forward, up]:
   * the front edge of the fist or palm, at its middle. A fist bump or high five meets there; a pose
   * without one takes the default rig's proportions. Needs `rows`.
   */
  readonly hands?: Readonly<Record<string, readonly [number, number]>>;
  /** Rig pixels from the feet to the top of the head, using its declared crown: a mark centred over the
   * head (the "?" and the "!!") sits just above it. The layout height when absent. Needs `rows`. */
  readonly crown?: number;
  /** Draws the character with its feet centred on (cx, by), `h` tall. */
  draw(ctx: Ctx, cx: number, by: number, h: number, state: CharacterState, env: MotionEnv): void;
}


export interface Shot {
  /** The registry id of the character that performs the shot; the default rig when absent or unknown. */
  character?: string;
  palette?: string;
  pose: PoseId | (string & {});
  expression: ExpressionId | (string & {});
  move: MoveId | (string & {});
  backdrop: BackdropId;
  diagram: DiagramId;
  headline: string;
  /** The line above the headline, the brand's name; empty or absent hides it. */
  eyebrow?: string;
  /** Animation only: true draws no headline or eyebrow on the stage, a duo or an ensemble; a
   * `screen`, `title` or `signoff` layout ignores it (they need their own text). */
  hideHeadline?: boolean;
  /** The eyebrow's colour, a #rrggbb brand accent; ACCENT when absent. */
  accent?: string;
  custom?: PartSpec;
  /** Index of the scene word the diagram lands on. */
  cueAt?: number;
  /** A second character sharing the shot (animation only), on the right of the lead. */
  costar?: Costar;
  /** How the two play off each other; `none` when absent. Only read with a co-star. */
  interaction?: Interaction;
  bubble?: BubbleId;
  /** More performers after the lead and the co-star, in one row (animation only; ensemble.ts). */
  ensemble?: Costar[];
  camera?: Camera;
  layout?: StageLayout;
  screen?: PhoneScreen;
  badges?: Badge[];
  /** The `signoff` layout's optional pixel text; absent means no mark. */
  mark?: string;
  /** A duo's or an ensemble's headline over the co-star, fading in a quarter of the way through the scene. */
  headlineAt?: HeadlineSpot;
  /** One to six entries size the board; blanks draw empty nodes. Omitted labels draw three nodes. */
  boardLabels?: string[];
}

/** A duo shot's second character: its own rig, palette and look. */
export interface Costar {
  character?: string;
  palette?: string;
  pose: PoseId | (string & {});
  expression: ExpressionId | (string & {});
  move: MoveId | (string & {});
  custom?: PartSpec;
  bubble?: BubbleId;
}

/** Scene-local times (seconds from scene start) that a shot animates against. */
export interface ShotTiming {
  cueT?: number;
  firstT?: number;
  /** The scene's length: a push-in lands on it. */
  endT?: number;
  /** The shot's own cue word ("Lands on"), when it names a kept one: a cameo springs in on it.
   * Absent, a cameo springs in as the scene starts. */
  inT?: number;
}

export type CueType = 'graphic' | 'anim' | 'broll' | 'music' | 'sfx';

export interface Cue {
  type?: CueType;
  item: string;
  text?: string;
  /** Index of the scene word the cue is anchored to. */
  at?: number;
  /** B-roll length in kept words. */
  len?: number;
  start?: number;
  end?: number;
}

export interface Word {
  w: string;
  start: number;
  end: number;
  cut?: boolean;
}

export interface ScriptWord {
  w: string;
  cut?: boolean;
}

export interface ScriptScene {
  words: ScriptWord[];
  cues: Cue[];
  shot?: Shot;
}

export interface ScriptProject {
  scenes: ScriptScene[];
}

export interface TimedWord extends Word {
  si: number;
  i: number;
  seg: number;
}

export interface TimedCue extends Cue {
  ci: number;
  si: number;
  start: number;
  end: number;
}

export interface SceneTiming extends ShotTiming {
  si: number;
  start: number;
  end: number;
  words: TimedWord[];
  cues: TimedCue[];
}

export interface Timeline {
  dur: number;
  scenes: SceneTiming[];
  words: TimedWord[];
  music: TimedCue[];
}

export interface Layout {
  W: number;
  H: number;
  characterX: number;
  characterY: number;
  characterH: number;
  tx: number;
  ty: number;
  tw: number;
  fs: number;
  dx: number;
  dy: number;
  dw: number;
  left: boolean;
  capY: number;
  capFs: number;
  /** Centre the headline's runs in [tx, tx + tw] instead of setting them flush left. */
  center?: boolean;
}

export interface TextRun {
  text: string;
  x: number;
  y: number;
  size: number;
  weight: number;
  color: string;
}
