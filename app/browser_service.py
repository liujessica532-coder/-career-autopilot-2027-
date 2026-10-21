"""Authenticated, application-scoped human control of a real browser session."""
import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from app.db import Session, init_db, BrowserSession, Application, EventLog, now
from app.browser_runtime import BrowserManager
from app.browser_failures import BrowserProblem

STATIC=Path(__file__).parent/'static'
COOKIE='career_handover'

def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

class StartSession(BaseModel):
    application_id:int
    spec:dict

class Control(BaseModel):
    action:str
    x:int=Field(default=0,ge=0,le=1279)
    y:int=Field(default=0,ge=0,le=899)
    delta:int=Field(default=0,ge=-1500,le=1500)
    text:str=Field(default='',max_length=2000)
    key:str=''
    artifact_id:int|None=None

class ReceiptReview(BaseModel):
    confirmed_receipt_review:bool
    receipt_selector:str
    receipt_text_pattern:str
    receipt_url_pattern:str
    receipt_number_selector:str|None=None

class Cursor(BaseModel):
    cursor:int=Field(ge=0)
    confirmed_after_portal_review:bool

def create_app(manager=None, sessions=Session, token_file=None):
    @asynccontextmanager
    async def lifespan(app):
        nonlocal manager
        owned=manager is None
        if owned:
            init_db()
            manager=BrowserManager(sessions,os.getenv('BROWSER_STATE_DIR','state/browser-service'),os.environ['BROWSER_STATE_KEY_FILE'])
            manager.restore()
        app.state.manager=manager
        yield
        if owned:manager.close()
    app=FastAPI(title='Career Autopilot human handover',lifespan=lifespan)
    app.state.manager=manager
    token_path=token_file or os.getenv('OPERATOR_TOKEN_FILE','private/operator.token')

    @app.middleware('http')
    async def secure_headers(request,call_next):
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        return response

    def transport(request):
        # Docker ports MUST bind loopback; remote users use SSH forwarding or HTTPS.
        if request.url.scheme!='https' and request.url.hostname not in ('127.0.0.1','localhost','::1'):
            raise HTTPException(400,'Use HTTPS or an SSH-forwarded loopback address')

    def operator(request:Request):
        if not request.url.path.startswith('/internal/'):
            transport(request)
        expected=Path(token_path).read_text().strip()
        supplied=request.headers.get('authorization','')
        if not expected or not hmac.compare_digest(supplied,'Bearer '+expected):raise HTTPException(401,'Operator authentication required')

    def handover(sid:str,request:Request):
        transport(request)
        cookie=request.cookies.get(COOKIE,'')
        with sessions() as db:
            row=db.get(BrowserSession,sid)
            if not row or row.status not in ('needs_human','submission_uncertain') or not row.capability_hash or not row.capability_expires:
                raise HTTPException(403,'No active handover for this application')
            if utc(row.capability_expires)<=now() or not hmac.compare_digest(row.capability_hash,hashlib.sha256(cookie.encode()).hexdigest()):
                raise HTTPException(403,'Handover expired, revoked or belongs to another session')
        if request.method not in ('GET','HEAD'):
            allowed=os.getenv('HANDOVER_ORIGINS','http://127.0.0.1:8765,http://localhost:8765').split(',')
            if request.headers.get('origin') not in allowed:raise HTTPException(403,'Cross-origin control denied')
        return sid

    @app.exception_handler(BrowserProblem)
    async def browser_problem(request,exc):
        return JSONResponse(status_code=409,content={'kind':str(exc.kind),'detail':str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_operation(request,exc):
        return JSONResponse(status_code=409,content={'detail':str(exc)})

    @app.get('/health')
    def health():
        with sessions() as db:db.execute(text('SELECT 1'))
        return {'status':'ok'}

    @app.get('/')
    def home(request:Request):
        transport(request)
        return FileResponse(STATIC/'login.html')

    @app.get('/admin/sessions',dependencies=[Depends(operator)])
    def list_sessions():
        with sessions() as db:
            return [{'id':s.id,'application_id':s.application_id,'status':s.status,'failure_kind':s.failure_kind,'cursor':s.cursor} for s in db.scalars(select(BrowserSession))]

    @app.post('/internal/sessions',dependencies=[Depends(operator)])
    @app.post('/admin/sessions',dependencies=[Depends(operator)])
    def start(body:StartSession):
        sid=app.state.manager.start(body.application_id,body.spec)
        return {'session_id':sid}

    @app.post('/admin/sessions/{sid}/handover',dependencies=[Depends(operator)])
    def issue(sid:str,request:Request):
        token=secrets.token_urlsafe(32)
        with sessions() as db:
            record=db.get(BrowserSession,sid)
            if not record or record.status not in ('needs_human','submission_uncertain'):raise HTTPException(409,'Session is not paused')
            # A new issue invalidates the previous capability, including other browser tabs.
            record.capability_hash=hashlib.sha256(token.encode()).hexdigest();record.capability_expires=now()+timedelta(minutes=10)
            db.add(EventLog(application_id=record.application_id,event='handover_issued',details={'session_id':sid,'ttl_seconds':600}));db.commit()
        response=JSONResponse({'panel_path':'/panel/'+sid})
        response.set_cookie(COOKIE,token,httponly=True,secure=request.url.scheme=='https',samesite='strict',max_age=600,path='/panel/'+sid)
        return response

    @app.post('/admin/sessions/{sid}/revoke',dependencies=[Depends(operator)])
    def revoke(sid:str):
        with sessions() as db:
            record=db.get(BrowserSession,sid)
            if not record:raise HTTPException(404)
            record.capability_hash=None;record.capability_expires=None;db.commit()
        return {'revoked':True}

    @app.get('/admin/sessions/{sid}/inspect',dependencies=[Depends(operator)])
    def inspect(sid:str):return app.state.manager.command(sid,'inspect')

    @app.post('/admin/sessions/{sid}/receipt-review',dependencies=[Depends(operator)])
    def receipt_review(sid:str,body:ReceiptReview):return app.state.manager.command(sid,'receipt_review',**body.model_dump())

    @app.post('/admin/sessions/{sid}/cursor',dependencies=[Depends(operator)])
    def reconcile(sid:str,body:Cursor):
        if not body.confirmed_after_portal_review:raise HTTPException(400,'Reconcile employer state first')
        return app.state.manager.command(sid,'reconcile_cursor',cursor=body.cursor)

    @app.get('/panel/{sid}',dependencies=[Depends(handover)])
    def panel(sid:str):return FileResponse(STATIC/'panel.html')

    @app.get('/panel/{sid}/screen',dependencies=[Depends(handover)])
    def screen(sid:str):
        return Response(app.state.manager.command(sid,'screenshot'),media_type='image/png')

    @app.post('/panel/{sid}/control',dependencies=[Depends(handover)])
    def control(sid:str,body:Control):
        return app.state.manager.command(sid,'control',**body.model_dump())

    @app.post('/panel/{sid}/resume',dependencies=[Depends(handover)])
    def resume(sid:str):return app.state.manager.command(sid,'resume')

    app.mount('/assets',StaticFiles(directory=STATIC),name='assets')
    return app

app=create_app()
