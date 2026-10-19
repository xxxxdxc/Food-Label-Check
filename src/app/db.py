"""SQLite tables and short lived SQLAlchemy sessions."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import create_engine, event, ForeignKey, String, Text, Integer, DateTime, Boolean, UniqueConstraint, Index, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow():
    return datetime.now(timezone.utc)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String)
    standard_mode: Mapped[str] = mapped_column(String)
    channel: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    sample_id: Mapped[str | None] = mapped_column(String)
    document_version: Mapped[int] = mapped_column(Integer, default=0)
    check_doc_version: Mapped[int | None] = mapped_column(Integer)
    review_pending: Mapped[int] = mapped_column(Integer, default=0)
    summary_json: Mapped[str | None] = mapped_column(Text)
    error_json: Mapped[str | None] = mapped_column(Text)
    warnings_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    files: Mapped[list[File]] = relationship(back_populates="task", cascade="all, delete-orphan")


class File(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    display_name: Mapped[str] = mapped_column(String)
    stored_name: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String)
    width_px: Mapped[int] = mapped_column(Integer)
    height_px: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String)
    task: Mapped[Task] = relationship(back_populates="files")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("task_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    doc_json: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Correction(Base):
    __tablename__ = "corrections"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    from_version: Mapped[int] = mapped_column(Integer)
    to_version: Mapped[int] = mapped_column(Integer)
    decisions_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CheckRun(Base):
    __tablename__ = "check_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    doc_version: Mapped[int] = mapped_column(Integer)
    engine_input_json: Mapped[str] = mapped_column(Text)
    engine_output_json: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Issue(Base):
    __tablename__ = "issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    check_run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"), index=True)
    issue_json: Mapped[str] = mapped_column(Text)


class IssueDecision(Base):
    __tablename__ = "issue_decisions"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    issue_id: Mapped[str] = mapped_column(String, primary_key=True)
    doc_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision: Mapped[str] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("one_active_job", "task_id", unique=True,
                            sqlite_where=text("status IN ('queued', 'running')")),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class RuleClause(Base):
    __tablename__ = "rule_clauses"
    rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    standard: Mapped[str] = mapped_column(String)
    edition: Mapped[str] = mapped_column(String)
    clause_id: Mapped[str | None] = mapped_column(String)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_note: Mapped[str | None] = mapped_column(Text)


class ClaimPrecheck(Base):
    __tablename__ = "claim_prechecks"
    __table_args__ = (UniqueConstraint("task_id", "doc_version", "fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    doc_version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)
    input_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClaimPrecheckAttempt(Base):
    __tablename__ = "claim_precheck_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("claim_prechecks.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClaimPrecheckValidation(Base):
    """A derived offline validation; never another model request/attempt."""
    __tablename__ = "claim_precheck_validations"
    __table_args__ = (UniqueConstraint("source_attempt_id", "validator_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("claim_prechecks.id"), index=True)
    source_attempt_id: Mapped[int] = mapped_column(ForeignKey("claim_precheck_attempts.id"))
    validator_version: Mapped[str] = mapped_column(String)
    input_fingerprint: Mapped[str] = mapped_column(String)
    raw_sha256: Mapped[str] = mapped_column(String)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine = None
Session = None
_db_path = None


def init_db():
    global _engine, Session, _db_path
    root = get_settings().data_dir.resolve()
    if _engine is None or _db_path != root:
        if _engine is not None:
            _engine.dispose()
        _db_path = root
        root.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{(root / 'app.db').as_posix()}", connect_args={"timeout": 30})
        @event.listens_for(_engine, "connect")
        def sqlite_pragmas(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        Session = sessionmaker(_engine, expire_on_commit=False)
        Base.metadata.create_all(_engine)
        # create_all does not add columns to databases from previous releases.
        with _engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(tasks)")}
            if "warnings_json" not in columns:
                connection.exec_driver_sql("ALTER TABLE tasks ADD COLUMN warnings_json TEXT")
            connection.exec_driver_sql(
                "UPDATE tasks SET warnings_json='[' || error_json || ']', error_json=NULL "
                "WHERE status != 'FAILED' AND error_json IS NOT NULL "
                "AND json_extract(error_json, '$.code') LIKE 'VLM_%' AND warnings_json IS NULL")
    return Session


class Usage(Base):
    __tablename__ = "daily_usage"
    day: Mapped[str] = mapped_column(String, primary_key=True)
    service: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
