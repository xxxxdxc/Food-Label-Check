import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Empty, Input, Spin } from "antd";
import { api, ApiError, errorText, json } from "../api/client";
import { taskWarnings, useTask } from "../api/hooks";
import type { AuditField, AuditTask, CorrectionsRequest, FieldDecision, ManualTable, ReviewImage, ReviewItem, ReviewItemsResponse } from "../api/types";
import { Panel, ScopeNotice, TaskPill, WarningList } from "../components/Common";
import ImageViewer from "../components/ImageViewer";
import NutritionEditor from "../components/NutritionEditor";
import { cellDraft, emptyNutrition, groupCells, nutritionGroups, nutritionProblems, replacementOf, type NutritionDraft } from "../lib/nutritionReview";
import ExtractionDetails from "../components/ExtractionDetails";
import { nutritionFields } from "../components/NutritionPreview";
import TableReviewForm, { emptyTable, tableKey, tableProblems, tableValid, type TableDraft } from "../components/TableReviewForm";
import { evidenceBoxes, evidenceSummary, viewerImages } from "../lib/evidence";
import { extractionImages } from "../lib/extraction";
import { pathLabel, reviewReason } from "../lib/labels";
import { fieldProblems, initialFieldDraft, netContentMismatch, NET_UNITS, type FieldDraft } from "../lib/fieldReview";

type Draft = FieldDraft;
const draftOf = initialFieldDraft;

export default function Review() {
  const { id = "" } = useParams();
  const task = useTask(id);
  const review = useQuery({ queryKey: ["review", id, task.data?.document_version], queryFn: () => api<ReviewItemsResponse>(`/api/audits/${id}/review-items`), enabled: !!task.data && task.data.status === "WAITING_REVIEW", placeholderData: keepPreviousData });
  const capabilities = useQuery({ queryKey: ["contract-capabilities"], queryFn: async () => {
    const response = await fetch("/openapi.json", {credentials: "same-origin"});
    if (!response.ok) return false;
    const schema = await response.json();
    return !!schema.components?.schemas?.CorrectionsRequest?.properties?.no_table_images;
  }, retry: false, staleTime: 60_000 });
  if (task.isError || review.isError) return <Alert type="error" showIcon title={errorText(task.error ?? review.error)} action={<Button onClick={() => { task.refetch(); review.refetch(); }}>重试</Button>} />;
  if (!task.data) return <Spin><div className="loading-space" /></Spin>;
  if (task.data.status !== "WAITING_REVIEW") return <div className="stack"><Link to="/audits">返回工作台</Link><Alert type="info" title="当前任务没有待提交的复核队列" action={<Link to={`/audits/${id}/results`}><Button>查看结果与进度</Button></Link>} /></div>;
  if (!review.data || review.data.document_version !== task.data.document_version) return <Spin><div className="loading-space" /></Spin>;
  if (!review.data.items.length && !review.data.table_items.length) return <ResumeReview id={id} />;
  return <ReviewEditor key={id} task={task.data} data={review.data} noTableSupported={capabilities.data === true} />;
}

function ResumeReview({id}: {id: string}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const resume = useMutation({mutationFn: () => api(`/api/audits/${id}/resume`, json({})), onSuccess: async () => {
    await queryClient.invalidateQueries({queryKey: ["task", id]});
    navigate(`/audits/${id}/results`);
  }});
  return <div className="stack"><Link to="/audits">返回工作台</Link><Alert type="success" showIcon title="全部复核项已保存，可以重新检查" action={<Button type="primary" loading={resume.isPending} onClick={() => resume.mutate()}>使用已保存数据重新检查</Button>} />{resume.isError && <Alert type="error" title={errorText(resume.error)} />}</div>;
}

function ReviewEditor({task, data, noTableSupported}: {task: AuditTask; data: ReviewItemsResponse; noTableSupported: boolean}) {
  const zoned = data.pipeline === "vlm_zones_ocr_v1";
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const preview = useQuery({
    queryKey: ["fields", task.id, data.document_version],
    queryFn: () => api<{document_version: number; fields: AuditField[]}>(`/api/audits/${task.id}/fields?version=${data.document_version}`),
    enabled: data.table_items.some(item => !!item.table_id) || data.items.some(item => item.path.startsWith("facts.nutritionTables[")),
  });
  const previewFields = preview.data?.document_version === data.document_version && !preview.isError ? preview.data.fields : null;
  const unifiedGroups = previewFields ? nutritionGroups(previewFields, data.items, data.table_items).filter(group => task.files.some(f => f.id === group.imageId)) : [];
  const unifiedPaths = new Set(unifiedGroups.flatMap(group => group.pending.map(item => item.path)));
  const fallbackItems = data.items.filter(item => !unifiedPaths.has(item.path) && (!item.path.startsWith("facts.nutritionTables[") || !preview.isPending));
  const fallbackTables = data.table_items.flatMap((item, index) => unifiedGroups.some(group => group.diagnostic === item) || preview.isPending ? [] : [{item, index}]);
  const extraction = extractionImages(data.extraction, task.files);
  const zones = extraction.flatMap(entry => entry.regions.map(region => region.box));
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [nutritionDrafts, setNutritionDrafts] = useState<Record<string, NutritionDraft>>({});
  const [tables, setTables] = useState<Record<string, TableDraft>>({});
  const [active, setActive] = useState<string | null>(data.items[0]?.path ?? null);
  const [reviewer, setReviewer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { setDrafts({}); setTables({}); setNutritionDrafts({}); }, [data.document_version]);
  const fieldDraft = (item: ReviewItem) => drafts[item.path] ?? draftOf(item);
  const setField = (item: ReviewItem, patch: Partial<Draft>) => setDrafts(prev => ({...prev, [item.path]: {...(prev[item.path] ?? draftOf(item)), ...patch}}));
  const nutritionDraft = (id: string) => nutritionDrafts[id] ?? emptyNutrition();
  const selectedNutrition = unifiedGroups.filter(group => nutritionDraft(group.id).checked);
  const selected = [...fallbackItems.filter(i => fieldDraft(i).checked), ...selectedNutrition.filter(group => nutritionDraft(group.id).mode === "fields").flatMap(group => group.pending)];
  const decisionDraft = (item: ReviewItem): Draft => {
    const group = selectedNutrition.find(group => group.pending.some(i => i.path === item.path));
    const cell = group && groupCells(group).find(c => c.path === item.path);
    return group && cell ? {...cellDraft(cell, nutritionDraft(group.id)), checked: true} : fieldDraft(item);
  };
  const selectedTables = fallbackTables.flatMap(({item, index}) => {
    const key = tableKey(item, index); const draft = tables[key];
    return draft && draft.mode !== "pending" ? [{item, draft, key}] : [];
  });
  const totalSelected = fallbackItems.filter(i => fieldDraft(i).checked).length + selectedNutrition.length + selectedTables.filter(t => tableValid(t.draft)).length;
  const replacementConflict = selectedTables.some(t => t.draft.mode === "manual" && selected.some(item => item.path.startsWith("facts.nutritionTables[") && item.evidence.some(e => e.image_id === t.item.image_id)));
  const invalid = replacementConflict || selected.some(i => fieldProblems(i, decisionDraft(i)).length > 0) || selectedNutrition.some(group => nutritionProblems(group, nutritionDraft(group.id)).length > 0) || selectedTables.some(t => !tableValid(t.draft) || (t.draft.mode === "confirm" && !nutritionFields(previewFields ?? [], t.item.table_id).some(field => field.field_id.endsWith(".nutrient"))));
  const submissionHints = [
    ...(replacementConflict ? ["整表替换不能与尚未关联清楚的同图营养字段一起提交，请取消这些字段的单独确认"] : []),
    ...selected.flatMap(i => fieldProblems(i, decisionDraft(i)).map(problem => `${pathLabel(i.path)}：${problem.message}`)),
    ...selectedNutrition.flatMap(group => nutritionProblems(group, nutritionDraft(group.id)).slice(0, 1).map(p => `营养表${group.index + 1}：${p.message}`)),
    ...selectedTables.flatMap(t => {
      const problem = tableProblems(t.draft)[0];
      if (problem) return [problem.message];
      if (t.draft.mode === "confirm" && !nutritionFields(previewFields ?? [], t.item.table_id).some(field => field.field_id.endsWith(".nutrient"))) return ["请先成功读取对应版本整表，再确认全部行列"];
      return t.draft.checked ? [] : ["请勾选表格下方的“我已核对全部行列”"];
    }),
    ...(!totalSelected && !selectedTables.length ? ["请至少勾选一项已核对的基础信息或一张营养表"] : []),
    ...(!reviewer.trim() ? ["请填写复核人"] : []),
  ];
  const boxes = [...data.items.flatMap(i => evidenceBoxes(i.evidence, i.path, fieldDraft(i).checked ? "ok" : "warn")), ...unifiedGroups.flatMap(group => groupCells(group).flatMap(cell => evidenceBoxes(cell.field.evidence, cell.path, nutritionDraft(group.id).checked ? "ok" : "warn"))), ...data.table_items.flatMap((t, index) => t.box_px ? [{ key: tableKey(t, index), imageId: t.image_id, box: t.box_px, kind: "region" as const, targetId: tableKey(t, index), tone: "warn" as const }] : [])];
  const refresh = async () => { await queryClient.invalidateQueries({queryKey: ["task", task.id]}); await queryClient.invalidateQueries({queryKey: ["review", task.id]}); await queryClient.invalidateQueries({queryKey: ["audits"]}); };
  const submit = async () => {
    if (!reviewer.trim() || !totalSelected || invalid) return;
    setBusy(true); setError(null); setNotice(null);
    let saved = 0;
    try {
      let version = data.document_version;
      const imageFor = (item: ReviewItem) => item.evidence.find(e => e.image_id && task.files.some(f => f.id === e.image_id))?.image_id ?? null;
      const groups = new Set([...selected.map(imageFor), ...selectedTables.map(t => t.item.image_id), ...selectedNutrition.map(group => group.imageId)]);
      for (const imageId of groups) {
        const file = task.files.find(f => f.id === imageId);
        const image = (data.images as unknown as ReviewImage[]).find(i => i.id === imageId);
        if (!file || !image) throw new Error("复核项缺少原图绑定信息，无法安全提交，请刷新并检查接口数据。");
        const decisions: FieldDecision[] = selected.filter(i => imageFor(i) === imageId).map(i => {
          const d = decisionDraft(i); const unit = d.unit || null;
          const unchanged = d.value === i.value && unit === i.unit && !!i.raw?.trim();
          return {path: i.path, action: unchanged ? "confirm" : "correct", originalRaw: i.raw, originalValue: i.value, originalUnit: i.unit,
            sourceEvidenceRefs: i.evidence.map(e => e.id), manualText: unchanged ? i.raw! : d.value, correctedValue: d.value, correctedUnit: unit};
        });
        const entries = selectedTables.filter(t => t.item.image_id === imageId);
        const nutritionEntries = selectedNutrition.filter(group => group.imageId === imageId);
        const replacements = nutritionEntries.filter(group => nutritionDraft(group.id).mode === "replace");
        const confirmations = nutritionEntries.filter(group => nutritionDraft(group.id).mode === "fields" && group.diagnostic);
        const body: CorrectionsRequest = { base_version: version, image_id: file.id, reviewSchemaVersion: "1.0", documentId: data.document_id, productId: data.product_id,
          imageName: image.fileName, imageSha256: file.sha256, threshold: data.threshold, reviewer: reviewer.trim(), reviewedAt: new Date().toISOString(), decisions,
          table_checked_all: entries.length > 0 || replacements.length > 0 || confirmations.length > 0,
          tableDecisions: [...confirmations.map(group => ({tableId: group.id, originalCode: group.diagnostic!.code, sourceEvidenceRefs: group.diagnostic!.evidence_refs, action: "confirm_complete" as const})), ...entries.filter(t => t.draft.mode === "confirm" && t.item.table_id).map(t => ({tableId: t.item.table_id!, originalCode: t.item.code, sourceEvidenceRefs: t.item.evidence_refs, action: "confirm_complete" as const})),],
          manual_tables: [...replacements.map(group => { const draft = nutritionDraft(group.id); const r = draft.replacement ?? replacementOf(group, draft); return {table_id: group.id, original_code: group.diagnostic!.code, source_evidence_refs: group.diagnostic!.evidence_refs, basis: r.basis as ManualTable["basis"], basis_raw: {per_100g: "每100g", per_100ml: "每100mL", per_serving: "每份"}[r.basis] ?? "每份", serving_g: r.basis === "per_serving" ? r.serving : null, box_px: group.diagnostic!.box_px, rows: r.rows}; }), ...entries.filter(t => t.draft.mode === "manual").map(t => ({table_id: t.item.table_id ?? `manual-${crypto.randomUUID()}`, original_code: t.item.code, source_evidence_refs: t.item.evidence_refs,
            basis: t.draft.basis, basis_raw: {per_100g: "每100g", per_100ml: "每100mL", per_serving: "每份"}[t.draft.basis], serving_g: t.draft.serving || null, box_px: t.item.box_px, rows: t.draft.rows})),],
          ...(noTableSupported ? {no_table_images: [...new Set(entries.filter(t => t.draft.mode === "none").map(() => file.id))]} : {}),
        };
        const updated = await api<AuditTask>(`/api/audits/${task.id}/corrections`, json(body));
        version = updated.document_version; saved += decisions.length + entries.length + replacements.length + confirmations.length;
      }
      // 使用服务端回读值及剩余队列，不以输入框内容冒充保存成功。
      const [fields, remaining] = await Promise.all([
        api<{document_version: number; fields: AuditField[]}>(`/api/audits/${task.id}/fields`),
        api<ReviewItemsResponse>(`/api/audits/${task.id}/review-items`),
      ]);
      if (fields.document_version !== version || remaining.document_version !== version) throw new Error("保存后的数据版本已变化，请重新核对。");
      if (remaining.items.length || remaining.table_items.length) {
        setNotice(`已保存 ${saved} 项，仍有 ${remaining.items.length + remaining.table_items.length} 项需要复核。`);
        await refresh();
      } else {
        await api(`/api/audits/${task.id}/resume`, json({}));
        await refresh();
        navigate(`/audits/${task.id}/results`);
      }
    } catch (err) {
      const conflict = err instanceof ApiError && err.status === 409;
      const unsupportedStructure = err instanceof ApiError && err.status === 422 && /Stale table decision/.test(errorText(err));
      const missingNetUnit = err instanceof ApiError && err.status === 422 && /Ready field lacks unit at facts\.netContent/.test(errorText(err));
      setError((saved ? `已有 ${saved} 项保存成功。` : "") + (conflict ? "数据已变化，已刷新，请核对最新版本后提交。" : missingNetUnit ? "净含量缺少单位，本次请求未保存。请对照原图核对数值并选择单位后再提交，当前草稿已保留。" : unsupportedStructure ? "当前后端未接受本表的结构确认，草稿已保留。请在同一张表点击“调整行结构”，对照原图核对后使用整表替换提交。" : errorText(err)));
      if (conflict || saved) await refresh();
    } finally { setBusy(false); }
  };
  return <div className="stack review-page">
    <div className="page-heading"><div><Link to="/audits">返回工作台</Link><h1>核对异常数据 <TaskPill task={task} /></h1><p className="muted">{zoned ? "文字与数值均来自 OCR，以下仅列出识别或结构校验中仍不确定的数据。" : "请对照原图确认当前识别值，以下列出仍需复核的数据。"}当前版本 {data.document_version}</p></div><Link to={`/audits/${task.id}/results`}>查看现有结果</Link></div>
    <ScopeNotice /><WarningList warnings={taskWarnings(task)} />
    {error && <Alert type="error" showIcon title={error} />}{notice && <Alert type="success" showIcon title={notice} />}
    <div className="review-layout">
      <Panel title="对照包装原图" className="image-panel"><ImageViewer images={viewerImages(task.files)} boxes={boxes} zones={zones} activeTarget={active} onSelect={key => { setActive(key); document.getElementById(key)?.scrollIntoView({behavior: "smooth", block: "nearest"}); }} caption="点击字段切换定位；区域框仅供定位，请以原图文字为准。" /><ExtractionDetails images={extraction} onLocate={setActive} /></Panel>
      <div className="stack">
      {fallbackItems.length > 0 && <Panel title={`基础信息与独立复核项（${fallbackItems.length}）`}>
        <p className="muted small">{zoned ? "通过可信度与格式校验的字段已自动放行；营养表在下方统一核对。" : "逐项对照原图核对后勾选确认；营养表在下方统一核对。"}</p>
        {([false, true]).map(nutrition => {
          const items = fallbackItems.filter(i => i.path.includes("nutritionTables") === nutrition);
          return items.length ? <section key={String(nutrition)}><h3 className="group-title">{nutrition ? "营养成分表" : "基础信息"}</h3>{items.map(item => {
            const d = fieldDraft(item);
            const mismatch = netContentMismatch(item);
            const problems = fieldProblems(item, d);
            const net = item.path === "facts.netContent";
            const valueError = net ? problems.find(p => p.key === "value") : undefined;
            const unitError = net ? problems.find(p => p.key === "unit") : undefined;
            const valueErrorId = `${item.path}:value-error`;
            const unitErrorId = `${item.path}:unit-error`;
            return <article className={`field-card ${active === item.path ? "active" : ""}`} id={item.path} key={item.path} onFocus={() => setActive(item.path)}>
              <div className="field-heading"><button className="text-button" onClick={() => setActive(item.path)}>{pathLabel(item.path)}</button></div>
              {mismatch && <Alert type="warning" showIcon title="净含量疑似字段错配" description={mismatch} />}
              <div className="candidate-grid single"><div className="candidate"><small>{zoned ? "OCR 原文" : "当前识别记录"}</small><p>{item.raw ?? "未识别到有效文字，请对照原图补充"}</p><div className="inline"><Button size="small" disabled={item.value == null || busy || !!mismatch} onClick={() => setField(item, {value: item.value ?? "", unit: item.unit ?? "", checked: false})}>恢复当前值</Button>{net && <Button size="small" onClick={() => setActive(item.path)}>定位净含量原始证据</Button>}</div>{mismatch && <p className="muted small">疑似错误候选未填入确认框；原始 OCR 记录保留，系统不会自动补单位或猜测净含量。</p>}</div></div>
              {!!item.candidates?.length && <div className="stack-s"><p className="warn-line">不同区域或图片的候选存在冲突，请对照原图核对：</p>{item.candidates.map((c, index) => <Button key={index} onClick={() => setField(item, {value: c.value ?? c.raw ?? "", unit: c.unit ?? "", checked: false})}>{task.files.find(f => f.id === c.image_id)?.display_name ?? c.image_id ?? "未知原图"}：{c.raw ?? c.value} {c.unit}</Button>)}</div>}
              <div className="field-inputs"><label className="form-label">{net ? "净含量数值（必填）" : "确认值"}{item.path.endsWith(".basis") ? <select className="basis-select" value={d.value} disabled={busy} onChange={e => setField(item, {value: e.target.value, unit: "", checked: false})}><option value="unknown">请选择计量基准</option><option value="per_100g">每100克</option><option value="per_100ml">每100毫升</option><option value="per_serving">每份</option></select> : net ? <Input aria-label="净含量数值" inputMode="decimal" placeholder="按原图填写数值" aria-invalid={!!valueError} aria-describedby={valueError ? valueErrorId : undefined} status={valueError ? "error" : undefined} value={d.value} disabled={busy} onChange={e => setField(item, {value: e.target.value, checked: false})} /> : <Input.TextArea autoSize={{ minRows: 1, maxRows: 5 }} value={d.value} disabled={busy} onChange={e => setField(item, {value: e.target.value, checked: false})} />}{valueError && <span className="error-text" role="alert" id={valueErrorId}>{valueError.message}</span>}</label><label className="form-label">{net ? "单位（必填）" : "单位"}{net ? <select className="basis-select" aria-label="净含量单位" aria-invalid={!!unitError} aria-describedby={unitError ? unitErrorId : undefined} value={d.unit} disabled={busy} onChange={e => setField(item, {unit: e.target.value, checked: false})}><option value="">按原图选择</option>{d.unit && !NET_UNITS.some(option => option.value === d.unit) && <option value={d.unit} disabled>请重选：{d.unit}</option>}{NET_UNITS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select> : <Input value={d.unit} disabled={busy || item.path.endsWith(".basis")} onChange={e => setField(item, {unit: e.target.value, checked: false})} placeholder="无单位留空" />}{unitError && <span className="error-text" role="alert" id={unitErrorId}>{unitError.message}</span>}</label></div>
              <div className="muted small">{evidenceSummary(item.evidence)}</div>
              <p className="warn-line small">{item.review_reasons.map(reviewReason).join("；") || "请对照原图核对该项"}</p>
              <Checkbox checked={d.checked} disabled={problems.length > 0 || busy} onChange={e => setField(item, {checked: e.target.checked})}>我已对照原图核对该值</Checkbox>
            </article>;
          })}</section> : null;
        })}
        {!data.items.length && <Empty description="没有待复核字段" />}
      </Panel>}
      {(unifiedGroups.length > 0 || preview.isFetching || preview.isError || (preview.data && previewFields === null)) && <Panel title={`营养表复核${unifiedGroups.length ? `（${unifiedGroups.length}）` : ""}`}>
        {preview.isFetching && <p className="muted" role="status">读取同版本营养表中…</p>}
        {preview.isError && <Alert type="error" showIcon title="无法读取整表" description={errorText(preview.error)} action={<Button onClick={() => preview.refetch()}>重新读取</Button>} />}
        {preview.data && previewFields === null && !preview.isError && <Alert type="warning" title="营养表版本与复核队列不一致" action={<Button onClick={() => preview.refetch()}>重新读取</Button>} />}
        {unifiedGroups.map(group => <NutritionEditor key={group.id} group={group} draft={nutritionDraft(group.id)} busy={busy} onLocate={setActive} onChange={draft => setNutritionDrafts(prev => ({...prev, [group.id]: draft}))} />)}
      </Panel>}
      {!!fallbackTables.length && <Panel title={`需单独处理的结构项（${fallbackTables.length}）`}>
        <p className="muted small">这些诊断尚不能可靠关联到同一张完整营养表，请对照原图处理，系统不会猜测或重复提交同表草稿。</p>
        {preview.isFetching && <p className="muted" role="status">读取当前版本整表中…</p>}
        {preview.isError && <Alert type="error" showIcon title="无法读取整表预览" description={errorText(preview.error)} action={<Button onClick={() => preview.refetch()}>重新读取整表</Button>} />}
        {preview.data && preview.data.document_version !== data.document_version && <Alert type="warning" showIcon title="整表版本与复核队列不一致" description="请重新读取对应版本后再确认全部行列。" action={<Button onClick={() => preview.refetch()}>重新读取整表</Button>} />}
        {fallbackTables.map(({item, index}) => {const key = tableKey(item, index); return <div id={key} key={key}><TableReviewForm item={item} draft={tables[key] ?? emptyTable()} fields={null} version={data.document_version} noTableSupported={noTableSupported} onChange={draft => setTables(prev => ({...prev, [key]: draft}))} onFocus={() => setActive(key)} /></div>;})}</Panel>}
      </div>
    </div>
    <div className="review-submit"><div><strong>本次确认 {totalSelected} 项</strong><span className="muted"> · 共 {fallbackItems.length + unifiedGroups.length + fallbackTables.length} 组待复核</span>{!!submissionHints.length && <p className="submission-hint small" id="review-submit-hint" role="status">暂不能提交：{[...new Set(submissionHints)].join("；")}</p>}</div><label className="reviewer-label">复核人<Input aria-label="复核人" value={reviewer} maxLength={100} onChange={e => setReviewer(e.target.value)} placeholder="填写姓名" disabled={busy} /></label><Button type="primary" size="large" loading={busy} disabled={!reviewer.trim() || !totalSelected || invalid} aria-describedby={submissionHints.length ? "review-submit-hint" : undefined} onClick={submit}>提交修正并重新检查</Button></div>
  </div>;
}
