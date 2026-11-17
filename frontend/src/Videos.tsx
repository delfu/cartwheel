import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import type { VideoSummary } from "./api";
import { VideoDetail } from "./VideoDetail";

function videoFromHash() {
  const match = /^#\/videos\/([^/]+)$/.exec(window.location.hash);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

export function Videos() {
  const [videoId, setVideoId] = useState(videoFromHash);
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onHashChange = () => setVideoId(videoFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    api
      .videos(controller.signal)
      .then((page) => setVideos(page.items))
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });
    return () => controller.abort();
  }, [videoId]);

  if (videoId) return <VideoDetail key={videoId} videoId={videoId} />;
  return (
    <section>
      <div className="page-heading">
        <h1>
          Your videos<span className="heading-dot">.</span>
        </h1>
        <button
          className="button primary"
          disabled={isUploading}
          onClick={() => input.current?.click()}
        >
          {isUploading ? "Uploading…" : "Upload video"}
        </button>
      </div>
      <input
        ref={input}
        type="file"
        accept="video/mp4,.mp4"
        aria-label="Upload MP4 video"
        hidden
        onChange={async (event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (!file) return;
          setError(null);
          setIsUploading(true);
          try {
            if (file.size > 100 * 1024 * 1024)
              throw new Error("Video exceeds the 100 MiB limit.");
            const video = await api.uploadVideo(file);
            window.location.hash = `/videos/${video.id}`;
          } catch (error) {
            setError(errorMessage(error));
          } finally {
            setIsUploading(false);
          }
        }}
      />
      <p className="video-hint">
        H.264 MP4 · Up to 100 MiB and 10 minutes. The whole video processes
        after uploading.
      </p>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p role="status">Loading videos…</p>
      ) : !videos.length ? (
        <p>No videos uploaded yet.</p>
      ) : (
        <div className="image-grid">
          {videos.map((video) => (
            <a
              className="image-card"
              key={video.id}
              href={`#/videos/${video.id}`}
            >
              <video
                className="video-thumbnail"
                src={video.original_url}
                preload="metadata"
                muted
                playsInline
              />
              <div className="card-info">
                <h2>{video.filename}</h2>
                <p>
                  {video.duration.toFixed(2)} seconds · {video.width} ×{" "}
                  {video.height}
                </p>
              </div>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}
