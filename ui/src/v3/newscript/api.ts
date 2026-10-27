// Client for the script-video routes (scripts/studio_app.py).
import type { Project } from "../../types/project";
import { ApiError, request, sendJson } from "../api";

export interface Created {
  video: string;
  revision: number;
  project: Project;
}

export type TakeState = "none" | "not_started" | "queued" | "running" | "failed" | "ready" | "applied";

export interface TakeStatus {
  take: string | null;
  state: TakeState;
  message?: string;
}

export interface Uploaded {
  take: string;
  transcription: { status: string; error?: string };
}

export interface Applied {
  project: Project;
  revision: number;
  /** Script words heard verbatim in the take; null when the take was already applied. */
  alignment: { matched: number; total: number } | null;
}

/** A co-pilot script draft: `source` is always "copilot" — an offline/failed draft is an ApiError instead. */
export interface Draft {
  title: string;
  script: string;
  source: string;
}

const narration = (video: string) => `/api/script-video/${encodeURIComponent(video)}/narration`;

function uploadTake(video: string, file: File, onProgress: (progress: number) => void): Promise<Uploaded> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `${narration(video)}/${encodeURIComponent(file.name)}`);
    if (file.type) xhr.setRequestHeader("Content-Type", file.type);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: { error?: unknown } & Partial<Uploaded> = {};
      try {
        body = JSON.parse(xhr.responseText) as typeof body;
      } catch {
        // a non-JSON body only matters on failure, where the status line says enough
      }
      if (xhr.status >= 200 && xhr.status < 300 && body.take) resolve(body as Uploaded);
      else reject(new ApiError(xhr.status, typeof body.error === "string" ? body.error : `${xhr.status} ${xhr.statusText}`.trim()));
    };
    xhr.onerror = () => reject(new ApiError(0, "The upload failed. Check your connection and try again."));
    xhr.send(file);
  });
}

export const scriptApi = {
  create: (body: { title: string; script: string; workspaceId?: string }) =>
    request<Created>("/api/script-video", sendJson("POST", body)),
  status: (video: string, take?: string) =>
    request<TakeStatus>(take ? `${narration(video)}?take=${encodeURIComponent(take)}` : narration(video)),
  uploadTake,
  apply: (video: string, take: string, baseRevision: number) =>
    request<Applied>(`${narration(video)}/${encodeURIComponent(take)}/apply`, sendJson("POST", { baseRevision })),
  draft: (idea: string) => request<Draft>("/api/script/draft", sendJson("POST", { idea })),
};
