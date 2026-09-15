"""Opt-in local traces. Never store HTTP headers or configured credentials."""
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

active_trace = ContextVar('group_report_trace', default=None)


class DebugLog:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.data_dir / 'debug'
        self.secrets = [s for s in (settings.llm_key, settings.os_password) if s]

    def clean(self, value):
        if isinstance(value, dict):
            return {k:('[REDACTED]' if re.search(r'authorization|api[_-]?key|password|secret|token$',k,re.I)
                       and k not in ('max_tokens','load_token') else self.clean(v)) for k,v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret,'[REDACTED]')
            value = re.sub(r'Bearer\s+[^\s"\\]+','Bearer [REDACTED]',value,flags=re.I)
        return value

    def save(self, record):
        folder = self.root / record['job_id']
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        path = folder / (record['id']+'.json')
        temp = path.with_suffix('.tmp')
        fd = os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(self.clean(record),stream,ensure_ascii=False,indent=2)
        os.replace(temp,path)

    def purge(self):
        cutoff = time.time()-max(1,self.settings.debug_retention_days)*86400
        if not self.root.exists():
            return
        for folder in self.root.iterdir():
            if folder.is_symlink() or not re.fullmatch('[a-f0-9]{32}',folder.name) or not folder.is_dir():
                continue
            for path in folder.glob('*.json'):
                if re.fullmatch('[a-f0-9]{32}.json',path.name) and path.stat().st_mtime<cutoff:
                    path.unlink()

    @contextmanager
    def call(self, job_id, stage, payload, depth):
        if not self.settings.debug_enabled:
            yield
            return
        parent = active_trace.get()
        record = dict(id=uuid.uuid4().hex,job_id=job_id,stage=stage,depth=depth,
                      parent_id=parent[1]['id'] if parent else None,
                      section=payload.get('section'),started_at=time.time(),status='running',attempts=[])
        self.save(record)
        token = active_trace.set((self,record))
        try:
            yield
            record['status']='completed' if record['attempts'] else 'completed_without_http'
        except Exception as exc:
            record.update(status='failed',error_type=type(exc).__name__,error=str(exc))
            raise
        finally:
            record['elapsed_seconds']=round(time.time()-record['started_at'],3)
            self.save(record)
            active_trace.reset(token)

    def records(self, job_id):
        self.purge()
        if not self.settings.debug_enabled:
            return []
        folder = self.root/job_id
        return sorted((json.loads(p.read_text(encoding='utf-8')) for p in folder.glob('*.json')),
                      key=lambda r:r['started_at'])


def trace_event(kind, value):
    context = active_trace.get()
    if context:
        log,record = context
        if kind=='request':
            record['attempts'].append(dict(request=value,started_at=time.time()))
        elif record['attempts']:
            record['attempts'][-1][kind]=value
        log.save(record)
