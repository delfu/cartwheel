import type { VideoResult } from "./api";
import { errorMessage } from "./api";
import { createVideoRenderer } from "./videoRenderer";
import type { VideoView } from "./videoRenderer";

type Decoder = {
  video: HTMLVideoElement;
  ready: Promise<void>;
  controller: AbortController;
  callback: number | null;
  frame: number;
};
export type PlaybackState = {
  frame: number;
  isPlaying: boolean;
  isSyncing: boolean;
  error: string | null;
};

function waitForMedia(
  video: HTMLVideoElement,
  event: "loadeddata" | "seeked" | "canplay",
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

export function createVideoPlayback(
  canvas: HTMLCanvasElement,
  result: VideoResult,
  onState: (state: PlaybackState) => void,
) {
  const renderer = createVideoRenderer(canvas);
  const decoders = new Map<string, Decoder>();
  let active: Decoder[] = [];
  let selected: number[] = [];
  let view: VideoView = "original";
  let shouldPlay = false;
  let isSyncing = true;
  let isDisposed = false;
  let frame = 0;
  let desiredTime = 0.25 / result.fps;
  let animation = 0;
  let operation = new AbortController();
  let error: string | null = null;
  let lastDraw = performance.now();
  let lastNotification = 0;
  let lastRenderedFrame = -1;
  let resyncTimes: number[] = [];
  const getRequiredUrls = () => [
    result.playback_url,
    ...(view === "original"
      ? []
      : result.tracks
          .filter((track) => selected.includes(track.track_id))
          .map((track) => track.mask_video_url)),
  ];
  const onNotify = (force = false) => {
    const now = performance.now();
    if (force || now - lastNotification >= 100) {
      lastNotification = now;
      onState({ frame, isPlaying: shouldPlay, isSyncing, error });
    }
  };
  const onPauseVideos = () => {
    for (const entry of decoders.values()) entry.video.pause();
  };
  const onFail = (cause: unknown) => {
    if (isDisposed) return;
    error = errorMessage(cause);
    shouldPlay = false;
    isSyncing = false;
    operation.abort();
    onPauseVideos();
    cancelAnimationFrame(animation);
    onNotify(true);
  };
  const onRelease = (entry: Decoder) => {
    entry.controller.abort();
    entry.video.pause();
    if (entry.callback !== null)
      entry.video.cancelVideoFrameCallback(entry.callback);
    entry.video.removeAttribute("src");
    entry.video.load();
  };
  const onDecoder = (url: string) => {
    const cached = decoders.get(url);
    if (cached) return cached;
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    const controller = new AbortController();
    const ready = waitForMedia(video, "loadeddata", controller.signal);
    // Observe rejection even if a rapid selection change discards this decoder before awaiting it.
    void ready.catch(() => {});
    const entry: Decoder = {
      video,
      ready,
      controller,
      callback: null,
      frame: -1,
    };
    const onFrame: VideoFrameRequestCallback = (_now, metadata) => {
      if (controller.signal.aborted) return;
      entry.frame = Math.round(metadata.mediaTime * result.fps);
      entry.callback = video.requestVideoFrameCallback(onFrame);
    };
    entry.callback = video.requestVideoFrameCallback(onFrame);
    video.addEventListener(
      "error",
      () => {
        if (!controller.signal.aborted)
          onFail(new Error("Video decoding failed. Reload to try again."));
      },
      { signal: controller.signal },
    );
    video.addEventListener(
      "waiting",
      () => {
        if (shouldPlay && !isSyncing) void onSynchronize();
      },
      { signal: controller.signal },
    );
    video.src = url;
    video.load();
    decoders.set(url, entry);
    return entry;
  };
  const onDraw = (force = false) => {
    const original = active[0];
    if (!original || isSyncing) return;
    const next = Math.min(result.frame_count - 1, Math.max(0, original.frame));
    // Keep the last good composite until every selected mask has presented the same frame.
    if (
      active.some(
        (entry) =>
          entry.frame !== original.frame ||
          entry.video.seeking ||
          entry.video.readyState < 2,
      )
    )
      return;
    if (!force && next === lastRenderedFrame) return;
    renderer.onDraw(
      original.video,
      active.slice(1).map((entry) => entry.video),
      view,
      next,
    );
    frame = next;
    lastRenderedFrame = next;
    lastDraw = performance.now();
    onNotify(force);
  };
  const onTick = () => {
    if (isDisposed || !shouldPlay || isSyncing) return;
    try {
      onDraw();
      const original = active[0];
      if (original?.video.ended) {
        shouldPlay = false;
        desiredTime = (result.frame_count - 0.75) / result.fps;
        isSyncing = true;
        void onSynchronize();
        return;
      }
      const drift = active.some(
        (entry) =>
          original &&
          Math.abs(entry.video.currentTime - original.video.currentTime) >
            2 / result.fps,
      );
      if (drift || performance.now() - lastDraw > 500) {
        const now = performance.now();
        resyncTimes = resyncTimes.filter((time) => now - time < 5000);
        if (resyncTimes.length >= 3) {
          onFail(
            new Error(
              "These video streams cannot stay synchronized. Select fewer people and try again.",
            ),
          );
          return;
        }
        resyncTimes.push(now);
        void onSynchronize();
        return;
      }
      animation = requestAnimationFrame(onTick);
    } catch (cause) {
      onFail(cause);
    }
  };
  const onSynchronize = async () => {
    if (isDisposed) return;
    if (!isSyncing && active[0]) desiredTime = active[0].video.currentTime;
    isSyncing = true;
    operation.abort();
    operation = new AbortController();
    const signal = operation.signal;
    cancelAnimationFrame(animation);
    onPauseVideos();
    onNotify(true);
    try {
      const urls = getRequiredUrls();
      for (const [url, entry] of decoders)
        if (!urls.includes(url)) {
          onRelease(entry);
          decoders.delete(url);
        }
      active = urls.map(onDecoder);
      const current = active;
      await Promise.all(current.map((entry) => entry.ready));
      if (signal.aborted) return;
      const targetFrame = Math.min(
        result.frame_count - 1,
        Math.max(0, Math.floor(desiredTime * result.fps)),
      );
      const targetTime = (targetFrame + 0.25) / result.fps;
      await Promise.all(
        current.map((entry) => seekVideo(entry.video, targetTime, signal)),
      );
      if (signal.aborted) return;
      for (const entry of current) entry.frame = targetFrame;
      renderer.onKeepVideos(current.map((entry) => entry.video));
      isSyncing = false;
      lastRenderedFrame = -1;
      onDraw(true);
      if (shouldPlay) {
        await Promise.all(
          current.map((entry) =>
            entry.video.readyState >= 3
              ? Promise.resolve()
              : waitForMedia(entry.video, "canplay", signal),
          ),
        );
        if (signal.aborted) return;
        await Promise.all(current.map((entry) => entry.video.play()));
        if (signal.aborted) return;
        lastDraw = performance.now();
        animation = requestAnimationFrame(onTick);
      }
      onNotify(true);
    } catch (cause) {
      if (!signal.aborted) onFail(cause);
    }
  };
  return {
    onConfigure(next: number[], nextView: VideoView) {
      selected = next;
      view = nextView;
      error = null;
      resyncTimes = [];
      const urls = getRequiredUrls();
      const ready = urls.flatMap((url) => {
        const entry = decoders.get(url);
        return entry && entry.video.readyState >= 2 && !entry.video.seeking
          ? [entry]
          : [];
      });
      if (
        !isSyncing &&
        ready.length === urls.length &&
        (shouldPlay || ready.every((entry) => entry.frame === ready[0]?.frame))
      ) {
        for (const [url, entry] of decoders)
          if (!urls.includes(url)) {
            onRelease(entry);
            decoders.delete(url);
          }
        active = ready;
        renderer.onKeepVideos(active.map((entry) => entry.video));
        lastRenderedFrame = -1;
        try {
          onDraw(true);
          onNotify(true);
        } catch (cause) {
          onFail(cause);
        }
      } else void onSynchronize();
    },
    onTogglePlay() {
      error = null;
      if (shouldPlay) {
        shouldPlay = false;
        desiredTime = (frame + 0.25) / result.fps;
        isSyncing = true;
        void onSynchronize();
      } else {
        shouldPlay = true;
        resyncTimes = [];
        if (frame >= result.frame_count - 1) {
          desiredTime = 0.25 / result.fps;
          isSyncing = true;
        }
        void onSynchronize();
      }
    },
    onSeek(next: number) {
      shouldPlay = false;
      frame = Math.max(0, Math.min(result.frame_count - 1, next));
      desiredTime = (frame + 0.25) / result.fps;
      isSyncing = true;
      error = null;
      resyncTimes = [];
      void onSynchronize();
    },
    onDispose() {
      isDisposed = true;
      shouldPlay = false;
      operation.abort();
      cancelAnimationFrame(animation);
      for (const entry of decoders.values()) onRelease(entry);
      decoders.clear();
      renderer.onDispose();
    },
  };
}
