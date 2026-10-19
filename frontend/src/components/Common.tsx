import type { ReactNode } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { Alert } from "antd";
import { useStandards } from "../api/hooks";
import type { AuditTask } from "../api/types";
import { SCOPE_TEXT, TASK_STATUS, type Tone } from "../lib/labels";

export function Pill({ tone, children }: { tone: Tone; children?: ReactNode }) {
  return (
    <span className="pill" style={{ background: tone.bg, color: tone.fg, borderColor: tone.border ?? "transparent" }}>
      {children ?? tone.label}
    </span>
  );
}

export function TaskPill({ task }: { task: AuditTask }) {
  return <Pill tone={TASK_STATUS[task.status]} />;
}

export function ScopeNotice() {
  const standards = useStandards();
  const coverage = standards.data?.[0]?.coverage ?? SCOPE_TEXT;
  return (
    <div className="scope" role="note">
      <strong>{standards.data?.[0]?.label ?? "GB 28050-2025 新版预检"}</strong>
      <span>{coverage}</span>
    </div>
  );
}

export function Panel({ title, extra, children, className }: { title?: ReactNode; extra?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={"panel " + (className ?? "")}>
      {(title || extra) && (
        <header className="panel-head">
          {title && <h2>{title}</h2>}
          {extra}
        </header>
      )}
      {children}
    </section>
  );
}

export function Shell() {
  return (
    <div className="app">
      <header className="top">
        <Link to="/audits" className="brand" aria-label="食品包装预审工作台 首页">
          <span className="brand-mark">预审</span>
          <span>食品包装预审工作台</span>
        </Link>
        <nav aria-label="主导航">
          <NavLink to="/audits" end>工作台</NavLink>
        </nav>
        <div className="top-right">
          <span className="service-badge">本机工作台</span>
          <span className="pulse" aria-hidden="true" />
          <span>自动预审 · 异常复核</span>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
      <footer className="foot">结果仅针对已确认的数据和已实现的规则，不等于最终印刷或上市放行。</footer>
    </div>
  );
}

export function WarningList({ warnings }: { warnings: { code: string; message: string }[] }) {
  if (!warnings.length) return null;
  return (
    <div className="stack-s">
      {warnings.map((w, i) => (
        <Alert key={w.code + i} type="warning" showIcon title={w.message} />
      ))}
    </div>
  );
}
