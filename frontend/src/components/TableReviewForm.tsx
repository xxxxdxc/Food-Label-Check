import { useId } from "react";
import { Button, Checkbox, Input, Select } from "antd";
import type { AuditField, ManualRow, ManualTable, TableReviewItem } from "../api/types";
import { NUTRIENTS, TABLE_CODE, reviewReason } from "../lib/labels";
import NutritionPreview from "./NutritionPreview";

export interface TableDraft {
  mode: "pending" | "confirm" | "manual" | "none";
  checked: boolean;
  basis: ManualTable["basis"];
  serving: string;
  rows: ManualRow[];
}
export const emptyTable = (): TableDraft => ({ mode: "pending", checked: false, basis: "per_100g", serving: "", rows: [] });
export const tableKey = (t: TableReviewItem, index: number) => `table:${t.image_id}:${t.table_id}:${t.code}:${index}`;
const number = /^\d+(?:\.\d+)?$/;
export function tableProblems(d: TableDraft): {key: string; message: string}[] {
  if (d.mode !== "manual") return [];
  const errors: {key: string; message: string}[] = [];
  if (!d.rows.length) errors.push({key: "rows", message: "请录入原图中的全部营养项，至少需要一行。"});
  if (d.rows.length > 100) errors.push({key: "rows", message: "营养表最多支持100行。"});
  if (d.basis === "per_serving" && (!number.test(d.serving) || !Number.isFinite(Number(d.serving)) || Number(d.serving) <= 0)) errors.push({key: "serving", message: "每份质量需填写大于0的纯数字，单位为g。"});
  d.rows.forEach((row, i) => {
    const add = (key: string, message: string) => errors.push({key: `row-${i}-${key}`, message: `第${i + 1}行：${message}`});
    if (!row.nutrient) add("nutrient", "请选择营养项。");
    else if (d.rows.some((other, j) => j !== i && other.nutrient === row.nutrient)) add("nutrient", "营养项重复，请检查。");
    if (!number.test(row.amount)) add("amount", "含量需填写非负纯数字，单位请填在右侧，不要包含空格。");
    if (!row.unit.trim() || row.unit.trim().length > 20) add("unit", "请填写单位，最多20个字符。");
    if (row.nrv_percent && !number.test(row.nrv_percent)) add("nrv", "NRV% 只填非负数字，例如10，不要填写%号或空格；原图未标示可留空。");
  });
  return errors;
}
export function tableValid(d: TableDraft) {
  if (d.mode === "pending" || !d.checked) return false;
  return tableProblems(d).length === 0;
}

export default function TableReviewForm({ item, draft, onChange, noTableSupported, onFocus, fields, version }: {
  item: TableReviewItem; draft: TableDraft; onChange: (d: TableDraft) => void; noTableSupported: boolean; onFocus: () => void;
  fields: AuditField[] | null; version: number;
}) {
  const formId = useId();
  const problems = tableProblems(draft);
  const errorId = (key: string) => problems.some(p => p.key === key) ? `${formId}-${key}` : undefined;
  const errorFor = (key: string) => { const problem = problems.find(p => p.key === key); return problem ? <span id={errorId(key)} className="error-text" role="alert">{problem.message}</span> : null; };
  const basis = fields?.find(field => field.field_id.endsWith(".basis"));
  const originalRows = fields?.filter(field => field.field_id.endsWith(".nutrient")).length ?? 0;
  const canConfirm = !!fields?.some(field => field.field_id.endsWith(".nutrient"));
  // Acknowledgement applies to the exact values the reviewer inspected.
  const update = (v: Partial<TableDraft>) => onChange({ ...draft, checked: false, ...v });
  const rowChange = (index: number, patch: Partial<ManualRow>) => update({ rows: draft.rows.map((r, i) => i === index ? { ...r, ...patch } : r) });
  return <article className="field-card table-review" onFocus={onFocus}>
    <h3>{TABLE_CODE[item.code] ?? "营养成分表需人工核对"}</h3><p className="muted">仅异常表需要在此处理。请核对原图中的行列关系，必要时修正或重新录入。</p>
    {item.code === "TABLE_STRUCTURE_UNCERTAIN" && <p className="warn-line small">{item.message.split(/;\s*/).map(reviewReason).join("；")}</p>}
    {item.table_id && (fields?.length ? <NutritionPreview fields={fields} version={version} /> : <p className="warn-line">{fields === null ? "整表预览尚未加载，暂不能确认全部行列。" : "未读取到对应的营养表字段，请按原图手动录入。"}</p>)}
    <label className="form-label">处理方式<Select value={draft.mode} onChange={mode => update({mode, checked: false})} options={[
      { value: "pending", label: "暂不处理" },
      ...(item.table_id ? [{ value: "confirm", label: "表格已完整，确认全部行列", disabled: !canConfirm }] : []),
      { value: "manual", label: "按原图手动录入整表" },
      { value: "none", label: "确认本图没有营养表", disabled: !noTableSupported || !!item.table_id },
    ]} /></label>
    {!noTableSupported && <p className="muted small">当前后端尚不支持“本图无营养表”，该选项暂不可提交，需更新接口后使用。</p>}
    {draft.mode === "manual" && <div className="stack-s">
      <p className="warn-line small">此操作会替换整张营养表，并非只补充修改的几行。只改个别值可使用上方对应字段的“确认值”。</p>
      {basis && <p className="muted small">当前识别表头：{basis.raw ?? "未识别"}。请按原图选择基准，切换基准不会自动换算含量。</p>}
      <label className="form-label">表头基准<Select value={draft.basis} onChange={basis => update({basis})} options={[{value: "per_100g", label: "每100g"}, {value: "per_100ml", label: "每100mL"}, {value: "per_serving", label: "每份"}]} /></label>
      {draft.basis === "per_serving" && <label className="form-label">每份质量（g）<Input aria-label="每份质量（g）" inputMode="decimal" aria-invalid={!!errorId("serving")} aria-describedby={errorId("serving")} status={errorId("serving") ? "error" : undefined} value={draft.serving} onChange={e => update({serving: e.target.value})} />{errorFor("serving")}</label>}
      {draft.rows.map((r, i) => <div className="manual-row" key={i}>
        <label className="form-label">营养项<Select aria-label={`第${i + 1}行营养项`} aria-invalid={!!errorId(`row-${i}-nutrient`)} aria-describedby={errorId(`row-${i}-nutrient`)} status={errorId(`row-${i}-nutrient`) ? "error" : undefined} value={r.nutrient || undefined} placeholder="选择营养项" onChange={nutrient => rowChange(i, {nutrient, unit: NUTRIENTS.find(n => n.key === nutrient)?.unit ?? r.unit})} options={NUTRIENTS.map(n => ({value: n.key, label: n.label}))} /></label>
        <label className="form-label">含量<Input aria-label={`第${i + 1}行含量`} aria-invalid={!!errorId(`row-${i}-amount`)} aria-describedby={errorId(`row-${i}-amount`)} status={errorId(`row-${i}-amount`) ? "error" : undefined} inputMode="decimal" value={r.amount} onChange={e => rowChange(i, {amount: e.target.value})} /></label>
        <label className="form-label">单位<Input aria-label={`第${i + 1}行单位`} aria-invalid={!!errorId(`row-${i}-unit`)} aria-describedby={errorId(`row-${i}-unit`)} status={errorId(`row-${i}-unit`) ? "error" : undefined} value={r.unit} onChange={e => rowChange(i, {unit: e.target.value})} /></label>
        <label className="form-label">NRV%<Input aria-label={`第${i + 1}行NRV%`} aria-invalid={!!errorId(`row-${i}-nrv`)} aria-describedby={errorId(`row-${i}-nrv`)} status={errorId(`row-${i}-nrv`) ? "error" : undefined} inputMode="decimal" placeholder="填10，不填10%" value={r.nrv_percent ?? ""} onChange={e => rowChange(i, {nrv_percent: e.target.value || null})} /></label>
        <Button danger onClick={() => update({rows: draft.rows.filter((_, index) => index !== i)})} aria-label={`删除第${i + 1}行`}>删除</Button>
        {problems.some(p => p.key.startsWith(`row-${i}-`)) && <div className="manual-row-errors">{["nutrient", "amount", "unit", "nrv"].map(part => <div key={part}>{errorFor(`row-${i}-${part}`)}</div>)}</div>}
      </div>)}
      <Button onClick={() => update({ rows: [...draft.rows, { nutrient: "", amount: "", unit: "g", nrv_percent: null }] })}>添加营养项</Button>
      {errorFor("rows")}
      {originalRows > draft.rows.length && <p className="warn-line small">当前识别表有{originalRows}行，录入草稿只有{draft.rows.length}行；请确认没有漏掉原图中的真实营养项。</p>}
      <p className="muted small">含量和 NRV% 只接受非负纯数字。NRV% 请输入10，不要输入10%；原图未标示的 NRV% 留空。请保留全部真实营养项。</p>
    </div>}
    {draft.mode !== "pending" && <Checkbox checked={draft.checked} onChange={e => update({checked: e.target.checked})}>{draft.mode === "none" ? "我已检查原图，确认本图没有营养成分表" : "我已核对全部行列，包括表头基准、含量和 NRV%"}</Checkbox>}
    {draft.mode !== "pending" && draft.checked && problems.length > 0 && <p className="error-text" role="alert">暂不能提交：{problems[0].message}{problems.length > 1 ? ` 另有${problems.length - 1}处，请查看各输入框下的提示。` : ""}</p>}
  </article>;
}
