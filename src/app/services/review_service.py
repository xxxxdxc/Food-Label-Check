"""Image-bound optimistic corrections; the engine's pending-only guard is retained."""
import copy
import hashlib
import json
from decimal import Decimal

from audit_engine.label_check.shared import document_from_dict, to_dict, validate_document
from audit_engine.label_check.review.apply import apply_decisions
from app.db import Correction, File
from app.errors import AppError
from app.services.audit_service import append_document, current_document
from app.services.projection import project_review_items
from app.services.storage import file_path


def pending(doc):
    queue = project_review_items(doc, 0)
    return len(queue["items"]) + len(queue["table_items"])


def apply_corrections(db, task, body):
    if body.base_version != task.document_version:
        raise AppError(409, "VERSION_CONFLICT", "数据版本已变化，请刷新后重试")
    if task.status not in {"WAITING_REVIEW", "COMPLETED", "PARTIAL"}:
        raise AppError(409, "STATE_CONFLICT", "当前状态不允许复核")
    file = db.get(File, body.image_id)
    if file is None or file.task_id != task.id:
        raise AppError(404, "FILE_NOT_FOUND", "原图不存在")
    doc = current_document(db, task)
    image = next((i for i in doc["images"] if i["id"] == body.image_id), None)
    data = file_path(file).read_bytes()
    if (not image or image["fileName"] != body.image_name or body.document_id != doc["documentId"]
            or body.product_id != doc["productId"] or body.threshold != .96
            or body.image_sha256 != hashlib.sha256(data).hexdigest() or not body.reviewer.strip()):
        raise AppError(422, "INVALID_CORRECTION", "复核文档、原图指纹或复核人不匹配")
    queue = project_review_items(doc, task.document_version)
    allowed_paths = {item["path"] for item in queue["items"]}
    requested_paths = [item.path for item in body.decisions]
    if len(set(requested_paths)) != len(requested_paths) or not set(requested_paths) <= allowed_paths:
        raise AppError(422, "INVALID_CORRECTION", "只能提交当前待复核队列中的字段，且不得重复")
    if (body.table_decisions or body.manual_tables) and not body.table_checked_all:
        raise AppError(422, "TABLE_CONFIRMATION_REQUIRED", "请核对全部行列并勾选 table_checked_all")
    decisions = body.model_dump(by_alias=True, mode="json")
    try:
        updated = copy.deepcopy(doc)
        if body.decisions or body.table_decisions:
            updated = to_dict(apply_decisions(document_from_dict(doc), data, decisions))
        for table in body.manual_tables:
            updated = manual_table(updated, table, image)
        if not (body.decisions or body.table_decisions or body.manual_tables):
            raise ValueError("没有复核决定")
        validate_document(document_from_dict(updated))
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError(422, "INVALID_CORRECTION", str(exc)) from None
    old_version = task.document_version
    append_document(db, task, updated, "human")
    task.review_pending = pending(updated)
    db.add(Correction(task_id=task.id, from_version=old_version, to_version=task.document_version,
                      decisions_json=json.dumps(decisions, ensure_ascii=False)))


def manual_table(doc, table, image):
    doc = copy.deepcopy(doc)
    diagnostic = next((d for d in doc.get("tableDiagnostics", [])
                       if d["status"] != "ok" and d["imageId"] == image["id"]
                       and d.get("tableId") in {table.table_id, None}
                       and d["code"] == table.original_code
                       and d["evidenceRefs"] == table.source_evidence_refs), None)
    if diagnostic is None:
        raise ValueError("整表录入必须对应尚待复核的表格诊断")
    old = next((t for t in doc["facts"]["nutritionTables"] if t["id"] == table.table_id), None)
    if old and diagnostic.get("tableId") != old["id"]:
        raise ValueError("不能覆盖另一张表")
    if table.basis == "per_serving" and (not table.serving_g or Decimal(table.serving_g) <= 0):
        raise ValueError("每份基准需要大于0的 serving_g")
    box = table.box_px.model_dump() if table.box_px else diagnostic.get("boxPx")
    if box and (box["left"] + box["width"] > image["widthPx"] or box["top"] + box["height"] > image["heightPx"]):
        raise ValueError("表格区域越出原图")

    def field(raw, value, unit=None):
        identifier = "manual-table:" + str(len(doc["evidence"]) + 1)
        doc["evidence"].append({"id": identifier, "imageId": None, "sourceType": "manual",
                                "text": raw, "boxPx": None, "ocrConfidence": None})
        return {"raw": raw, "value": value, "unit": unit,
                "sourceRefs": [*diagnostic["evidenceRefs"], identifier], "status": "ready", "reviewReasons": []}

    basis_field = field(table.basis_raw, table.basis)
    basis = {"kind": table.basis, "raw": table.basis_raw, "status": "ready",
             "sourceRefs": basis_field["sourceRefs"],
             "servingSize": field(table.serving_g + "g", table.serving_g, "g") if table.basis == "per_serving" else None}
    names = [r.nutrient for r in table.rows]
    if len(names) != len(set(names)):
        raise ValueError("整表录入不能含重复营养项")
    replacement = {"id": table.table_id, "basis": basis, "sourceRefs": basis_field["sourceRefs"], "rows": [
        {"nutrient": field(r.nutrient, r.nutrient), "amount": field(r.amount + r.unit, r.amount, r.unit),
         "nrvPercent": field(r.nrv_percent, r.nrv_percent, "%") if r.nrv_percent is not None else None} for r in table.rows]}
    if old:
        doc["facts"]["nutritionTables"][doc["facts"]["nutritionTables"].index(old)] = replacement
    else:
        doc["facts"]["nutritionTables"].append(replacement)
    diagnostic.update(status="ok", code="HUMAN_REVIEWED_TABLE", tableId=table.table_id,
                      message="人工已核对全部行列", boxPx=box)
    return doc
