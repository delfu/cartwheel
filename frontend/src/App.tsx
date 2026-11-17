import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { api, errorMessage } from "./api";
import { Gallery } from "./Gallery";
import { Detail } from "./Detail";
import { Videos } from "./Videos";
import { VideoDetail } from "./VideoDetail";

function detailFromHash(
  hash: string,
): { kind: "image" | "video"; id: string } | null {
  const match = /^#\/(images|videos)\/([^/]+)$/.exec(hash);
  if (!match) return null;
  try {
    return {
      kind: match[1] === "images" ? "image" : "video",
      id: decodeURIComponent(match[2]),
    };
  } catch {
    return null;
  }
}

export function App() {
  const [hash, setHash] = useState(window.location.hash);
  const isVideoRoute = hash.startsWith("#/videos");
  const detail = detailFromHash(hash);
  const [revision, setRevision] = useState(0);
  const [upload, setUpload] = useState<{
    current: number;
    total: number;
  } | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  const onCloseDetails = () => {
    const next = detail?.kind === "video" ? "#/videos" : "#/";
    window.history.replaceState(null, "", next);
    setHash(next);
  };

  useEffect(() => {
    const modal = dialog.current;
    if (!modal || !detail) return;
    modal.scrollTop = 0;
    modal.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      modal.close();
      document.body.style.overflow = previousOverflow;
    };
  }, [detail?.kind, detail?.id]);

  useEffect(() => {
    const onHashChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const onFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;
    const failures: string[] = [];
    let uploadedId: string | null = null;
    setUploadError(null);
    for (const [index, file] of files.entries()) {
      setUpload({ current: index + 1, total: files.length });
      try {
        if (file.size > 25 * 1024 * 1024)
          throw new Error("Image exceeds the 25 MiB limit.");
        uploadedId = (await api.upload(file)).id;
      } catch (error) {
        failures.push(`${file.name}: ${errorMessage(error)}`);
      }
    }
    setUpload(null);
    setRevision((value) => value + 1);
    if (failures.length) setUploadError(failures.join(" "));
    if (files.length === 1 && uploadedId)
      window.location.hash = `/images/${uploadedId}`;
    else window.location.hash = "/";
  };

  return (
    <>
      <input
        ref={input}
        type="file"
        multiple
        accept="image/png,image/jpeg,.png,.jpg,.jpeg"
        onChange={onFileChange}
        hidden
        aria-label="Upload PNG or JPEG images"
      />
      <main>
        <nav className="media-tabs" aria-label="Media type">
          <a href="#/" aria-current={!isVideoRoute ? "page" : undefined}>
            Images
          </a>
          <a href="#/videos" aria-current={isVideoRoute ? "page" : undefined}>
            Videos
          </a>
        </nav>
        {upload && (
          <div className="upload-progress" role="status">
            Uploading {upload.current} of {upload.total}. Images will process
            automatically.
          </div>
        )}
        {uploadError && (
          <div className="error-banner" role="alert">
            <p>{uploadError}</p>
            <button
              className="text-button"
              onClick={() => setUploadError(null)}
            >
              Dismiss
            </button>
          </div>
        )}
        {isVideoRoute ? (
          <Videos />
        ) : (
          <Gallery
            key={revision}
            onUpload={() => input.current?.click()}
            upload={upload}
          />
        )}
      </main>
      <dialog
        ref={dialog}
        className="image-dialog"
        aria-label={
          detail?.kind === "video" ? "Video details" : "Image details"
        }
        onCancel={(event) => {
          event.preventDefault();
          onCloseDetails();
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) onCloseDetails();
        }}
      >
        <div className="modal-content">
          {detail?.kind === "image" && (
            <Detail
              key={detail.id}
              imageId={detail.id}
              onClose={onCloseDetails}
            />
          )}
          {detail?.kind === "video" && (
            <VideoDetail
              key={detail.id}
              videoId={detail.id}
              onClose={onCloseDetails}
            />
          )}
        </div>
      </dialog>
    </>
  );
}
