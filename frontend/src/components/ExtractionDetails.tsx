import { Button } from "antd";
import type { ExtractionImage } from "../lib/extraction";

export default function ExtractionDetails({ images, onLocate }: { images: ExtractionImage[]; onLocate: (key: string) => void }) {
  if (!images.length) return null;
  return <details className="extraction-details">
    <summary>识别区域与处理说明</summary>
    <p className="muted small">视觉模型定位区域，OCR 读取文字与数值。分区框仅用于定位，不代表精确文字或单元格边界。</p>
    <div className="stack-s">{images.map((entry, index) => <section className="extraction-image" key={`${entry.image.id}:${index}`}>
      <strong>{entry.image.display_name}</strong>
      {entry.mode === "whole_image_fallback" ? <p className="warn-line">分区未完成，已降级为整图 OCR；没有可展示的分区框。</p> : <>
        <p className="muted small">已按区域识别 · {entry.regions.length} 个可定位区域</p>
        <div className="region-links">{entry.regions.map((region, i) => <Button size="small" key={region.box.key} onClick={() => onLocate(region.box.key)} aria-label={`定位${entry.image.display_name}的${region.label}区域${i + 1}`}>{i + 1}. {region.label}</Button>)}</div>
      </>}
    </section>)}</div>
  </details>;
}
