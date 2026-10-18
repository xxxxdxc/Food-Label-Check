"""Pass only ready facts to the deterministic engine; expose every gate."""
from __future__ import annotations

import re
from audit_engine.label_check_v2.core import EXPECTED_UNITS, finding, load_policy
from audit_engine.label_check_v2.engine import run
from audit_engine.label_check_v2.packaging import evaluate_packaging, ALLERGEN_TERMS

SCOPE = ("GB 28050-2025 新版预检（2027-03-16 起实施）；"
         "GB 7718 仅覆盖必填字段有无与可计算的配料顺序。"
         "仅对已提供数据初检，覆盖范围有限，不等同人工签核。")
BASIC = {"food_name": ("productName", "食品名称"), "net_content": ("netContent", "净含量"),
         "ingredients_text": ("ingredientsText", "配料表")}


def refs(*fields):
    return list(dict.fromkeys(ref for field in fields if field for ref in field.get("sourceRefs", [])))


def ready(field):
    return bool(field and field["status"] == "ready" and field.get("value") is not None)


def checker_input(document: dict, revision: str, channel: str | None = None):
    facts = document["facts"]
    tables = facts["nutritionTables"]
    gates = []
    payload = {"product_id": document.get("productId"), "revision": revision, "basis": "per_100g",
               "table_header": "每100g", "nutrition_complete": False, "nutrients": {}}
    table = tables[0] if len(tables) == 1 else None
    structural = bool(table) and not any(
        d["status"] != "ok" and d.get("tableId") in {table["id"], None}
        for d in document.get("tableDiagnostics", []))
    basis = table["basis"] if table else None
    supported = bool(basis and basis["status"] == "ready" and basis["kind"] in {"per_100g", "per_serving"})
    serving = basis.get("servingSize") if basis else None
    if supported and basis["kind"] == "per_serving":
        supported = ready(serving) and serving["unit"] == "g" and float(serving["value"]) > 0
    can_nutrition = structural and supported
    if not can_nutrition:
        message = ("当前版本暂不支持多张营养表" if len(tables) > 1 else
                   "营养表行列结构待复核" if table and not structural else
                   "营养表缺失或基准未确认；每100mL等口径暂不支持")
        gates.append(finding("L1", "NOT_CHECKED", "OCR-DOWNSTREAM-GATE", message, "input",
                             [{"source_refs": refs(basis)}] if basis else []))
    blocked_metrics = set()
    if can_nutrition:
        payload["basis"] = basis["kind"]
        payload["table_header"] = ("每100g" if basis["kind"] == "per_100g" and
                                  re.fullmatch(r"每\s*100\s*(?:克|[gG])", basis["raw"] or "") else basis["raw"])
        if serving:
            payload["serving_g"] = serving["value"]
        evidence = {e["id"]: e for e in document["evidence"]}
        seen = set()
        for row in table["rows"]:
            nutrient, amount, nrv = row["nutrient"], row["amount"], row.get("nrvPercent")
            key = nutrient.get("value")
            key = "fiber" if key == "dietary_fiber" else key
            if key in seen:
                payload["nutrients"].pop(key, None)
                blocked_metrics.add(key)
                gates.append(finding("L1", "NOT_CHECKED", "OCR-DUPLICATE-METRIC",
                                     "重复营养项，须人工核实", "input", [{"source_refs": refs(nutrient, amount)}]))
                continue
            seen.add(key)
            if not ready(nutrient) or not ready(amount) or key not in EXPECTED_UNITS:
                blocked_metrics.add(key)
                gates.append(finding("L1", "NOT_CHECKED", "OCR-FIELD-GATE",
                                     "营养项名称或含量待复核/不受支持", "input",
                                     [{"source_refs": refs(nutrient, amount)}]))
                continue
            field_refs = refs(nutrient, amount, basis, nrv if ready(nrv) else None)
            sources = [evidence[r] for r in field_refs if r in evidence]
            manual = any(e["sourceType"] == "manual" for e in sources)
            scored = [e["ocrConfidence"] for e in sources if e.get("ocrConfidence") is not None]
            item = {"value": amount["value"], "unit": amount["unit"], "source_refs": field_refs,
                    "source_text": amount["raw"], "review_status": "human_confirmed" if manual else "ocr_ready",
                    "confidence": None if manual or not scored else min(scored)}
            if ready(nrv):
                item["nrv_percent"] = nrv["value"]
            elif key not in load_policy()["nrv_not_defined"] or nrv is not None:
                gates.append(finding("L1", "NOT_CHECKED", "OCR-NRV-GATE",
                                     str(key) + "的NRV%缺失或待复核", "input",
                                     [{"source_refs": refs(nutrient, amount, nrv)}]))
            payload["nutrients"][key] = item
    payload["claims"] = []
    for claim in facts.get("claims", []):
        if claim.get("category") == "front_title":
            continue
        text = claim["text"]
        if ready(text):
            mapped = {"高蛋白": "high_protein", "富含蛋白质": "high_protein", "高纤维": "high_fiber",
                      "富含膳食纤维": "high_fiber", "低糖": "low_sugar", "无糖": "sugar_free"}
            payload["claims"].append(mapped.get(text["value"], text["value"]))
        else:
            gates.append(finding("L3", "NOT_CHECKED", "OCR-CLAIM-GATE", "声称文字待复核", "input",
                                 [{"source_refs": refs(text)}]))
    if channel:
        payload["channel"] = channel
        payload["label_text"] = "\n".join(e["text"] for e in document["evidence"] if e["sourceType"] != "manual")
    return payload, {"nutrition_enabled": can_nutrition, "blocked_metrics": list(blocked_metrics),
                     "findings": gates, "message": "仅已确认输入参与计算"}


def packaging_findings(document):
    facts = document["facts"]
    output = []
    for key, (name, label) in BASIC.items():
        field = facts[name]
        output.append(finding("L1", "PASS" if ready(field) else "NOT_CHECKED",
                              "PACKAGE-FIELD-" + key.upper(),
                              label + ("已在已确认字段中出现" if ready(field) else "缺少已确认输入，需核对完整包装"),
                              "packaging_completeness", [{"source_refs": refs(field)}]))
    # These fields do not exist in LabelDocument v1. Never infer absence from partial OCR.
    patterns = {"PRODUCER": r"(?:生产者|生产商|制造商|生产企业)[：:]?\s*\S{2,}",
                "SHELF_LIFE": r"保质期[：:]?\s*\d+\s*(?:天|个月|月|年)",
                "STORAGE": r"(?:贮存|储存|贮藏|储藏)(?:条件|方法)?[：:]?\s*\S{2,}",
                "LICENSE_NUMBER": r"SC\s*\d{14}"}
    for key, pattern in patterns.items():
        sources = [e for e in document["evidence"] if e["sourceType"] == "baidu_accurate" and re.search(pattern, e["text"], re.I)]
        present = any(e.get("ocrConfidence") is not None and e["ocrConfidence"] >= .96 for e in sources)
        output.append(finding("L1", "PASS" if present else "NOT_CHECKED", "PACKAGE-FIELD-" + key,
                              "已在高置信原文中出现；仅检查有无" if present else "未得到已确认的完整字段，需核对原稿",
                              "packaging_completeness", [{"source_refs": [e["id"] for e in sources]}]))
    ingredient = facts["ingredientsText"]
    pack = {}
    if ready(ingredient):
        text = re.sub(r"^配料(?:表)?[：:]?", "", ingredient["value"])
        parts = re.split(r"[、,，;；]", text)
        parsed = []
        for part in parts:
            match = re.fullmatch(r"\s*([^()（）%]+?)(?:[（(](\d+(?:\.\d+)?)%[）)])?\s*", part)
            if not match:
                parsed = []
                break
            row = {"name": match[1].strip()}
            if match[2]:
                row["amount_percent"] = match[2]
            parsed.append(row)
        if parsed:
            pack["ingredients"] = parsed
            declaration = facts["allergenText"]
            pack["allergen_declarations"] = [category for category, terms in ALLERGEN_TERMS.items()
                if ready(declaration) and any(term in declaration["value"] for term in terms)]
    ingredient_findings = evaluate_packaging({"packaging": pack})
    for item in ingredient_findings:
        item["evidence"] = [{"source_refs": refs(ingredient, facts["allergenText"])}]
    output.extend(ingredient_findings)
    if not any(i["rule_id"] == "INGREDIENT-ORDER" for i in ingredient_findings):
        output.append(finding("L1", "NOT_CHECKED", "INGREDIENT-ORDER",
                              "未提供完整可计算的配方占比，配料顺序需人工核实", "structured_ingredient_check",
                              [{"source_refs": refs(ingredient)}]))
    # Preserve each observed net-content location, including conflicting ready/OCR candidates.
    entries = [e for e in document["evidence"] if e["sourceType"] == "baidu_accurate"
               and re.search(r"净含量|净重|net\s*(?:weight|content)", e["text"], re.I)]
    values = []
    for e in entries:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|千克|g|克|ml|毫升|l|升)", e["text"], re.I)
        if match:
            from decimal import Decimal
            value = Decimal(match[1]) * (1000 if match[2].lower() in {"kg", "千克", "l", "升"} else 1)
            unit = "ml" if match[2].lower() in {"ml", "毫升", "l", "升"} else "g"
            values.append((str(value), unit, e))
    if len(values) > 1:
        uncertain = any(v[2].get("ocrConfidence") is None or v[2]["ocrConfidence"] < .96 for v in values)
        unequal = len({(v[0], v[1]) for v in values}) > 1
        output.append(finding("L1", "REVIEW" if uncertain else "FAIL" if unequal else "PASS",
                              "CROSS-NET_CONTENT", "跨位置净含量" + ("存在差异，需核对" if unequal else "一致"),
                              "cross_position", [{"source_refs": [v[2]["id"]]} for v in values],
                              {"observed": [{"value": v[0], "unit": v[1]} for v in values]}))
    return output


def check(document: dict, revision: str, channel: str | None = None, store=None):
    payload, gate = checker_input(document, revision, channel)
    output = run(payload, store=store)
    if not gate["nutrition_enabled"]:
        output["findings"] = [f for f in output["findings"] if f["layer"] in {"L2", "L3"}]
        output["normalized_per_100g"] = {}
    # Omitting an uncertain optional energy contributor must never produce a false PASS.
    if set(gate["blocked_metrics"]) & {"fiber", "energy", "protein", "fat", "carbohydrate", None, "unknown"}:
        output["findings"] = [f for f in output["findings"] if f["rule_id"] != "ENERGY-ARITHMETIC"]
        output["findings"].append(finding("L1", "NOT_CHECKED", "ENERGY-INPUT-GATE",
                                          "供能输入未全部确认，能量算式未检查", "input"))
    output["findings"].extend(gate["findings"])
    output["findings"].extend(packaging_findings(document))
    output["scope"] = SCOPE
    output["status"] = "FAIL" if any(f["status"] == "FAIL" for f in output["findings"]) else "REVIEW"
    output["human_queue"] = [f for f in output["findings"] if f["status"] in {"REVIEW", "NOT_CHECKED"}]
    return payload, output, gate
