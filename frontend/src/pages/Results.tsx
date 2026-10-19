import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Empty, Spin } from "antd";
import { api, downloadJsonReport, errorText, json, openHtmlReport } from "../api/client";
import { taskWarnings, useTask } from "../api/hooks";
import type { AuditIssue, IssueDecisionRequest, ReviewItemsResponse } from "../api/types";
import { Panel, Pill, ScopeNotice, TaskPill, WarningList } from "../components/Common";
import ImageViewer from "../components/ImageViewer";
import ExtractionDetails from "../components/ExtractionDetails";
import IssueEvidence from "../components/IssueEvidence";
import ClaimPrecheck from "../components/ClaimPrecheck";
import RegulationReference from "../components/RegulationReference";
import { evidenceBoxes, viewerImages } from "../lib/evidence";
import { extractionImages } from "../lib/extraction";
import { ISSUE_STATUS, LAYER, ruleTitle } from "../lib/labels";
import { issuePresentation } from "../lib/issuePresentation";

const ORDER = { FAIL: 0, REVIEW: 1, NOT_CHECKED: 2, PASS: 3 };
const DECISION = { open: "尚未处理", confirmed: "已确认问题", false_positive: "已标记误报" };
const printable = (v: unknown) => typeof v === "object" ? JSON.stringify(v, null, 2) : String(v ?? "—");
const CALCULATION_LABEL: Record<string, string> = {reported_kj: "标示能量（kJ）", computed_kj: "重算能量（kJ）", formula: "换算公式", tolerance: "允许范围", deviation: "偏差", amount_per_100g: "每100g含量", nrv_reference: "NRV 参考值", expected_percent: "重算 NRV%", reported_percent: "标示 NRV%", rounding: "修约方法", observed: "各处标示值"};

export default function Results() {
  const { id = "" } = useParams();
  const task = useTask(id);
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const [status, setStatus] = useState("all");
  const [layer, setLayer] = useState("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [focusBoxKey, setFocusBoxKey] = useState<string | null>(null);
  const [regionTarget, setRegionTarget] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const version = task.data?.check_doc_version;
  const issues = useQuery({ queryKey: ["issues", id, version], queryFn: () => api<AuditIssue[]>(`/api/audits/${id}/issues`), enabled: version != null });
  const extractionQuery = useQuery({queryKey: ["review", id, task.data?.document_version], queryFn: () => api<ReviewItemsResponse>(`/api/audits/${id}/review-items`), enabled: !!task.data && !["QUEUED", "EXTRACTING", "FAILED"].includes(task.data.status)});
  const list = (issues.data ?? []).filter(i => (status === "all" || i.status === status) && (layer === "all" || i.layer === layer)).sort((a, b) => ORDER[a.status] - ORDER[b.status]);
  const active = list.find(i => i.issue_id === selected) ?? list[0];
  const presentation = active ? issuePresentation(active) : null;
  const decision = useMutation({
    mutationFn: (value: IssueDecisionRequest["decision"]) => api(`/api/audits/${id}/issues/${encodeURIComponent(active!.issue_id)}/decision`, json({ decision: value }, "PUT")),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["issues", id] }); message.success("已保存处理意见"); },
    onError: async err => { message.error(errorText(err)); await task.refetch(); },
  });
  const exportReport = async (html: boolean) => {
    setExporting(true);
    try { await (html ? openHtmlReport(id) : downloadJsonReport(id)); } catch (err) { message.error(errorText(err)); } finally { setExporting(false); }
  };
  if (task.isError) return <Alert type="error" showIcon title={errorText(task.error)} action={<Button onClick={() => task.refetch()}>重试</Button>} />;
  if (!task.data) return <Spin tip="读取任务中"><div className="loading-space" /></Spin>;
  const t = task.data;
  const checking = ["QUEUED", "EXTRACTING", "CHECKING"].includes(t.status);
  const stage = {QUEUED: "等待开始识别", EXTRACTING: "正在进行包装分区与文字识别", CHECKING: "正在用已确认数据进行规则检查"}[t.status as "QUEUED" | "EXTRACTING" | "CHECKING"];
  const extraction = extractionQuery.data?.document_version === t.document_version ? extractionImages(extractionQuery.data.extraction, t.files) : [];
  return <div className="stack">
    <div className="page-heading"><div><Link to="/audits">返回工作台</Link><h1>预审结果 <TaskPill task={t} /></h1><p className="muted">任务 #{id.slice(0, 8)} · 数据版本 {t.document_version} · 检查版本 {version ?? "尚未生成"}</p></div>
      <div className="actions"><Button disabled={version == null || t.stale || checking} loading={exporting} onClick={() => exportReport(false)}>导出 JSON</Button><Button disabled={version == null || t.stale || checking} loading={exporting} onClick={() => exportReport(true)}>打印版报告</Button></div></div>
    <ScopeNotice />
    <WarningList warnings={taskWarnings(t)} />
    {t.stale && <Alert type="warning" showIcon title="结果已过期" description="下面显示的是旧版本结果。请完成剩余复核并重新检查，暂不可标记问题或导出。" />}
    {checking && <Alert type="info" showIcon title={stage} description="完成后将自动更新结果。" />}
    {t.status === "FAILED" && <Alert type="error" showIcon title={t.error?.message ?? "任务失败"} />}
    {t.status === "WAITING_REVIEW" && <Alert type="warning" showIcon title={`还有 ${t.review_pending} 项待复核`} action={<Link to={`/audits/${id}/review`}><Button>继续复核</Button></Link>} />}
    {issues.isError && <Alert type="error" title={errorText(issues.error)} action={<Button onClick={() => issues.refetch()}>重试</Button>} />}
    <div className={`results-layout ${t.stale ? "stale-results" : ""}`}>
      <Panel title="包装原图" className="image-panel"><ImageViewer images={viewerImages(t.files)} boxes={(issues.data ?? []).flatMap(i => evidenceBoxes(i.evidence, i.issue_id, i.status === "FAIL" ? "fail" : i.status === "PASS" ? "ok" : "warn"))} zones={extraction.flatMap(entry => entry.regions.map(region => region.box))} activeTarget={regionTarget ?? active?.issue_id ?? null} focusBoxKey={focusBoxKey} onSelect={key => { if (key.startsWith("zone:")) { setRegionTarget(key); return; } setRegionTarget(null); setStatus("all"); setLayer("all"); setSelected(key); setFocusBoxKey(null); }} /><ExtractionDetails images={extraction} onLocate={key => {setRegionTarget(key); setFocusBoxKey(null);}} />{extractionQuery.isError && <p className="muted small">识别区域说明暂时无法读取。<Button size="small" onClick={() => extractionQuery.refetch()}>重试</Button></p>}</Panel>
      <Panel title={`检查项（${list.length}）`} className="issue-panel">
        <div className="filters"><select aria-label="按结论筛选" value={status} onChange={e => setStatus(e.target.value)}><option value="all">全部结论</option>{Object.entries(ISSUE_STATUS).map(([value, tone]) => <option key={value} value={value}>{tone.label}</option>)}</select><select aria-label="按审核层级筛选" value={layer} onChange={e => setLayer(e.target.value)}><option value="all">全部层级</option>{Object.entries(LAYER).map(([value, tone]) => <option key={value} value={value}>{value} {tone.label}</option>)}</select></div>
        {issues.isLoading ? <Spin /> : !list.length ? <Empty description={version == null ? "检查结果尚未生成" : "当前筛选下没有检查项"} /> : <div className="issue-list">{list.map(i => <button key={i.issue_id} className={`issue-row ${i.issue_id === active?.issue_id ? "selected" : ""}`} onClick={() => { setRegionTarget(null); setFocusBoxKey(null); setSelected(i.issue_id); }} aria-pressed={i.issue_id === active?.issue_id}>
          <div className="inline"><Pill tone={ISSUE_STATUS[i.status]} /><span className="muted">{i.layer}</span></div><strong>{ruleTitle(i.rule_id) ?? i.title}</strong><p>{issuePresentation(i).message}</p><span className="muted small">{DECISION[i.review_decision]}</span></button>)}</div>}
      </Panel>
      <Panel title="问题详情" className="detail-panel">{active ? <div className="stack-s">
        <div className="inline"><Pill tone={ISSUE_STATUS[active.status]} /><Pill tone={LAYER[active.layer]} /></div>
        <h2>{ruleTitle(active.rule_id) ?? active.title}</h2><code className="muted small">{active.rule_id}</code><p>{presentation?.message}</p>
        {active.rule_id === "CLAIM-UNKNOWN" && <ClaimPrecheck key={`${active.issue_id}:${active.doc_version}`} auditId={id} documentVersion={t.document_version} issueVersion={active.doc_version} checkedVersion={version ?? null} claimId={active.claim_id} stale={t.stale} checking={checking} evidence={active.evidence} onLocate={ref => { const index = active.evidence.findIndex(e => e.id === ref); if (index >= 0) { setRegionTarget(null); setFocusBoxKey(`${active.issue_id}:${ref}:${index}`); } }} />}
        <IssueEvidence key={`evidence:${active.issue_id}:${active.doc_version}`} evidence={active.evidence} onLocate={(e, index) => {setRegionTarget(null); setFocusBoxKey(`${active.issue_id}:${e.id}:${index}`);}} />
        {active.calculation && Object.keys(active.calculation).length > 0 && <details open><summary>计算过程</summary><dl className="calculation">{Object.entries(active.calculation).map(([k, v]) => <div key={k}><dt>{CALCULATION_LABEL[k] ?? k}</dt><dd>{printable(v)}</dd></div>)}</dl></details>}
        <h3>规则依据</h3><RegulationReference regulation={active.regulation} title={presentation?.regulationTitle} note={presentation?.regulationNote} />
        {active.suggestion && active.rule_id !== "CLAIM-UNKNOWN" && <><h3>处理建议</h3><p>{active.suggestion}</p></>}
        <p className="muted">处理状态：{DECISION[active.review_decision]}</p>
        <div className="actions"><Button disabled={t.stale || checking || active.status === "PASS"} loading={decision.isPending} onClick={() => decision.mutate("confirmed")}>确认问题</Button><Button disabled={t.stale || checking || active.status === "PASS"} loading={decision.isPending} onClick={() => decision.mutate("false_positive")}>标记误报</Button>{active.review_decision !== "open" && <Button disabled={t.stale || checking} onClick={() => decision.mutate("open")}>撤销标记</Button>}</div>
      </div> : <Empty description="选择一个检查项查看详情" />}</Panel>
    </div>
  </div>;
}
