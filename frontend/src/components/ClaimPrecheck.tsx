import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button } from "antd";
import { useState } from "react";
import { api, errorText } from "../api/client";
import type { ClaimPrechecksResponse, Evidence } from "../api/types";

const VERDICT = {
  supported: { label: "有材料支持", type: "success" },
  contradiction: { label: "发现矛盾", type: "warning" },
  insufficient_evidence: { label: "材料不足", type: "info" },
} as const;
const SOURCES: Record<string, string> = { national_standard: "国标资料", internal_sop: "内部核查提示", historical_case: "历史案例", channel_doc: "渠道资料", internal_guidance: "内部核查提示" };

export default function ClaimPrecheck({ auditId, documentVersion, issueVersion, checkedVersion, claimId, stale, checking, evidence, onLocate }: {
  auditId: string; documentVersion: number; issueVersion: number; checkedVersion: number | null;
  claimId: string | null | undefined; stale: boolean; checking: boolean;
  evidence: Evidence[]; onLocate: (ref: string) => void;
}) {
  const client = useQueryClient();
  const [selectedClaim, setSelectedClaim] = useState<string | null>(null);
  const queryKey = ["claim-prechecks", auditId, documentVersion];
  const path = `/api/audits/${auditId}/claim-prechecks`;
  const query = useQuery({
    queryKey,
    queryFn: () => api<ClaimPrechecksResponse>(`${path}?version=${documentVersion}`),
    enabled: checkedVersion === documentVersion && issueVersion === documentVersion && !checking,
    retry: false,
    refetchInterval: q => {
      const data = q.state.data;
      return !q.state.error && !stale && data?.document_version === documentVersion && !data.stale && ["pending", "running"].includes(data.status) ? 2000 : false;
    },
  });
  const retry = useMutation({
    mutationFn: () => api<ClaimPrechecksResponse>(`${path}/retry?version=${documentVersion}`, { method: "POST" }),
    retry: false,
    onSuccess: data => client.setQueryData(queryKey, data),
    onError: () => { client.invalidateQueries({ queryKey: ["task", auditId] }); },
  });
  const data = query.data;
  const outdated = stale || issueVersion !== documentVersion || !!data && (data.stale || data.document_version !== documentVersion);
  const matches = data?.items.filter(item => item.claim_id === (claimId || selectedClaim)) ?? [];
  const item = matches.length === 1 ? matches[0] : null;
  const complete = !outdated && data?.status !== "unavailable" && item?.status === "completed";
  const verdict = complete && item.verdict ? VERDICT[item.verdict] : null;
  const retryCount = data?.items.filter(entry => entry.status === "unavailable").length ?? 0;
  const canRetry = !outdated && !checking && checkedVersion === documentVersion && retryCount > 0 && (data?.status === "unavailable" || data?.status === "partial");
  return <section className="claim-precheck stack-s" aria-label="模型初审">
    <h3>模型初审</h3>
    <p className="muted small">独立意见，不改变规则检查结论或人工处理状态；材料不足不代表违规。</p>
    {!claimId && !outdated && <Alert type="info" title="当前检查项缺少声称关联" description="以下为本版本初审批次。请选择要查看的声称；这些意见未自动绑定到当前检查项。" />}
    {!claimId && !outdated && data && <div className="claim-batch stack-s" aria-label="本版本初审批次">{data.items.map((entry, index) => <Button key={`${entry.claim_id}:${index}`} aria-pressed={selectedClaim === entry.claim_id} onClick={() => setSelectedClaim(entry.claim_id)}>第{index + 1}项 · {entry.claim_text} · {entry.status === "completed" && entry.verdict ? VERDICT[entry.verdict].label : entry.status === "unavailable" ? "此项不可用" : "等待初审"}</Button>)}</div>}
    {!outdated && data?.status === "partial" && <Alert type="info" title={`已完成 ${data.items.filter(entry => entry.status === "completed").length}/${data.items.length} 项初审`} description="其余意见未通过校验，具体原因显示在对应声称中；已完成意见可以继续查看。" />}
    {!outdated && (data?.status === "pending" || data?.status === "running") && complete && <p role="status">失败项{data.status === "pending" ? "已排队重试" : "正在重新初审"}，已完成意见保留。</p>}
    {outdated ? <Alert type="warning" title="初审意见已过期" description="当前文档或初审配置已变化，旧意见不用于当前判断。" />
      : checking || checkedVersion !== documentVersion ? <Alert type="info" title="等待当前版本规则检查完成" />
      : query.isError ? <Alert type="warning" title="初审意见读取失败" description={errorText(query.error)} action={<Button size="small" onClick={() => query.refetch()}>重新读取</Button>} />
      : !data ? <p role="status">正在读取初审意见…</p>
      : (data.status === "pending" || data.status === "running") && !complete ? <p role="status">{data.status === "pending" ? "模型初审已排队" : "模型正在初审"}，完成后自动更新。</p>
      : item?.status === "unavailable" ? <Alert type="warning" title="此项初审暂不可用" description={item.error_message ?? data.error_message ?? "当前没有可用的模型意见。"} />
      : data.status === "unavailable" ? <Alert type="warning" title="模型初审暂不可用" description={data.error_message ?? "当前没有可用的模型意见。"} />
      : !claimId && !selectedClaim ? <p className="muted small">选择上方批次条目后查看详细意见。</p>
      : !item ? <Alert type="info" title="当前声称没有可匹配的初审意见" description="不使用其他声称或其他版本的结果代替。" />
      : !verdict ? <Alert type="warning" title="当前声称尚无完整初审结论" />
      : <>
        <p><strong>声称原文：</strong>{item.claim_text}</p>
        <Alert type={verdict.type} title={verdict.label} description={item.reason || "未提供详细说明。"} />
        {(item.missing_materials ?? []).length > 0 && <div><h4>缺失材料</h4><ul>{item.missing_materials!.map((text, i) => <li key={i}>{text}</li>)}</ul></div>}
        {(item.reference_citations ?? []).length > 0 ? <div className="stack-s"><h4>参考依据</h4>{item.reference_citations!.map(ref => <article className="claim-reference stack-s" key={ref.id}>
          <strong>{ref.title}</strong><span className="muted small">{SOURCES[ref.source_type] ?? ref.source_type}{!ref.can_support_verdict ? " · 仅作核查提示，不能证明产品事实" : ""}</span>
          <p>{ref.excerpt}</p><details><summary>来源与适用范围</summary><p>{ref.file} · {ref.section}</p><p>适用范围：{ref.scope_keywords.join("、") || "未注明"}</p><p>审核记录：{Object.entries(ref.review).map(([k, v]) => `${k}：${v}`).join("；")}</p></details>
        </article>)}</div> : <p className="muted small">本项未引用可用参考资料。</p>}
        {(item.source_evidence_refs.length > 0 || (item.supporting_evidence_refs ?? []).length > 0) && <div className="actions">{[...new Set([...item.source_evidence_refs, ...(item.supporting_evidence_refs ?? [])])].map((ref, i) => evidence.some(e => e.id === ref && e.box_px && e.image_id)
          ? <Button size="small" key={ref} onClick={() => onLocate(ref)}>定位原图依据 {i + 1}</Button>
          : <span className="muted small" key={ref}>依据 {i + 1} 暂无对应原图定位框</span>)}</div>}
        <details><summary>初审追溯信息</summary><dl className="calculation"><div><dt>文档版本</dt><dd>{data.document_version}</dd></div><div><dt>请求模型 / 实际模型</dt><dd>{item.model_requested} / {item.model_actual ?? "未返回"}</dd></div><div><dt>参考资料 / 提示词版本</dt><dd>{item.reference_set_version} / {item.prompt_version}</dd></div></dl></details>
      </>}
    {!outdated && data?.reference_notice && <p className="muted small">{data.reference_notice}</p>}
    {!outdated && data?.revalidated_from_attempt_id != null && <p className="muted small">已从保存的模型响应恢复意见，本次未新增模型调用。</p>}
    {retry.isError && <Alert type="error" title="初审重试未成功" description={errorText(retry.error)} />}
    {canRetry && <div className="stack-s"><Button loading={retry.isPending} disabled={retry.isPending} onClick={() => retry.mutate()}>重试失败项（{retryCount}）</Button><p className="muted small">只重试当前版本失败的 {retryCount} 项，保留已完成意见；可能消耗一次模型调用，每日最多10批次，并受VLM总额度限制。刷新页面不会重试。</p></div>}
  </section>;
}
