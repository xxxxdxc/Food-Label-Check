"""Preview one task's managed files; --confirm <same UUID> deletes that task."""
import argparse
import json
import uuid

from sqlalchemy import delete, select
from app.config import get_settings
from app.db import CheckRun, Correction, Document, File, Issue, IssueDecision, Job, Task, init_db
from app.db import ClaimPrecheck, ClaimPrecheckAttempt, ClaimPrecheckValidation
from app.deps import begin_write
from app.errors import AppError
from app.services.audit_service import get_task
from app.services.storage import file_path


def delete_audit(task_id, confirm=None):
    if str(uuid.UUID(task_id)) != task_id:
        raise ValueError("需要完整的小写任务 UUID")
    if confirm is not None and confirm != task_id:
        raise ValueError("--confirm 必须与任务 UUID 完全一致")
    root = get_settings().data_dir.resolve()
    with init_db()() as db:
        begin_write(db)
        task = get_task(db, task_id)
        if db.scalar(select(Job.id).where(Job.task_id == task_id, Job.status == "running")):
            raise AppError(409, "JOB_RUNNING", "请先停止 Worker，等待任务锁恢复后再删除")
        if db.scalar(select(ClaimPrecheck.id).where(ClaimPrecheck.task_id == task_id, ClaimPrecheck.status == "running")):
            raise AppError(409, "JOB_RUNNING", "模型初审正在运行，请等待结束后再删除")
        raw_root = (root / "ocr" / task_id).resolve()
        if raw_root.parent != (root / "ocr").resolve() or not raw_root.is_relative_to(root):
            raise ValueError("OCR 目录不在 DATA_DIR 内")
        paths = []
        for file in task.files:
            paths.append(file_path(file))
            paths.extend(raw_root / (file.id + suffix) for suffix in
                         (".accurate.json", ".table.json", ".accurate.tmp", ".table.tmp"))
        paths = [p for p in paths if p.is_file()]
        if any(not p.resolve().is_relative_to(root) for p in paths):
            raise ValueError("文件不在 DATA_DIR 内")
        result = {"task_id": task_id, "deleted": confirm is not None, "files": [str(p) for p in paths]}
        if confirm is None:
            return result
        # Delete children first; foreign-key checks remain enabled.
        db.execute(delete(ClaimPrecheckValidation).where(ClaimPrecheckValidation.batch_id.in_(
            select(ClaimPrecheck.id).where(ClaimPrecheck.task_id == task_id))))
        db.execute(delete(ClaimPrecheckAttempt).where(ClaimPrecheckAttempt.batch_id.in_(
            select(ClaimPrecheck.id).where(ClaimPrecheck.task_id == task_id))))
        db.execute(delete(ClaimPrecheck).where(ClaimPrecheck.task_id == task_id))
        for model in (IssueDecision, Issue, CheckRun, Correction, Document, Job, File):
            db.execute(delete(model).where(model.task_id == task_id))
        db.execute(delete(Task).where(Task.id == task_id))
        db.commit()
    for path in paths:
        path.unlink(missing_ok=True)
    if raw_root.is_dir() and not any(raw_root.iterdir()):
        raw_root.rmdir()
    return result


def main():
    parser = argparse.ArgumentParser(description="删除单个任务及其文件（请先停止 API 和 Worker，并备份 DATA_DIR）")
    parser.add_argument("task_id")
    parser.add_argument("--confirm", help="再次输入同一个任务 UUID 才执行删除；省略时仅预览")
    args = parser.parse_args()
    try:
        print(json.dumps(delete_audit(args.task_id, args.confirm), ensure_ascii=False, indent=2))
    except (ValueError, AppError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
