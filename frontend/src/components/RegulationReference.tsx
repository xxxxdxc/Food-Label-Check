import type { AuditIssue } from "../api/types";

export default function RegulationReference({ regulation, title, note }: {
  regulation: AuditIssue["regulation"]; title?: string | null; note?: string | null;
}) {
  const special = regulation.verification_status === "not_applicable" || regulation.verification_status === "course_requirement";
  const label = title || (regulation.verification_status === "not_applicable" ? "当前固定规则未覆盖"
    : regulation.verification_status === "course_requirement" ? "课程 / 内部要求，非国标条款"
    : [regulation.standard, regulation.edition].filter(Boolean).join("—") || "暂无关联标准");
  return <div className={`regulation regulation-${regulation.verification_status}`}>
    <strong style={{display: "block", textWrap: "balance"}}>{label}{!special && regulation.source_title ? ` ${regulation.source_title}` : ""}</strong>
    {!special && regulation.source_url?.startsWith("https://")
      ? <p><a href={regulation.source_url} target="_blank" rel="noopener noreferrer">查看标准原文</a></p>
      : special ? note && <p>{note}</p> : <p>暂未关联标准原文</p>}
  </div>;
}
