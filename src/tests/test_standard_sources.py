"""Whole-standard reference links never imply verified clause matching."""
import json

import pytest
from sqlalchemy import select

from app.db import Issue, init_db
from app.schemas.contract import Regulation
from app.services.regulation import standard_source
from app.worker import run_one
from tests.test_workflow import client, create_upload, extraction


@pytest.mark.parametrize("standard,rule,title", [
    ("GB 28050", "NRV-100G-PROTEIN", "预包装食品营养标签通则"),
    ("GB 28050", "ENERGY-ARITHMETIC", "预包装食品营养标签通则"),
    ("GB 7718", "PACKAGE-FIELD-FOOD_NAME", "预包装食品标签通则"),
])
def test_mapping_preserves_verification_and_clause_identity(standard, rule, title):
    original = Regulation(standard=standard, edition="2025", clause_id=None, verification_status="pending", note="待核验")
    linked = standard_source(original, rule)
    assert title in linked.source_title and linked.source_url.startswith("https://")
    assert linked.clause_id is None and linked.verification_status == "pending"
    assert original.source_url is None
    assert standard_source(original.model_copy(update={"edition": "2011"}), rule).source_url is None
    for status in ("course_requirement", "not_applicable"):
        assert standard_source(original.model_copy(update={"verification_status": status}), rule).source_url is None


def test_old_issue_get_and_reports_gain_links_without_rechecking_or_rewriting(client, monkeypatch):
    extraction(monkeypatch)
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    with init_db()() as db:
        rows = db.scalars(select(Issue).where(Issue.task_id == task["id"])).all()
        for row in rows:
            data = json.loads(row.issue_json)
            data["regulation"].pop("source_title", None)
            data["regulation"].pop("source_url", None)
            row.issue_json = json.dumps(data, ensure_ascii=False)
        db.commit()
        before = {row.id: row.issue_json for row in rows}
    issues = client.get(prefix + "/issues").json()
    regulation = next(i["regulation"] for i in issues if i["rule_id"] == "NRV-100G-FAT")
    assert regulation["source_url"].endswith(".pdf") and regulation["source_title"]
    assert regulation["verification_status"] == "pending" and regulation["clause_id"] is None
    report = client.get(prefix + "/report").json()
    assert any(i["regulation"]["source_url"] for i in report["issues"])
    html = client.get(prefix + "/report?format=html").text
    assert '查看标准原文</a>' in html
    assert 'rel="noopener noreferrer"' in html
    assert '条款待确认' not in html
    with init_db()() as db:
        assert {r.id: r.issue_json for r in db.scalars(select(Issue).where(Issue.task_id == task["id"]))} == before
    assert client.get(prefix).json()["document_version"] == 0
