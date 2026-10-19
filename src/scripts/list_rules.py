"""List literal rule IDs and dynamic prefixes from the actual Python AST."""
import ast
import json
from pathlib import Path


def catalog():
    root = Path(__file__).resolve().parents[1]
    paths = [*(root / "audit_engine" / "label_check_v2").glob("*.py"), root / "app" / "engine_bridge.py"]
    records = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "finding" or len(node.args) < 3:
                continue
            rule = node.args[2]
            records.append({"source": path.relative_to(root).as_posix(), "line": node.lineno,
                            "rule_id_expression": ast.unparse(rule),
                            "authority_expression": ast.unparse(node.args[4]) if len(node.args) > 4 else None})
    return sorted(records, key=lambda r: (r["source"], r["line"]))


if __name__ == "__main__":
    print(json.dumps(catalog(), ensure_ascii=False, indent=2))
