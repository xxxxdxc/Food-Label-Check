"""Bounded live vision smoke test. Does not call Baidu or claim OCR accuracy."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from audit_engine.label_check.shared import to_dict
from audit_engine.label_check.shared.models import Facts, ImageInfo, LabelDocument, TableDiagnostic
from app.config import get_settings
from app.engine_bridge import checker_input
from app.services.ocr_client import ServiceError
from app.services.projection import project_fields, project_review_items
from app.services.storage import validate_image
from app.services.vision_client import propose_tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/vision-smoke"))
    args = parser.parse_args()
    if not 1 <= len(args.images) <= 2:
        parser.error("One or two explicitly authorized images only")
    settings = get_settings()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for source in args.images:
        data, width, height, _, suffix = validate_image(source.read_bytes())
        digest = hashlib.sha256(data).hexdigest()
        marker = args.output / (digest + ".attempted")
        if marker.exists():
            saved = args.output / (digest + ".result.json")
            if saved.exists():
                results.append(json.loads(saved.read_text(encoding="utf-8")))
            print(json.dumps({"image": source.name, "status": "already_attempted"}), flush=True)
            continue
        image_path = args.output / (digest + suffix)
        image_path.write_bytes(data)
        doc = to_dict(LabelDocument("1.0", digest, None, [ImageInfo("image", image_path.name, width, height)],
            [], Facts(), [], tableDiagnostics=[TableDiagnostic("VISION_SMOKE_NO_OCR", "needs_vision",
                "本测试未调用百度 OCR；只验证真实视觉与门禁", "image", None)]))
        # Exclusive marker is written before sending; reruns never spend twice.
        with marker.open("x", encoding="utf-8") as file:
            file.write("One live request authorized; automatic timeout retry disabled.\n")
        started = time.monotonic()
        result = {"image": source.name, "sha256": digest, "ocr_called": False,
                  "provider": settings.vlm_base_url, "requested_model": settings.vlm_model,
                  "maximum_attempts": 1}
        try:
            updated = propose_tables(doc, data, timeout_retries=0)
            fields = project_fields(updated, 1)
            inputs, _ = checker_input(updated, "1")
            assert not inputs["nutrients"], "Unconfirmed vision values entered rule calculations"
            assert all(f.status != "ready" for f in fields if f.vision)
            (args.output / (digest + ".document.json")).write_text(
                json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            result.update(status="passed", nutrition_tables=len(updated["facts"]["nutritionTables"]),
                          review_items=len(project_review_items(updated, 1)["items"]),
                          gate_blocks_unconfirmed=True)
        except ServiceError as exc:
            result.update(status="failed", error=exc.as_dict())
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (args.output / (digest + ".result.json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    (args.output / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if any(r["status"] == "failed" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
