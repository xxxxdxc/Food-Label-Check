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
from app.services.table_association import associate_review_tables, table_refs


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
    doc = associate_review_tables(current_document(db, task))
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
            engine_doc, engine_decisions = prepare_engine_decisions(doc, decisions, queue, image)
            updated = to_dict(apply_decisions(document_from_dict(engine_doc), data, engine_decisions))
            from audit_engine.label_check.review.apply import _field_at_path
            from app.services.vision_client import _replace_path
            for row in body.decisions:
                original = _field_at_path(document_from_dict(doc), row.path)
                field = to_dict(_field_at_path(document_from_dict(updated), row.path))
                field["sourceRefs"] = list(dict.fromkeys([*original.sourceRefs, *field["sourceRefs"]]))
                _replace_path(updated, row.path, field)
            if "_platform" in doc:
                updated["_platform"] = copy.deepcopy(doc["_platform"])
        for table in body.manual_tables:
            updated = manual_table(updated, table, image)
        if body.no_table_images:
            updated = confirm_no_table(updated, body, image)
        if not (body.decisions or body.table_decisions or body.manual_tables or body.no_table_images):
            raise ValueError("没有复核决定")
        validate_document(document_from_dict(updated))
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError(422, "INVALID_CORRECTION", str(exc)) from None
    old_version = task.document_version
    append_document(db, task, updated, "human")
    task.review_pending = pending(updated)
    db.add(Correction(task_id=task.id, from_version=old_version, to_version=task.document_version,
                      decisions_json=json.dumps(decisions, ensure_ascii=False)))


def prepare_engine_decisions(doc, decisions, queue, image):
    from audit_engine.label_check.review.apply import _field_at_path
    from app.services.vision_client import _replace_path
    engine_doc, payload = copy.deepcopy(doc), copy.deepcopy(decisions)
    pending_items = {item["path"]: item for item in queue["items"]}
    evidence = {e["id"]: e for e in doc["evidence"]}
    for row in payload["decisions"]:
        item = pending_items[row["path"]]
        if (row["originalRaw"] != item["raw"] or row["originalValue"] != item["value"]
                or row["originalUnit"] != item["unit"]
                or row["sourceEvidenceRefs"] != [e.id for e in item["evidence"]]):
            raise ValueError("复核原值或证据已变化")
        field = to_dict(_field_at_path(document_from_dict(engine_doc), row["path"]))
        images = {evidence[r]["imageId"] for r in field["sourceRefs"] if evidence[r]["imageId"] is not None}
        if row["path"] in {"facts.productName", "facts.netContent", "facts.ingredientsText", "facts.allergenText"} and len(images) > 1:
            if image["id"] not in images:
                raise ValueError("冲突字段不属于当前原图")
            field["sourceRefs"] = [r for r in field["sourceRefs"] if evidence[r]["imageId"] in {image["id"], None}]
            _replace_path(engine_doc, row["path"], field)
        row["sourceEvidenceRefs"] = field["sourceRefs"]
    return engine_doc, payload


def confirm_no_table(doc, body, image):
    # An image-bound request may only resolve its own image-wide missing-table
    # diagnostic. Existing detected tables must be reviewed, never deleted here.
    if body.no_table_images != [image["id"]]:
        raise ValueError("无营养表确认只能提交当前原图，且不得重复")
    if body.manual_tables or body.table_decisions:
        raise ValueError("无营养表确认不能同时录入或确认营养表")
    evidence = {e["id"]: e for e in doc["evidence"]}
    for table in doc["facts"]["nutritionTables"]:
        refs = [*table.get("sourceRefs", []), *table["basis"]["sourceRefs"]]
        for row in table["rows"]:
            for field in (row["nutrient"], row["amount"], row.get("nrvPercent")):
                if field:
                    refs.extend(field["sourceRefs"])
        if any(evidence[r]["imageId"] == image["id"] for r in refs):
            raise ValueError("当前图片已有营养表，不能声明无表")
    diagnostics = [d for d in doc.get("tableDiagnostics", [])
                   if d["imageId"] == image["id"] and d["status"] != "ok"]
    if not diagnostics or any(d.get("tableId") is not None for d in diagnostics):
        raise ValueError("没有可确认无表的待复核诊断")
    identifier = "manual-no-table:" + str(len(doc["evidence"]) + 1)
    doc["evidence"].append({"id": identifier, "imageId": None, "sourceType": "manual",
                            "text": "人工确认本图无营养表", "boxPx": None, "ocrConfidence": None})
    for diagnostic in diagnostics:
        diagnostic.update(status="ok", code="HUMAN_CONFIRMED_NO_TABLE", message="人工确认本图无营养表")
        diagnostic["evidenceRefs"] = [*diagnostic["evidenceRefs"], identifier]
    return doc


def manual_table(doc, table, image):
    doc = associate_review_tables(doc)
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
    if old is None and any(
        any(e["id"] in table_refs(existing) and e["imageId"] == image["id"] for e in doc["evidence"])
        for existing in doc["facts"]["nutritionTables"]
    ):
        raise ValueError("当前图片已有候选营养表，请使用原表 ID 调整结构，不能新增重复表")
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
