import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from studio.priority import normalize_priority, PRIORITY_ID
from studio.harness import Harness
from studio.models import default_template
from studio.store import Store
from studio.config import Settings
from test_harness import FakeLLM, FakeSource


class PriorityTests(unittest.TestCase):
    def test_generation_builds_dynamic_sections_and_verifies_body(self):
        class LLM(FakeLLM):
            def complete(self,stage,system,payload,schema):
                if stage=='priority_topics':
                    return dict(topics=[dict(title='Spica 조건 개선',evidence_ids=[payload['facts'][0]['id']])])
                return super().complete(stage,system,payload,schema)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            template=dict(name='t',sections=[dict(id='spica',group='수율',title='Spica',instructions=''),
                         dict(id='psdi',group='중점 추진과제',title='PSDI',instructions='')])
            report=store.create(dict(week='2026-01',template=template,references=[],expected_teams=[]))
            llm=LLM()
            harness=Harness(store,FakeSource(),llm,Settings(data_dir=Path(folder)))
            job=store.create_job(report['id'],'generate',dict(base_version=0))
            harness.run(job['id'])
            self.assertEqual(store.job(job['id'])['status'],'succeeded',store.job(job['id']).get('error'))
            saved=store.get(report['id'])
            self.assertEqual(saved['sections'][-1]['title'],'Spica 조건 개선')
            self.assertTrue(saved['sections'][-1]['blocks'][0]['evidence_ids'])
            self.assertIn('verify',llm.calls)

    def test_old_topics_become_one_dynamic_slot_without_mutating_input(self):
        old = dict(name='t',sections=[dict(id='x',group='수율',title='DRAM'),
                    dict(id='psdi',group='5. 중점 추진과제',title='PSDI'),
                    dict(id='wlqm',group='5. 중점 추진과제',title='WLQM')])
        result = normalize_priority(old)
        self.assertEqual([s['id'] for s in result['sections']],['x',PRIORITY_ID])
        self.assertEqual(len(old['sections']),3)
        self.assertEqual(normalize_priority(result),result)

    def test_current_unmapped_facts_are_selected_and_prior_facts_excluded(self):
        harness = Harness.__new__(Harness)
        harness.settings=SimpleNamespace(max_input_bytes=40000)
        facts=[dict(id='now',quote='계측 자동화 평가 진행',team='A',source_kind='current',section_ids=[]),
               dict(id='old',quote='PSDI 진행',team='B',source_kind='prior',section_ids=[PRIORITY_ID])]
        def call(job,stage,system,payload,schema,validate):
            self.assertEqual(stage,'priority_topics')
            self.assertEqual([f['id'] for f in payload['facts']],['now'])
            value=dict(topics=[dict(title='계측 자동화',evidence_ids=['now'])])
            validate(value)
            return value
        harness.call=call
        template,mapped=harness.priority_sections('job',default_template(),facts)
        chosen=[s for s in template['sections'] if s['id'].startswith(PRIORITY_ID)]
        self.assertEqual([s['title'] for s in chosen],['계측 자동화'])
        self.assertIn(chosen[0]['id'],mapped[0]['section_ids'])
        self.assertEqual(mapped[1]['section_ids'],[])
        self.assertEqual(facts[0]['section_ids'],[])
        self.assertEqual(normalize_priority(template)['sections'][-1]['id'],PRIORITY_ID)

    def test_large_input_considers_every_current_fact_before_reduction(self):
        harness=Harness.__new__(Harness)
        harness.settings=SimpleNamespace(max_input_bytes=6000)
        facts=[dict(id=str(i),quote='자동화 과제 검토. '*40,team='A',source_kind='current',section_ids=[]) for i in range(30)]
        seen=set()
        def call(job,stage,system,payload,schema,validate):
            if stage=='priority_candidates':
                seen.update(f['id'] for f in payload['candidates'])
                value=dict(indices=[0])
            else:
                value=dict(topics=[dict(title='자동화',evidence_ids=[payload['facts'][0]['id']])])
            validate(value)
            return value
        harness.call=call
        harness.priority_sections('job',default_template(),facts)
        self.assertEqual(seen,{str(i) for i in range(30)})

    def test_no_current_evidence_does_not_call_model(self):
        harness=Harness.__new__(Harness)
        harness.settings=SimpleNamespace(max_input_bytes=40000)
        harness.call=Mock()
        template,facts=harness.priority_sections('job',default_template(),[
            dict(id='old',quote='과거 과제',team='A',source_kind='prior',section_ids=[PRIORITY_ID])])
        harness.call.assert_not_called()
        self.assertEqual(template['sections'][-1]['id'],PRIORITY_ID)
        self.assertEqual(facts[0]['section_ids'],[])
