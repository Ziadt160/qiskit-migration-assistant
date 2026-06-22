"""Jobs persistence for async migration requests.

Reworked from the original raw-sqlite module, which had three real bugs: the
`JobStatus` enum values were lowercase but the CHECK constraint required uppercase;
the FK referenced a `users` table that was never created; and the timestamp trigger
re-updated its own table. This version uses SQLAlchemy (so the same code runs on
SQLite locally and Postgres in production via `DATABASE_URL`), drops the phantom FK,
and handles timestamps with column defaults instead of a recursive trigger.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from qiskit_migration.config import get_settings


def _now() -> datetime:
    return datetime.now(UTC)


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    query: Mapped[str] = mapped_column(Text)  # the submitted code
    source_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING.value)
    result_code: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON MigrationResult
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


@dataclass
class JobRecord:
    """Detached, serialization-friendly view of a Job row."""

    id: str
    status: str
    query: str
    source_version: str | None
    result_code: str | None
    error: str | None
    user_id: str | None = None

    @classmethod
    def from_orm(cls, job: Job) -> JobRecord:
        return cls(
            id=job.id,
            status=job.status,
            query=job.query,
            source_version=job.source_version,
            result_code=job.result_code,
            error=job.error,
            user_id=job.user_id,
        )


class JobStore:
    def __init__(self, database_url: str | None = None):
        url = database_url or get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args, future=True)
        self._Session = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def create_job(
        self, query: str, source_version: str | None = None, user_id: str | None = None
    ) -> str:
        job_id = uuid.uuid4().hex
        with self._Session() as session:
            session.add(
                Job(
                    id=job_id,
                    query=query,
                    source_version=source_version,
                    user_id=user_id,
                    status=JobStatus.PENDING.value,
                )
            )
            session.commit()
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        with self._Session() as session:
            job = session.get(Job, job_id)
            return JobRecord.from_orm(job) if job else None

    def set_status(
        self,
        job_id: str,
        status: str,
        result_code: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._Session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.status = status
            if result_code is not None:
                job.result_code = result_code
            if error is not None:
                job.error = error
            session.commit()

    def recent(self, limit: int = 50) -> list[JobRecord]:
        with self._Session() as session:
            rows = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)).all()
            return [JobRecord.from_orm(j) for j in rows]


def init_db(database_url: str | None = None) -> JobStore:
    store = JobStore(database_url)
    store.create_all()
    return store


if __name__ == "__main__":
    init_db()
    print("Initialized jobs table.")
