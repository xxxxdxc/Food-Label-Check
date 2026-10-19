import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Button, Segmented } from "antd";
import { MinusOutlined, PlusOutlined } from "@ant-design/icons";
import type { BoxPx } from "../api/types";

export interface ViewerImage {
  id: string;
  url: string;
  width: number;
  height: number;
  name: string;
}

export interface ViewerBox {
  key: string;
  imageId: string | null;
  box: BoxPx;
  // ocr = OCR 精确定位；vision = AI 估计位置（虚线）；region = 整表/整图区域
  kind: "ocr" | "vision" | "region" | "zone";
  targetId: string;
  tone?: "warn" | "fail" | "ok" | "neutral";
}

interface Props {
  images: ViewerImage[];
  boxes: ViewerBox[];
  activeTarget: string | null;
  onSelect?: (targetId: string) => void;
  caption?: ReactNode;
  focusBoxKey?: string | null;
  zones?: ViewerBox[];
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 8;

// 原图 + SVG 叠加层。SVG 的 viewBox 就是原图像素尺寸，后端的 box_px 原样使用，不做坐标换算。
export default function ImageViewer({ images, boxes, activeTarget, onSelect, caption, focusBoxKey, zones = [] }: Props) {
  const [imageId, setImageId] = useState(images[0]?.id ?? "");
  const [zoom, setZoom] = useState(1);
  const [showBoxes, setShowBoxes] = useState(true);
  const [showZones, setShowZones] = useState(false);
  const [, setTick] = useState(0);
  const [size, setSize] = useState({ w: 600, h: 480 });
  const viewport = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(1);
  const pendingCenter = useRef<BoxPx | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef({ moved: 0, pinch: 0 });

  const image = images.find((i) => i.id === imageId) ?? images[0];
  zoomRef.current = zoom;

  useEffect(() => {
    if (images.length && !images.some((i) => i.id === imageId)) setImageId(images[0].id);
  }, [images, imageId]);

  useLayoutEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const fit = image ? Math.min(size.w / image.width, size.h / image.height) : 1;
  const scale = fit * zoom;

  const zoomTo = useCallback((next: number, clientX?: number, clientY?: number) => {
    const el = viewport.current;
    if (!el) return;
    const target = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
    const rect = el.getBoundingClientRect();
    const ax = (clientX ?? rect.left + el.clientWidth / 2) - rect.left;
    const ay = (clientY ?? rect.top + el.clientHeight / 2) - rect.top;
    const ratio = target / zoomRef.current;
    const left = (el.scrollLeft + ax) * ratio - ax;
    const top = (el.scrollTop + ay) * ratio - ay;
    zoomRef.current = target;
    setZoom(target);
    requestAnimationFrame(() => {
      el.scrollLeft = left;
      el.scrollTop = top;
    });
  }, []);

  // 选中字段或问题时：切到对应图片，小目标自动放大，并在下一次布局后居中。
  useEffect(() => {
    if (!activeTarget) return;
    const all = [...boxes, ...zones];
    const hit = all.find(b => b.key === focusBoxKey && b.targetId === activeTarget) ?? all.find((b) => b.targetId === activeTarget && images.some((i) => i.id === b.imageId));
    if (!hit) return;
    if (hit.kind === "zone") { setShowZones(true); setShowBoxes(true); }
    const img = images.find((i) => i.id === hit.imageId)!;
    setImageId(img.id);
    const coverage = (hit.box.width * hit.box.height) / (img.width * img.height);
    if (coverage < 0.25) setZoom((z) => Math.max(z, 2.5));
    pendingCenter.current = hit.box;
    setTick((t) => t + 1);
    // 只在切换选中项时触发；数据刷新不打断用户当前视野。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTarget, focusBoxKey]);

  useLayoutEffect(() => {
    const el = viewport.current;
    const box = pendingCenter.current;
    if (!el || !box) return;
    pendingCenter.current = null;
    el.scrollLeft = (box.left + box.width / 2) * scale - el.clientWidth / 2;
    el.scrollTop = (box.top + box.height / 2) * scale - el.clientHeight / 2;
  });

  // 只有按住 Ctrl 时滚轮才缩放，避免鼠标停在图上时页面无法滚动。
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      zoomTo(zoomRef.current * (event.deltaY < 0 ? 1.2 : 1 / 1.2), event.clientX, event.clientY);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomTo]);

  const distance = () => {
    const [a, b] = [...pointers.current.values()];
    return Math.hypot(a.x - b.x, a.y - b.y);
  };

  const onPointerDown = (event: React.PointerEvent) => {
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    gesture.current.moved = 0;
    if (pointers.current.size === 2) gesture.current.pinch = distance();
  };
  const onPointerMove = (event: React.PointerEvent) => {
    const previous = pointers.current.get(event.pointerId);
    const el = viewport.current;
    if (!previous || !el) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.current.size === 1) {
      const dx = event.clientX - previous.x;
      const dy = event.clientY - previous.y;
      el.scrollLeft -= dx;
      el.scrollTop -= dy;
      gesture.current.moved += Math.abs(dx) + Math.abs(dy);
    } else if (pointers.current.size === 2) {
      const now = distance();
      const [a, b] = [...pointers.current.values()];
      if (gesture.current.pinch > 0) zoomTo(zoomRef.current * (now / gesture.current.pinch), (a.x + b.x) / 2, (a.y + b.y) / 2);
      gesture.current.pinch = now;
      gesture.current.moved += 10;
    }
  };
  const onPointerEnd = (event: React.PointerEvent) => {
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) gesture.current.pinch = 0;
  };

  const visible = image ? [...boxes, ...(showZones ? zones : [])].filter((b) => b.imageId === image.id) : [];
  // Several fields can share the same whole-table evidence. Draw each geometry
  // once, preferring the active field, rather than stacking identical overlays.
  const unique = new Map<string, ViewerBox>();
  for (const b of visible) {
    const key = `${b.kind}:${b.box.left}:${b.box.top}:${b.box.width}:${b.box.height}`;
    if (!unique.has(key) || b.targetId === activeTarget) unique.set(key, b);
  }
  const distinct = [...unique.values()];
  const ordered = [...distinct.filter((b) => b.targetId !== activeTarget), ...distinct.filter((b) => b.targetId === activeTarget)];

  return (
    <div className="viewer">
      <div className="viewer-bar">
        {images.length > 1 && (
          <Segmented
            size="small"
            value={image?.id}
            onChange={(value) => setImageId(String(value))}
            options={images.map((img, index) => ({ label: `图 ${index + 1}`, value: img.id, title: img.name }))}
          />
        )}
        <div className="viewer-zoom">
          <Button size="small" icon={<MinusOutlined />} aria-label="缩小" onClick={() => zoomTo(zoom / 1.25)} disabled={zoom <= MIN_ZOOM} />
          <span className="viewer-percent" aria-live="polite">{Math.round(zoom * 100)}%</span>
          <Button size="small" icon={<PlusOutlined />} aria-label="放大" onClick={() => zoomTo(zoom * 1.25)} disabled={zoom >= MAX_ZOOM} />
          <Button size="small" onClick={() => zoomTo(1)}>适应整图</Button>
          <Button size="small" aria-pressed={showBoxes} onClick={() => setShowBoxes(value => !value)}>{showBoxes ? "隐藏标记" : "显示标记"}</Button>
          {zones.length > 0 && <Button size="small" aria-pressed={showZones} onClick={() => { setShowZones(value => !value); setShowBoxes(true); }}>{showZones ? "隐藏分区" : "显示分区"}</Button>}
        </div>
      </div>
      {caption && <div className="viewer-caption">{caption}</div>}
      <div
        ref={viewport}
        className="viewer-viewport"
        style={{ touchAction: zoom > 1 ? "none" : "pan-y" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        onPointerLeave={onPointerEnd}
      >
        {image ? (
          <div className="viewer-stage" style={{ width: image.width * scale, height: image.height * scale }}>
            <img src={image.url} alt={image.name} draggable={false} />
            <svg viewBox={`0 0 ${image.width} ${image.height}`} preserveAspectRatio="none" aria-hidden="true">
              {showBoxes && ordered.map((b) => (
                <rect
                  key={b.key}
                  x={b.box.left}
                  y={b.box.top}
                  width={b.box.width}
                  height={b.box.height}
                  vectorEffect="non-scaling-stroke"
                  className={[
                    "box",
                    `box-${b.kind}`,
                    `tone-${b.tone ?? "neutral"}`,
                    b.targetId === activeTarget ? "box-active" : "",
                  ].join(" ")}
                  onClick={() => {
                    if (gesture.current.moved > 6) return;
                    onSelect?.(b.targetId);
                  }}
                />
              ))}
            </svg>
          </div>
        ) : (
          <div className="viewer-empty">暂无图片</div>
        )}
      </div>
      <div className="viewer-legend">
        {showBoxes && visible.some(b => b.kind === "ocr") && <span><i className="legend-line" />OCR 定位</span>}
        {showBoxes && visible.some(b => b.kind === "vision") && <span><i className="legend-line dashed" />图像区域定位</span>}
        {showBoxes && visible.some(b => b.kind === "region") && <span><i className="legend-line dotted" />整表/整图区域</span>}
        {showBoxes && visible.some(b => b.kind === "zone") && <span><i className="legend-line zone" />VLM 分区</span>}
        <span className="viewer-hint">拖动平移 · Ctrl+滚轮或双指缩放 · 点击框可选中</span>
      </div>
    </div>
  );
}
