import type { AuditFile, BoxPx } from "../api/types";
import type { ViewerBox } from "../components/ImageViewer";

const REGION_LABEL: Record<string, string> = {
  product_name: "商品名称", net_content: "净含量", ingredients: "配料表",
  allergens: "过敏原", nutrition_table: "营养成分表", claims: "包装声称",
  barcode: "条码", other_text: "其他文字",
};
export interface ExtractionImage {
  image: AuditFile;
  mode: "regions" | "whole_image_fallback";
  regions: { label: string; box: ViewerBox }[];
}
const object = (v: unknown): Record<string, unknown> | null => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null;

// extraction is an untyped API dictionary. Only draw valid, original-image boxes.
export function extractionImages(records: unknown[] | undefined, files: AuditFile[]): ExtractionImage[] {
  return (records ?? []).flatMap((record, index) => {
    const row = object(record);
    const image = files.find(file => file.id === row?.image_id);
    if (!image || (row?.mode !== "regions" && row?.mode !== "whole_image_fallback")) return [];
    const regions = row.mode === "regions" && Array.isArray(row.regions) ? row.regions.flatMap((value, regionIndex) => {
      const region = object(value);
      const b = object(region?.box_px);
      if (!b || ![b.left, b.top, b.width, b.height].every(v => typeof v === "number" && Number.isFinite(v))) return [];
      const box = b as unknown as BoxPx;
      if (box.left < 0 || box.top < 0 || box.width <= 0 || box.height <= 0 || box.left + box.width > image.width_px || box.top + box.height > image.height_px) return [];
      const key = `zone:${image.id}:${index}:${regionIndex}`;
      return [{label: REGION_LABEL[String(region?.kind)] ?? "其他区域", box: {key, imageId: image.id, box, kind: "zone" as const, targetId: key}}];
    }) : [];
    return [{image, mode: row.mode, regions}];
  });
}
