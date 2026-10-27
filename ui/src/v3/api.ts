// Typed client for the workspace and shared asset library routes, and the
// cast: character profiles and their custom expressions.
import type { PartSpec } from "motion-core";
import type { ComputeJob } from "../api";

export type AssetKind = "video" | "image" | "audio";

export interface AssetAttribution {
  provider: "pexels" | "pixabay";
  author: string;
  authorUrl?: string | null;
  sourceUrl: string;
  license: string;
}

export interface Asset {
  id: string;
  sha256: string;
  name: string;
  kind: AssetKind;
  file: string;
  bytes: number;
  durationS?: number;
  width?: number;
  height?: number;
  tags: string[];
  createdAt: string;
  attribution?: AssetAttribution;
}

/** A workspace's brand: the eyebrow over its videos' animated headlines, in its accent colour. */
export interface Brand {
  name: string;
  accent: string;
}

/** A change to a workspace's brand; null (or an empty name) restores that field's default. */
export interface BrandPatch {
  name?: string | null;
  accent?: string | null;
}

export interface Workspace {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  videoCount: number;
  assetCount: number;
  coverAssetId?: string;
  brand?: Brand;
  /** Hidden from Home and the switcher, with its videos and assets kept. */
  archived?: boolean;
  /** The latest of its own edits, its library's last change and its videos' last saves. */
  lastUsedAt?: string | null;
}

export interface VideoSummary {
  slug: string;
  title: string;
  updatedAt: string;
  durationS?: number;
  thumbUrl?: string;
  /** A script video: no footage still, so its cover is its first animated scene. */
  script?: boolean;
  /** The video has a thumbnail: it is the video's cover. */
  thumbnail?: boolean;
}

/** A video for Home's Recent row: a `VideoSummary` plus the workspace it lives in. */
export interface RecentVideo extends VideoSummary {
  workspaceId: string;
  workspaceName: string;
}

export interface AssetUsage {
  videos: number;
}

export interface WorkspaceDetail {
  workspace: Workspace;
  videos: VideoSummary[];
  assets: Asset[];
}

export interface UploadResult {
  assets: Asset[];
  deduped: number | string[];
}

export interface AssetPatch {
  name?: string;
  tags?: string[];
}

/**
 * A custom expression: a named combination of its character's rig parts. A described one is a
 * draft until the user approves it, and only then can a scene wear it.
 */
export interface CastExpression {
  id: string;
  name: string;
  spec: PartSpec;
  createdAt: string;
  draft?: true;
}

/**
 * A character profile, stored in its workspace: the motion-core rig that draws it, its approved art
 * (image asset ids in that workspace's library), its palette (one of the rig's), whether the user has
 * approved its look (a client rig starts as a draft) and its expressions.
 */
export interface CharacterProfile {
  available?: boolean;
  id: string;
  name: string;
  rig: string;
  art: string[];
  palette: string;
  approved: boolean;
  expressions: CastExpression[];
  workspaceId: string;
  createdAt: string;
}

/**
 * What a video (or a workspace's videos) can cast; `workspace` is the client workspace, if any, and
 * `brand` the eyebrow its animated headlines carry.
 */
export interface VideoCast {
  characters: CharacterProfile[];
  default: string;
  workspace: { id: string; name: string } | null;
  brand?: Brand;
}

/** Whose cast to load: a video's (by its workspace), a workspace's, or the brand's alone. */
export interface CastScope {
  workspace?: string | null;
  video?: string | null;
}

export interface NewCharacter {
  workspace: string;
  name: string;
  rig: string;
  art?: string[];
  palette?: string;
}

/** How many animated scenes (in how many videos) draw a character: what deleting it changes. */
export interface CharacterUsage {
  scenes: number;
  videos: number;
}

export interface CharacterPatch {
  name?: string;
  art?: string[];
  palette?: string;
  rig?: string;
  approved?: boolean;
}

/** A proposed expression for a short description; `source` says what proposed it ("copilot" or, offline, "keywords"). */
export interface CastProposal {
  name: string;
  spec: PartSpec;
  matched: string[];
  source: string;
}

// status 0 = the request never reached the studio (offline, CORS, aborted). `reason` is a refusal
// explained for a person, when the studio gives one (a 409's).
export class ApiError extends Error {
  constructor(readonly status: number, message: string, readonly reason?: string, readonly detail?: string) {
    super(message);
    this.name = "ApiError";
  }
}

export function apiErrorText(err: unknown): string {
  if (err instanceof ApiError) return err.reason ?? err.detail ?? err.message;
  return err instanceof Error ? err.message : String(err);
}

export const BUILTIN_WORKSPACES = new Set(["brand", "unsorted"]);

export type StockKind = "video" | "photo";

export interface StockItem {
  id: string;
  provider: "pexels" | "pixabay";
  kind: StockKind;
  thumbUrl: string | null;
  previewUrl: string | null;
  downloadUrl: string;
  width: number | null;
  height: number | null;
  durationS: number | null;
  author: string;
  authorUrl: string | null;
  sourceUrl: string;
  license: string;
}

export interface StockSearchResult {
  results: StockItem[];
  page: number;
  hasMore: boolean;
  errors: Record<string, string>;
}

export interface StockProviderStatus {
  configured: boolean;
}

export type StockProviders = Record<"pexels" | "pixabay", StockProviderStatus>;

export interface StockKeysPatch {
  pexels?: string | null;
  pixabay?: string | null;
}

/** A thumbnail PNG the studio rendered: where it is served, and its size. */
export interface ThumbnailRender {
  format: "16:9" | "9:16";
  width: number;
  height: number;
  url: string;
}

export interface StockImportResult {
  asset: Asset;
  deduped: boolean;
}

const ws = (id: string) => `/api/workspaces/${encodeURIComponent(id)}`;
const asset = (id: string, assetId: string) => `${ws(id)}/assets/${encodeURIComponent(assetId)}`;
const character = (id: string) => `/api/cast/characters/${encodeURIComponent(id)}`;
const expression = (characterId: string, id: string) =>
  `/api/cast/expressions/${encodeURIComponent(id)}?${new URLSearchParams({ character: characterId })}`;

function castQuery({ workspace, video }: CastScope): string {
  const q = new URLSearchParams(video ? { video } : workspace ? { workspace } : {}).toString();
  return q ? `?${q}` : "";
}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : String(err));
  }
  if (!res.ok) {
    let detail = res.statusText;
    let reason: string | undefined;
    try {
      const body = (await res.json()) as { error?: unknown; reason?: unknown };
      if (typeof body?.error === "string") detail = body.error;
      if (typeof body?.reason === "string") reason = body.reason;
    } catch {
      // non-JSON error body; the status line is all we have
    }
    throw new ApiError(res.status, `${res.status} ${detail}`.trim(), reason, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const sendJson = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export interface ClaudeConnection {
  connected: boolean;
  source: "environment" | "settings" | null;
  state: "not_connected" | "unknown" | "checking" | "ready" | "error";
  message?: string;
}

export interface RuntimeHealth {
  capabilities: { localInference: boolean; cpuRender: boolean; packaged?: boolean; transcription?: AddonStatus;
    assistant?: AddonStatus & { keySource: "none" | "saved" | "environment"; verification: ClaudeConnection["state"] | "unknown" } };
}

export interface UpdateStatus {
  state: "not_configured" | "disabled" | "checking" | "up_to_date" | "available" | "downloading" | "verifying" | "installing" | "failed";
  reason: string;
  automatic: boolean;
  currentVersion: string;
  version: string | null;
  releaseNotesUrl: string | null;
  canInstall: boolean;
  lastCheck: number | null;
}

export interface AddonTier {
  tier: string;
  label?: string;
  note?: string;
  approximateBytes: number;
  supported: boolean;
  acceptance: string;
  declaration: { licenses: { label: string; url: string }[] };
}

export interface AddonStatus {
  name: string;
  title?: string;
  description?: string;
  downloadNote?: string;
  removalNote?: string;
  state: string;
  tier: string | null;
  available: boolean;
  restartRequired: boolean;
  installedBytes: number;
  sourceInstallation: boolean;
  jobId: string | null;
  job?: ComputeJob | null;
  tiers?: AddonTier[];
}

export interface AddonCatalog {
  addons: AddonStatus[];
  recommendedTier: string;
  gpus: { name: string; driver: string; memoryMiB: number; supported: boolean }[];
}

export const v3api = {
  updates: () => request<UpdateStatus>("/api/updates"),
  checkUpdates: () => request<UpdateStatus>("/api/updates/check", { method: "POST" }),
  updatePreferences: (automatic: boolean) => request<UpdateStatus>("/api/updates/preferences", sendJson("PUT", { automatic })),
  installUpdate: () => request<UpdateStatus>("/api/updates/install", { method: "POST" }),
  health: () => request<RuntimeHealth>("/api/health"),
  addons: () => request<AddonCatalog>("/api/addons"),
  installAddon: (name: string, tier: string, acceptance: string, retry = false) =>
    request<{ jobId: string }>(`/api/addons/${encodeURIComponent(name)}/install`, sendJson("POST", { tier, acceptance, retry })),
  removeAddon: (name: string) => request<AddonStatus>(`/api/addons/${encodeURIComponent(name)}`, { method: "DELETE" }),
  cancelAddon: (job: string) => request<ComputeJob>(`/api/jobs/${encodeURIComponent(job)}/cancel`, { method: "POST" }),
  claudeConnection: () => request<ClaudeConnection>("/api/agent/connection"),
  claudeKeyPresence: () => request<ClaudeConnection>("/api/agent/connection?verify=0"),
  connectClaude: (key: string) => request<ClaudeConnection>("/api/agent/connection", sendJson("POST", { key })),
  verifyClaude: () => request<ClaudeConnection>("/api/agent/connection/verify", { method: "POST" }),
  disconnectClaude: () => request<ClaudeConnection>("/api/agent/connection", { method: "DELETE" }),
  workspaces: () => request<Workspace[]>("/api/workspaces"),
  recentVideos: (limit = 8) => request<RecentVideo[]>(`/api/videos/recent?limit=${limit}`),
  createWorkspace: (name: string) => request<Workspace>("/api/workspaces", sendJson("POST", { name })),
  renameWorkspace: (id: string, name: string) => request<Workspace>(ws(id), sendJson("PATCH", { name })),
  setWorkspaceBrand: (id: string, brand: BrandPatch) => request<Workspace>(ws(id), sendJson("PATCH", { brand })),
  archiveWorkspace: (id: string, archived: boolean) => request<Workspace>(ws(id), sendJson("PATCH", { archived })),
  deleteWorkspace: (id: string) => request<{ ok: true }>(ws(id), { method: "DELETE" }),
  workspace: (id: string) => request<WorkspaceDetail>(ws(id)),
  uploadAssets: (id: string, files: Iterable<File>) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    return request<UploadResult>(`${ws(id)}/assets`, { method: "POST", body: form });
  },
  updateAsset: (id: string, assetId: string, patch: AssetPatch) =>
    request<Asset>(asset(id, assetId), sendJson("PATCH", patch)),
  deleteAsset: (id: string, assetId: string) => request<void>(asset(id, assetId), { method: "DELETE" }),
  assetUsage: (id: string, assetId: string) => request<AssetUsage>(`${asset(id, assetId)}/usage`),
  // "New video from footage": link a library video asset into DATA_DIR (no re-upload) so it
  // can back /api/projects the same way an /upload'd file does.
  useAssetAsSource: (id: string, assetId: string) =>
    request<{ ok: true; name: string; slug: string }>(`${asset(id, assetId)}/use-as-source`, { method: "POST" }),
  assignVideo: (id: string, video: string) =>
    request<{ project: unknown }>(`${ws(id)}/videos/${encodeURIComponent(video)}`, { method: "PUT" }),
  cast: (scope: CastScope = {}) => request<VideoCast>(`/api/cast${castQuery(scope)}`),
  createCharacter: (body: NewCharacter) => request<CharacterProfile>("/api/cast/characters", sendJson("POST", body)),
  updateCharacter: (id: string, patch: CharacterPatch) => request<CharacterProfile>(character(id), sendJson("PATCH", patch)),
  deleteCharacter: (id: string) => request<{ ok: true }>(character(id), { method: "DELETE" }),
  characterUsage: (id: string) => request<CharacterUsage>(`${character(id)}/usage`),
  setDefaultCharacter: (workspace: string, characterId: string | null) =>
    request<VideoCast>("/api/cast/default", sendJson("PUT", { workspace, character: characterId })),
  createExpression: (characterId: string, name: string, spec: PartSpec, draft = false) =>
    request<CastExpression>("/api/cast/expressions", sendJson("POST", { character: characterId, name, spec, ...(draft && { draft }) })),
  updateExpression: (characterId: string, id: string, patch: { name?: string; spec?: PartSpec; approved?: true }) =>
    request<CastExpression>(expression(characterId, id), sendJson("PATCH", patch)),
  deleteExpression: (characterId: string, id: string) => request<{ ok: true }>(expression(characterId, id), { method: "DELETE" }),
  describeExpression: (text: string, rig: string) => request<CastProposal>("/api/cast/describe", sendJson("POST", { text, rig })),
  renderThumbnail: (video: string, format: ThumbnailRender["format"]) =>
    request<ThumbnailRender>(`/api/project/${encodeURIComponent(video)}/thumbnail/render`, sendJson("POST", { format })),
  assetThumbUrl: (id: string, assetId: string) => `${asset(id, assetId)}/thumb`,
  assetFileUrl: (id: string, assetId: string) => `${asset(id, assetId)}/file`,
  // Stock search: key status/edit and merged Pexels+Pixabay search live outside any one
  // workspace; importing a result writes into a specific workspace's library, like an upload.
  stockProviders: () => request<StockProviders>("/api/stock/providers"),
  setStockKeys: (patch: StockKeysPatch) => request<StockProviders>("/api/stock/keys", sendJson("POST", patch)),
  searchStock: (q: string, kind: "all" | StockKind, page: number) =>
    request<StockSearchResult>(`/api/stock/search?${new URLSearchParams({ q, kind, page: String(page) })}`),
  importStock: (workspaceId: string, item: StockItem) =>
    request<StockImportResult>(`${ws(workspaceId)}/stock/import`, sendJson("POST", item)),
};

export function dedupedCount(result: UploadResult): number {
  return Array.isArray(result.deduped) ? result.deduped.length : Number(result.deduped) || 0;
}
