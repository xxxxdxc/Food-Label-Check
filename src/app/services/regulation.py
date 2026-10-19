"""Seed only pending mappings; verification requires a human checking original standards."""
import json
from functools import lru_cache
from app.config import SRC_ROOT
from app.db import RuleClause, init_db


@lru_cache(maxsize=1)
def _standard_sources():
    return json.loads((SRC_ROOT / "app" / "data" / "standard_sources.json").read_text(encoding="utf-8"))


def standard_source(regulation, rule_id):
    """Enrich new and historical projections; never assert clause verification."""
    if regulation.verification_status in {"course_requirement", "not_applicable"}:
        return regulation.model_copy(update={"source_title": None, "source_url": None})
    records = _standard_sources()
    source = next((r for r in records if r["standard"] == regulation.standard
                   and r["edition"] == regulation.edition
                   and (rule_id in r["rule_ids"] or any(rule_id.startswith(p) for p in r["rule_prefixes"]))), None)
    return regulation.model_copy(update={"source_title": source["source_title"] if source else None,
                                         "source_url": source["source_url"] if source else None})


def seed_clauses():
    records = json.loads((SRC_ROOT / "app" / "data" / "rule_clauses.seed.json").read_text(encoding="utf-8"))
    with init_db()() as db:
        for record in records:
            if db.get(RuleClause, record["rule_id"]) is None:
                db.add(RuleClause(**record))
        db.commit()
