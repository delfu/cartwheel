import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent, PointerEvent } from "react";

type Viewport = { scale: number; x: number; y: number };
const fitted: Viewport = { scale: 1, x: 0, y: 0 };

function constrain(value: Viewport, viewer: HTMLElement, image: HTMLElement) {
  const limitX = Math.max(
    0,
    (image.clientWidth * value.scale - viewer.clientWidth) / 2,
  );
  const limitY = Math.max(
    0,
    (image.clientHeight * value.scale - viewer.clientHeight) / 2,
  );
  return {
    ...value,
    x: Math.max(-limitX, Math.min(limitX, value.x)),
    y: Math.max(-limitY, Math.min(limitY, value.y)),
  };
}

export function useImageNavigation(isEnabled: boolean) {
  const viewerRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    id: number;
    startX: number;
    startY: number;
    x: number;
    y: number;
    hasMoved: boolean;
  } | null>(null);
  const shouldSuppressClick = useRef(false);
  const [viewport, setViewport] = useState(fitted);
  const [isDragging, setIsDragging] = useState(false);
  const onReset = useCallback(() => setViewport(fitted), []);

  const onZoom = useCallback(
    (factor: number, point?: { x: number; y: number }) => {
      const viewer = viewerRef.current;
      const image = imageRef.current;
      if (!viewer || !image) return;
      const bounds = viewer.getBoundingClientRect();
      const x = point ? point.x - bounds.left - bounds.width / 2 : 0;
      const y = point ? point.y - bounds.top - bounds.height / 2 : 0;
      setViewport((previous) => {
        const scale = Math.max(0.25, Math.min(12, previous.scale * factor));
        const ratio = scale / previous.scale;
        return constrain(
          {
            scale,
            x: x - (x - previous.x) * ratio,
            y: y - (y - previous.y) * ratio,
          },
          viewer,
          image,
        );
      });
    },
    [],
  );

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isEnabled) return;
    const onWheel = (event: WheelEvent) => {
      const image = imageRef.current;
      if (!image) return;
      event.preventDefault();
      const dx =
        event.deltaX *
        (event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? viewer.clientWidth
            : 1);
      const dy =
        event.deltaY *
        (event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? viewer.clientHeight
            : 1);
      if (event.ctrlKey || event.metaKey)
        onZoom(Math.exp(-dy * 0.005), { x: event.clientX, y: event.clientY });
      else
        setViewport((previous) =>
          constrain(
            { ...previous, x: previous.x - dx, y: previous.y - dy },
            viewer,
            image,
          ),
        );
    };
    viewer.addEventListener("wheel", onWheel, { passive: false });
    const observer = new ResizeObserver(() => setViewport(fitted));
    observer.observe(viewer);
    return () => {
      viewer.removeEventListener("wheel", onWheel);
      observer.disconnect();
    };
  }, [isEnabled, onZoom]);

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (
      !event.isPrimary ||
      event.button !== 0 ||
      !imageRef.current ||
      (event.target instanceof Element &&
        event.target.closest("button, a, input"))
    )
      return;
    shouldSuppressClick.current = false;
    drag.current = {
      id: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      x: event.clientX,
      y: event.clientY,
      hasMoved: false,
    };
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    const image = imageRef.current;
    if (!current || current.id !== event.pointerId || !image) return;
    if (event.buttons !== 1) {
      drag.current = null;
      setIsDragging(false);
      return;
    }
    if (
      !current.hasMoved &&
      Math.hypot(
        event.clientX - current.startX,
        event.clientY - current.startY,
      ) < 4
    )
      return;
    event.preventDefault();
    current.hasMoved = true;
    shouldSuppressClick.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    setIsDragging(true);
    const dx = event.clientX - current.x;
    const dy = event.clientY - current.y;
    current.x = event.clientX;
    current.y = event.clientY;
    const viewer = event.currentTarget;
    setViewport((previous) =>
      constrain(
        { ...previous, x: previous.x + dx, y: previous.y + dy },
        viewer,
        image,
      ),
    );
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current = null;
    if (event.type === "pointercancel") shouldSuppressClick.current = false;
    setIsDragging(false);
  };
  const onClickCapture = (event: MouseEvent<HTMLDivElement>) => {
    if (shouldSuppressClick.current && event.detail > 0) {
      event.preventDefault();
      event.stopPropagation();
      shouldSuppressClick.current = false;
    }
  };

  return {
    viewerRef,
    imageRef,
    viewport,
    isDragging,
    onZoom,
    onReset,
    viewerEvents: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onClickCapture,
      onDragStart: (event: MouseEvent<HTMLDivElement>) =>
        event.preventDefault(),
    },
  };
}
