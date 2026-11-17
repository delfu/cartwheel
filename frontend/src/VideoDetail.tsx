import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import type { VideoRecord } from "./api";
import { CompositeVideo } from "./CompositeVideo";
import type { VideoView } from "./CompositeVideo";

const views: { key: VideoView; label: string; shortcut: string }[] = [
  { key: "original", label: "Original", shortcut: "q" },
  { key: "mask", label: "Mask", shortcut: "w" },
  { key: "isolated", label: "Cutout", shortcut: "e" },
];

export function VideoDetail({ videoId }: { videoId: string }) {
  const [video, setVideo] = useState<VideoRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [chosen, setChosen] = useState<number[] | null>(null);
  const [view, setView] = useState<VideoView>("original");
  const [tracker, setTracker] = useState<"botsort" | "bytetrack">("botsort");
  const run = video?.runs.find((item) => item.id === runId) ?? video?.runs[0];
  const result = run?.result;
  const selected =
    chosen ?? result?.tracks.map((track) => track.track_id) ?? [];
  const isProcessing =
    video?.runs.some(
      (item) => item.status === "queued" || item.status === "processing",
    ) ?? false;

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
          event.target.closest("input, textarea, select"))
      )
        return;
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
  }, [result]);
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

  if (!video)
    return (
      <section>
        <a href="#/videos">← Videos</a>
        <p role={error ? "alert" : "status"}>{error ?? "Loading video…"}</p>
      </section>
    );
  return (
    <section className="video-detail">
      <a className="back-link" href="#/videos">
        ← Videos
      </a>
      <div className="page-heading">
        <div>
          <h1>{video.filename}</h1>
          <p className="video-hint">
            {video.width} × {video.height} · {video.duration.toFixed(2)} seconds
          </p>
        </div>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div className="composite-workspace">
        <div>
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
        <aside className="video-tracks">
          <h2>People{result ? ` · ${selected.length} selected` : ""}</h2>
          {!result ? (
            <p className="video-hint">
              People will appear after the whole video has been processed.
            </p>
          ) : (
            <>
              <div className="selection-actions">
                <button
                  className="text-button"
                  onClick={() =>
                    setChosen(result.tracks.map((track) => track.track_id))
                  }
                >
                  Select all
                </button>
                <button className="text-button" onClick={() => setChosen([])}>
                  Clear selection
                </button>
              </div>
              {!result.tracks.length && <p>No people detected.</p>}
              {result.tracks.map((track) => (
                <label className="video-track" key={track.track_id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(track.track_id)}
                    onChange={() => onToggleTrack(track.track_id)}
                  />
                  Person {track.track_id}
                </label>
              ))}
              <p className="video-hint">
                Selections apply throughout the video. Missing people contribute
                black frames.
              </p>
            </>
          )}
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
        </aside>
      </div>
    </section>
  );
}
