import tempfile
import unittest
from pathlib import Path

import httpx

from studio.config import Settings
from test_harness import FakeLLM, FakeSource


class APITests(unittest.IsolatedAsyncioTestCase):
    async def test_create_generate_edit_restore_finalize_export(self):
        from studio.api import create_app
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(Settings(data_dir=Path(folder)), source=FakeSource(), llm=FakeLLM(), inline=True)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,base_url='http://test') as client:
                config = (await client.get('/api/config')).json()
                self.assertEqual(config['default_template']['sections'][0]['id'], 'events')
                payload = {'title':'검증 주보','week':'2026-01','template':{'name':'t','sections':[
                    {'id':'spica','group':'1. 수율','title':'Spica'}]},'expected_teams':['YIELD팀']}
                response = await client.post('/api/reports',json=payload)
                self.assertEqual(response.status_code,200,response.text)
                rid = response.json()['id']
                job = (await client.post(f'/api/reports/{rid}/generate',json={'base_version':0})).json()
                self.assertEqual((await client.get('/api/jobs/'+job['id'])).json()['status'], 'succeeded')
                saved_report=(await client.get('/api/reports/'+rid)).json()
                self.assertEqual(saved_report['format_review']['status'],'passed')
                self.assertEqual((await client.get(f'/api/reports/{rid}/export?version=1')).status_code,409)
                edit = await client.post(f'/api/reports/{rid}/edit',json={'base_version':1,'message':'줄여줘','section_id':'spica'})
                self.assertEqual(edit.status_code,200,edit.text)
                stale = await client.post(f'/api/reports/{rid}/finalize',json={'base_version':1})
                self.assertEqual(stale.status_code,409)
                restored = await client.post(f'/api/reports/{rid}/restore',json={'base_version':2,'target_version':1})
                self.assertEqual(restored.json()['version'],3)
                finalized = await client.post(f'/api/reports/{rid}/finalize',json={'base_version':3})
                self.assertEqual(finalized.json()['status'],'finalized')
                doc = await client.get(f'/api/reports/{rid}/export?version=4')
                self.assertEqual(doc.status_code,200,doc.text if doc.status_code!=200 else '')
                self.assertTrue(doc.content.startswith(b'PK'))
                self.assertEqual((await client.get('/api/reports/not-there')).status_code,404)
            app.state.executor.shutdown(wait=True)

    async def test_reference_errors_do_not_leak_raw_validation_values(self):
        from studio.api import create_app
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(Settings(data_dir=Path(folder)),source=FakeSource(),llm=FakeLLM(),inline=True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                bad = await client.post('/api/references/parse',json={'name':'x.txt','content_base64':'!wrong'})
                self.assertEqual(bad.status_code,400)
                config = (await client.get('/api/config')).json()
                self.assertNotIn('password',str(config).lower())
            app.state.executor.shutdown(wait=True)


if __name__ == '__main__':
    unittest.main()
