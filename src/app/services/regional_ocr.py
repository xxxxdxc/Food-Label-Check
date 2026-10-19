"""Layout-first OCR, original-pixel evidence, and confidence/structure triage."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re

from PIL import Image
from audit_engine.label_check.ocr.dual import parse_dual, _amount_exceeds_per_100g
from audit_engine.label_check.ocr.parser import _field, _paragraph, _quantity, _barcode_check_digit
from audit_engine.label_check.shared import to_dict, validate_document
from audit_engine.label_check.shared.confidence import LOW_OCR_CONFIDENCE
from audit_engine.label_check.shared.models import BoxPx, Claim, Evidence, FieldValue
from audit_engine.label_check.shared.review import _fields
from app.config import get_settings
from app.services.ocr_client import ServiceError, require_ocr_capacity
from app.services.storage import file_path
from app.services.zoning import PIPELINE, prepare_regions, propose_regions, encode_crop

FIELD_ROLES = {"product_name": "productName", "net_content": "netContent",
               "ingredients": "ingredientsText", "allergens": "allergenText"}


def _save(path, value):
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    staging.replace(path)


def _point(x, y, transform):
    if (isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or
            not isinstance(y, (int, float)) or not math.isfinite(x) or not math.isfinite(y) or
            not 0 <= x <= transform["width"] or not 0 <= y <= transform["height"]):
        raise ServiceError("OCR_COORDINATES", "OCR 返回越出裁剪图的坐标，无法可靠定位")
    return (round(x * transform["scale"][0] + transform["origin"][0]),
            round(y * transform["scale"][1] + transform["origin"][1]))


def map_accurate(raw, transform):
    """Return a projection; keep the original service response unchanged in cache."""
    result = copy.deepcopy(raw)
    for line in result.get("words_result", []):
        score = (line.get("probability") or {}).get("average")
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or
                                  not math.isfinite(score) or not 0 <= score <= 1):
            raise ServiceError("OCR_CONFIDENCE", "OCR 返回无效置信度，拒绝自动放行")
        b = line.get("location")
        if not b:
            continue
        try:
            x, y = _point(b["left"], b["top"], transform)
            r, bottom = _point(b["left"] + b["width"], b["top"] + b["height"], transform)
        except (KeyError, TypeError):
            raise ServiceError("OCR_COORDINATES", "OCR 文字坐标格式无效") from None
        if r <= x or bottom <= y:
            raise ServiceError("OCR_COORDINATES", "OCR 文字区域尺寸无效")
        line["location"] = {"left": x, "top": y, "width": r - x, "height": bottom - y}
    return result


def map_table(raw, transform):
    result = copy.deepcopy(raw)
    def visit(item):
        if isinstance(item, list):
            for value in item:
                visit(value)
        elif isinstance(item, dict):
            for key, value in item.items():
                if key in {"table_location", "cell_location", "poly_location"} and value:
                    for point in value:
                        x, y = _point(point["x"], point["y"], transform)
                        point.update(x=x, y=y)
                else:
                    visit(value)
    visit(result)
    return result


def _contains(box, entry):
    b = entry.boxPx
    return b is not None and (box["left"] <= b.left + b.width / 2 <= box["left"] + box["width"] and
                             box["top"] <= b.top + b.height / 2 <= box["top"] + box["height"])


def _region_field(role, entries):
    entries = sorted(entries, key=lambda e: (e.boxPx.top, e.boxPx.left))
    raw = "\n".join(e.text for e in entries)
    text = "".join(e.text.strip() for e in entries)
    if role == "net_content":
        scores = [e.ocrConfidence for e in entries]
        score = min(scores) if all(s is not None for s in scores) else None
        combined = Evidence("temporary", entries[0].imageId, "baidu_accurate", text, entries[0].boxPx, score)
        quantity = _quantity(combined)
        return _field(raw, quantity.value, quantity.unit, entries, numeric=True, extra=quantity.reviewReasons)
    if role == "ingredients":
        # Reuse the bracket/anomaly checks, but do not depend on a printed heading.
        field = _paragraph(entries, r"配料(?:表)?[：:]\s*", r"致敏原信息[：:]|过敏原[：:]|贮藏条件[：:]|贮存条件[：:]")
        if field.status != "missing":
            return field
    text = re.sub({"product_name": r"^(?:产品名称|食品名称|品名)[：:]\s*",
                   "ingredients": r"^配料(?:表)?[：:]\s*",
                   "allergens": r"^(?:致敏原信息|过敏原信息|过敏原)[：:]\s*"}[role], "", text)
    text = re.split(r"[○●]|(?:产品类型|产品标准|贮藏条件|贮存条件|食用方法)[：:]", text)[0].strip()
    reasons = []
    normalized = text.replace("（", "(").replace("）", ")")
    depth = 0
    for char in normalized:
        depth += (1 if char == "(" else -1 if char == ")" else 0)
        if depth < 0:
            break
    if depth != 0 or re.search(r"[≥≤]\s*\(", normalized):
        reasons.append("OCR_TEXT_ANOMALY")
    if not text:
        reasons.append("OCR_REGION_EMPTY")
    return _field(raw, text or None, None, entries, extra=reasons)


def apply_regions(document, regions):
    """Use model roles only to select OCR evidence. All field values come from OCR."""
    accurate = [e for e in document.evidence if e.sourceType == "baidu_accurate"]
    candidates = {name: [] for name in FIELD_ROLES.values()}
    for index, region in enumerate(regions):
        role, box = region["kind"], region["box_px"]
        entries = [e for e in accurate if _contains(box, e)]
        if role in FIELD_ROLES:
            if entries:
                field = _region_field(role, entries)
            else:
                # Geometry only, not a transcribed model value. Keeps empty zones editable.
                ref = f"{document.images[0].id}:zone-empty:{index}"
                document.evidence.append(Evidence(ref, document.images[0].id, "codex_vision", "", BoxPx(**box), None))
                field = FieldValue(None, None, None, [ref], "needs_review", ["OCR_REGION_EMPTY"])
            candidates[FIELD_ROLES[role]].append(field)
        elif role == "claims":
            used = {ref for claim in document.facts.claims for ref in claim.text.sourceRefs}
            for e in entries:
                if e.id not in used:
                    document.facts.claims.append(Claim(f"zone-claim:{index}:{e.id}", "unknown",
                        _field(e.text, e.text, None, [e]), None, "unknown"))
        elif role == "barcode" and entries:
            digits = "".join(e.text.strip().replace(" ", "") for e in sorted(entries, key=lambda e: e.boxPx.left))
            if digits.isdecimal() and not any(b.value == digits for b in document.facts.barcodes):
                reasons = [] if len(digits) == 13 and _barcode_check_digit(digits) else ["BARCODE_CHECKSUM_INVALID"]
                document.facts.barcodes.append(_field(" ".join(e.text for e in entries), digits, None, entries, extra=reasons))
    conflicts = {}
    for name, values in candidates.items():
        if not values:
            continue
        selected = copy.deepcopy(values[0])
        signatures = {(v.value, v.unit) for v in values}
        selected.sourceRefs = list(dict.fromkeys(r for v in values for r in v.sourceRefs))
        selected.reviewReasons = list(dict.fromkeys(r for v in values for r in v.reviewReasons))
        if len(signatures) > 1:
            selected.reviewReasons.append("REGION_FIELD_CONFLICT")
            conflicts["facts." + name] = {"candidates": [{"image_id": document.images[0].id,
                "raw": v.raw, "value": v.value, "unit": v.unit} for v in values]}
        selected.status = "needs_review" if selected.reviewReasons else "ready"
        setattr(document.facts, name, selected)
    return conflicts


def triage_structure(document):
    """A table-level structure fault need not mark every otherwise clear cell uncertain."""
    by_id = {e.id: e for e in document.evidence}
    from audit_engine.label_check.ocr.parser import _basis
    for table in document.facts.nutritionTables:
        codes = [row.nutrient.value for row in table.rows]
        for row in table.rows:
            code = row.nutrient.value
            allowed = ({"kJ", "kcal"} if code == "energy" else {"g"} if code in {
                "protein", "fat", "saturated_fat", "trans_fat", "carbohydrate", "sugar", "dietary_fiber"}
                else {"mg", "g"} if code in {"sodium", "calcium"} else None)
            reason = ("NUTRIENT_UNIT_MISMATCH" if allowed and row.amount.unit not in allowed else
                      "AMOUNT_EXCEEDS_100G_BASIS" if _amount_exceeds_per_100g(table.basis, row.amount) else None)
            if reason:
                row.amount.status = "needs_review"
                row.amount.reviewReasons = list(dict.fromkeys([*row.amount.reviewReasons, reason]))
            if code and codes.count(code) > 1:
                row.nutrient.status = "needs_review"
                row.nutrient.reviewReasons = list(dict.fromkeys([*row.nutrient.reviewReasons, "DUPLICATE_NUTRIENT"]))
        basis = table.basis
        accurate = [by_id[r] for r in basis.sourceRefs if by_id[r].sourceType == "baidu_accurate"]
        matching = [e for e in accurate if e.text.strip() == (basis.raw or "").strip() and _basis([e]).kind == basis.kind]
        if (basis.status == "needs_review" and basis.kind != "unknown" and matching and
                all(e.ocrConfidence is not None and e.ocrConfidence >= LOW_OCR_CONFIDENCE for e in accurate)):
            basis.status = "ready"
    for path, field in _fields(document):
        if not path.startswith("facts.nutritionTables[") or not hasattr(field, "reviewReasons"):
            continue
        if field.reviewReasons != ["TABLE_STRUCTURE_UNCERTAIN"]:
            continue
        accurate = [by_id[r] for r in field.sourceRefs if by_id[r].sourceType == "baidu_accurate"]
        if field.value is not None and accurate and all(e.ocrConfidence is not None and e.ocrConfidence >= LOW_OCR_CONFIDENCE for e in accurate):
            field.status, field.reviewReasons = "ready", []
    # The structural diagnostic itself is retained, so computation stays blocked
    # until ambiguous row/column mapping is resolved. No table is declared complete here.


def extract_file(task, file, client):
    settings = get_settings()
    path = file_path(file)
    image_bytes = path.read_bytes()
    image_sha = hashlib.sha256(image_bytes).hexdigest()
    with Image.open(path) as image:
        width, height = image.size
    if image_sha != file.sha256 or (width, height) != (file.width_px, file.height_px):
        raise ServiceError("OCR_IMAGE_CHANGED", "原图指纹或尺寸已变化，拒绝识别")
    # Keep filenames short enough for Windows installations without long-path
    # support. Full fingerprints inside the record still prevent cache mixing.
    cache = settings.data_dir / "ocr" / task.id
    cache.mkdir(parents=True, exist_ok=True)
    plan_key = hashlib.sha256((PIPELINE + file.id + image_sha).encode()).hexdigest()
    plan_path = cache / ("z1-" + plan_key[:16] + ".json")
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    else:
        try:
            layout = propose_regions(image_bytes)
            units = prepare_regions(layout, width, height)
            plan = {"pipeline": PIPELINE, "file_id": file.id, "image_sha256": image_sha, "mode": "regions", "units": units, "warnings": []}
        except (ServiceError, ValueError, TypeError, KeyError) as exc:
            warning = (exc.as_dict() if isinstance(exc, ServiceError) else
                       {"code": "ZONING_INVALID", "message": "分区结果无法使用，已改用整图 OCR", "retryable": False})
            warning["message"] = warning["message"] + "（采用整图 OCR 降级路径）"
            plan = {"pipeline": PIPELINE, "file_id": file.id, "image_sha256": image_sha, "mode": "whole_image_fallback",
                    "units": [{"crop": [0, 0, width, height], "table": True, "regions": []}], "warnings": [warning]}
        _save(plan_path, plan)
    if plan.get("pipeline") != PIPELINE or plan.get("image_sha256") != image_sha or plan.get("file_id") != file.id:
        raise ServiceError("OCR_CACHE_MISMATCH", "分区缓存与原图不匹配，拒绝混用")
    prepared = []
    for unit in plan["units"]:
        encoded, transform = encode_crop(image_bytes, unit["crop"])
        identity = hashlib.sha256(PIPELINE.encode() + encoded + json.dumps(unit, sort_keys=True).encode()).hexdigest()
        for kind in (("accurate", "table") if unit["table"] else ("accurate",)):
            request_key = hashlib.sha256((identity + kind).encode()).hexdigest()
            prepared.append((unit, encoded, transform, kind, request_key, cache / ("r1-" + request_key[:16] + "." + kind + ".json")))
    from app.db import init_db
    from app.errors import AppError
    remaining = sum(not p.is_file() for *_, p in prepared)
    try:
        if remaining:
            with init_db()() as db:
                require_ocr_capacity(db, remaining)
    except AppError:
        raise ServiceError("OCR_QUOTA", "分区后实际需要的 OCR 调用数超过今日剩余额度，请稍后重试", True) from None
    lines, tables, seen = [], [], {}
    for unit, encoded, transform, kind, identity, response_path in prepared:
        if response_path.is_file():
            record = json.loads(response_path.read_text(encoding="utf-8"))
            if record.get("cache_key") != identity:
                raise ServiceError("OCR_CACHE_MISMATCH", "OCR 缓存指纹不匹配，拒绝混用")
            raw = record["response"]
        else:
            raw = client.recognize(encoded, kind)
            _save(response_path, {"cache_key": identity, "response": raw})
        if kind == "accurate":
            for line in map_accurate(raw, transform).get("words_result", []):
                signature = json.dumps([line.get("words"), line.get("location")], sort_keys=True, ensure_ascii=False)
                if signature not in seen:
                    seen[signature] = line
                    lines.append(line)
                else:
                    # Overlapping crops must never upgrade a lower-confidence observation.
                    old = seen[signature]
                    scores = [(v.get("probability") or {}).get("average") for v in (old, line)]
                    old["probability"] = ({"average": min(scores)} if all(isinstance(s, (int, float)) for s in scores) else {})
        else:
            tables.extend(map_table(raw, transform).get("tables_result", []))
    lines.sort(key=lambda item: ((item.get("location") or {}).get("top", 0), (item.get("location") or {}).get("left", 0)))
    doc = parse_dual(path, {"words_result": lines}, {"table_num": len(tables), "tables_result": tables}, task.id,
                     image_id=file.id, expect_nutrition_table=any(u["table"] for u in plan["units"]))
    regions = [r for u in plan["units"] for r in u["regions"]]
    conflicts = apply_regions(doc, regions)
    triage_structure(doc)
    # An explicitly zoned table with no usable OCR still requires a human. Never
    # treat a model's absence of table regions as proof a table does not exist.
    validate_document(doc)
    result = to_dict(doc)
    result["_platform"] = {"pipeline": PIPELINE, "comparisons": conflicts,
        "extraction": [{"image_id": file.id, "mode": plan["mode"], "regions": regions,
                        "ocr_calls": len(prepared), "cache_key": image_sha}]}
    return result, [{**w, "image_id": file.id} for w in plan["warnings"]]
