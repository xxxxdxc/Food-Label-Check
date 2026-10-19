import type { AuditIssue } from "../api/types";

// Only this agreed rule gets a display override. IDs, results and exports remain
// backend-owned; other course_requirement records retain their original source.
export function issuePresentation(issue: AuditIssue) {
  if (issue.rule_id === "CLAIM-UNKNOWN") return {
    message: issue.message.replace("转质量负责人判断。", "") + "模型初审意见单独展示，本项不代表已发现违规。",
    regulationTitle: "当前固定规则未覆盖",
    regulationNote: "尚未对该声称作出确定性规则判断，请结合初审依据与实际材料核对。",
  };
  if (issue.rule_id !== "COURSE-100G-001") return {
    message: issue.message,
    regulationTitle: null,
    regulationNote: issue.regulation.note,
  };
  const requirement = "按企业渠道要求，营养含量统一换算至每100g，并按该口径计算NRV%。";
  const outcome = issue.status === "PASS"
    ? "当前营养表满足每100g口径要求。"
    : issue.calculation?.provided_basis === "per_serving"
      ? "当前识别为“每份”，请核对并调整表头、含量和NRV%。"
      : "请核对营养表是否明确标示“每100g”，并确保含量和NRV%采用相同口径。";
  return {
    message: requirement + outcome,
    regulationTitle: "企业渠道要求（非国标条款）",
    regulationNote: "企业渠道的统一交付口径，不作为国标条款依据。",
  };
}
