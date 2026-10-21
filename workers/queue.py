from datetime import timedelta
from sqlalchemy import select, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.db import Task, now

def enqueue(db, key, kind, payload=None):
    values = dict(key=key, kind=kind, payload=payload or {}, status='pending', attempts=0, available_at=now())
    insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
    db.execute(insert(Task).values(**values).on_conflict_do_nothing(index_elements=['key']))
    db.commit()

def claim(db, lease_seconds=120):
    # Lock on PostgreSQL; conditional update also protects SQLite test/local consumers.
    t = db.scalar(select(Task).where(or_((Task.status == 'pending') & (Task.available_at <= now()), (Task.status == 'running') & (Task.lease_until < now()))).order_by(Task.id).with_for_update(skip_locked=True).limit(1))
    if not t: return None
    result = db.execute(update(Task).where(Task.id == t.id, Task.status == t.status, Task.attempts == t.attempts).values(status='running', attempts=Task.attempts + 1, lease_until=now()+timedelta(seconds=lease_seconds)))
    db.commit()
    if result.rowcount != 1: return None
    db.refresh(t)
    return t

def fail(db, task, error, max_attempts=5):
    task.error = type(error).__name__  # Do not persist credentials or sensitive exception bodies.
    task.status = 'failed' if task.attempts >= max_attempts else 'pending'
    task.available_at = now() + timedelta(seconds=min(3600, 2 ** task.attempts * 10))
    task.lease_until = None
    db.commit()
