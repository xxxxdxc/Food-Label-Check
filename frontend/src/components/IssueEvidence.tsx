import type { Evidence } from "../api/types";
import { SOURCE_TYPE } from "../lib/labels";

type Entry = { evidence: Evidence; index: number };

export default function IssueEvidence({ evidence, onLocate }: {
  evidence: Evidence[];
  onLocate: (evidence: Evidence, index: number) => void;
}) {
  const groups: { manual: Entry[]; ocr: Entry[]; region: Entry[] } = { manual: [], ocr: [], region: [] };
  evidence.forEach((entry, index) => {
    const group = entry.source_type === "manual" ? "manual" : entry.source_type === "codex_vision" ? "region" : "ocr";
    groups[group].push({ evidence: entry, index });
  });
  const records = (entries: Entry[]) => entries.map(({ evidence: e, index }) => {
    const canLocate = !!(e.box_px && e.image_id);
    const content = <>
      <strong>{e.source_type === "manual" ? "人工确认" : e.source_type === "codex_vision" && !e.text ? "区域定位" : SOURCE_TYPE[e.source_type] ?? e.source_type}</strong>
      <span>{e.text || (e.source_type === "manual" ? "无文字记录" : "无识别文本")}</span>
      <small>{canLocate ? "点击在左侧原图定位，请以原图文字为准" : e.source_type === "manual" ? "人工确认记录，无独立原图定位框" : "无可用原图定位框"}{e.source_type !== "manual" && e.ocr_confidence != null ? ` · 可信度 ${(e.ocr_confidence * 100).toFixed(1)}%` : ""}</small>
    </>;
    // Keep the original index: ImageViewer keys are derived from the full list.
    return canLocate
      ? <button type="button" className="evidence-text" key={`${e.id}:${index}`} onClick={() => onLocate(e, index)}>{content}</button>
      : <div className="evidence-text" key={`${e.id}:${index}`}>{content}</div>;
  });
  return <section className="issue-evidence stack-s" aria-label="核对记录与原图证据">
    <h3>核对记录与原图证据</h3>
    {!evidence.length && <p className="muted">暂无核对记录或原图证据。</p>}
    {groups.manual.length > 0 && <section className="manual-evidence stack-s" aria-label="人工确认记录">
      <h4>人工确认记录（{groups.manual.length}）</h4>
      {records(groups.manual)}
    </section>}
    {groups.ocr.length > 0 && <details className="ocr-evidence">
      <summary>原始 OCR 记录（{groups.ocr.length}）</summary>
      <div className="stack-s"><p className="muted small">保留用于来源追溯，可能包含表头或整表记录，不代表每条都直接参与本项计算。</p>{records(groups.ocr)}</div>
    </details>}
    {groups.region.length > 0 && <details className="region-evidence">
      <summary>图像识别与区域记录（{groups.region.length}）</summary>
      <div className="stack-s">{records(groups.region)}</div>
    </details>}
  </section>;
}
