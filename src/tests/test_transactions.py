"""Offline evidence for atomic upload publication and safe retry selection."""
import json
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from app.db import Document, Job, Task, init_db
from app.errors import AppError
from app.services.commands import create_task, retry_task
from app.services.samples import prepare_sample
from app.services.storage import file_path


def test_commit_failure_rolls_back_only_new_files():
    identifier = str(uuid.uuid4())
    prepared = prepare_sample("4260290260010", identifier)
    with init_db()() as db:
        with patch.object(db, "commit", side_effect=RuntimeError("disk full")):
            with pytest.raises(RuntimeError):
                create_task(db, identifier, "sample", "4260290260010", None, [prepared])
    assert not file_path(prepared[0]).exists()
    with init_db()() as db:
        assert db.get(Task, identifier) is None
        assert db.scalar(select(Job).where(Job.task_id == identifier)) is None


def test_upload_publication_never_overwrites_existing_file():
    identifier = str(uuid.uuid4())
    prepared = prepare_sample("4260290260010", identifier)
    path = file_path(prepared[0])
    path.write_bytes(b"existing-owned-content")
    with init_db()() as db, pytest.raises(FileExistsError):
        create_task(db, identifier, "sample", "4260290260010", None, [prepared])
    assert path.read_bytes() == b"existing-owned-content"


def test_successful_publication_keeps_committed_files_and_job():
    identifier = str(uuid.uuid4())
    prepared = prepare_sample("4260290260010", identifier)
    with init_db()() as db:
        result = create_task(db, identifier, "sample", "4260290260010", None, [prepared])
    assert result.id == identifier
    assert file_path(prepared[0]).is_file()
    with init_db()() as db:
        assert db.get(Task, identifier).files[0].sha256 == prepared[0].sha256
        assert db.scalar(select(Job).where(Job.task_id == identifier)).kind == "extract"


def test_retry_rejects_an_older_document_instead_of_scheduling_check():
    identifier = str(uuid.uuid4())
    with init_db()() as db:
        db.add(Task(id=identifier, status="FAILED", standard_mode="gb28050_2025_precheck", source="upload",
                    document_version=1, review_pending=0,
                    error_json=json.dumps({"code": "WORKER_ERROR", "retryable": True})))
        db.flush()
        db.add(Document(task_id=identifier, version=0, doc_json="{}", created_by="ocr"))
        db.commit()
    with init_db()() as db, pytest.raises(AppError) as caught:
        retry_task(db, identifier)
    assert caught.value.code == "DOCUMENT_NOT_FOUND"
    with init_db()() as db:
        assert db.get(Task, identifier).status == "FAILED"
        assert db.scalar(select(Job).where(Job.task_id == identifier)) is None
