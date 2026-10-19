"""Issue decisions round-trip through HTTP, persistence, rechecks and reports."""
import pytest
from sqlalchemy import select

from app.db import IssueDecision, init_db
from app.worker import run_one
from tests.test_workflow import client, correction_body, create_upload, extraction


@pytest.mark.parametrize("decision", ["confirmed", "false_positive"])
def test_decision_can_be_revoked_and_does_not_leak_into_new_version(client, monkeypatch, decision):
    extraction(monkeypatch)
    task = create_upload(client)
    assert run_one()
    prefix = "/api/audits/" + task["id"]
    issue = next(i for i in client.get(prefix + "/issues").json() if i["rule_id"] == "PACKAGE-FIELD-PRODUCER")
    endpoint = prefix + "/issues/" + issue["issue_id"] + "/decision"

    def persisted_decision():
        issues = client.get(prefix + "/report").json()["issues"]
        return next(i for i in issues if i["issue_id"] == issue["issue_id"])["review_decision"]

    assert client.put(endpoint, json={"decision": decision}).json()["review_decision"] == decision
    assert persisted_decision() == decision
    for _ in range(2):  # Revocation is idempotent, including across HTTP requests.
        response = client.put(endpoint, json={"decision": "open"})
        assert response.status_code == 200, response.text
        assert response.json()["review_decision"] == persisted_decision() == "open"
    with init_db()() as db:
        assert db.scalar(select(IssueDecision).where(IssueDecision.task_id == task["id"])) is None
    assert client.put(endpoint, json={"decision": "invented"}).status_code == 422

    assert client.put(endpoint, json={"decision": decision}).status_code == 200
    assert client.post(prefix + "/corrections", json=correction_body(client, task["id"])).status_code == 200
    stale = client.put(endpoint, json={"decision": "open"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_RESULT"
    assert client.post(prefix + "/resume").status_code == 200
    assert run_one()
    assert persisted_decision() == "open"


def test_bad_png_checksum_is_rejected_without_creating_a_task(client):
    import base64
    damaged = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=")
    response = client.post("/api/audits", files={"files": ("damaged.png", damaged, "image/png")})
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "INVALID_IMAGE"
    assert client.get("/api/audits").json()["total"] == 0
