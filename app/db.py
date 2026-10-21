import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, JSON, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

def now():
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class Candidate(Base):
    __tablename__ = 'candidates'
    id: Mapped[int] = mapped_column(primary_key=True)
    profile: Mapped[dict] = mapped_column(JSON)
    source_pdf: Mapped[str] = mapped_column(Text)
    photo: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(default=False)

class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    jd: Mapped[str] = mapped_column(Text)
    assessment: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Application(Base):
    __tablename__ = 'applications'
    __table_args__ = (UniqueConstraint('candidate_id', 'job_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey('candidates.id'))
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    status: Mapped[str] = mapped_column(default='draft')
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_number: Mapped[str | None] = mapped_column(String)

class Artifact(Base):
    __tablename__ = 'artifacts'
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey('applications.id'))
    kind: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String)

class EventLog(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey('applications.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    event: Mapped[str] = mapped_column(String)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

class HumanAction(Base):
    __tablename__ = 'human_actions'
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey('applications.id'))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Task(Base):
    __tablename__ = 'tasks'
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(default='pending')
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:///artifacts/local.db'))
Session = sessionmaker(engine, expire_on_commit=False)
def init_db():
    Base.metadata.create_all(engine)

class BrowserSession(Base):
    __tablename__ = 'browser_sessions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey('applications.id'), unique=True)
    status: Mapped[str] = mapped_column(String, default='active')
    spec: Mapped[dict] = mapped_column(JSON)
    cursor: Mapped[int] = mapped_column(default=0)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    failure_kind: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    capability_hash: Mapped[str | None] = mapped_column(String)
    capability_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
