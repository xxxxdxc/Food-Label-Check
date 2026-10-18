"""Only bundled, attributed public samples are available in cache mode."""
import copy
import hashlib
import json
import uuid

from app.config import SRC_ROOT
from app.db import File
from app.errors import AppError
from app.services.storage import validate_image

SAMPLE_ROOT = SRC_ROOT / "samples_public"


def catalog():
    return json.loads((SAMPLE_ROOT / "manifest.json").read_text(encoding="utf-8"))


def prepare_sample(sample_id, task_id):
    if sample_id not in {row["code"] for row in catalog()}:
        raise AppError(404, "SAMPLE_NOT_FOUND", "样本不存在")
    encoded, width, height, mime, suffix = validate_image((SAMPLE_ROOT / (sample_id + ".jpg")).read_bytes())
    identifier = str(uuid.uuid4())
    return File(id=identifier, task_id=task_id, display_name=sample_id + ".jpg",
                stored_name=identifier + suffix, sha256=hashlib.sha256(encoded).hexdigest(),
                width_px=width, height_px=height, mime=mime), encoded


def bind_document(original, task_id, file):
    doc = copy.deepcopy(original)
    old_image = doc["images"][0]
    if (old_image["widthPx"], old_image["heightPx"]) != (file.width_px, file.height_px):
        raise ValueError("样本图片尺寸与证据不匹配")
    evidence_map = {row["id"]: file.id + ":" + row["id"] for row in doc["evidence"]}
    table_map = {t["id"]: file.id + ":" + t["id"] for t in doc["facts"]["nutritionTables"]}

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in {"sourceRefs", "evidenceRefs", "unassignedEvidenceIds"}:
                    result[key] = [evidence_map[r] for r in item]
                elif key == "imageId":
                    result[key] = file.id if item is not None else None
                elif key == "tableId":
                    result[key] = table_map.get(item, item)
                else:
                    result[key] = rewrite(item)
            return result
        return value

    doc = rewrite(doc)
    for row in doc["evidence"]:
        row["id"] = evidence_map[row["id"]]
    for row in doc["facts"]["nutritionTables"]:
        row["id"] = table_map[row["id"]]
    for row in doc["facts"].get("claims", []):
        row["id"] = file.id + ":" + row["id"]
    doc["documentId"] = task_id
    doc["images"] = [{"id": file.id, "fileName": file.stored_name,
                      "widthPx": file.width_px, "heightPx": file.height_px}]
    return doc


def merge_documents(documents):
    result = copy.deepcopy(documents[0])
    for doc in documents[1:]:
        for key in ("images", "evidence", "tableDiagnostics", "unassignedEvidenceIds"):
            result.setdefault(key, []).extend(doc.get(key, []))
        for key in ("nutritionTables", "claims", "barcodes"):
            result["facts"][key].extend(doc["facts"].get(key, []))
        for key in ("productName", "netContent", "ingredientsText", "allergenText"):
            if result["facts"][key]["status"] == "missing" and doc["facts"][key]["status"] != "missing":
                result["facts"][key] = doc["facts"][key]
    product_ids = {d.get("productId") for d in documents if d.get("productId")}
    if len(product_ids) > 1:
        result["productId"] = None
        result["productIdSource"] = "unknown"
    # Associate image-wide failures only with a wholly pending table from that image.
    # Ready tables never acquire replacement permission from a neighboring failure.
    for diagnostic in result.get("tableDiagnostics", []):
        if diagnostic["status"] != "needs_vision" or diagnostic.get("tableId") is not None:
            continue
        evidence = {e["id"]: e for e in result["evidence"]}
        candidates = []
        for table in result["facts"]["nutritionTables"]:
            sources = [evidence[r] for r in table["basis"]["sourceRefs"] if r in evidence]
            if (table["basis"]["status"] == "needs_review" and sources and
                    all(e["imageId"] == diagnostic["imageId"] for e in sources) and
                    all(row["amount"]["status"] == "needs_review" for row in table["rows"])):
                candidates.append(table)
        if len(candidates) == 1:
            diagnostic["tableId"] = candidates[0]["id"]
    return result
