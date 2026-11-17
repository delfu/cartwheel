import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { api, errorMessage } from "./api";
import { Gallery } from "./Gallery";
import { Detail } from "./Detail";

function imageFromHash() {
  const match = /^#\/images\/([^/]+)$/.exec(window.location.hash);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

export function App() {
  const [imageId, setImageId] = useState(imageFromHash);
  const [revision, setRevision] = useState(0);
  const [upload, setUpload] = useState<{
    current: number;
    total: number;
  } | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  const onCloseDetails = () => {
    window.history.replaceState(null, "", "#/");
    setImageId(null);
  };

  useEffect(() => {
    const modal = dialog.current;
    if (!modal || !imageId) return;
    modal.scrollTop = 0;
    modal.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      modal.close();
      document.body.style.overflow = previousOverflow;
    };
  }, [imageId]);

  useEffect(() => {
    const onHashChange = () => setImageId(imageFromHash());
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
        <Gallery
          key={revision}
          onUpload={() => input.current?.click()}
          upload={upload}
        />
      </main>
      <dialog
        ref={dialog}
        className="image-dialog"
        aria-label="Image details"
        onCancel={(event) => {
          event.preventDefault();
          onCloseDetails();
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) onCloseDetails();
        }}
      >
        <div className="modal-content">
          {imageId && (
            <Detail key={imageId} imageId={imageId} onClose={onCloseDetails} />
          )}
        </div>
      </dialog>
    </>
  );
}
