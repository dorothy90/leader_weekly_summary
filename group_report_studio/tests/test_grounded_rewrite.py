import tempfile
import unittest
from pathlib import Path

from studio.config import Settings
from studio.harness import Harness
from studio.llm import LLMError, LLMFormatError
from studio.models import SectionContent
from studio.store import Store
from test_harness import FakeLLM, FakeSource


class GroundedRewriteTests(unittest.TestCase):
    def test_verifier_format_failure_uses_source_excerpt(self):
        class Model(FakeLLM):
            def complete(self,stage,system,payload,schema):
                if stage=='write':
                    return {'blocks':[{'kind':'paragraph','text':'matching 완료.',
                                       'evidence_ids':[payload['facts'][0]['id']]}]}
                if stage=='verify':
                    raise LLMFormatError('invalid verifier response')
                return super().complete(stage,system,payload,schema)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            report=store.create(dict(title='test',week='2026-37',references=[],expected_teams=['YIELD팀'],
                template={'name':'test','sections':[{'id':'spica','group':'수율','title':'Spica','instructions':''}]}))
            job=store.create_job(report['id'],'generate',{'base_version':0})
            Harness(store,FakeSource(),Model(),Settings()).run(job['id'])
            self.assertEqual(store.job(job['id'])['status'],'succeeded')
            saved=store.get(report['id'])['sections'][0]
            self.assertIn('Spica CUM0 92.3%. 조건 개선 진행.',saved['blocks'][0]['text'])
            self.assertNotIn('matching 완료',saved['blocks'][0]['text'])

    def test_only_write_format_failure_uses_excerpt(self):
        class Model:
            error = LLMFormatError('invalid response after retries')
            def complete(self,*args):
                raise self.error
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            report=store.create(dict(title='test',week='2026-37',references=[],expected_teams=[],
                template={'name':'test','sections':[]}))
            job=store.create_job(report['id'],'generate',{'base_version':0})
            model=Model()
            harness=Harness(store,None,model,Settings())
            payload={'facts':[dict(id='f1',quote='matching 미완료.',week='2026-37',team='개발팀')]}
            result=harness.call(job['id'],'write','test',payload,SectionContent)
            self.assertIn('matching 미완료.',result['blocks'][0]['text'])
            with self.assertRaises(LLMFormatError):
                harness.call(job['id'],'edit','test',payload,SectionContent)
            model.error=LLMError('HTTP 503')
            with self.assertRaises(LLMError):
                harness.call(job['id'],'write','different request',payload,SectionContent)

    def test_unsupported_claim_is_rewritten_then_verified(self):
        self.run_case(False)

    def test_repeated_unsupported_claim_is_replaced_by_exact_source(self):
        self.run_case(True)

    def test_unknown_citations_are_replaced_by_source_citations(self):
        self.run_case(True,invalid_ids=True)

    def run_case(self, always_bad, invalid_ids=False):
        class Model(FakeLLM):
            writes=[]
            def complete(self,stage,system,payload,schema):
                if stage=='write':
                    self.writes.append(payload)
                    text='지속 모니터링이 필요하다.' if always_bad or len(self.writes)==1 else payload['facts'][0]['quote']
                    return {'blocks':[{'kind':'paragraph','text':text,'evidence_ids':['unknown' if invalid_ids else payload['facts'][0]['id']]}]}
                if stage=='verify':
                    bad='모니터링' in payload['blocks'][0]['text']
                    return {'supported':not bad,'issues':['원문에는 모니터링 필요성이 없습니다.'] if bad else []}
                return super().complete(stage,system,payload,schema)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            report=store.create(dict(title='test',week='2026-01',references=[],expected_teams=['YIELD팀'],
                template={'name':'test','sections':[{'id':'spica','group':'수율','title':'Spica','instructions':''}]}))
            job=store.create_job(report['id'],'generate',{'base_version':0})
            model=Model();model.writes=[]
            Harness(store,FakeSource(),model,Settings()).run(job['id'])
            self.assertEqual(len(model.writes),3 if always_bad else 2)
            self.assertIn('validation_feedback',model.writes[1])
            self.assertIn('rejected_draft',model.writes[1])
            self.assertEqual(store.job(job['id'])['status'],'succeeded')
            if always_bad:
                saved=store.get(report['id'])['sections'][0]
                self.assertIn('원문 발췌',saved['warnings'][0])
                self.assertIn('Spica CUM0 92.3%. 조건 개선 진행.',saved['blocks'][0]['text'])
                self.assertNotIn('모니터링',saved['blocks'][0]['text'])
            else:
                self.assertNotIn('모니터링',store.get(report['id'])['sections'][0]['blocks'][0]['text'])

    def test_excerpt_keeps_incomplete_status_and_prior_week_distinction(self):
        facts=[dict(id='f1',quote='신규 장비 1대 matching 미완료.',week='2026-37',team='M15X개발팀',source_kind='current'),
               dict(id='f2',quote='장비 matching 2대 완료.',week='2026-36',team='이전 그룹 주보',source_kind='prior')]
        result=Harness.source_excerpt({'facts':facts})
        self.assertIn('신규 장비 1대 matching 미완료.',result['blocks'][0]['text'])
        self.assertIn('2026-36',result['blocks'][1]['text'])
        self.assertIn('금주 미확인',result['blocks'][1]['text'])
        self.assertEqual(result['blocks'][0]['evidence_ids'],['f1'])
