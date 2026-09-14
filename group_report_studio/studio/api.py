import base64
import binascii
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .documents import export_docx, parse_reference
from .harness import Harness
from .llm import ChatModel
from .models import (CreateReport, EditRequest, Model, RestoreRequest, Template,
                     VersionRequest, default_template)
from .source import OpenSearchSource
from .store import Conflict, Store


class ReferenceUpload(Model):
    name: str = Field(min_length=1,max_length=200)
    content_base64: str = Field(max_length=14_000_000)


def public_job(job):
    return {key:job.get(key) for key in ('id','report_id','status','progress','message','error')}


def create_app(settings=None, *, source=None, llm=None, inline=False):
    settings = settings or Settings.from_env()
    store = Store(settings.data_dir/'studio.db')
    store.recover()
    source = source or OpenSearchSource(settings)
    llm = llm or ChatModel(settings)
    harness = Harness(store,source,llm,settings)
    executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='group-report')
    mutation_lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        executor.shutdown(wait=True,cancel_futures=True)
        if hasattr(source,'close'):
            source.close()
        if hasattr(llm,'close'):
            llm.close()

    app = FastAPI(title='그룹 주보 스튜디오',lifespan=lifespan)
    app.state.executor = executor
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost','test','testserver'])

    @app.middleware('http')
    async def local_requests(request: Request, call_next):
        # Same-origin browser edits only; CLI clients without Origin remain usable on loopback.
        origin = request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS') and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'같은 웹 화면에서 다시 요청하세요.'},status_code=403)
        length = request.headers.get('content-length')
        if length and (not length.isdigit() or int(length)>15_000_000):
            return JSONResponse({'detail':'요청 크기가 너무 큽니다.'},status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(KeyError)
    async def missing_handler(request, exc):
        return JSONResponse({'detail':str(exc.args[0])},status_code=404)

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse({'detail':str(exc)},status_code=409)

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        return JSONResponse({'detail':str(exc)},status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        errors = ['.'.join(str(p) for p in e['loc'][1:])+': '+e['msg'] for e in exc.errors()]
        return JSONResponse({'detail':'입력 내용을 확인하세요. '+'; '.join(errors)},status_code=422)

    def report_response(report):
        report = dict(report)
        report['versions'] = store.versions(report['id'])
        jobs = store.jobs(report['id'])
        report['active_job'] = public_job(jobs[-1]) if jobs else None
        return report

    def idle(report_id):
        if any(job['status'] in ('queued','running') for job in store.jobs(report_id)):
            raise Conflict('진행 중인 작업을 완료하거나 취소한 뒤 다시 요청하세요.')

    def submit(job):
        if inline:
            harness.run(job['id'])
        else:
            executor.submit(harness.run,job['id'])
        return public_job(store.job(job['id']))

    @app.get('/api/config')
    def config():
        return dict(ready=not settings.missing(),missing=settings.missing(),default_template=default_template())

    @app.get('/api/templates')
    def templates():
        return store.templates()

    @app.post('/api/templates')
    def save_template(payload: Template):
        return store.save_template(payload.model_dump())

    @app.get('/api/reports')
    def reports():
        return [{k:r[k] for k in ('id','title','week','version','status','updated_at')} for r in store.list_reports()]

    @app.post('/api/reports')
    def create(payload: CreateReport):
        return report_response(store.create(payload.model_dump()))

    @app.get('/api/reports/{report_id}')
    def get_report(report_id: str):
        return report_response(store.get(report_id))

    @app.post('/api/reports/{report_id}/generate')
    def generate(report_id: str,payload: VersionRequest):
        if settings.missing() and not inline:
            raise ValueError('연결 설정이 필요합니다: '+', '.join(settings.missing()))
        with mutation_lock:
            return submit(store.create_job(report_id,'generate',payload.model_dump()))

    @app.post('/api/reports/{report_id}/edit')
    def edit(report_id: str,payload: EditRequest):
        if settings.missing() and not inline:
            raise ValueError('연결 설정이 필요합니다: '+', '.join(settings.missing()))
        with mutation_lock:
            return submit(store.create_job(report_id,'edit',payload.model_dump()))

    @app.post('/api/reports/{report_id}/restore')
    def restore(report_id: str,payload: RestoreRequest):
        with mutation_lock:
            idle(report_id)
            return report_response(store.restore(report_id,payload.base_version,payload.target_version))

    @app.post('/api/reports/{report_id}/finalize')
    def finalize(report_id: str,payload: VersionRequest):
        with mutation_lock:
            idle(report_id)
            report = store.get(report_id)
            if not report['sections']:
                raise Conflict('초안 생성 후 확정할 수 있습니다.')
            return report_response(store.save(report_id,payload.base_version,{'status':'finalized'},'최종본 확정'))

    @app.get('/api/reports/{report_id}/export')
    def export(report_id: str,version: int):
        report = store.get(report_id,version)
        if report['status']!='finalized':
            raise Conflict('확정된 버전만 Word로 내려받을 수 있습니다.')
        return Response(export_docx(report),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        headers={'Content-Disposition':f'attachment; filename="group-report-{report["week"]}-v{version}.docx"'})

    @app.get('/api/jobs/{job_id}')
    def job_status(job_id: str):
        return public_job(store.job(job_id))

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel(job_id: str):
        with mutation_lock:
            job = store.job(job_id)
            if job['status']=='queued':
                job = store.update_job(job_id,cancelled=True,status='cancelled',message='작업을 취소했습니다.')
            elif job['status']=='running':
                job = store.update_job(job_id,cancelled=True,message='현재 요청이 끝나면 취소합니다.')
            return public_job(job)

    @app.post('/api/jobs/{job_id}/retry')
    def retry(job_id: str):
        with mutation_lock:
            job = store.job(job_id)
            if job['status'] not in ('failed','cancelled'):
                raise Conflict('실패하거나 취소한 작업만 다시 시도할 수 있습니다.')
            idle(job['report_id'])
            report = store.get(job['report_id'])
            if report['version'] != job['payload']['base_version'] and report.get('last_job_id') != job_id:
                raise Conflict('주보가 변경되었습니다. 최신 버전에서 새로 요청하세요.')
            return submit(store.update_job(job_id,status='queued',cancelled=False,error=None,message='저장된 단계부터 다시 시작합니다.'))

    @app.post('/api/references/parse')
    def parse(payload: ReferenceUpload):
        try:
            content = base64.b64decode(payload.content_base64,validate=True)
        except binascii.Error:
            raise ValueError('파일 전송 형식이 올바르지 않습니다. 파일을 다시 선택하세요.') from None
        return dict(name=payload.name,text=parse_reference(payload.name,content))

    static = Path(__file__).parent/'static'
    app.mount('/static',StaticFiles(directory=static),name='static')

    @app.get('/')
    def index():
        return FileResponse(static/'index.html')

    return app
