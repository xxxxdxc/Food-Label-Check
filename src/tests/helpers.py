"""Synthetic, non-private image and LabelDocument fixtures."""
import io
from PIL import Image
from audit_engine.label_check.shared import to_dict, validate_document
from audit_engine.label_check.shared.models import (
    Basis, BoxPx, Evidence, Facts, FieldValue, ImageInfo, LabelDocument, NutritionRow, NutritionTable,
)


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (120, 100), "white").save(stream, format="PNG")
    return stream.getvalue()


def synthetic_document(uncertain=True):
    evidence = []

    def field(value, unit=None, status="ready"):
        identifier = "ocr:" + str(len(evidence) + 1)
        evidence.append(Evidence(identifier, "front", "baidu_accurate", value + (unit or ""),
                                 BoxPx(5, 5, 50, 10), .5 if status == "needs_review" else .99))
        return FieldValue(value, value, unit, [identifier], status,
                          ["LOW_OCR_CONFIDENCE"] if status == "needs_review" else [])
    facts = Facts(productName=field("测试食品"), netContent=field("100", "g"), ingredientsText=field("大豆、白砂糖"))
    basis_field = field("每100g")
    rows = []
    for key, amount, unit, percent in (("energy", "800", "kJ", "10"), ("protein", "32", "g", "5"),
                                        ("fat", "4", "g", "7"), ("carbohydrate", "35", "g", "12")):
        rows.append(NutritionRow(field(key), field(amount, unit, "needs_review" if uncertain and key == "protein" else "ready"), field(percent, "%")))
    facts.nutritionTables = [NutritionTable("table-1", Basis("per_100g", "每100g", basis_field.sourceRefs, "ready"), rows)]
    doc = LabelDocument("1.0", "synthetic", None, [ImageInfo("front", "synthetic.png", 120, 100)], evidence, facts, [])
    validate_document(doc)
    return to_dict(doc)
