import time
from pathlib import Path
from sqlalchemy import text
from app.db import Session

def check():
    if time.time()-Path('state/worker.heartbeat').stat().st_mtime>300:
        raise RuntimeError('Worker heartbeat stale')
    with Session() as db:db.execute(text('SELECT 1'))

if __name__=='__main__':check()
