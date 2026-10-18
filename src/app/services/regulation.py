"""Seed only pending mappings; verification requires a human checking original standards."""
import json
from app.config import SRC_ROOT
from app.db import RuleClause, init_db


def seed_clauses():
    records = json.loads((SRC_ROOT / "app" / "data" / "rule_clauses.seed.json").read_text(encoding="utf-8"))
    with init_db()() as db:
        for record in records:
            if db.get(RuleClause, record["rule_id"]) is None:
                db.add(RuleClause(**record))
        db.commit()
