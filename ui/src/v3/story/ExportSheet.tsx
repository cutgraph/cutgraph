import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api";
import { useProjectStore } from "../../store/useProjectStore";
import { mmss, projectDurationF } from "../../timeline/geometry";
import { openExport, openThumbnail, useStory } from "./storyStore";
import { formatAspect, matchingFormat, renderProjectName, FORMAT_LABEL, FORMAT_ORDER, type ExportFormat } from "./export/formats";
import { startFormatExport, retryFormatExport } from "./export/runExport";
import { applyJobEvent, initRow, isTerminal, sceneLabel, stageLabel, type ExportRow } from "./export/progress";
import ThumbnailSection from "./thumbnail/ThumbnailSection";
import ThumbnailSheet from "./thumbnail/ThumbnailSheet";
import "./ExportSheet.css";

// Formats, captions, progress, reveal. Over a dimmed story, with each format's
// animated scenes listed as they render or come straight from the scene cache.
export default function ExportSheet() {
  const project = useProjectStore((s) => s.project);
  const [selected, setSelected] = useState<Set<ExportFormat>>(() => new Set([matchingFormat(project ?? { width: 0, height: 0 }) ?? "16:9"]));
  const [captionsOn, setCaptionsOn] = useState(() => project?.captions?.enabled ?? true);
  const [rows, setRows] = useState<ExportRow[]>([]);
  const [playing, setPlaying] = useState<ExportFormat | null>(null);
  const [cancelling, setCancelling] = useState<Set<string>>(() => new Set());
  const [cancelErrors, setCancelErrors] = useState<Record<string, string>>({});
  const pendingCancels = useRef(new Set<string>());
  const cancelledJobs = useRef(new Set<string>());
  const thumbnailOpen = useStory((s) => s.thumbnailOpen);
  const cleanups = useRef<Array<() => void>>([]);
  const sceneNames = useMemo(
    () => new Map((project?.scenes ?? []).map((scene, i) => [scene.id, `${i + 1} · ${scene.title}`])),
    [project?.scenes],
  );

  useEffect(() => () => {
    cleanups.current.forEach((fn) => fn());
    openThumbnail(false);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (useStory.getState().thumbnailOpen) openThumbnail(false);
      else openExport(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (!project) return null;
  const video = project.name;

  function updateRow(row: ExportRow): void {
    setRows((prev) => {
      const idx = prev.findIndex((r) => r.format === row.format);
      if (idx === -1) return [...prev, row];
      const next = prev.slice();
      if (next[idx].jobId === row.jobId && (
        next[idx].status === "cancelled" || (isTerminal(next[idx].status) && !isTerminal(row.status))
      )) return prev;
      next[idx] = row;
      return next;
    });
  }

  function toggleFormat(format: ExportFormat): void {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(format)) next.delete(format);
      else next.add(format);
      return next;
    });
  }

  function doExport(): void {
    if (!project || selected.size === 0) return;
    cleanups.current.forEach((fn) => fn());
    cleanups.current = [];
    const formats = FORMAT_ORDER.filter((format) => selected.has(format));
    setRows(formats.map((format) => initRow(format, renderProjectName(video, project, format))));
    setPlaying(null);
    for (const format of formats) {
      startFormatExport({
        video,
        working: project,
        format,
        captionsOn,
        onRow: updateRow,
        isCancelled: (id) => cancelledJobs.current.has(id),
        registerCleanup: (fn) => cleanups.current.push(fn),
      });
    }
  }

  function doRetry(row: ExportRow): void {
    if (!row.jobId) return;
    retryFormatExport({
      project: row.project,
      format: row.format,
      previousJobId: row.jobId,
      onRow: updateRow,
      isCancelled: (id) => cancelledJobs.current.has(id),
      registerCleanup: (fn) => cleanups.current.push(fn),
    });
  }

  function doReveal(row: ExportRow): void {
    void api.reveal({ project: row.project }).catch(() => {});
  }

  async function doCancel(row: ExportRow): Promise<void> {
    const id = row.jobId;
    if (!id || pendingCancels.current.has(id)) return;
    pendingCancels.current.add(id);
    setCancelling(new Set(pendingCancels.current));
    setCancelErrors((prev) => ({ ...prev, [id]: "" }));
    try {
      const job = await api.cancelJob(id);
      if (job.status === "cancelled") cancelledJobs.current.add(id);
      setRows((prev) => prev.map((current) =>
        current.jobId === id && !isTerminal(current.status) ? applyJobEvent(current, job) : current,
      ));
    } catch (error) {
      setCancelErrors((prev) => ({ ...prev, [id]: error instanceof Error ? error.message : String(error) }));
    } finally {
      pendingCancels.current.delete(id);
      setCancelling(new Set(pendingCancels.current));
    }
  }

  return (
    <>
      <div className="sheet-backdrop" aria-hidden="true" onClick={() => openExport(false)} />
      <div className="sheet" data-slot="export" role="dialog" aria-label="Export">
        <header>
          <h2>Export</h2>
          <button type="button" className="icon" aria-label="Close" onClick={() => openExport(false)}>
            ×
          </button>
        </header>

        <p className="eyebrow">Format</p>
        <div className="formats" role="group" aria-label="Format">
          {FORMAT_ORDER.map((format) => {
            const label = FORMAT_LABEL[format];
            const on = selected.has(format);
            return (
              <button
                key={format}
                type="button"
                className={on ? "on" : undefined}
                aria-pressed={on}
                onClick={() => toggleFormat(format)}
              >
                <i style={{ aspectRatio: formatAspect(format).replace(":", "/") }} />
                <b>{label.name}</b>
                <small>
                  {label.detail} · {formatAspect(format)}
                </small>
              </button>
            );
          })}
        </div>

        <p className="eyebrow">Captions</p>
        <div className="pills" role="group" aria-label="Captions">
          <button type="button" className={captionsOn ? "on" : undefined} aria-pressed={captionsOn} onClick={() => setCaptionsOn(true)}>
            Burned in
          </button>
          <button type="button" className={!captionsOn ? "on" : undefined} aria-pressed={!captionsOn} onClick={() => setCaptionsOn(false)}>
            Off
          </button>
        </div>

        {rows.length > 0 && (
          <div className="ex-list">
            {rows.map((row) => (
              <div key={row.format} className={`ex-row ${row.status}`}>
                <div className="ex-row-head">
                  <b>{FORMAT_LABEL[row.format].name} · {formatAspect(row.format)}</b>
                  <span className="ex-stage">{row.error ?? stageLabel(row)}</span>
                </div>
                <div className="ex-bar">
                  <b style={{ width: `${Math.round(row.progress * 100)}%` }} />
                </div>
                {row.scenes.length > 0 && (
                  <ul className="ex-scenes" aria-label={`${FORMAT_LABEL[row.format].name} scenes`}>
                    {row.scenes.map((scene) => (
                      <li key={scene.id} className={`ex-scene ${scene.state}`}>
                        <span>{sceneNames.get(scene.id) ?? scene.id}</span>
                        <i><b /></i>
                        <small>{sceneLabel(scene, row)}</small>
                      </li>
                    ))}
                  </ul>
                )}
                {row.status === "error" && <p className="ex-error">{row.error}</p>}
                {row.jobId && !isTerminal(row.status) && (
                  <div className="ex-actions">
                    <button type="button" className="ghost" disabled={cancelling.has(row.jobId)} onClick={() => void doCancel(row)}>
                      {cancelling.has(row.jobId) ? "Cancelling…" : "Cancel"}
                    </button>
                    {cancelErrors[row.jobId] && <p className="ex-error" role="alert">{cancelErrors[row.jobId]}</p>}
                  </div>
                )}
                {row.status === "done" && row.output && (
                  <div className="ex-actions">
                    <button type="button" className="ghost" onClick={() => setPlaying(row.format)}>
                      Play
                    </button>
                    <button type="button" className="ghost" onClick={() => doReveal(row)}>
                      Show in folder
                    </button>
                  </div>
                )}
                {row.status === "error" && (
                  <div className="ex-actions">
                    <button type="button" className="ghost" onClick={() => doRetry(row)}>
                      Retry
                    </button>
                  </div>
                )}
                {playing === row.format && row.output && (
                  <video className="ex-preview" src={row.output} controls preload="metadata" />
                )}
              </div>
            ))}
          </div>
        )}

        <button type="button" className="primary full" disabled={selected.size === 0} onClick={doExport}>
          {rows.length > 0 ? "Export again" : `Export ${mmss(projectDurationF(project), project.fps)} video`}
        </button>
        <p className="fineprint">Renders scene by scene with ffmpeg on this computer (NVENC when available); scenes that haven't changed come from the cache.</p>

        <ThumbnailSection />
      </div>
      {thumbnailOpen && <ThumbnailSheet />}
    </>
  );
}
