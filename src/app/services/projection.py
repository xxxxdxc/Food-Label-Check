"""Project OCR facts and engine findings onto the public API contract."""
from __future__ import annotations

from collections import defaultdict
import re
from audit_engine.label_check.shared import document_from_dict
from audit_engine.label_check.shared.review import _fields
from audit_engine.label_check.shared.models import Basis
from app.schemas.contract import AuditField, AuditIssue, Evidence, Regulation
from app.services.regulation import standard_source

BASIC_LABELS = {"productName": "商品名称", "netContent": "净含量", "ingredientsText": "配料", "allergenText": "过敏原"}


def evidence_index(doc: dict) -> dict[str, Evidence]:
    return {row["id"]: Evidence(
        id=row["id"], image_id=row["imageId"], source_type=row["sourceType"],
        text=row["text"], box_px=row["boxPx"], ocr_confidence=row["ocrConfidence"],
    ) for row in doc["evidence"]}


def project_fields(doc: dict, version: int) -> list[AuditField]:
    typed = document_from_dict(doc)
    by_id = evidence_index(doc)
    output = []
    for path, field in _fields(typed):
        if path.startswith("facts.nutritionTables"):
            group = "nutrition"
            index = int(path.split("[")[1].split("]")[0])
            table = typed.facts.nutritionTables[index]
            suffix = path.split("].", 1)[1]
            match = re.match(r"rows\[(\d+)\]\.(.+)", suffix)
            if match:
                row = table.rows[int(match[1])]
                suffix = f"{row.nutrient.value or 'unknown_' + match[1]}.{match[2]}"
            field_id = "nutrition." + table.id + "." + suffix
            label = field_id
        else:
            group = "basic"
            name = path.removeprefix("facts.")
            field_id = name
            label = BASIC_LABELS.get(name, name)
        refs = [by_id[ref] for ref in field.sourceRefs if ref in by_id]
        comparison = doc.get("_platform", {}).get("comparisons", {}).get(path, {})
        vision_ref = (comparison.get("vision") or {}).get("evidence_id")
        if vision_ref in by_id and all(e.id != vision_ref for e in refs):
            refs.append(by_id[vision_ref])
        manual = any(item.source_type == "manual" for item in refs)
        output.append(AuditField(
            field_id=field_id, group=group, label=label,
            raw=field.raw, value=field.kind if isinstance(field, Basis) else field.value,
            unit=None if isinstance(field, Basis) else field.unit,
            status="human_confirmed" if manual and field.status == "ready" else field.status,
            review_reasons=[] if isinstance(field, Basis) else field.reviewReasons,
            evidence=refs, version=version,
            **doc.get("_platform", {}).get("comparisons", {}).get(path, {}),
        ))
    return output


def project_review_items(doc: dict, version: int) -> dict:
    from audit_engine.label_check.shared import collect_review_items
    from app.services.table_association import associate_review_tables
    doc = associate_review_tables(doc)
    typed = document_from_dict(doc)
    by_id = evidence_index(doc)
    items = []
    for row in collect_review_items(typed):
        comparison = doc.get("_platform", {}).get("comparisons", {}).get(row["path"], {})
        refs = [by_id[item["id"]] for item in row["evidence"] if item["id"] in by_id]
        vision_ref = (comparison.get("vision") or {}).get("evidence_id")
        if vision_ref in by_id and all(e.id != vision_ref for e in refs):
            refs.append(by_id[vision_ref])
        items.append({
            "path": row["path"], "raw": row["raw"], "value": row["value"],
            "unit": row["unit"], "review_reasons": row["reviewReasons"],
            "evidence": refs,
            **doc.get("_platform", {}).get("comparisons", {}).get(row["path"], {}),
        })
    tables = [{
        "table_id": row.get("tableId"), "code": row["code"], "message": row["message"],
        "image_id": row["imageId"], "box_px": row.get("boxPx"),
        "evidence_refs": row.get("evidenceRefs", []),
        "vision_no_table": row["imageId"] in doc.get("_platform", {}).get("vision_no_table_images", []),
    } for row in doc.get("tableDiagnostics", []) if row["status"] != "ok"]
    return {"pipeline": doc.get("_platform", {}).get("pipeline", "legacy_ocr_vision"),
            "extraction": doc.get("_platform", {}).get("extraction", []),
            "document_version": version, "items": items, "table_items": tables,
            "document_id": doc["documentId"], "product_id": doc["productId"],
            "images": doc["images"], "threshold": 0.96}


def project_issues(output: dict, doc: dict, version: int, clauses: dict | None = None) -> list[AuditIssue]:
    by_id = evidence_index(doc)
    counts = defaultdict(int)
    issues = []
    for finding in output.get("findings", []):
        rule_id = finding["rule_id"]
        counts[rule_id] += 1
        refs = []
        for source in finding.get("evidence", []):
            refs.extend(source.get("source_refs", []))
        authority = finding.get("authority")
        matches = [value for prefix, value in (clauses or {}).items() if rule_id.startswith(prefix)]
        clause = max(matches, key=lambda c: len(c["rule_id"])) if matches else None
        if rule_id == "CLAIM-UNKNOWN":
            regulation = Regulation(standard="", edition="", clause_id=None,
                                    verification_status="not_applicable", note="当前固定规则未覆盖，不表示已发现违规")
        elif authority == "course_requirement":
            regulation = Regulation(standard="课程/内部要求", edition="", clause_id=None,
                                    verification_status="course_requirement", note="非国标条款")
        elif clause and clause.get("verified") and clause.get("clause_id"):
            regulation = Regulation(standard=clause["standard"], edition=clause["edition"],
                                    clause_id=clause["clause_id"], verification_status="verified",
                                    note=clause.get("source_note"))
        else:
            standard = "GB 7718" if rule_id.startswith(("PACKAGE-", "INGREDIENT-", "ALLERGEN-")) else "GB 28050"
            regulation = Regulation(standard=clause["standard"] if clause else standard, edition="2025", clause_id=None,
                                    verification_status="pending", note="条款尚未核验")
        issues.append(AuditIssue(
            issue_id=f"{rule_id}:{counts[rule_id]}", rule_id=rule_id,
            layer=finding["layer"], status=finding["status"], title=rule_id,
            message=finding["message"], evidence=[by_id[ref] for ref in dict.fromkeys(refs) if ref in by_id],
            calculation=finding.get("details"), regulation=standard_source(regulation, rule_id),
            suggestion=(None if rule_id == "CLAIM-UNKNOWN" else
                        "请对照原图复核后修正，并使用最新版本重新检查" if finding["status"] in {"FAIL", "REVIEW"} else None),
            claim_id=finding.get("claim_id"),
            doc_version=version,
        ))
    return issues
