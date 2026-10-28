"""
Single source of truth for APPLICATION constants: paths, model sizes, thresholds, stages.
Dependency-free (no imports from other pipeline modules).

Machine-specific and secret values never live here: API keys come from the environment or from
Settings (stored under the state home), and runtime.py resolves the state home itself.
"""
import os
from pathlib import Path

from runtime import resolve_runtime

# ── Project root + local paths ─────────────────────────────────────────────────
CODE_ROOT = Path(__file__).parent
_PACKS_HOME = os.environ.get("CUTGRAPH_PACKS_DIR", "").strip()
PACKS_DIR = (Path(_PACKS_HOME).expanduser() if _PACKS_HOME else CODE_ROOT / "packs").resolve()
_RUNTIME = resolve_runtime(CODE_ROOT)
STATE_HOME = _RUNTIME.state_home

# ROOT remains the checkout for code-generation compatibility. Mutable Studio
# state must be rooted at STATE_HOME, never at ROOT.
ROOT = CODE_ROOT
DATA_DIR = STATE_HOME / "data" / "source"
PROJECT_MEDIA_DIR = STATE_HOME / "data" / "project-media"  # user-imported assets, isolated per editor project
OUTPUTS_DIR = STATE_HOME / "outputs"
RUNTIME_DIR = STATE_HOME / "runtime"

# Update checks remain disabled until the release verification key is pinned.
UPDATE_REPOSITORY = "cutgraph/cutgraph"
UPDATE_PUBLIC_KEY = ""

TRANSCRIPTS_DIR = OUTPUTS_DIR / "transcripts"
BROLL_DIR       = OUTPUTS_DIR / "broll"
REFRAME_DIR     = OUTPUTS_DIR / "reframe"
CAPTIONS_DIR    = OUTPUTS_DIR / "captions"
FINAL_DIR       = OUTPUTS_DIR / "final"
AUDIO_DIR       = OUTPUTS_DIR / "audio"
PROJECTS_DIR    = OUTPUTS_DIR / "projects"    # project.json namespace
WORKSPACES_DIR  = STATE_HOME / "workspaces"   # Projects & shared asset library: <id>/{workspace.json,library/}
PROXIES_DIR     = OUTPUTS_DIR / "proxies"     # scrub proxies (formalizes studio's existing dir)
DECISIONS_DIR   = OUTPUTS_DIR / "decisions"   # local editorial learning events

CHANNELS_JSON = STATE_HOME / "channels.json"
CHANNELS_EXAMPLE_JSON = CODE_ROOT / "channels.example.json"

BRAND_EYEBROW = ""
BRAND_ACCENT = "#f5c518"

# ── Assistant proposal, note and chat storage ──────────
# Zero-migration siblings of outputs/projects/<slug>.json (mirror the flat .words.json convention):
# proposals/<slug>/<id>.json (reviewable RFC-6902 patches), notes/<slug>.md (decision log),
# chat/<slug>.jsonl (durable chat turns — deltas ride SSE but are ephemeral).
PROPOSALS_DIR   = OUTPUTS_DIR / "proposals"
NOTES_DIR       = OUTPUTS_DIR / "notes"
CHAT_DIR        = OUTPUTS_DIR / "chat"
# Assistant compute tools: detached jobs (job_id-keyed registry mirror) + windowed judge/choose preview segments.
JOBS_DIR        = OUTPUTS_DIR / "jobs"
PREVIEW_DIR     = OUTPUTS_DIR / "preview"

RUN_LOG = STATE_HOME / "run.log"

MAX_SOURCE_UPLOAD_BYTES = 20 * 1024**3
MAX_NARRATION_UPLOAD_BYTES = 2 * 1024**3
MAX_LIBRARY_UPLOAD_BYTES = 2 * 1024**3
MAX_JSON_BODY_BYTES = 1024**2
# Full documents and edit batches need headroom for thousands of clips and scene specs.
MAX_PROJECT_BODY_BYTES = 16 * 1024**2

# ── Agent host (the embedded Claude co-editor; SDK lives behind scripts/agent_host.py) ──
# Supported host modes use studio-owned warm assistant sessions; other values disable the host.
# SDK auth uses a protected API key. AGENT_MODEL=None ⇒ SDK default.
AGENT_HOST_MODE: str = "inproc"
AGENT_MODEL: "str | None" = None
# Warm-session LRU/TTL: cap concurrent warm SDK sessions and evict idle ones so a
# day-long studio session doesn't strand a claude.exe + mcp_gpu.py per project chatted with.
AGENT_MAX_WARM_SESSIONS = 4
AGENT_SESSION_TTL_S      = 1800   # evict a per-project session idle longer than this (30 min)

# Co-editor knobs the studio chat panel lets the creator pick per project. They map straight onto
# ClaudeAgentOptions.{model,effort,permission_mode}; options are frozen at connect, so a change evicts +
# rebuilds that project's warm session (AgentHost). The UI reads this list from GET /api/chat/config so
# the model roster lives in ONE place. id=None ⇒ SDK default (the API default). model=
# None also means "fall back to AGENT_MODEL" when a request omits/garbles the field.
AGENT_MODELS: "list[dict]" = [
    {"id": None,                          "label": "Default"},
    {"id": "claude-opus-4-8",             "label": "Opus 4.8"},
    {"id": "claude-sonnet-5",             "label": "Sonnet 5"},
    {"id": "claude-haiku-4-5-20251001",   "label": "Haiku 4.5"},
]
AGENT_EFFORTS: "tuple[str, ...]" = ("low", "medium", "high", "xhigh", "max")
# Only "default" and "plan" change real behaviour: the co-editor has NO direct-mutation tools (every edit
# is a reviewable propose_patch), so acceptEdits/bypassPermissions/etc. would be inert, misleading toggles.
# "plan" is enforced self-contained — can_use_tool denies the action tools — independent of SDK internals.
AGENT_MODES: "tuple[str, ...]" = ("default", "plan")
AGENT_DEFAULT_EFFORT: "str | None" = None   # None ⇒ SDK default reasoning effort
AGENT_DEFAULT_MODE: str = "default"

# ── Assistant compute tools: judge/choose preview windows ──────────────────────
# A preview is a windowed compile of the timeline. Windows are CAPPED so an unbounded agent request
# can't launch a multi-minute encode; small windows render inline, larger ones detach as a GPU job.
PREVIEW_MAX_FRAMES      = 450   # hard cap (~15s @ 30fps); a larger window is rejected with a message
PREVIEW_SYNC_MAX_FRAMES = 90    # windows this small render inline (~3s); larger become a detached job
PREVIEW_KEEP            = 20    # keep the newest N preview mp4s; older are pruned on each new write

# ── Animated scene renders (outputs/scenes/, content-addressed) ──
SCENE_CACHE_MAX_BYTES = 4 * 1024**3   # trimmed least recently used first after each scene render pass

# ── Video document: project.json contract + proxy verb ──────────────────────────────
DEFAULT_FPS    = 30    # seeds new/skeleton projects ONLY; timebase always takes fps as a param
PROXY_HEIGHT   = 540
PROXY_GOP      = 12
PROXY_BFRAMES  = 0
PROXY_CQ       = 28
PROXY_AUDIO_BR = "96k"
EDITS_SEAM_FADE_MS = 30   # default afade/acrossfade at a ripple-cut join (ms)

# ── Silence / pause detection (VAD) ──
SILENCE_MIN_PAUSE_S = 0.9     # gap longer than this ⇒ a proposal
SILENCE_PAD_MS      = 150     # breath padding kept each side of a cut
SILENCE_NOISE_DB    = -30     # ffmpeg silencedetect fallback threshold

# ── Waveform peaks (make_peaks) ──
PEAKS_SAMPLE_RATE = 8000      # mono s16le pipe rate for the peak scan
PEAKS_WINDOW_MS   = 50        # one [min,max] pair per this many ms


# EXPANDS a filler cut to swallow the clicky co-articulated edges (clipped to kept + neighbour words).
# Distinct from SILENCE_PAD_MS=150, which SHRINKS a pause cut inward to keep breath.
FILLER_PAD_MS  = 40

# WhisperX
WHISPER_MODEL = "large-v3"
WHISPER_BATCH_BY_FREE_VRAM = ((14 * 1024**3, 16), (7 * 1024**3, 8), (0, 4))
WHISPER_LANGUAGE = "en"

# ── Encoding ───────────────────────────────────────────────────────────────────
ENCODE_CRF        = 18
ENCODE_PRESET     = "slow"

# Export loudness (EBU R128 loudnorm, YouTube's reference level). The export measures the mix first
# and applies one constant gain; LOUDNORM_LRA is the loudness-range target of the single-pass fallback.
LOUDNORM_I   = -14.0     # integrated loudness, LUFS
LOUDNORM_TP  = -1.5      # true-peak ceiling, dBTP
LOUDNORM_LRA = 11.0      # loudness range, LU

# ── B-Roll (Pexels + Pixabay; keys come from env, never hardcoded) ─────────────
BROLL_RESULTS_PER_KEYWORD = 3
BROLL_CACHE_DIR = STATE_HOME / ".cache" / "broll"

# ── Captions ──────────────────────────────────────────────────────────────────
CAPTION_FONT           = "Inter Bold"
CAPTION_SIZE           = 72   # px at a 1080p reference; caption_burn scales it by frame height
CAPTION_COLOR_DEFAULT  = "&HFFFFFF"   # white
CAPTION_COLOR_ACTIVE   = "&H00FFFF"   # yellow highlight
CAPTION_POSITION       = "bottom_third"
CAPTION_MAX_WORDS_LINE = 3

# ── Text overlays (ffmpeg drawtext burn; single-track path) ──
OVERLAYS_DIR     = OUTPUTS_DIR / "overlays"   # per-overlay textfiles for drawtext textfile=
