// A raw, project-scoped subscription to `job` SSE events, independent of ui/src/sse.ts.
// That module's connectProjectSSE also adopts the project's document into the shared useProjectStore
// on every revision bump — correct for the ONE currently-open project, but a derived render doc
// (`<video>__9x16`) is a different, unopened project; adopting it there would clobber the working
// doc. This subscribes to the per-project event stream (studio/routes/events.py) purely to observe job
// progress, and touches no shared state.
import type { SceneProgress } from "./progress";

export interface JobEventMessage {
  type: "job";
  id: string;
  status: string;
  progress: number;
  progressStage: string;
  output?: string;
  error?: string;
  scenes?: SceneProgress[];
  [key: string]: unknown;
}

function isJobEvent(msg: { type?: unknown }): msg is JobEventMessage {
  return msg.type === "job" && typeof (msg as { id?: unknown }).id === "string";
}

export function subscribeJobEvents(project: string, onJob: (job: JobEventMessage) => void): () => void {
  const source = new EventSource(`/api/events?project=${encodeURIComponent(project)}`);
  source.onmessage = (event: MessageEvent<string>) => {
    let msg: { type?: unknown };
    try {
      msg = JSON.parse(event.data) as { type?: unknown };
    } catch {
      return;
    }
    if (isJobEvent(msg)) onJob(msg);
  };
  return () => source.close();
}
