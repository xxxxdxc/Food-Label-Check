import type { AuditField, ManualRow, ReviewItem, TableReviewItem } from "../api/types";
import { tableProblems } from "../components/TableReviewForm";

export interface NutritionCell { path: string; field: AuditField; pending?: ReviewItem }
export interface NutritionRow { nutrient: NutritionCell; amount?: NutritionCell; nrv?: NutritionCell }
export interface NutritionGroup {
  id: string; index: number; prefix: string; basis: NutritionCell; serving?: NutritionCell;
  rows: NutritionRow[]; pending: ReviewItem[]; diagnostic?: TableReviewItem; imageId: string;
}
export interface CellDraft { value: string; unit: string }
export interface NutritionDraft {
  mode: "fields" | "replace"; checked: boolean; edits: Record<string, CellDraft>;
  replacement?: {basis: string; serving: string; rows: ManualRow[]};
}
export const emptyNutrition = (): NutritionDraft => ({mode: "fields", checked: false, edits: {}});
export const cellDraft = (cell: NutritionCell, draft: NutritionDraft): CellDraft => draft.edits[cell.path] ?? {value: cell.field.value ?? "", unit: cell.field.unit ?? ""};
export const groupCells = (group: NutritionGroup) => [group.basis, ...(group.serving ? [group.serving] : []), ...group.rows.flatMap(r => [r.nutrient, ...(r.amount ? [r.amount] : []), ...(r.nrv ? [r.nrv] : [])])];
const decimal = /^\d+(?:\.\d+)?$/;
const bases = ["per_100g", "per_100ml", "per_serving"];

// Current API projects both lists in document order. Verify every pending path
// against its exact value and evidence, never use a nutrient name as a row key.
export function nutritionGroups(fields: AuditField[], items: ReviewItem[], diagnostics: TableReviewItem[]): NutritionGroup[] {
  const candidates: {id: string; index: number; cells: Map<string, AuditField>; valid: boolean}[] = [];
  let current: typeof candidates[number] | undefined;
  let rowIndex = -1;
  let rowCode = "";
  for (const field of fields.filter(f => f.group === "nutrition")) {
    const basis = field.field_id.match(/^nutrition\.(.+)\.basis$/);
    if (basis) {
      current = {id: basis[1], index: candidates.length, cells: new Map([["basis", field]]), valid: true};
      candidates.push(current); rowIndex = -1; rowCode = ""; continue;
    }
    if (!current) continue;
    const start = `nutrition.${current.id}.`;
    if (!field.field_id.startsWith(start)) { current.valid = false; continue; }
    const suffix = field.field_id.slice(start.length);
    if (suffix === "basis.servingSize") { current.cells.set(suffix, field); continue; }
    const row = suffix.match(/^(.+)\.(nutrient|amount|nrvPercent)$/);
    if (!row) { current.valid = false; continue; }
    if (row[2] === "nutrient") { rowIndex++; rowCode = row[1]; }
    if (rowIndex < 0 || rowCode !== row[1]) { current.valid = false; continue; }
    const key = `rows[${rowIndex}].${row[2]}`;
    if (current.cells.has(key)) current.valid = false;
    current.cells.set(key, field);
  }
  return candidates.flatMap(candidate => {
    const prefix = `facts.nutritionTables[${candidate.index}]`;
    const pending = items.filter(i => i.path.startsWith(prefix + "."));
    const diagnostic = diagnostics.filter(d => d.table_id === candidate.id);
    if (!candidate.valid || diagnostic.length > 1 || (!pending.length && !diagnostic.length)) return [];
    const same = (field: AuditField | undefined, item: ReviewItem) => field && field.raw === item.raw && field.value === item.value && field.unit === item.unit && JSON.stringify(field.evidence.map(e => e.id)) === JSON.stringify(item.evidence.map(e => e.id));
    if (pending.some(item => !same(candidate.cells.get(item.path.slice(prefix.length + 1)), item))) return [];
    // A hidden needs_review cell would make the single acknowledgement incomplete.
    if ([...candidate.cells].some(([key, field]) => field.status === "needs_review" && !pending.some(item => item.path === `${prefix}.${key}`) && key !== "basis")) return [];
    const images = new Set([...candidate.cells.values()].flatMap(f => f.evidence.flatMap(e => e.image_id ? [e.image_id] : [])));
    if (diagnostic[0]?.image_id) images.add(diagnostic[0].image_id);
    if (images.size !== 1) return [];
    const cell = (key: string): NutritionCell | undefined => {
      const field = candidate.cells.get(key); const path = `${prefix}.${key}`;
      return field ? {path, field, pending: pending.find(item => item.path === path)} : undefined;
    };
    const rows: NutritionRow[] = [];
    for (let i = 0; ; i++) {
      const nutrient = cell(`rows[${i}].nutrient`);
      if (!nutrient) break;
      rows.push({nutrient, amount: cell(`rows[${i}].amount`), nrv: cell(`rows[${i}].nrvPercent`)});
    }
    return [{id: candidate.id, index: candidate.index, prefix, basis: cell("basis")!, serving: cell("basis.servingSize"), rows, pending, diagnostic: diagnostic[0], imageId: [...images][0]}];
  });
}

export function replacementOf(group: NutritionGroup, draft: NutritionDraft): NonNullable<NutritionDraft["replacement"]> {
  return {basis: cellDraft(group.basis, draft).value, serving: group.serving ? cellDraft(group.serving, draft).value : "", rows: group.rows.map(row => ({
    nutrient: cellDraft(row.nutrient, draft).value, amount: row.amount ? cellDraft(row.amount, draft).value : "",
    unit: row.amount ? cellDraft(row.amount, draft).unit : "", nrv_percent: row.nrv ? cellDraft(row.nrv, draft).value || null : null,
  }))};
}

export function nutritionProblems(group: NutritionGroup, draft: NutritionDraft): {key: string; message: string}[] {
  if (draft.mode === "replace") {
    const r = draft.replacement ?? replacementOf(group, draft);
    if (!bases.includes(r.basis)) return [{key: "basis", message: "请选择原图的表头基准"}];
    return tableProblems({mode: "manual", checked: draft.checked, basis: r.basis as "per_100g" | "per_100ml" | "per_serving", serving: r.serving, rows: r.rows});
  }
  const errors: {key: string; message: string}[] = [];
  if (!group.rows.length) errors.push({key: "rows", message: "没有可核对的营养项，请调整行结构并按原图补录"});
  const codes = group.rows.map(row => cellDraft(row.nutrient, draft).value);
  group.rows.forEach((row, index) => {
    if (codes[index] && codes[index] !== "unknown" && codes.filter(code => code === codes[index]).length > 1) errors.push({key: row.nutrient.path, message: "营养项重复，请核对名称或调整行结构"});
  });
  for (const cell of groupCells(group)) {
    if (!cell.pending) {
      if (cell.field.status === "missing") errors.push({key: cell.path, message: "本表存在缺失字段，请调整行结构后补录"});
      continue;
    }
    const d = cellDraft(cell, draft);
    const part = cell.path.slice(group.prefix.length + 1);
    let message = "";
    if (part === "basis") {
      if (!bases.includes(d.value)) message = "请选择原图的表头基准";
      else if (d.value === "per_serving" && !group.serving) message = "每份基准缺少份量，请在调整行结构中补录";
    } else if (part.endsWith(".nutrient")) {
      if (!d.value || d.value === "unknown") message = "请选择营养项名称";
    } else if (!decimal.test(d.value)) {
      message = part.endsWith(".nrvPercent") ? "NRV% 只填非负数字，例如10，不要填写%号；若原图未标示，请在调整行结构中留空" : "含量或份量需填写非负纯数字，单位单独填写";
    } else if (part === "basis.servingSize" && Number(d.value) <= 0) message = "每份质量必须大于0";
    else if ((part.endsWith(".amount") || part === "basis.servingSize") && !d.unit.trim()) message = "请填写单位";
    if (message) errors.push({key: cell.path, message});
  }
  return errors;
}
