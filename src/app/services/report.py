"""Version-aware JSON reports and standalone, escaped printable HTML."""
import base64
import io
import json
from html import escape

from PIL import Image
from sqlalchemy import select
from app.db import CheckRun, Correction, Issue, IssueDecision
from app.engine_bridge import SCOPE
from app.errors import AppError
from app.schemas.contract import AuditIssue, AuditReport
from app.services.audit_service import current_document, task_schema
from app.services.projection import project_fields
from app.services.storage import file_path
from app.services.samples import catalog
from app.services.regulation import standard_source


def latest_run(db, task):
    return db.scalar(select(CheckRun).where(CheckRun.task_id == task.id).order_by(CheckRun.id.desc()))


def issues_for_task(db, task):
    run = latest_run(db, task)
    if not run:
        return []
    decisions = {r.issue_id: r.decision for r in db.scalars(select(IssueDecision).where(
        IssueDecision.task_id == task.id, IssueDecision.doc_version == run.doc_version))}
    result = []
    for row in db.scalars(select(Issue).where(Issue.check_run_id == run.id).order_by(Issue.id)):
        item = AuditIssue.model_validate_json(row.issue_json)
        item.regulation = standard_source(item.regulation, item.rule_id)
        item.review_decision = decisions.get(item.issue_id, "open")
        result.append(item)
    rank = {"FAIL": 0, "REVIEW": 1, "NOT_CHECKED": 2, "PASS": 3}
    return sorted(result, key=lambda item: rank[item.status])


def report_json(db, task):
    run = latest_run(db, task)
    if not run:
        raise AppError(409, "REPORT_NOT_READY", "尚无检查结果")
    # Match the fields to the result version. Current task.stale still warns about newer edits.
    fields = project_fields(current_document(db, task, run.doc_version), run.doc_version)
    issues = issues_for_task(db, task)
    corrections = [{"from_version": r.from_version, "to_version": r.to_version,
                    "decisions": json.loads(r.decisions_json)} for r in db.scalars(
        select(Correction).where(Correction.task_id == task.id).order_by(Correction.id))]
    sample = next((s for s in catalog() if s["code"] == task.sample_id), None) if task.source == "sample" else None
    attribution = {"source_url": sample["productUrl"], "image_url": sample["imageUrl"], "license": sample["license"]} if sample else None
    return AuditReport(task=task_schema(task), policy_version=run.policy_version, scope=SCOPE,
                       fields=fields, issues=issues, unchecked=[i for i in issues if i.status == "NOT_CHECKED"],
                       confirmed_fields=[f.field_id for f in fields if f.status == "human_confirmed"],
                       corrections=corrections, sample_attribution=attribution)


def report_html(report, task):
    def table(items):
        rows = []
        for item in items:
            rule = item.regulation
            regulation = (rule.standard + "—" + rule.edition + " " + (rule.source_title or "")
                          if rule.verification_status not in {"course_requirement", "not_applicable"} and rule.standard else
                          "课程/内部要求，非国标条款" if rule.verification_status == "course_requirement" else
                          "当前固定规则未覆盖" if rule.verification_status == "not_applicable" else "暂无关联标准")
            regulation = escape(regulation)
            if rule.source_url and rule.source_url.startswith("https://"):
                regulation += '<br><a href="' + escape(rule.source_url, quote=True) + '" target="_blank" rel="noopener noreferrer">查看标准原文</a>'
            evidence = "；".join(e.text for e in item.evidence)
            calculation = json.dumps(item.calculation, ensure_ascii=False) if item.calculation else ""
            rows.append("<tr>" + "".join("<td>" + escape(str(v)) + "</td>" for v in
                        (item.rule_id, item.status, item.message, evidence, calculation))
                        + "<td>" + regulation + "</td><td>" + escape(item.review_decision) + "</td></tr>")
        return "<table><tr><th>规则</th><th>结论</th><th>发现</th><th>证据</th><th>计算</th><th>依据</th><th>人工决定</th></tr>" + "".join(rows) + "</table>"
    images = []
    for file in task.files:
        with Image.open(file_path(file)) as image:
            image.thumbnail((700, 700))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=75)
        images.append('<figure><img alt="包装原图缩略图" src="data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode() + '"><figcaption>' + escape(file.display_name) + '</figcaption></figure>')
    confirmed = "".join("<li>" + escape(f.field_id + ": " + str(f.value) + " " + str(f.unit or "")) + "</li>"
                        for f in report.fields if f.status == "human_confirmed")
    source = report.sample_attribution
    attribution = ('<p>公开缓存样本：<a href="' + escape(source.source_url, quote=True) + '">Open Food Facts</a>；' +
                   escape(source.license) + '</p>') if source else ''
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>食品包装预审报告</title>'
            '<style>body{font:14px sans-serif;margin:24px;color:#17202a}table{border-collapse:collapse;width:100%}'
            'td,th{border:1px solid #bbb;padding:7px;overflow-wrap:anywhere}img{max-width:100%}'
            '@media print{body{margin:0}tr,figure{break-inside:avoid}h2{break-after:avoid}}</style><body>'
            '<h1>食品包装 AI 预审报告</h1><p>' + escape(report.scope) + '</p><p>任务 ' + escape(task.id) +
            '；检查文档版本 ' + str(task.check_doc_version) + '；规则 ' + escape(report.policy_version) + '</p>' +
            ('<p>结果已过期，请使用最新数据重新检查。</p>' if report.task.stale else '') +
            attribution + ''.join(images) + '<h2>已检查项</h2>' + table([i for i in report.issues if i.status != 'NOT_CHECKED']) +
            '<h2>未检查项</h2>' + table(report.unchecked) + '<h2>人工确认字段</h2><ul>' + confirmed +
            '</ul><p>AI 预审完成不等于人工签核。</p></body></html>')
