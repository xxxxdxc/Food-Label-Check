from fastapi.testclient import TestClient

from app.main import app
from app.schemas.contract import CorrectionsRequest, Evidence


def test_contract_routes_and_review_payload():
    schema = app.openapi()
    assert len(schema["paths"]) == 15
    assert schema["paths"]["/api/audits/{audit_id}/corrections"]["post"]["requestBody"]
    assert schema["paths"]["/api/audits"]["get"]["security"]
    assert not schema["paths"]["/api/auth/login"]["post"].get("security")
    report_media = schema["paths"]["/api/audits/{audit_id}/report"]["get"]["responses"]["200"]["content"]
    assert {"application/json", "text/html"} <= set(report_media)

    payload = {
        "base_version": 0,
        "image_id": "front",
        "reviewSchemaVersion": "1.0",
        "documentId": "doc-1",
        "productId": None,
        "imageName": "front.png",
        "imageSha256": "a" * 64,
        "threshold": 0.96,
        "reviewer": "tester",
        "decisions": [],
        "tableDecisions": [
            {
                "tableId": "table-1",
                "originalCode": "VISION_CANDIDATE_UNVERIFIED",
                "sourceEvidenceRefs": ["ocr:1"],
                "action": "confirm_complete",
            }
        ],
    }
    assert CorrectionsRequest.model_validate(payload).table_decisions[0].table_id == "table-1"

    manual = Evidence(
        id="manual:1", image_id=None, source_type="manual",
        text="3.2g", box_px=None, ocr_confidence=None,
    )
    assert manual.box_px is None

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"passcode": "test-passcode"})
        assert login.status_code == 200
        unavailable = client.get("/api/standards")
        invalid = client.get("/api/audits?limit=0")
    assert unavailable.status_code == 200
    assert unavailable.json()[0]["mode"] == "gb28050_2025_precheck"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


def test_business_routes_require_a_valid_session():
    with TestClient(app) as client:
        audits = client.get("/api/audits")
        audit_file = client.get("/api/audits/x/files/y")
        health = client.get("/api/health")

    assert audits.status_code == 401
    assert audits.json()["error"]["code"] == "UNAUTHORIZED"
    assert audit_file.status_code == 401
    assert audit_file.json()["error"]["code"] == "UNAUTHORIZED"
    assert health.status_code == 200


def test_login_rejects_an_invalid_passcode_with_business_error_code():
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"passcode": "wrong"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
