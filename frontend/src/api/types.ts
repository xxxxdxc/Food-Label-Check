import type { components } from "./schema";

type S = components["schemas"];

// 兼容当前契约与旧部署：新增视觉信息保持可选。
export type Agreement = "agree" | "disagree" | "vision_only" | "ocr_only" | null;
export interface VisionValue {
  raw: string | null;
  value: string | null;
  unit: string | null;
  evidence_id?: string | null;
}
export interface FieldCandidate {
  image_id: string | null;
  raw: string | null;
  value: string | null;
  unit: string | null;
}
interface VisionExtras {
  vision?: VisionValue | null;
  agreement?: Agreement;
  candidates?: FieldCandidate[];
}
export interface TaskWarning {
  code: string;
  message: string;
}

export type Evidence = S["Evidence"];
export type BoxPx = S["BoxPx"];
export type AuditFile = S["AuditFile"];
export type AuditTask = Omit<S["AuditTask"], "warnings"> & { warnings?: TaskWarning[] };
export type AuditField = Omit<S["AuditField"], keyof VisionExtras> & VisionExtras;
export type ReviewItem = Omit<S["ReviewItem"], keyof VisionExtras> & VisionExtras;
export type TableReviewItem = S["TableReviewItem"];
export type ReviewItemsResponse = Omit<S["ReviewItemsResponse"], "items"> & { items: ReviewItem[] };
export type AuditIssue = S["AuditIssue"];
export type ClaimPrechecksResponse = S["ClaimPrechecksResponse"];
export type IssueDecisionRequest = S["IssueDecisionRequest"];
export type AuditList = Omit<S["AuditList"], "items"> & { items: AuditTask[] };
export type AuditReport = S["AuditReport"];
export type SampleSummary = S["SampleSummary"];
export type StandardOption = S["StandardOption"];
export type JobReceipt = S["JobReceipt"];
export type ManualTable = S["ManualTable"];
export type ManualRow = S["ManualRow"];
export type FieldDecision = S["FieldDecision"];
export type TableDecision = S["TableDecision"];
export type CorrectionsRequest = S["CorrectionsRequest"] & { no_table_images?: string[] };
export type TaskStatus = S["AuditTask"]["status"];
export type IssueStatus = S["AuditIssue"]["status"];
export type FieldStatus = S["AuditField"]["status"];

// review-items 的 images 在后端是无类型字典，这里按 LabelDocument 的实际字段声明。
export interface ReviewImage {
  id: string;
  fileName: string;
  widthPx: number;
  heightPx: number;
  sha256?: string;
  display_name?: string;
}
