"""Only bundled, attributed public samples are available in cache mode."""
import copy
import hashlib
import json
import re
import uuid
from decimal import Decimal, InvalidOperation

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
                elif key == "evidence_id":
                    result[key] = evidence_map.get(item, item)
                elif key == "image_id":
                    result[key] = file.id
                elif key == "vision_no_table_images":
                    result[key] = [file.id for _ in item]
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
        offset = len(result["facts"]["nutritionTables"])
        platform = result.setdefault("_platform", {})
        platform.setdefault("extraction", []).extend(copy.deepcopy(doc.get("_platform", {}).get("extraction", [])))
        for path, comparison in doc.get("_platform", {}).get("comparisons", {}).items():
            if path.startswith("facts.nutritionTables["):
                path = re.sub(r"\[(\d+)\]", lambda m: "[" + str(int(m[1]) + offset) + "]", path, count=1)
            if path.startswith("facts.nutritionTables[") or result["facts"].get(path.removeprefix("facts."), {}).get("status") == "missing":
                platform.setdefault("comparisons", {})[path] = copy.deepcopy(comparison)
        platform.setdefault("vision_no_table_images", []).extend(doc.get("_platform", {}).get("vision_no_table_images", []))
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
    comparisons = result.setdefault("_platform", {}).setdefault("comparisons", {})
    for key in ("productName", "netContent", "ingredientsText", "allergenText"):
        candidates = []
        sources = []
        signatures = set()
        reasons = []
        pending_field = False
        for doc in documents:
            field = doc["facts"][key]
            if field["status"] == "missing":
                continue
            value = field["value"]
            if key == "netContent" and value is not None:
                try:
                    value = Decimal(value)
                except InvalidOperation:
                    pass
            signatures.add((value, field["unit"]))
            candidates.append({"image_id": doc["images"][0]["id"], "raw": field["raw"],
                               "value": field["value"], "unit": field["unit"]})
            sources.extend(field["sourceRefs"])
            if field["status"] == "needs_review":
                pending_field = True
                reasons.extend(field["reviewReasons"])
            comparison = doc.get("_platform", {}).get("comparisons", {}).get("facts." + key, {})
            if comparison.get("vision") and (
                    comparison.get("agreement") == "disagree" or not comparisons.get("facts." + key, {}).get("vision")):
                comparisons["facts." + key] = copy.deepcopy(comparison)
        if len(signatures) > 1 or pending_field:
            field = result["facts"][key]
            field["status"] = "needs_review"
            field["sourceRefs"] = list(dict.fromkeys(sources))
            field["reviewReasons"] = list(dict.fromkeys([*field["reviewReasons"], *reasons]))
        if len(signatures) > 1:
            field["reviewReasons"].append("MULTI_IMAGE_CONFLICT")
            comparisons.setdefault("facts." + key, {})["candidates"] = candidates
        comparison = comparisons.get("facts." + key, {})
        visual = comparison.get("vision")
        if comparison.get("agreement") in {"agree", "disagree"} and visual and visual.get("value") is not None and result["facts"][key].get("value") is not None:
            from app.services.vision_client import equivalent
            comparison["agreement"] = "agree" if equivalent(result["facts"][key], visual) else "disagree"
    from app.services.table_association import associate_review_tables
    return associate_review_tables(result)
