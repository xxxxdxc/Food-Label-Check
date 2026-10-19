import { Fragment, useId, useState } from "react";
import { Button, Checkbox, Input } from "antd";
import type { NutritionCell, NutritionDraft, NutritionGroup } from "../lib/nutritionReview";
import { cellDraft, nutritionProblems, replacementOf } from "../lib/nutritionReview";
import { evidenceSummary } from "../lib/evidence";
import { NUTRIENTS, reviewReason, TABLE_CODE } from "../lib/labels";

const basisNames: Record<string, string> = {per_100g: "每100克", per_100ml: "每100毫升", per_serving: "每份"};
export default function NutritionEditor({group, draft, onChange, onLocate, busy}: {
  group: NutritionGroup; draft: NutritionDraft; onChange: (value: NutritionDraft) => void; onLocate: (path: string) => void; busy: boolean;
}) {
  const id = useId();
  const [openRow, setOpenRow] = useState<number | null>(null);
  const problems = nutritionProblems(group, draft);
  const replacement = draft.mode === "replace";
  const data = draft.replacement ?? replacementOf(group, draft);
  const update = (patch: Partial<NutritionDraft>) => onChange({...draft, checked: false, ...patch});
  const edit = (cell: NutritionCell, value: string, unit?: string) => update({edits: {...draft.edits, [cell.path]: {value, unit: unit ?? cellDraft(cell, draft).unit}}});
  const editRow = (i: number, patch: Partial<typeof data.rows[number]>) => update({replacement: {...data, rows: data.rows.map((r, index) => index === i ? {...r, ...patch} : r)}});
  const errorId = (key: string) => problems.some(p => p.key === key) ? `${id}-${key}` : undefined;
  const textInput = (value: string, label: string, key: string, change: (value: string) => void, locate?: string) => <Input aria-label={label} aria-invalid={!!errorId(key)} aria-describedby={errorId(key)} status={errorId(key) ? "error" : undefined} value={value} disabled={busy} inputMode={label.endsWith("单位") ? "text" : "decimal"} placeholder={label.endsWith("NRV%") ? "只填数字" : undefined} onFocus={() => locate && onLocate(locate)} onChange={e => change(e.target.value)} />;
  const nutrientInput = (value: string, label: string, key: string, change: (value: string) => void, locate?: string) => <select className="basis-select" aria-label={label} aria-invalid={!!errorId(key)} aria-describedby={errorId(key)} value={value} disabled={busy} onFocus={() => locate && onLocate(locate)} onChange={e => change(e.target.value)}>
    <option value="">请选择营养项</option>{value && value !== "unknown" && !NUTRIENTS.some(n => n.key === value) && <option value={value}>{value}</option>}{NUTRIENTS.map(n => <option key={n.key} value={n.key}>{n.label}</option>)}
  </select>;
  const proof = (cell: NutritionCell | undefined) => cell ? <div className="cell-proof"><strong>{cell.field.label.startsWith("nutrition.") ? cell.path.endsWith(".nutrient") ? "营养项" : cell.path.endsWith(".nrvPercent") ? "NRV%" : cell.path.endsWith(".basis") ? "表头" : "含量 / 份量" : cell.field.label}</strong><p>OCR 原文：{cell.field.raw ?? "未识别"}</p><p className="muted small">{evidenceSummary(cell.pending?.evidence ?? cell.field.evidence)}</p><p className="warn-line small">{(cell.pending?.review_reasons ?? cell.field.review_reasons).map(reviewReason).join("；")}</p><Button size="small" onClick={() => onLocate(cell.path)}>定位原图</Button></div> : null;
  const basis = replacement ? data.basis : cellDraft(group.basis, draft).value;
  const serving = replacement ? data.serving : group.serving ? cellDraft(group.serving, draft).value : "";
  const rows = replacement ? data.rows.map((row, i) => ({row, original: group.rows[i]})) : group.rows.map(original => ({original, row: {nutrient: cellDraft(original.nutrient, draft).value, amount: original.amount ? cellDraft(original.amount, draft).value : "", unit: original.amount ? cellDraft(original.amount, draft).unit : "", nrv_percent: original.nrv ? cellDraft(original.nrv, draft).value : null}}));
  return <article className="nutrition-editor stack-s" id={`nutrition-group-${group.index}`}>
    <div className="field-heading"><h3>营养表 {group.index + 1}</h3><span className="muted small">{draft.checked ? "已核对，待提交" : `${group.pending.length} 个字段待核对${group.diagnostic ? " · 含结构确认" : ""}`}</span></div>
    {group.diagnostic && <p className="warn-line small">{TABLE_CODE[group.diagnostic.code] ?? "请核对本表行列对应关系"}</p>}
    <p className="muted small">在这一处核对数值与行列，修改后只需确认一次。以下显示当前草稿，尚未提交。</p>
    <div className="nutrition-heading-fields">
      <label className="form-label" id={group.basis.path}>表头基准{replacement || group.basis.pending ? <select className="basis-select" aria-label={`营养表${group.index + 1}表头基准`} value={basis} disabled={busy} onFocus={() => onLocate(group.basis.path)} onChange={e => replacement ? update({replacement: {...data, basis: e.target.value}}) : edit(group.basis, e.target.value)}><option value="unknown">请选择基准</option>{Object.entries(basisNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select> : <strong>{basisNames[basis] ?? "未识别"}</strong>}</label>
      {basis === "per_serving" && <label className="form-label" id={group.serving?.path}>每份质量（g）{replacement ? textInput(serving, `营养表${group.index + 1}每份质量`, "serving", value => update({replacement: {...data, serving: value}})) : group.serving?.pending ? textInput(serving, `营养表${group.index + 1}每份质量`, group.serving.path, value => edit(group.serving!, value), group.serving.path) : <strong>{serving || "未识别"} g</strong>}</label>}
    </div>
    <details className="nutrition-proof"><summary>表头识别依据</summary>{proof(group.basis)}{proof(group.serving)}</details>
    {replacement && <p className="warn-line small">正在调整行结构：已带入当前草稿，提交后替换本表。请保留全部真实营养项；原图未标示的 NRV% 留空。</p>}
    <div className="nutrition-scroll" role="region" aria-label={`营养表${group.index + 1}编辑区`} tabIndex={0}><table className="nutrition-editable">
      <caption className="sr-only">营养表 {group.index + 1} 当前草稿</caption>
      <thead><tr><th scope="col">营养项</th><th scope="col">含量</th><th scope="col">单位</th><th scope="col">NRV%</th><th scope="col">{replacement ? "调整" : "识别依据"}</th></tr></thead>
      <tbody>{rows.map(({row, original}, i) => {
        const label = `营养表${group.index + 1}第${i + 1}行`;
        const pending = (cell: NutritionCell | undefined) => !replacement && !!cell?.pending;
        const rowErrors = problems.filter(p => replacement ? p.key.startsWith(`row-${i}-`) : p.key.startsWith(`${group.prefix}.rows[${i}].`));
        return <Fragment key={i}><tr className="nutrition-data-row">
          <td id={original?.nutrient.path} className={pending(original?.nutrient) ? "cell-pending" : ""}>{replacement ? nutrientInput(row.nutrient, `${label}营养项`, `row-${i}-nutrient`, value => editRow(i, {nutrient: value})) : original.nutrient.pending ? nutrientInput(row.nutrient, `${label}营养项`, original.nutrient.path, value => edit(original.nutrient, value), original.nutrient.path) : NUTRIENTS.find(n => n.key === row.nutrient)?.label ?? original.nutrient.field.raw ?? row.nutrient}</td>
          <td id={original?.amount?.path} className={pending(original?.amount) ? "cell-pending" : ""}>{replacement ? textInput(row.amount, `${label}含量`, `row-${i}-amount`, value => editRow(i, {amount: value})) : original.amount?.pending ? textInput(row.amount, `${label}含量`, original.amount.path, value => edit(original.amount!, value), original.amount.path) : row.amount || "未识别"}</td>
          <td className={pending(original?.amount) ? "cell-pending" : ""}>{replacement ? textInput(row.unit, `${label}单位`, `row-${i}-unit`, value => editRow(i, {unit: value})) : original.amount?.pending ? textInput(row.unit, `${label}单位`, original.amount.path, value => edit(original.amount!, row.amount, value), original.amount.path) : row.unit || "—"}</td>
          <td id={original?.nrv?.path} className={pending(original?.nrv) ? "cell-pending" : ""}>{replacement ? textInput(row.nrv_percent ?? "", `${label}NRV%`, `row-${i}-nrv`, value => editRow(i, {nrv_percent: value || null})) : original.nrv?.pending ? textInput(row.nrv_percent ?? "", `${label}NRV%`, original.nrv.path, value => edit(original.nrv!, value), original.nrv.path) : original.nrv ? row.nrv_percent ? `${row.nrv_percent}%` : "未识别" : "未标示 / 未识别"}</td>
          <td>{replacement ? <Button size="small" danger disabled={busy} aria-label={`删除${label}`} onClick={() => update({replacement: {...data, rows: data.rows.filter((_, index) => index !== i)}})}>删除</Button> : <Button size="small" aria-label={`查看${label}识别依据`} aria-expanded={openRow === i} onClick={() => setOpenRow(openRow === i ? null : i)}>依据</Button>}</td>
        </tr>{rowErrors.length > 0 && <tr><td colSpan={5} className="nutrition-row-errors">{rowErrors.map(p => <p className="error-text small" role="alert" id={`${id}-${p.key}`} key={p.key}>第{i + 1}行：{p.message}</p>)}</td></tr>}{!replacement && openRow === i && <tr><td colSpan={5}><div className="nutrition-proof-grid">{proof(original.nutrient)}{proof(original.amount)}{proof(original.nrv)}</div></td></tr>}</Fragment>;
      })}</tbody>
    </table></div>
    {problems.filter(p => replacement ? !p.key.startsWith("row-") : !p.key.includes(".rows[")).map(p => <p className="error-text small" role="alert" key={p.key} id={`${id}-${p.key}`}>{p.message}</p>)}
    <p className="muted small">含量与 NRV% 只填非负数字，例如10，不填10%。浅色单元格为待核对项；其余数据保留已保存值。</p>
    {group.diagnostic && <div className="inline">{replacement ? <><Button disabled={busy} onClick={() => update({replacement: {...data, rows: [...data.rows, {nutrient: "", amount: "", unit: "g", nrv_percent: null}]}})}>添加营养项</Button><Button disabled={busy} onClick={() => update({mode: "fields", replacement: undefined})}>撤销结构调整</Button></> : <Button disabled={busy} onClick={() => { setOpenRow(null); update({mode: "replace", replacement: replacementOf(group, draft)}); }}>调整行结构</Button>}</div>}
    <Checkbox disabled={busy} checked={draft.checked} onChange={e => onChange({...draft, checked: e.target.checked})}>我已核对本表的数值和行列</Checkbox>
  </article>;
}
