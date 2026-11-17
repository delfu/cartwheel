import { useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";
import type { VideoResult } from "./api";
import { createVideoPlayback } from "./videoPlayback";
import type { PlaybackState } from "./videoPlayback";
import type { VideoView } from "./videoRenderer";

export function CompositeVideo({
  result,
  selected,
  view,
}: {
  result: VideoResult;
  selected: number[];
  view: VideoView;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const playback = useRef<ReturnType<typeof createVideoPlayback> | null>(null);
  const [state, setState] = useState<PlaybackState>({
    frame: 0,
    isPlaying: false,
    isSyncing: true,
    error: null,
  });
  const selectionKey = [...selected].sort((a, b) => a - b).join(",");
  useEffect(() => {
    if (!canvas.current) return;
    try {
      const engine = createVideoPlayback(canvas.current, result, setState);
      playback.current = engine;
      return () => {
        playback.current = null;
        engine.onDispose();
      };
    } catch (cause) {
      setState((previous) => ({
        ...previous,
        isSyncing: false,
        error: errorMessage(cause),
      }));
    }
    // Published playback URLs identify immutable run outputs; polling must not reset playback.
  }, [result.playback_url]);
  useEffect(() => {
    playback.current?.onConfigure(
      selectionKey ? selectionKey.split(",").map(Number) : [],
      view,
    );
  }, [result.playback_url, selectionKey, view]);
  return (
    <div className="composite-player">
      <div className="composite-canvas">
        <canvas
          ref={canvas}
          width={result.width}
          height={result.height}
          aria-label={`${view === "isolated" ? "Cutout" : view === "mask" ? "Mask" : "Original"} video frame ${state.frame + 1}`}
        />
        {state.isSyncing && (
          <span className="composite-loading" role="status">
            Synchronizing videos…
          </span>
        )}
      </div>
      {state.error && (
        <p role="alert" className="error-banner">
          {state.error}
        </p>
      )}
      <div className="composite-controls">
        <button
          className="button secondary"
          onClick={() => playback.current?.onTogglePlay()}
          disabled={state.error !== null}
        >
          {state.isPlaying ? "Pause" : "Play"}
        </button>
        <button
          className="button secondary"
          disabled={state.frame === 0}
          onClick={() => playback.current?.onSeek(state.frame - 1)}
          aria-label="Previous frame"
        >
          ←
        </button>
        <button
          className="button secondary"
          disabled={state.frame === result.frame_count - 1}
          onClick={() => playback.current?.onSeek(state.frame + 1)}
          aria-label="Next frame"
        >
          →
        </button>
        <span>
          Frame {state.frame + 1} / {result.frame_count} ·{" "}
          {(state.frame / result.fps).toFixed(2)}s
        </span>
      </div>
      <input
        className="composite-timeline"
        type="range"
        min={0}
        max={result.frame_count - 1}
        step={1}
        value={state.frame}
        aria-label="Video timeline"
        onChange={(event) =>
          playback.current?.onSeek(Number(event.target.value))
        }
      />
      <p className="video-hint">
        GPU-composited, silent preview. Videos decode continuously during
        playback.
      </p>
    </div>
  );
}
