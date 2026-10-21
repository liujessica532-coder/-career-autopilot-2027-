import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base
from workers.queue import enqueue, claim

def test_postgres_locking():
    url=os.getenv('TEST_POSTGRES_URL')
    if not url: pytest.skip('Requires isolated PostgreSQL test database')
    engine=create_engine(url)
    Base.metadata.create_all(engine)
    # Caller must provide disposable database; no production database cleanup.
    with sessionmaker(engine)() as db:
        enqueue(db,'postgres-lock-test','report')
        first=claim(db)
        assert first is not None
        with sessionmaker(engine)() as other:
            assert claim(other) is None
        first.status='done';db.commit()
    engine.dispose()
