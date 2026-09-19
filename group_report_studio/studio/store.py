import copy
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, version INTEGER, body TEXT);
                CREATE TABLE IF NOT EXISTS versions (report_id TEXT, version INTEGER, body TEXT, reason TEXT,
                    created_at TEXT, PRIMARY KEY(report_id,version));
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, report_id TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS preferences (name TEXT PRIMARY KEY, body TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, data):
        report = dict(sections=[], facts=[], sources=[], warnings=[], coverage={}, messages=[])
        report.update(data)
        report.update(id=uuid4().hex, version=0, status='draft', created_at=now(), updated_at=now())
        body = json.dumps(report, ensure_ascii=False)
        with self.connect() as db:
            db.execute('INSERT INTO reports VALUES (?,?,?)', (report['id'], 0, body))
            db.execute('INSERT INTO versions VALUES (?,?,?,?,?)', (report['id'], 0, body, '새 주보', now()))
        return report

    def default_template(self):
        with self.connect() as db:
            row = db.execute("SELECT body FROM preferences WHERE name='default_template'").fetchone()
        return json.loads(row[0]) if row else None

    def save_default_template(self, template):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO preferences VALUES ('default_template',?)",
                       (json.dumps(template,ensure_ascii=False),))
        return template

    def get(self, report_id, version=None):
        with self.connect() as db:
            if version is None:
                row = db.execute('SELECT body FROM reports WHERE id=?', (report_id,)).fetchone()
            else:
                row = db.execute('SELECT body FROM versions WHERE report_id=? AND version=?', (report_id, version)).fetchone()
        if row is None:
            raise KeyError('주보를 찾을 수 없습니다.')
        return json.loads(row[0])

    def save(self, report_id, base_version, changes, reason):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version,body FROM reports WHERE id=?', (report_id,)).fetchone()
            if row is None:
                raise KeyError('주보를 찾을 수 없습니다.')
            if row[0] != base_version:
                raise Conflict('다른 수정이 먼저 저장되었습니다. 최신 주보를 확인한 뒤 다시 요청하세요.')
            report = json.loads(row[1])
            report.update(copy.deepcopy(changes))
            report.update(id=report_id, version=base_version+1, updated_at=now())
            body = json.dumps(report, ensure_ascii=False)
            db.execute('UPDATE reports SET version=?,body=? WHERE id=?', (report['version'], body, report_id))
            db.execute('INSERT INTO versions VALUES (?,?,?,?,?)', (report_id, report['version'], body, reason, now()))
        return report

    def restore(self, report_id, base_version, target_version):
        historical = self.get(report_id, target_version)
        historical['status'] = 'draft'
        return self.save(report_id, base_version, historical, f'버전 {target_version} 복원')

    def list_reports(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM reports ORDER BY rowid DESC')]

    def versions(self, report_id):
        with self.connect() as db:
            return [dict(version=r[0], reason=r[1], created_at=r[2]) for r in db.execute(
                'SELECT version,reason,created_at FROM versions WHERE report_id=? ORDER BY version DESC', (report_id,))]

    def create_job(self, report_id, kind, payload):
        job = dict(id=uuid4().hex, report_id=report_id, kind=kind, payload=payload, status='queued',
                   progress=0, message='작업 대기 중', error=None, cache={}, cancelled=False, created_at=now())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for (body,) in db.execute('SELECT body FROM jobs WHERE report_id=?', (report_id,)):
                if json.loads(body)['status'] in ('queued', 'running'):
                    raise Conflict('이 주보의 작업이 진행 중입니다. 완료 후 다시 요청하세요.')
            row = db.execute('SELECT version FROM reports WHERE id=?', (report_id,)).fetchone()
            if not row or row[0] != payload['base_version']:
                raise Conflict('최신 주보를 확인한 뒤 다시 요청하세요.')
            db.execute('INSERT INTO jobs VALUES (?,?,?)', (job['id'], report_id, json.dumps(job)))
        return job

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT body FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise KeyError('작업을 찾을 수 없습니다.')
        return json.loads(row[0])

    def update_job(self, job_id, **changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError('작업을 찾을 수 없습니다.')
            job = json.loads(row[0])
            job.update(changes)
            db.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(job, ensure_ascii=False), job_id))
        return job

    def jobs(self, report_id=None):
        with self.connect() as db:
            rows = db.execute('SELECT body FROM jobs' + (' WHERE report_id=?' if report_id else ''),
                              (report_id,) if report_id else ()).fetchall()
        return [json.loads(row[0]) for row in rows]

    def record_call(self, job_id, stage, seconds, failed, retry):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:
                raise KeyError('작업을 찾을 수 없습니다.')
            job=json.loads(row[0])
            stats=job.setdefault('metrics',{}).setdefault(stage,dict(calls=0,seconds=0,failures=0,retries=0))
            stats['calls']+=1
            stats['seconds']=round(stats['seconds']+seconds,3)
            stats['failures']+=int(failed)
            stats['retries']+=int(retry)
            db.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(job,ensure_ascii=False),job_id))

    def cache_value(self, job_id, key, value):
        # Merge under a write transaction: parallel completions must not overwrite each other.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:
                raise KeyError('작업을 찾을 수 없습니다.')
            job=json.loads(row[0])
            job['cache'][key]=value
            db.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(job,ensure_ascii=False),job_id))

    def recover(self):
        for job in self.jobs():
            if job['status'] in ('queued', 'running'):
                self.update_job(job['id'], status='failed', error='서버가 중단되었습니다. 저장된 단계부터 다시 시도할 수 있습니다.')

    def templates(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM templates ORDER BY rowid DESC')]

    def save_template(self, template):
        value = dict(**template, id=uuid4().hex)
        with self.connect() as db:
            db.execute('INSERT INTO templates VALUES (?,?)', (value['id'], json.dumps(value, ensure_ascii=False)))
        return value
