import json
import os
import time
import tempfile
import unittest
from pathlib import Path

import httpx

from studio.debug import DebugLog
from studio.config import Settings
from studio.harness import Harness
from studio.llm import ChatModel, LLMOutputLimitError
from studio.models import Verification
from studio.store import Store


class DebugTests(unittest.TestCase):
    def test_expired_records_are_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            log=DebugLog(Settings(data_dir=Path(folder),debug_enabled=True,debug_retention_days=1))
            with log.call('a'*32,'write',{},0):
                pass
            path=next((log.root/('a'*32)).glob('*.json'))
            os.utime(path,(time.time()-172800,time.time()-172800))
            self.assertEqual(log.records('a'*32),[])

    def test_truncated_response_and_request_are_saved_without_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(data_dir=Path(folder),debug_enabled=True,llm_url='http://model.test',llm_key='private-key')
            model=ChatModel(settings); model.client.close()
            model.client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,json={
                'choices':[{'finish_reason':'length','message':{'content':'{"broken": private-key'}}],
                'usage':{'completion_tokens':6000}})))
            store=Store(Path(folder)/'db'); report=store.create({'title':'test','week':'2026-37','template':{}})
            job=store.create_job(report['id'],'generate',{'base_version':0})
            h=Harness(store,None,model,settings)
            try:
                with self.assertRaises(LLMOutputLimitError):
                    h.call(job['id'],'verify','system prompt',{'text':'source text'},Verification)
                records=h.debug.records(job['id'])
                self.assertEqual(len(records),1)
                record=records[0]; attempt=record['attempts'][0]
                self.assertIn('system prompt',attempt['request']['messages'][0]['content'])
                self.assertIn('source text',attempt['request']['messages'][1]['content'])
                self.assertIn('broken',attempt['response']['body'])
                self.assertEqual(record['status'],'failed')
                self.assertNotIn('private-key',json.dumps(records))
                self.assertNotIn('headers',attempt['request'])
            finally:
                model.close()

    def test_disabled_mode_creates_no_files(self):
        with tempfile.TemporaryDirectory() as folder:
            log=DebugLog(Settings(data_dir=Path(folder)))
            with log.call('a'*32,'write',{},0):
                pass
            self.assertFalse(log.root.exists())

    def test_nested_calls_and_validation_error(self):
        with tempfile.TemporaryDirectory() as folder:
            log=DebugLog(Settings(data_dir=Path(folder),debug_enabled=True))
            with self.assertRaises(ValueError):
                with log.call('a'*32,'write',{'section':{'title':'Spica'}},0):
                    with log.call('a'*32,'verify',{},0):
                        raise ValueError('근거 불일치')
            records=log.records('a'*32)
            self.assertEqual(records[1]['parent_id'],records[0]['id'])
            self.assertEqual(records[0]['error'],'근거 불일치')


class DebugAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_job_records_endpoint_and_unknown_job(self):
        from studio.api import create_app
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(data_dir=Path(folder),debug_enabled=True)
            app=create_app(settings)
            store=app.state.store
            report=store.create({'title':'test','week':'2026-37','template':{}})
            job=store.create_job(report['id'],'generate',{'base_version':0})
            log=DebugLog(settings)
            with log.call(job['id'],'verify',{},0):
                pass
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                    response=await client.get('/api/jobs/'+job['id']+'/debug')
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.json()['records'][0]['stage'],'verify')
                    missing=await client.get('/api/jobs/unknown/debug')
                    self.assertEqual(missing.status_code,404)
