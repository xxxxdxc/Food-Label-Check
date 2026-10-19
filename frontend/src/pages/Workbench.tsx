import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Empty, Steps, Upload } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd";
import { api, errorText, json } from "../api/client";
import { taskWarnings } from "../api/hooks";
import type { AuditList, AuditTask, JobReceipt } from "../api/types";
import { Panel, ScopeNotice, TaskPill } from "../components/Common";
import { ISSUE_STATUS, SETTLED } from "../lib/labels";

const MAX_FILES = 3;
const MAX_MB = 10;
const ACCEPT = ["image/png", "image/jpeg"];

function stepOf(task: AuditTask): { current: number; status: "process" | "wait" | "error" | "finish" } {
  switch (task.status) {
    case "QUEUED": return { current: 0, status: "wait" };
    case "EXTRACTING": return { current: 0, status: "process" };
    case "WAITING_REVIEW": return { current: 1, status: "process" };
    case "CHECKING": return { current: 2, status: "process" };
    case "FAILED": return { current: task.document_version > 0 ? 2 : 0, status: "error" };
    default: return { current: 3, status: "finish" };
  }
}

function TaskRow({ task }: { task: AuditTask }) {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api<JobReceipt>(`/api/audits/${task.id}/retry`, json({})),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["audits"] }),
    onError: (err) => message.error(errorText(err)),
  });
  const step = stepOf(task);
  const warnings = taskWarnings(task);
  const counts = task.summary ?? {};
  const name = task.files.map((f) => f.display_name).join("、") || "未命名";

  return (
    <article className="task-row">
      <div className="task-main">
        <div className="task-title">
          <TaskPill task={task} />
          <strong title={name}>{name}</strong>
          <span className="muted mono">#{task.id.slice(0, 8)}</span>
          <span className="muted">{task.source === "sample" ? "内置样本" : `上传 ${task.files.length} 张`}</span>
          <span className="muted">{new Date(task.created_at).toLocaleString("zh-CN", { hour12: false })}</span>
        </div>
        <Steps
          size="small"
          current={step.current}
          status={step.status}
          items={[{ title: "识别" }, { title: "异常复核" }, { title: "规则检查" }, { title: "完成" }]}
        />
        {task.status === "FAILED" && task.error && (
          <Alert type="error" showIcon message={task.error.message} description={<code>{task.error.code}</code>} />
        )}
        {warnings.length > 0 && <p className="warn-line">⚠ {warnings.map((w) => w.message).join("；")}</p>}
      </div>
      <div className="task-side">
        {task.summary && (
          <div className="counts" aria-label="检查结果统计">
            {(["FAIL", "REVIEW", "NOT_CHECKED", "PASS"] as const).map((s) => (
              <span key={s} style={{ color: ISSUE_STATUS[s].fg }}>
                {ISSUE_STATUS[s].label} <b>{counts[s] ?? 0}</b>
              </span>
            ))}
          </div>
        )}
        <div className="task-actions">
          {task.status === "WAITING_REVIEW" && (
            <Button type="primary" onClick={() => navigate(`/audits/${task.id}/review`)}>
              去复核（{task.review_pending}）
            </Button>
          )}
          {task.check_doc_version !== null && (
            <Button onClick={() => navigate(`/audits/${task.id}/results`)}>查看结果</Button>
          )}
          {task.status === "FAILED" && task.error?.retryable && (
            <Button loading={retry.isPending} onClick={() => retry.mutate()}>重试</Button>
          )}
        </div>
      </div>
    </article>
  );
}

export default function Workbench() {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const audits = useQuery({
    queryKey: ["audits"],
    queryFn: () => api<AuditList>("/api/audits?limit=50"),
    refetchInterval: (query) => (query.state.data?.items.some((t) => !SETTLED.includes(t.status)) ? 2000 : false),
  });

  const create = useMutation({
    mutationFn: (form: FormData) => api<AuditTask>("/api/audits", { method: "POST", body: form }),
    onSuccess: (task) => {
      setFiles([]);
      message.success("已创建审核任务 #" + task.id.slice(0, 8));
      queryClient.invalidateQueries({ queryKey: ["audits"] });
    },
    onError: (err) => setUploadError(errorText(err)),
  });

  const startUpload = () => {
    setUploadError(null);
    const form = new FormData();
    form.append("source", "upload");
    for (const f of files) if (f.originFileObj) form.append("files", f.originFileObj, f.name);
    create.mutate(form);
  };
  const beforeUpload = (file: File, batch: File[]) => {
    if (files.length + batch.length > MAX_FILES) {
      setUploadError(`每个任务最多 ${MAX_FILES} 张图片，请减少后重新选择`);
      return Upload.LIST_IGNORE;
    }
    if (!ACCEPT.includes(file.type)) {
      setUploadError(`「${file.name}」不是 PNG 或 JPG 图片`);
      return Upload.LIST_IGNORE;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      setUploadError(`「${file.name}」超过 ${MAX_MB}MB`);
      return Upload.LIST_IGNORE;
    }
    setUploadError(null);
    return false; // 不自动上传，统一在点击“开始审核”时提交
  };

  const tasks = audits.data?.items ?? [];

  return (
    <div className="stack">
      <div className="page-heading"><div><h1>包装审核工作台</h1><p className="muted">先定位包装文字区域，再由 OCR 读取字段，仅需复核不确定的数据。</p></div></div>
      <ScopeNotice />
      <div className="upload-layout">
        <Panel title="上传包装图" className="upload-panel">
          <Upload.Dragger
            multiple
            accept=".png,.jpg,.jpeg"
            fileList={files}
            beforeUpload={beforeUpload}
            onChange={({ fileList }) => setFiles(fileList.slice(0, MAX_FILES))}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">点击或拖拽图片到这里</p>
            <p className="ant-upload-hint">PNG / JPG，最多 {MAX_FILES} 张（正面、背面、营养表），单张不超过 {MAX_MB}MB</p>
          </Upload.Dragger>
          <p className="muted small">识别时会调用外部 OCR 与视觉模型服务，请勿上传未授权公开的包装稿。</p>
          {uploadError && <Alert type="error" showIcon message={uploadError} />}
          <Button type="primary" size="large" block disabled={!files.length || create.isPending} loading={create.isPending} onClick={startUpload}>
            开始审核
          </Button>
        </Panel>
        <Panel title="审核流程" className="upload-guide">
          <ol className="workflow-guide">
            <li><strong>上传包装图</strong><p>同一商品的正面、背面和营养成分表，最多三张。</p></li>
            <li><strong>视觉分区与 OCR</strong><p>VLM 仅定位区域，OCR 读取文字和数值。分区失败时降级整图 OCR。</p></li>
            <li><strong>自动校验与预审</strong><p>可信度达到96%且格式、结构校验通过的数据自动检查；仅异常项交人工，结果可定位原图并导出报告。</p></li>
          </ol>
        </Panel>
      </div>

      <Panel title="审核任务" extra={<span className="muted">{audits.data ? `共 ${audits.data.total} 个` : ""}</span>}>
        {audits.isError && <Alert type="error" showIcon message={errorText(audits.error)} />}
        {audits.isLoading && <p className="muted">加载中…</p>}
        {audits.isSuccess && !tasks.length && <Empty description="还没有审核任务，先上传一张包装图吧" />}
        <div className="task-list" aria-live="polite">
          {tasks.map((task) => (
            <TaskRow key={task.id} task={task} />
          ))}
        </div>
        {tasks.length > 0 && (
          <p className="muted small">
            "部分检查完成"表示有检查项因资料不足未执行，属于正常结果，详见结果页的未检查项。
            <Button type="link" size="small" onClick={() => audits.refetch()}>刷新</Button>
          </p>
        )}
      </Panel>
    </div>
  );
}
