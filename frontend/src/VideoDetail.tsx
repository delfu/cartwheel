import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import type { VideoRecord } from "./api";
import { CompositeVideo } from "./CompositeVideo";
import type { VideoView } from "./videoRenderer";
import { peoplePageSize } from "./peoplePageSize";

const views: { key: VideoView; label: string; shortcut: string }[] = [
  { key: "original", label: "Original", shortcut: "q" },
  { key: "mask", label: "Mask", shortcut: "w" },
  { key: "isolated", label: "Cutout", shortcut: "e" },
];

export function VideoDetail({
  videoId,
  onClose,
}: {
  videoId: string;
  onClose: () => void;
}) {
  const [video, setVideo] = useState<VideoRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [chosen, setChosen] = useState<number[] | null>(null);
  const [view, setView] = useState<VideoView>("original");
  const [peoplePerPage, setPeoplePerPage] = useState(peoplePageSize);
  const [personPage, setPersonPage] = useState(0);
  const [tracker, setTracker] = useState<"botsort" | "bytetrack">("botsort");
  const run = video?.runs.find((item) => item.id === runId) ?? video?.runs[0];
  const result = run?.result;
  const selected =
    chosen ?? result?.tracks.map((track) => track.track_id) ?? [];
  const isProcessing =
    video?.runs.some(
      (item) => item.status === "queued" || item.status === "processing",
    ) ?? false;

  const personPageCount = Math.max(
    1,
    Math.ceil((result?.tracks.length ?? 0) / peoplePerPage),
  );
  const currentPersonPage = Math.min(personPage, personPageCount - 1);
  useEffect(() => {
    const onResize = () => setPeoplePerPage(peoplePageSize());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api
      .video(videoId, controller.signal)
      .then(setVideo)
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [videoId]);
  useEffect(() => {
    if (!isProcessing) return;
    const controller = new AbortController();
    let timer: number;
    const onPoll = async () => {
      try {
        setVideo(await api.video(videoId, controller.signal));
        setError(null);
      } catch (error) {
        if (!controller.signal.aborted) setError(errorMessage(error));
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(onPoll, 1500);
      }
    };
    timer = window.setTimeout(onPoll, 1500);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [videoId, isProcessing]);
  useEffect(() => {
    setChosen(null);
    setPersonPage(0);
  }, [run?.id]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        !result
      )
        return;
      if (
        event.target instanceof HTMLElement &&
        (event.target.isContentEditable ||
          event.target.closest(
            "input:not([type='checkbox']):not([type='radio']), textarea, select",
          ))
      )
        return;
      if (/^[1-9]$/.test(event.key) && !event.repeat) {
        const index = Number(event.key) - 1;
        const track = result.tracks[index];
        if (track) {
          event.preventDefault();
          onToggleTrack(track.track_id);
          setPersonPage(Math.floor(index / peoplePerPage));
        }
        return;
      }
      const next = views.find(
        (item) => item.shortcut === event.key.toLowerCase(),
      );
      if (next) {
        event.preventDefault();
        setView(next.key);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [result, peoplePerPage]);
  const onProcess = async () => {
    if (isSubmitting || isProcessing) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const created = await api.processVideo(videoId, tracker);
      setRunId(created.id);
      setVideo(await api.video(videoId));
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  };
  const onToggleTrack = (id: number) =>
    setChosen((previous) => {
      const current =
        previous ?? result?.tracks.map((track) => track.track_id) ?? [];
      return current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id];
    });

  const closeButton = (
    <button
      className="close-details"
      aria-label="Close details"
      title="Close details"
      autoFocus
      onClick={onClose}
    >
      ×
    </button>
  );
  if (!video)
    return (
      <section className="detail-page video-detail">
        <div className="loading-details-actions">{closeButton}</div>
        <p role={error ? "alert" : "status"}>{error ?? "Loading video…"}</p>
      </section>
    );
  const status =
    run?.status === "succeeded" ? "ready" : (run?.status ?? "ready");
  return (
    <section className="detail-page video-detail">
      <div className="detail-heading">
        <div>
          <h1 title={video.filename}>{video.filename}</h1>
          <p className="subheading">
            {video.width.toLocaleString()} × {video.height.toLocaleString()} px
            · {video.duration.toFixed(2)}s{" "}
            <span className="inline-separator">/</span>
            <span className={`inline-status ${status}`}>
              {status === "ready"
                ? "Ready"
                : status === "queued"
                  ? "Queued"
                  : status === "processing"
                    ? "Processing"
                    : "Failed"}
            </span>
          </p>
        </div>
        <div className="detail-actions">
          <details className="video-options">
            <summary className="button secondary" role="button">Processing options</summary>
            <div className="video-options-menu">
              {!!video.runs.length && (
                <label className="run-picker">
                  Processing run
                  <select
                    value={run?.id ?? ""}
                    onChange={(event) => setRunId(event.target.value)}
                  >
                    {video.runs.map((item, index) => (
                      <option key={item.id} value={item.id}>
                        Run {video.runs.length - index} · {item.tracker} ·{" "}
                        {item.status}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="run-picker">
                Tracker
                <select
                  value={tracker}
                  onChange={(event) => {
                    if (
                      event.target.value === "botsort" ||
                      event.target.value === "bytetrack"
                    )
                      setTracker(event.target.value);
                  }}
                >
                  <option value="botsort">BoT-SORT</option>
                  <option value="bytetrack">ByteTrack</option>
                </select>
              </label>
              <button
                className="button secondary"
                disabled={isSubmitting || isProcessing}
                onClick={onProcess}
              >
                {isSubmitting ? "Queueing…" : "Process whole video again"}
              </button>
            </div>
          </details>
          {closeButton}
        </div>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div className="editor-layout">
        <div className="editor-main">
          <div className="viewer-toolbar">
            <div className="view-options" aria-label="Video view">
              {views.map((option) => (
                <button
                  key={option.key}
                  className={view === option.key ? "is-selected" : ""}
                  aria-pressed={view === option.key}
                  disabled={!result}
                  onClick={() => setView(option.key)}
                >
                  {option.label} <kbd>{option.shortcut.toUpperCase()}</kbd>
                </button>
              ))}
            </div>
          </div>
          {result ? (
            <CompositeVideo
              key={run.id}
              result={result}
              selected={selected}
              view={view}
            />
          ) : (
            <video
              className="unprocessed-video"
              controls
              playsInline
              src={video.original_url}
              preload="metadata"
            />
          )}
          {isProcessing && (
            <div className="video-processing" role="status">
              {run?.total_frames ? (
                <>
                  Processed {run.processed_frames} / {run.total_frames} frames
                  {run.processed_frames === run.total_frames
                    ? ". Encoding mask videos…"
                    : "…"}
                  <progress
                    max={run.total_frames}
                    value={run.processed_frames}
                  />
                </>
              ) : (
                "Queued for full-video processing…"
              )}
            </div>
          )}
          {run?.status === "failed" && (
            <p className="error-banner" role="alert">
              {run.error ?? "Processing failed. Try again."}
            </p>
          )}
        </div>
        <aside className="people-panel" aria-label="Person selection">
          <div className="panel-heading">
            <h2>
              People <span>{result?.tracks.length ?? 0}</span>
            </h2>
          </div>
          {!result ? (
            <div className="panel-state">
              <p>
                People will appear after the whole video has been processed.
              </p>
            </div>
          ) : (
            <>
              <p className="panel-description">
                Select the people you want to keep in your video.
              </p>
              <div className="selection-actions">
                <button
                  className="text-button"
                  disabled={selected.length === result.tracks.length}
                  onClick={() =>
                    setChosen(result.tracks.map((track) => track.track_id))
                  }
                >
                  Select all
                </button>
                <button
                  className="text-button"
                  disabled={!selected.length}
                  onClick={() => setChosen([])}
                >
                  Clear selection
                </button>
              </div>
              {!result.tracks.length && (
                <div className="panel-state">
                  <h3>No people detected</h3>
                </div>
              )}
              <div className="person-list">
                {result.tracks
                  .slice(
                    currentPersonPage * peoplePerPage,
                    (currentPersonPage + 1) * peoplePerPage,
                  )
                  .map((track, offset) => {
                    const number =
                      currentPersonPage * peoplePerPage + offset + 1;
                    return (
                      <label
                        className={`person-row ${selected.includes(track.track_id) ? "is-selected" : ""}`}
                        key={track.track_id}
                      >
                        <input
                          type="checkbox"
                          aria-keyshortcuts={
                            number <= 9 ? String(number) : undefined
                          }
                          checked={selected.includes(track.track_id)}
                          onChange={() => onToggleTrack(track.track_id)}
                        />
                        <span className="person-copy">
                          <strong>Person {number}</strong>
                        </span>
                      </label>
                    );
                  })}
              </div>
              {personPageCount > 1 && (
                <nav className="person-pagination" aria-label="People pages">
                  <button
                    className="text-button"
                    disabled={currentPersonPage === 0}
                    onClick={() => setPersonPage(currentPersonPage - 1)}
                  >
                    Previous
                  </button>
                  <span>
                    {currentPersonPage + 1} / {personPageCount}
                  </span>
                  <button
                    className="text-button"
                    disabled={currentPersonPage === personPageCount - 1}
                    onClick={() => setPersonPage(currentPersonPage + 1)}
                  >
                    Next
                  </button>
                </nav>
              )}
              <div className="selection-summary" role="status">
                <strong>
                  {selected.length} of {result.tracks.length} selected
                </strong>
                <span>
                  Your mask and cutout update automatically across the video.
                </span>
              </div>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
