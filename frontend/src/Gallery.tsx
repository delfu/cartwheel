import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import type { ImageRecord } from "./api";

export function Gallery({
  onUpload,
  upload,
}: {
  onUpload: () => void;
  upload: { current: number; total: number } | null;
}) {
  const [images, setImages] = useState<ImageRecord[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    setError(null);
    api
      .gallery(null, controller.signal)
      .then((page) => {
        setImages(page.items);
        setCursor(page.next_cursor);
        setTotal(page.total);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    const pending = images.filter(
      (image) => image.status === "queued" || image.status === "processing",
    );
    if (!pending.length) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      Promise.all(
        pending.map((image) => api.image(image.id, controller.signal)),
      )
        .then((updated) => {
          const byId = new Map(updated.map((image) => [image.id, image]));
          setImages((previous) =>
            previous.map((image) => byId.get(image.id) ?? image),
          );
        })
        .catch((error) => {
          if (!controller.signal.aborted) setError(errorMessage(error));
        });
    }, 2000);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [images, refresh]);

  const onDelete = async (image: ImageRecord) => {
    if (!window.confirm(`Delete "${image.filename}" and all its masks and cutouts? This cannot be undone.`)) return;
    setDeletingId(image.id);
    setError(null);
    try {
      await api.deleteImage(image.id);
      setImages((previous) => previous.filter((item) => item.id !== image.id));
      setRefresh((value) => value + 1);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setDeletingId(null);
    }
  };

  const onLoadMore = async () => {
    if (!cursor) return;
    setIsLoading(true);
    setError(null);
    try {
      const page = await api.gallery(cursor, new AbortController().signal);
      setImages((previous) => [
        ...previous,
        ...page.items.filter(
          (image) => !previous.some((existing) => existing.id === image.id),
        ),
      ]);
      setCursor(page.next_cursor);
      setTotal(page.total);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <section className="gallery-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">YOUR WORKSPACE</p>
          <h1>
            Your images<span className="heading-dot">.</span>
          </h1>
        </div>
        <div className="gallery-actions">
          <button
            className="button secondary"
            onClick={() => setRefresh((value) => value + 1)}
            disabled={isLoading}
          >
            Refresh gallery
          </button>
          <button
            className="button primary"
            onClick={onUpload}
            disabled={upload !== null}
          >
            <span aria-hidden="true">＋</span>{" "}
            {upload
              ? `Uploading ${upload.current} of ${upload.total}`
              : "Upload images"}
          </button>
        </div>
      </div>
      <div className="section-bar">
        <span>
          Showing {images.length} out of {total} total
        </span>
      </div>
      {error && (
        <div className="error-banner" role="alert">
          <p>{error}</p>
          <button
            className="text-button"
            onClick={() => setRefresh((value) => value + 1)}
          >
            Try again
          </button>
        </div>
      )}
      {isLoading && !images.length ? (
        <div className="empty-state" role="status">
          <span className="spinner" /> <h2>Loading your images</h2>
        </div>
      ) : !images.length && !error ? (
        <div className="empty-state">
          <div className="empty-illustration" aria-hidden="true">
            <span>＋</span>
          </div>
          <h2>A little less background.</h2>
          <p>Upload a photo to find people and create your first cutout.</p>
          <button
            className="button primary"
            onClick={onUpload}
            disabled={upload !== null}
          >
            Upload your first image
          </button>
          <small>PNG or JPEG, up to 25 MiB</small>
        </div>
      ) : (
        <div className="image-grid">
          {images.map((image) => (
            <article className="image-card" key={image.id}>
              <a href={`#/images/${image.id}`} aria-haspopup="dialog">
                <div className="card-image">
                  <img
                    src={image.original_url}
                    alt={image.filename}
                    loading="lazy"
                  />
                  <span
                    className={`status-badge ${image.status === "queued" ? "processing" : image.status}`}
                  >
                    {(image.status === "processing" ||
                      image.status === "queued") && (
                      <span className="status-dot" />
                    )}{" "}
                    {image.status === "ready"
                      ? `${image.people.length} ${image.people.length === 1 ? "person" : "people"} detected`
                      : image.status === "failed"
                        ? "Failed"
                        : "Processing"}
                  </span>
                  <span className="card-open" aria-hidden="true">
                    ↗
                  </span>
                </div>
                <div className="card-info">
                  <h2 title={image.filename}>{image.filename}</h2>
                  <p>
                    <span>
                      {new Date(image.created_at).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                  </p>
                </div>
              </a>
              <button
                className="text-button card-delete"
                aria-label={`Delete ${image.filename}`}
                disabled={isLoading || deletingId !== null}
                onClick={() => onDelete(image)}
              >
                {deletingId === image.id ? "Deleting…" : "Delete"}
              </button>
            </article>
          ))}
        </div>
      )}
      {cursor && (
        <div className="load-more">
          <button
            className="button secondary"
            disabled={isLoading}
            onClick={onLoadMore}
          >
            {isLoading ? "Loading…" : "Load more images"}
          </button>
        </div>
      )}
    </section>
  );
}
