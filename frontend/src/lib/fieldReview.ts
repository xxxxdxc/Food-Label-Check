import type { ReviewItem } from "../api/types";

export interface FieldDraft {value: string; unit: string; checked: boolean}
export const NET_UNITS = [
  {value: "g", label: "g（克）"}, {value: "kg", label: "kg（千克）"},
  {value: "mg", label: "mg（毫克）"}, {value: "µg", label: "µg（微克）"},
  {value: "mL", label: "mL（毫升）"}, {value: "L", label: "L（升）"},
  {value: "piece", label: "piece（个 / 片）"}, {value: "bag", label: "bag（袋）"},
];
const decimal = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;
const sameNumber = (a: string, b: string) => {
  const canonical = (s: string) => s.replace(/^0+(?=\d)/, "").replace(/\.0+$/, "").replace(/(\.\d*?[1-9])0+$/, "$1");
  return canonical(a) === canonical(b);
};

// Detect a specific identifier/value collision, never guess from a large number.
// An explicit net-content label for the same value takes precedence even when
// the OCR line also contains a standard number.
export function netContentMismatch(item: ReviewItem): string | null {
  if (item.path !== "facts.netContent" || !item.value || !/^\d+(?:\.\d+)?$/.test(item.value)) return null;
  const raw = item.raw ?? "";
  const labelled = [...raw.matchAll(/净\s*含\s*量\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:千克|毫克|微克|毫升|kg|mg|[µμ]g|mL|克|升|g|L)(?![a-z])/gi)];
  if (labelled.some(match => sameNumber(match[1], item.value!))) return null;
  const standards = [...raw.matchAll(/\b(?:GB|QB|NY|SB|SN|DB\d*)\s*(?:\/\s*T)?\s*[-：:]?\s*(\d+(?:\.\d+)?)/gi)];
  const standard = standards.find(match => sameNumber(match[1], item.value!));
  if (standard) return `原始候选 ${item.value} 与原文标准号 ${standard[0]} 一致，疑似字段错配。请对照包装上的“净含量”重新填写数值和单位。`;
  const licenses = [...raw.matchAll(/\bSC\s*(\d+)/gi)];
  if (licenses.some(match => sameNumber(match[1], item.value!))) return `原始候选 ${item.value} 疑似来自生产许可证编号。请对照包装上的“净含量”重新填写数值和单位。`;
  return null;
}

export function initialFieldDraft(item: ReviewItem): FieldDraft {
  return netContentMismatch(item) ? {value: "", unit: "", checked: false} : {value: item.value ?? item.raw ?? "", unit: item.unit ?? "", checked: false};
}

export function fieldProblems(item: ReviewItem, draft: FieldDraft): {key: "value" | "unit"; message: string}[] {
  if (item.path !== "facts.netContent") return draft.value.trim() ? [] : [{key: "value", message: "确认值不能为空"}];
  const errors: {key: "value" | "unit"; message: string}[] = [];
  if (!decimal.test(draft.value)) errors.push({key: "value", message: "净含量需填写非负纯数字，单位请在右侧选择，例如数值43、单位g。"});
  if (!NET_UNITS.some(option => option.value === draft.unit)) errors.push({key: "unit", message: "净含量单位必填，请按原图选择；不能留空。"});
  return errors;
}
