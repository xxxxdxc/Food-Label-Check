"""Derive review associations without rewriting historical OCR snapshots."""
import copy


def table_refs(table):
    refs = set(table.get("sourceRefs", [])) | set(table["basis"]["sourceRefs"])
    fields = [table["basis"].get("servingSize")]
    fields.extend(field for row in table["rows"]
                  for field in (row["nutrient"], row["amount"], row.get("nrvPercent")))
    for field in fields:
        if field:
            refs.update(field["sourceRefs"])
    return refs


def associate_review_tables(doc):
    """Bind only table-local failures whose evidence identifies one table.

    Image-wide omissions/count failures must remain separate even if only one
    candidate was recovered. Association never changes a review status.
    """
    result = copy.deepcopy(doc)
    evidence = {e["id"]: e for e in result["evidence"]}
    tables = [(t, table_refs(t)) for t in result["facts"]["nutritionTables"]]
    for diagnostic in result.get("tableDiagnostics", []):
        if (diagnostic["status"] == "ok" or diagnostic.get("tableId") is not None
                or diagnostic["code"] not in {"TABLE_V2_NOT_FOUND", "TABLE_LAYOUT_RECOVERED"}):
            continue
        refs = set(diagnostic["evidenceRefs"])
        image_id = diagnostic["imageId"]
        if not refs or any(evidence.get(ref, {}).get("imageId") != image_id for ref in refs):
            continue
        candidates = [table for table, sources in tables if refs <= sources
                      and {evidence[ref]["imageId"] for ref in sources
                           if ref in evidence and evidence[ref]["imageId"] is not None} == {image_id}]
        if len(candidates) == 1:
            diagnostic["tableId"] = candidates[0]["id"]
    return result
