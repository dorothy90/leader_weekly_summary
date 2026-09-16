import json
import tempfile
import unittest
from pathlib import Path

import httpx

from studio.config import Settings
from studio.source import OpenSearchSource
from studio.store import Store


def source():
    return dict(id='mail_part_0',team='YIELD팀',week='2026-01',mail_id='mail',part_index=0,
                total_parts=1,text='Spica CUM0 92.3%. 조건 개선 진행.')


class FakeSource:
    calls = 0
    def fetch_week(self, week):
        self.calls += 1
        return [source()]


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.fail_once = False

    def complete(self, stage, system, payload, schema):
        self.calls.append(stage)
        if stage == 'extract_candidates':
            return {'facts':[{'candidate_id':c['candidate_id'],'section_ids':['spica']} for c in payload['candidates']]}
        if stage == 'extract_lines':
            return {'facts':[{'start_line':payload['numbered_lines'][0]['line'],
                              'end_line':payload['numbered_lines'][-1]['line'],'section_ids':['spica']}]}
        if stage == 'extract':
            return {'facts':[{'text':'Spica 수율', 'quote': payload['source']['text'], 'section_ids':['spica']}]}
        if stage == 'plan_edit':
            return {'action':'edit','section_ids':['spica'],'instruction':'간결하게','question':''}
        if stage == 'verify':
            return {'supported':True,'issues':[]}
        if stage in ('write', 'edit', 'reduce'):
            if self.fail_once:
                self.fail_once = False
                raise ValueError('일시적인 모델 오류')
            fact = payload['facts'][0]
            return {'blocks':[{'kind':'paragraph','text':fact['quote'], 'evidence_ids':[fact['id']]}], 'warnings':[]}
        raise AssertionError(stage)


class HarnessTests(unittest.TestCase):
    def test_opensearch_reads_all_pages_and_clears_only_scroll(self):
        calls = []
        def handle(request):
            calls.append((request.method,request.url.path,json.loads(request.content)))
            if request.url.path.endswith('/_search'):
                return httpx.Response(200,json={'_scroll_id':'s','hits':{'total':{'value':1},'hits':[{'_id':'a','_source':source()}]}})
            if request.method == 'DELETE':
                return httpx.Response(200,json={'succeeded':True})
            return httpx.Response(200,json={'_scroll_id':'s','hits':{'hits':[]}})
        adapter = OpenSearchSource(Settings(os_url='http://search.test'), httpx.MockTransport(handle))
        self.assertEqual(len(adapter.fetch_week('2026-01')), 1)
        filters = calls[0][2]['query']['bool']['filter']
        self.assertIn({'term':{'mail_type':'weekly_report'}}, filters)
        self.assertEqual(calls[-1][:2], ('DELETE','/_search/scroll'))
        self.assertEqual(len(calls),3)
        adapter.close()

    def test_generate_resume_edit_and_citation_rejection(self):
        from studio.harness import Harness, validate_content
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'db')
            llm, origin = FakeLLM(), FakeSource()
            report = store.create(dict(title='그룹',week='2026-01',expected_teams=['YIELD팀'],references=[],
                                      template={'name':'t','sections':[{'id':'spica','group':'1. 수율','title':'Spica','instructions':''}]}))
            harness = Harness(store,origin,llm,Settings())
            job = store.create_job(report['id'],'generate',{'base_version':0})
            llm.fail_once = True
            harness.run(job['id'])
            self.assertEqual(store.job(job['id'])['status'], 'failed')
            store.update_job(job['id'],status='queued')
            harness.run(job['id'])
            self.assertEqual(store.job(job['id'])['status'], 'succeeded')
            self.assertEqual(llm.calls.count('extract_candidates'), 1)
            self.assertEqual(origin.calls, 1)
            saved = store.get(report['id'])
            self.assertEqual(saved['version'],1)
            self.assertIn('92.3', saved['sections'][0]['blocks'][0]['text'])
            edit = store.create_job(report['id'],'edit',{'base_version':1,'message':'줄여줘','section_id':'spica'})
            harness.run(edit['id'])
            self.assertEqual(store.get(report['id'])['version'],2)
            self.assertEqual(store.get(report['id'])['messages'][-2]['content'],'줄여줘')
            with self.assertRaises(ValueError):
                validate_content({'blocks':[{'kind':'paragraph','text':'수율 99%','evidence_ids':['made_up']}], 'warnings':[]}, saved['facts'])


if __name__ == '__main__':
    unittest.main()
