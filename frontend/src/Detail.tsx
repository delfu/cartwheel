import { useEffect, useState } from "react";
import { api, errorMessage, statusLabel } from "./api";
import type { ImageRecord } from "./api";

type View = "original" | "mask" | "isolated";
type Assets =
  | { kind: "idle" | "loading" }
  | { kind: "ready"; mask: string; isolated: string }
  | { kind: "error"; message: string };
const colors = ["#9f8cff", "#56d8b3", "#ffb76b", "#63c6ff", "#f68bb8"];
const views: { key: View; label: string }[] = [
  { key: "original", label: "Original" },
  { key: "mask", label: "Mask" },
  { key: "isolated", label: "Cutout" },
];

function peoplePageSize() {
  if (window.innerHeight < 600) return 1;
  if (window.innerWidth <= 760) return window.innerHeight < 740 ? 1 : 2;
  return window.innerHeight < 800 ? 2 : 4;
}

export function Detail({ imageId }: { imageId: string }) {
  const [image, setImage] = useState<ImageRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [isRetrying, setIsRetrying] = useState(false);
  const [chosen, setChosen] = useState<number[] | null>(null);
  const [view, setView] = useState<View>("original");
  const [peoplePerPage, setPeoplePerPage] = useState(peoplePageSize);
  const [personPage, setPersonPage] = useState(0);
  const [assets, setAssets] = useState<Assets>({ kind: "idle" });
  const [hasImageError, setHasImageError] = useState(false);
  const selected = chosen ?? image?.people.map((person) => person.index) ?? [];
  const selectionKey = [...selected].sort((a, b) => a - b).join(",");
  const hasAllSelected =
    image !== null && selected.length === image.people.length;
  const personPageCount = Math.max(
    1,
    Math.ceil((image?.people.length ?? 0) / peoplePerPage),
  );
  const currentPersonPage = Math.min(personPage, personPageCount - 1);

  useEffect(() => {
    const onResize = () => setPeoplePerPage(peoplePageSize());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const onLoad = async () => {
      try {
        const updated = await api.image(imageId, controller.signal);
        setImage(updated);
        setError(null);
        if (updated.status === "queued" || updated.status === "processing")
          timer = window.setTimeout(onLoad, 2000);
      } catch (error) {
        if (!controller.signal.aborted) setError(errorMessage(error));
      }
    };
    void onLoad();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [imageId, refresh]);

  useEffect(() => {
    setHasImageError(false);
    if (!image || image.status !== "ready") {
      setAssets({ kind: "idle" });
      return;
    }
    if (hasAllSelected && image.mask_url && image.isolated_url) {
      setAssets({
        kind: "ready",
        mask: image.mask_url,
        isolated: image.isolated_url,
      });
      return;
    }
    const controller = new AbortController();
    let urls: string[] = [];
    setAssets({ kind: "loading" });
    const indices = selectionKey ? selectionKey.split(",").map(Number) : [];
    Promise.all([
      api.selection(image.id, indices, "mask", controller.signal),
      api.selection(image.id, indices, "isolated", controller.signal),
    ])
      .then(([mask, isolated]) => {
        if (controller.signal.aborted) return;
        urls = [URL.createObjectURL(mask), URL.createObjectURL(isolated)];
        setAssets({ kind: "ready", mask: urls[0], isolated: urls[1] });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setAssets({ kind: "error", message: errorMessage(error) });
      });
    return () => {
      controller.abort();
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [image, hasAllSelected, selectionKey]);

  const onTogglePerson = (index: number) =>
    setChosen((previous) => {
      const current =
        previous ?? image?.people.map((person) => person.index) ?? [];
      return current.includes(index)
        ? current.filter((value) => value !== index)
        : [...current, index];
    });
  const onRetry = async () => {
    if (!image) return;
    setIsRetrying(true);
    setError(null);
    try {
      await api.retry(image.job.id);
      setRefresh((value) => value + 1);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setIsRetrying(false);
    }
  };

  if (!image)
    return (
      <section className="detail-page">
        {error ? (
          <div className="empty-state">
            <h1>Couldn't load this image</h1>
            <p role="alert">{error}</p>
            <button
              className="button secondary"
              onClick={() => setRefresh((value) => value + 1)}
            >
              Try again
            </button>
          </div>
        ) : (
          <div className="empty-state" role="status">
            <span className="spinner" />
            <h2>Loading image</h2>
          </div>
        )}
      </section>
    );

  const isReady = image.status === "ready";
  const asset =
    view === "original"
      ? image.original_url
      : assets.kind === "ready"
        ? assets[view]
        : null;
  const onChangeView = (next: View) => {
    setView(next);
    setHasImageError(false);
  };
  return (
    <section className="detail-page">
      <div className="detail-heading">
        <div>
          <h1 title={image.filename}>{image.filename}</h1>
          <p className="subheading">
            {image.width.toLocaleString()} × {image.height.toLocaleString()} px{" "}
            <span className="inline-separator">/</span>{" "}
            <span className={`inline-status ${image.status}`}>
              {statusLabel(image.status)}
            </span>
          </p>
        </div>
        <a
          className={`button primary ${assets.kind !== "ready" ? "is-disabled" : ""}`}
          href={assets.kind === "ready" ? assets.isolated : undefined}
          aria-disabled={assets.kind !== "ready"}
          download={`${image.filename.replace(/\.[^.]+$/, "")}-cutout.png`}
        >
          Download cutout <span aria-hidden="true">↓</span>
        </a>
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
      <div className="editor-layout">
        <div className="editor-main">
          <div className="viewer-toolbar">
            <div className="view-options" aria-label="Image view">
              {views.map((option) => (
                <button
                  key={option.key}
                  className={view === option.key ? "is-selected" : ""}
                  aria-pressed={view === option.key}
                  disabled={!isReady && option.key !== "original"}
                  onClick={() => onChangeView(option.key)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <div
            className={`viewer ${view === "isolated" ? "checkerboard" : ""}`}
          >
            <div className="viewer-inner">
              {asset ? (
                <div
                  className="image-plane"
                  style={{
                    width: `min(100cqw, ${(100 * image.width) / image.height}cqh)`,
                    aspectRatio: `${image.width}/${image.height}`,
                  }}
                >
                  <img
                    key={`${asset}:${refresh}`}
                    src={asset}
                    alt={`${view === "original" ? "Original image" : view === "mask" ? "Mask of selected people" : "Cutout of selected people"}: ${image.filename}`}
                    onError={() => setHasImageError(true)}
                  />
                  {view === "original" && isReady && (
                    <svg
                      viewBox={`0 0 ${image.width} ${image.height}`}
                      className="selection-overlay"
                      aria-label="Detected people"
                    >
                      <defs>
                        {image.people.map((person) => (
                          <mask
                            key={person.index}
                            id={`person-${image.id}-${person.index}`}
                            maskUnits="userSpaceOnUse"
                            x="0"
                            y="0"
                            width={image.width}
                            height={image.height}
                            style={{ maskType: "luminance" }}
                          >
                            <image
                              href={person.mask_url}
                              width={image.width}
                              height={image.height}
                            />
                          </mask>
                        ))}
                      </defs>
                      {image.people.map((person) => {
                        const isSelected = selected.includes(person.index);
                        const color =
                          colors[(person.index - 1) % colors.length];
                        return (
                          <g key={person.index}>
                            {isSelected && (
                              <rect
                                width={image.width}
                                height={image.height}
                                fill={color}
                                opacity="0.24"
                                mask={`url(#person-${image.id}-${person.index})`}
                                pointerEvents="none"
                              />
                            )}
                            <rect
                              x={person.bbox.x}
                              y={person.bbox.y}
                              width={person.bbox.width}
                              height={person.bbox.height}
                              fill="transparent"
                              stroke={isSelected ? color : "#ffffff"}
                              strokeWidth={image.width / 500}
                              strokeDasharray={
                                isSelected ? undefined : `${image.width / 150}`
                              }
                              role="button"
                              tabIndex={0}
                              aria-label={`Person ${person.index}`}
                              aria-pressed={isSelected}
                              onClick={() => onTogglePerson(person.index)}
                              onKeyDown={(event) => {
                                if (
                                  event.key === "Enter" ||
                                  event.key === " "
                                ) {
                                  event.preventDefault();
                                  onTogglePerson(person.index);
                                }
                              }}
                            />
                          </g>
                        );
                      })}
                    </svg>
                  )}
                </div>
              ) : (
                <div
                  className="viewer-message"
                  role={assets.kind === "error" ? "alert" : "status"}
                >
                  {assets.kind !== "error" && <span className="spinner" />}
                  <p>
                    {assets.kind === "error"
                      ? assets.message
                      : "Preparing your selection…"}
                  </p>
                  {assets.kind === "error" && (
                    <button
                      className="button secondary"
                      onClick={() => setRefresh((value) => value + 1)}
                    >
                      Try again
                    </button>
                  )}
                </div>
              )}
            </div>
            {hasImageError && (
              <div className="viewer-error" role="alert">
                Couldn't load the image.{" "}
                <button onClick={() => setRefresh((value) => value + 1)}>
                  Try again
                </button>
              </div>
            )}
          </div>
          <div className="viewer-caption">
            <span>
              {view === "original"
                ? "Click a person or use the list to change your selection."
                : view === "mask"
                  ? "White pixels are kept. Black pixels are removed."
                  : "The checkerboard shows transparent areas."}
            </span>
            <span>
              {isReady
                ? `${selected.length} selected`
                : statusLabel(image.status)}
            </span>
          </div>
          {assets.kind === "error" && view === "original" && (
            <div className="error-banner" role="alert">
              <p>{assets.message}</p>
              <button
                className="text-button"
                onClick={() => setRefresh((value) => value + 1)}
              >
                Try again
              </button>
            </div>
          )}
        </div>
        <aside className="people-panel" aria-label="Person selection">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">MAKE IT YOURS</p>
              <h2>
                People <span>{image.people.length}</span>
              </h2>
            </div>
          </div>
          {isReady ? (
            <>
              <p className="panel-description">
                Select the people you want to keep in your image.
              </p>
              {image.people.length > 0 ? (
                <>
                  <div className="selection-actions">
                    <button
                      className="text-button"
                      onClick={() =>
                        setChosen(image.people.map((person) => person.index))
                      }
                      disabled={hasAllSelected}
                    >
                      Select all
                    </button>
                    <button
                      className="text-button"
                      onClick={() => setChosen([])}
                      disabled={selected.length === 0}
                    >
                      Clear selection
                    </button>
                  </div>
                  <div className="person-list">
                    {image.people
                      .slice(
                        currentPersonPage * peoplePerPage,
                        (currentPersonPage + 1) * peoplePerPage,
                      )
                      .map((person) => (
                        <label
                          className={`person-row ${selected.includes(person.index) ? "is-selected" : ""}`}
                          key={person.index}
                        >
                          <input
                            type="checkbox"
                            checked={selected.includes(person.index)}
                            onChange={() => onTogglePerson(person.index)}
                          />
                          <span className="person-thumbnail checkerboard">
                            <svg
                              aria-hidden="true"
                              viewBox={`${person.bbox.x} ${person.bbox.y} ${person.bbox.width} ${person.bbox.height}`}
                            >
                              <image
                                href={person.isolated_url}
                                width={image.width}
                                height={image.height}
                              />
                            </svg>
                          </span>
                          <span className="person-copy">
                            <strong>Person {person.index}</strong>
                            <span>
                              {Math.round(person.confidence * 100)}% confidence
                            </span>
                          </span>
                          <span
                            className="person-color"
                            style={{
                              background:
                                colors[(person.index - 1) % colors.length],
                            }}
                            aria-hidden="true"
                          />
                        </label>
                      ))}
                  </div>
                  {personPageCount > 1 && (
                    <nav
                      className="person-pagination"
                      aria-label="People pages"
                    >
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
                      {selected.length} of {image.people.length} selected
                    </strong>
                    <span>
                      {selected.length
                        ? "Your mask and cutout update automatically."
                        : "Select a person to include them in your cutout."}
                    </span>
                  </div>
                </>
              ) : (
                <div className="panel-state">
                  <h3>No people detected</h3>
                  <p>Try another photo with people more clearly visible.</p>
                </div>
              )}
            </>
          ) : image.status === "failed" ? (
            <div className="panel-state">
              <span className="state-symbol" aria-hidden="true">
                !
              </span>
              <h3>Processing failed</h3>
              <p>{image.job.error ?? "The image could not be processed."}</p>
              <button
                className="button secondary"
                onClick={onRetry}
                disabled={isRetrying}
              >
                {isRetrying ? "Retrying…" : "Retry processing"}
              </button>
            </div>
          ) : (
            <div className="panel-state" role="status">
              <span className="spinner" />
              <h3>
                {image.status === "queued"
                  ? "Waiting to process"
                  : "Finding people…"}
              </h3>
              <p>
                {image.status === "queued"
                  ? "Your image is in the queue. Results will appear here automatically."
                  : "We’re creating a separate mask for each detected person."}
              </p>
              <small>
                Attempt {image.job.attempts || 1} of {image.job.max_attempts}
              </small>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
