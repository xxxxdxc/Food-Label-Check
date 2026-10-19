import type { AuditField } from "../api/types";
import { FIELD_STATUS, NUTRIENTS } from "../lib/labels";
import { Pill } from "./Common";

export function nutritionFields(fields: AuditField[], tableId: string | null) {
  return tableId ? fields.filter(field => field.field_id.startsWith(`nutrition.${tableId}.`)) : [];
}
const basisName: Record<string, string> = {per_100g: "每100克", per_100ml: "每100毫升", per_serving: "每份", unknown: "基准未识别"};
const content = (field?: AuditField) => field?.value != null ? `${field.value}${field.unit ? ` ${field.unit}` : ""}` : field?.raw || "未识别";

export default function NutritionPreview({ fields, version }: { fields: AuditField[]; version: number }) {
  const basis = fields.find(field => field.field_id.endsWith(".basis"));
  const serving = fields.find(field => field.field_id.endsWith(".basis.servingSize"));
  const rows: {key: string; nutrient?: AuditField; amount?: AuditField; nrvPercent?: AuditField}[] = [];
  for (const field of fields) {
    const match = field.field_id.match(/\.([^.]+)\.(nutrient|amount|nrvPercent)$/);
    if (!match || match[1] === "basis") continue;
    let row = rows.at(-1);
    if (!row || row.key !== match[1] || match[2] === "nutrient") {
      row = {key: match[1]}; rows.push(row);
    }
    row[match[2] as "nutrient" | "amount" | "nrvPercent"] = field;
  }
  const cell = (field?: AuditField) => <td><span title={field?.raw ?? undefined}>{content(field)}</span>{field?.status === "needs_review" && <small className="warn-line">待复核</small>}</td>;
  return <div className="nutrition-preview">
    <div className="inline"><strong>当前识别整表</strong><span className="muted small">已保存版本 {version}</span></div>
    <p className="small">表头：{basis?.raw || basisName[basis?.value ?? "unknown"] || "未识别"}{serving ? ` · 每份质量 ${content(serving)}` : ""}</p>
    <p className="muted small">用于对照原图，尚未提交的字段修改不会显示在此。空缺和待复核标记均按服务端记录展示。</p>
    <div className="nutrition-scroll" role="region" aria-label="当前识别营养表" tabIndex={0}><table>
      <caption className="sr-only">当前识别的营养成分、含量与 NRV%</caption>
      <thead><tr><th scope="col">营养项</th><th scope="col">含量</th><th scope="col">NRV%</th><th scope="col">行状态</th></tr></thead>
      <tbody>{rows.map((row, i) => {
        const values = [row.nutrient, row.amount, row.nrvPercent].filter((field): field is AuditField => !!field);
        const status = values.some(field => field.status === "needs_review") ? "needs_review" : !row.nutrient || !row.amount || values.some(field => field.status === "missing") ? "missing" : values.every(field => field.status === "human_confirmed") ? "human_confirmed" : "ready";
        return <tr key={`${row.key}:${i}`}><th scope="row" title={row.nutrient?.raw ?? undefined}>{NUTRIENTS.find(n => n.key === row.nutrient?.value)?.label || row.nutrient?.raw || row.nutrient?.value || "未识别营养项"}</th>{cell(row.amount)}{row.nrvPercent ? cell(row.nrvPercent) : <td>未标示 / 未识别</td>}<td><Pill tone={FIELD_STATUS[status]} /></td></tr>;
      })}</tbody>
    </table></div>
    {!rows.length && <p className="warn-line">尚未识别到营养项行，请对照原图手动录入。</p>}
  </div>;
}
