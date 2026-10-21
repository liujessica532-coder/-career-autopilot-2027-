from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from app.db import init_db, Session

@asynccontextmanager
async def lifespan(app):
    init_db()
    yield

app = FastAPI(title='Career Autopilot', lifespan=lifespan)

@app.get('/health')
def health():
    with Session() as db: db.execute(text('SELECT 1'))
    return {'status': 'ok'}
