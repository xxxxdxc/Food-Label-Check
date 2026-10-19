import type { AuditFile, Evidence } from "../api/types";
import type { ViewerBox } from "../components/ImageViewer";
import { SOURCE_TYPE } from "./labels";

export const viewerImages = (files: AuditFile[]) => files.map(f => ({ id: f.id, url: f.url, width: f.width_px, height: f.height_px, name: f.display_name }));
export function evidenceBoxes(evidence: Evidence[], targetId: string, tone: ViewerBox["tone"] = "neutral"): ViewerBox[] {
  return evidence.flatMap((e, i) => e.box_px ? [{ key: `${targetId}:${e.id}:${i}`, imageId: e.image_id, box: e.box_px,
    kind: e.source_type === "codex_vision" ? (e.text ? "vision" as const : "region" as const) : "ocr" as const, targetId, tone }] : []);
}

export function evidenceSummary(evidence: Evidence[]): string {
  const groups = new Map<string, Evidence[]>();
  for (const entry of evidence) {
    const group = groups.get(entry.source_type) ?? [];
    if (!group.some(e => e.id === entry.id)) group.push(entry);
    groups.set(entry.source_type, group);
  }
  return [...groups].map(([source, entries]) => {
    const name = SOURCE_TYPE[source] ?? source;
    if (source !== "baidu_accurate" && source !== "baidu_table_v2") return name;
    const scores = entries.flatMap(e => e.ocr_confidence == null ? [] : [e.ocr_confidence * 100]);
    const min = Math.min(...scores).toFixed(1), max = Math.max(...scores).toFixed(1);
    const range = scores.length ? `可信度 ${min === max ? min : `${min}–${max}`}%` : "未提供可信度";
    return `${name} · ${range}${scores.length && scores.length < entries.length ? "，部分未提供可信度" : ""}`;
  }).join("；") || "无法定位到原图";
}
