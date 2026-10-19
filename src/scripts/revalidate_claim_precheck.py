"""Recover a current batch from its saved response, without sending a request."""
import argparse
import json

from app.db import init_db
from app.services.claim_revalidation import revalidate_saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", type=int, required=True)
    args = parser.parse_args()
    with init_db()() as db:
        result = revalidate_saved(db, args.batch_id)
        db.commit()
    print(json.dumps({"status": result["status"], "completed": sum(i["status"] == "completed" for i in result["items"]),
                      "failed": sum(i["status"] == "unavailable" for i in result["items"]),
                      "source_attempt_id": result["revalidated_from_attempt_id"], "new_model_calls": 0}))


if __name__ == "__main__":
    main()
