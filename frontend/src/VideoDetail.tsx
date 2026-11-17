import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import type { VideoRecord } from "./api";

type View = "original" | "mask" | "isolated";
const colors = ["#8b6cff", "#2bc7a0", "#f6ab42", "#f477a0", "#5eb7ff"];

export function VideoDetail({ videoId }: { videoId: string }) {
  const [video, setVideo] = useState<VideoRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(2);
  const [currentTime, setCurrentTime] = useState(0);
  const [maxFrames, setMaxFrames] = useState(60);
  const [tracker, setTracker] = useState<"botsort" | "bytetrack">("botsort");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [chosen, setChosen] = useState<number[] | null>(null);
  const [view, setView] = useState<View>("original");
  const [asset, setAsset] = useState<{ key: string; url: string } | null>(null);
  const [assetError, setAssetError] = useState<string | null>(null);
  const player = useRef<HTMLVideoElement>(null);
  const run = video?.runs.find((item) => item.id === runId) ?? video?.runs[0];
  const result = run?.result;
  const frames = result?.frames ?? [];
  const selected = chosen ?? result?.track_ids ?? [];
  const selectionKey = [...selected].sort((a, b) => a - b).join(",");
  const isProcessing =
    video?.runs.some(
      (item) => item.status === "queued" || item.status === "processing",
    ) ?? false;
  const hasValidRange =
    video !== null &&
    Number.isFinite(start) &&
    Number.isFinite(end) &&
    start >= 0 &&
    start < end &&
    end <= video.duration &&
    Number.isInteger(maxFrames) &&
    maxFrames >= 1 &&
    maxFrames <= 60;
  // Show the closest processed frame only inside the run's selected time range.
  let frameNumber = -1;
  if (
    run &&
    currentTime >= run.start_seconds - 0.001 &&
    currentTime < run.end_seconds
  ) {
    for (let index = 0; index < frames.length; index++) {
      if (
        frameNumber === -1 ||
        Math.abs(frames[index].timestamp - currentTime) <
          Math.abs(frames[frameNumber].timestamp - currentTime)
      )
        frameNumber = index;
    }
  }
  const frame = frames[frameNumber];
  const assetKey = `${run?.id}:${frameNumber}:${view}:${selectionKey}`;

  useEffect(() => {
    const controller = new AbortController();
    api
      .video(videoId, controller.signal)
      .then((detail) => {
        setVideo(detail);
        setEnd(Math.min(2, detail.duration));
      })
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
      } catch (error) {
        if (!controller.signal.aborted) setError(errorMessage(error));
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(onPoll, 2000);
      }
    };
    timer = window.setTimeout(onPoll, 2000);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [videoId, isProcessing]);

  useEffect(() => {
    setChosen(null);
  }, [run?.id]);

  useEffect(() => {
    setAssetError(null);
    if (!run || !frame || view === "original") return;
    const controller = new AbortController();
    let url: string | undefined;
    api
      .videoSelection(
        run.id,
        frameNumber,
        selectionKey ? selectionKey.split(",").map(Number) : [],
        view,
        controller.signal,
      )
      .then((blob) => {
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setAsset({ key: assetKey, url });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setAssetError(errorMessage(error));
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [run?.id, frameNumber, view, selectionKey, assetKey, frame]);

  const onSeek = (time: number) => {
    if (player.current) {
      player.current.pause();
      player.current.currentTime = time;
    }
    setCurrentTime(time);
  };
  const onToggleTrack = (id: number) =>
    setChosen((previous) => {
      const current = previous ?? result?.track_ids ?? [];
      return current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id];
    });
  const onProcess = async () => {
    if (!hasValidRange || isSubmitting || isProcessing) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const created = await api.processVideo(videoId, {
        start_seconds: start,
        end_seconds: end,
        max_frames: maxFrames,
        tracker,
      });
      setRunId(created.id);
      setVideo(await api.video(videoId));
      onSeek(start);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  };

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
      <div className="video-workspace">
        <div className="video-source">
          <video
            ref={player}
            src={video.original_url}
            controls
            playsInline
            preload="metadata"
            onTimeUpdate={(event) =>
              setCurrentTime(event.currentTarget.currentTime)
            }
            onSeeked={(event) =>
              setCurrentTime(event.currentTarget.currentTime)
            }
            onError={() =>
              setError(
                "This browser could not play the video. Use an H.264 MP4.",
              )
            }
          />
          <label className="video-timeline">
            Video timeline · {currentTime.toFixed(2)}s
            <input
              aria-label="Video timeline"
              type="range"
              min={0}
              max={video.duration}
              step={0.01}
              value={currentTime}
              style={{
                background: `linear-gradient(to right, #d9d8df ${(start / video.duration) * 100}%, #b6a4ff ${(start / video.duration) * 100}%, #b6a4ff ${(end / video.duration) * 100}%, #d9d8df ${(end / video.duration) * 100}%)`,
              }}
              onChange={(event) => onSeek(Number(event.target.value))}
            />
          </label>
          <fieldset className="video-range">
            <legend>Processing range</legend>
            <div className="range-fields">
              <label>
                Start (seconds)
                <input
                  type="number"
                  min={0}
                  max={video.duration}
                  step={0.01}
                  value={Number.isFinite(start) ? start : ""}
                  onChange={(event) => setStart(event.target.valueAsNumber)}
                />
              </label>
              <button
                className="text-button"
                onClick={() => setStart(Number(currentTime.toFixed(2)))}
              >
                Use current time for start
              </button>
              <label>
                End (seconds)
                <input
                  type="number"
                  min={0}
                  max={video.duration}
                  step={0.01}
                  value={Number.isFinite(end) ? end : ""}
                  onChange={(event) => setEnd(event.target.valueAsNumber)}
                />
              </label>
              <button
                className="text-button"
                onClick={() => setEnd(Number(currentTime.toFixed(2)))}
              >
                Use current time for end
              </button>
            </div>
            <div className="range-fields">
              <label>
                Maximum frames
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={Number.isFinite(maxFrames) ? maxFrames : ""}
                  onChange={(event) => setMaxFrames(event.target.valueAsNumber)}
                />
              </label>
              <label>
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
            </div>
            <p className="video-hint">
              Up to {Number.isFinite(maxFrames) ? maxFrames : 60} frames,
              sampled across this range. Short ranges preserve more motion
              detail.
            </p>
            {!hasValidRange && (
              <p role="alert">
                Choose a start before the end, within the video, and 1–60
                frames.
              </p>
            )}
            <button
              className="button primary"
              disabled={!hasValidRange || isSubmitting || isProcessing}
              onClick={onProcess}
            >
              {isSubmitting
                ? "Queueing…"
                : isProcessing
                  ? "Processing…"
                  : "Process selected range"}
            </button>
            {isProcessing && (
              <p role="status">
                Processing the selected range. Results will appear here when
                ready.
              </p>
            )}
          </fieldset>
        </div>
        <div className="video-results">
          <h2>Processed frames</h2>
          {!!video.runs.length && (
            <label className="run-picker">
              Processing run
              <select
                value={run?.id ?? ""}
                onChange={(event) => {
                  setRunId(event.target.value);
                  const next = video.runs.find(
                    (item) => item.id === event.target.value,
                  );
                  if (next) onSeek(next.start_seconds);
                }}
              >
                {video.runs.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.start_seconds.toFixed(2)}–
                    {item.end_seconds.toFixed(2)}s · {item.tracker} ·{" "}
                    {item.status}
                  </option>
                ))}
              </select>
            </label>
          )}
          {run?.status === "failed" && (
            <p className="error-banner" role="alert">
              {run.error ?? "Processing failed."} Adjust the range or process it
              again.
            </p>
          )}
          {!result ? (
            <div className="video-results-empty">
              {isProcessing
                ? "Waiting for processed frames…"
                : "Choose a time range and click Process selected range."}
            </div>
          ) : (
            <>
              <div className="view-options" aria-label="Frame view">
                {(
                  [
                    { key: "original", label: "Original" },
                    { key: "mask", label: "Mask" },
                    { key: "isolated", label: "Cutout" },
                  ] satisfies { key: View; label: string }[]
                ).map((option) => (
                  <button
                    key={option.key}
                    className={view === option.key ? "is-selected" : ""}
                    aria-pressed={view === option.key}
                    onClick={() => setView(option.key)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <div
                className={`video-frame-view ${view === "isolated" ? "checkerboard" : ""}`}
              >
                {!frame ? (
                  <p>
                    Scrub inside {run.start_seconds.toFixed(2)}–
                    {run.end_seconds.toFixed(2)}s to inspect processed frames.
                  </p>
                ) : view === "original" ? (
                  <>
                    <img
                      src={frame.original_url}
                      alt={`Original frame at ${frame.timestamp.toFixed(2)} seconds`}
                    />
                    <svg
                      viewBox={`0 0 ${frame.width} ${frame.height}`}
                      aria-label="Tracked people"
                    >
                      {frame.people.map((person) => (
                        <g
                          key={person.track_id}
                          onClick={() => onToggleTrack(person.track_id)}
                        >
                          <rect
                            x={person.bbox.x}
                            y={person.bbox.y}
                            width={person.bbox.width}
                            height={person.bbox.height}
                            stroke={
                              colors[(person.track_id - 1) % colors.length]
                            }
                            strokeWidth={4}
                            fill={
                              selected.includes(person.track_id)
                                ? `${colors[(person.track_id - 1) % colors.length]}33`
                                : "transparent"
                            }
                          />
                          <text
                            x={person.bbox.x + 4}
                            y={person.bbox.y + 24}
                            fill="white"
                            stroke="black"
                            strokeWidth={0.5}
                            fontSize={24}
                          >
                            {person.track_id}
                          </text>
                        </g>
                      ))}
                    </svg>
                  </>
                ) : assetError ? (
                  <p role="alert">{assetError}</p>
                ) : asset?.key === assetKey ? (
                  <img
                    src={asset.url}
                    alt={`Selected people ${view} at ${frame.timestamp.toFixed(2)} seconds`}
                  />
                ) : (
                  <p role="status">Updating frame…</p>
                )}
              </div>
              <div className="processed-frame-controls">
                <button
                  className="button secondary"
                  disabled={frameNumber <= 0}
                  onClick={() =>
                    onSeek(frames[Math.max(0, frameNumber - 1)].timestamp)
                  }
                >
                  Previous frame
                </button>
                <span>
                  {frame
                    ? `${frameNumber + 1} / ${frames.length} · ${frame.timestamp.toFixed(2)}s`
                    : `${frames.length} processed frames`}
                </span>
                <button
                  className="button secondary"
                  disabled={frameNumber >= frames.length - 1}
                  onClick={() => onSeek(frames[frameNumber + 1].timestamp)}
                >
                  Next frame
                </button>
              </div>
              <div className="video-tracks">
                <h3>People · {selected.length} selected</h3>
                <div className="selection-actions">
                  <button
                    className="text-button"
                    onClick={() => setChosen(result.track_ids)}
                  >
                    Select all
                  </button>
                  <button className="text-button" onClick={() => setChosen([])}>
                    Clear selection
                  </button>
                </div>
                {!result.track_ids.length && (
                  <p>No people detected in this range.</p>
                )}
                {result.track_ids.map((id) => (
                  <label className="video-track" key={id}>
                    <input
                      type="checkbox"
                      checked={selected.includes(id)}
                      onChange={() => onToggleTrack(id)}
                    />
                    <span
                      className="track-color"
                      style={{ background: colors[(id - 1) % colors.length] }}
                    />
                    Person {id}
                    <small>
                      {frame?.people.some((person) => person.track_id === id)
                        ? "Visible"
                        : "Not in this frame"}
                    </small>
                  </label>
                ))}
                <p className="video-hint">
                  Selections apply across this run. A missing person stays
                  selected and contributes no pixels until their track returns.
                </p>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
