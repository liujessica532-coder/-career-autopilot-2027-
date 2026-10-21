import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base

@pytest.fixture
def db(tmp_path):
    engine = create_engine('sqlite:///'+str(tmp_path/'test.db'))
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()
