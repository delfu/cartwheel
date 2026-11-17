import { useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";
import type { VideoResult } from "./api";

export type VideoView = "original" | "mask" | "isolated";

function waitForMedia(
  video: HTMLVideoElement,
  event: "loadeddata" | "seeked",
  signal: AbortSignal,
) {
  return new Promise<void>((resolve, reject) => {
    const onCleanup = () => {
      window.clearTimeout(timeout);
      video.removeEventListener(event, onReady);
      video.removeEventListener("error", onError);
      signal.removeEventListener("abort", onAbort);
    };
    const onReady = () => {
      if (video.seeking) return;
      onCleanup();
      resolve();
    };
    const onError = () => {
      onCleanup();
      reject(
        new Error("Could not decode a video stream. Reload to try again."),
      );
    };
    const onAbort = () => {
      onCleanup();
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timeout = window.setTimeout(onError, 15000);
    video.addEventListener(event, onReady);
    video.addEventListener("error", onError);
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
  });
}

async function loadVideo(url: string, signal: AbortSignal) {
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  const ready = waitForMedia(video, "loadeddata", signal);
  video.src = url;
  video.load();
  try {
    await ready;
    return video;
  } catch (error) {
    video.removeAttribute("src");
    video.load();
    throw error;
  }
}

async function seekVideo(
  video: HTMLVideoElement,
  time: number,
  signal: AbortSignal,
) {
  if (
    !video.seeking &&
    video.readyState >= 2 &&
    Math.abs(video.currentTime - time) < 0.00001
  )
    return;
  const ready = waitForMedia(video, "seeked", signal);
  video.currentTime = time;
  await ready;
}

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
  const decoders = useRef(new Map<string, Promise<HTMLVideoElement>>());
  const lifetime = useRef(new AbortController());
  const [frame, setFrame] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isRendering, setIsRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectionKey = [...selected].sort((a, b) => a - b).join(",");

  useEffect(() => {
    const controller = new AbortController();
    const cache = new Map<string, Promise<HTMLVideoElement>>();
    decoders.current = cache;
    lifetime.current = controller;
    return () => {
      controller.abort();
      for (const decoder of cache.values())
        decoder.then(
          (video) => {
            video.removeAttribute("src");
            video.load();
          },
          () => {},
        );
    };
  }, [result.playback_url]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const onRender = async () => {
      const started = performance.now();
      setIsRendering(true);
      setError(null);
      const onDecode = (url: string) => {
        let decoder = decoders.current.get(url);
        if (!decoder) {
          decoder = loadVideo(url, lifetime.current.signal);
          decoders.current.set(url, decoder);
        }
        return decoder;
      };
      try {
        const tracks =
          view === "original"
            ? []
            : result.tracks.filter((track) =>
                selectionKey.split(",").includes(String(track.track_id)),
              );
        const [original, ...masks] = await Promise.all([
          onDecode(result.playback_url),
          ...tracks.map((track) => onDecode(track.mask_video_url)),
        ]);
        if (controller.signal.aborted) return;
        // shortcut: paused, frame-locked decoding favors alignment; use GPU compositing if preview playback is too slow.
        const time = (frame + 0.25) / result.fps;
        await Promise.all(
          [original, ...masks].map((video) =>
            seekVideo(video, time, controller.signal),
          ),
        );
        if (controller.signal.aborted) return;
        const target = canvas.current;
        const context = target?.getContext("2d", { willReadFrequently: true });
        if (!target || !context)
          throw new Error("This browser cannot render the video preview.");
        context.drawImage(original, 0, 0, result.width, result.height);
        if (view !== "original") {
          const output = context.getImageData(
            0,
            0,
            result.width,
            result.height,
          );
          const union = new Uint8Array(result.width * result.height);
          const scratch = document.createElement("canvas");
          scratch.width = result.width;
          scratch.height = result.height;
          const maskContext = scratch.getContext("2d", {
            willReadFrequently: true,
          });
          if (!maskContext)
            throw new Error("This browser cannot render masks.");
          for (const mask of masks) {
            maskContext.drawImage(mask, 0, 0, result.width, result.height);
            const pixels = maskContext.getImageData(
              0,
              0,
              result.width,
              result.height,
            ).data;
            for (let index = 0; index < union.length; index++)
              if (pixels[index * 4] >= 128) union[index] = 1;
          }
          for (let index = 0; index < union.length; index++) {
            const offset = index * 4;
            if (!union[index])
              output.data[offset] =
                output.data[offset + 1] =
                output.data[offset + 2] =
                  0;
            else if (view === "mask")
              output.data[offset] =
                output.data[offset + 1] =
                output.data[offset + 2] =
                  255;
            output.data[offset + 3] = 255;
          }
          context.putImageData(output, 0, 0);
        }
        setIsRendering(false);
        if (isPlaying) {
          if (frame >= result.frame_count - 1) setIsPlaying(false);
          else
            timer = window.setTimeout(
              () => setFrame(frame + 1),
              Math.max(0, 1000 / result.fps - (performance.now() - started)),
            );
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setError(errorMessage(error));
          setIsPlaying(false);
          setIsRendering(false);
        }
      }
    };
    void onRender();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [frame, isPlaying, result, selectionKey, view]);

  const onSeek = (next: number) => {
    setIsPlaying(false);
    setFrame(next);
  };
  return (
    <div className="composite-player">
      <div className="composite-canvas">
        <canvas
          ref={canvas}
          width={result.width}
          height={result.height}
          aria-label={`${view === "isolated" ? "Cutout" : view === "mask" ? "Mask" : "Original"} video frame ${frame + 1}`}
        />
        {isRendering && (
          <span className="composite-loading" role="status">
            Syncing frames…
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      <div className="composite-controls">
        <button
          className="button secondary"
          onClick={() => {
            if (frame === result.frame_count - 1) setFrame(0);
            setIsPlaying((value) => !value);
          }}
          disabled={error !== null}
        >
          {isPlaying ? "Pause" : "Play"}
        </button>
        <button
          className="button secondary"
          disabled={frame === 0}
          onClick={() => onSeek(frame - 1)}
          aria-label="Previous frame"
        >
          ←
        </button>
        <button
          className="button secondary"
          disabled={frame === result.frame_count - 1}
          onClick={() => onSeek(frame + 1)}
          aria-label="Next frame"
        >
          →
        </button>
        <span>
          Frame {frame + 1} / {result.frame_count} ·{" "}
          {(frame / result.fps).toFixed(2)}s
        </span>
      </div>
      <input
        className="composite-timeline"
        type="range"
        min={0}
        max={result.frame_count - 1}
        step={1}
        value={frame}
        aria-label="Video timeline"
        onChange={(event) => onSeek(Number(event.target.value))}
      />
      <p className="video-hint">
        Frame-synchronized, silent preview. Playback speed depends on your
        browser and the number of selected people.
      </p>
    </div>
  );
}
