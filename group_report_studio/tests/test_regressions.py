import json
import tempfile
import unittest
from pathlib import Path

from studio.config import Settings
from studio.harness import Harness
from studio.llm import json_size
from studio.store import Store
from test_harness import FakeLLM, FakeSource


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'db')
        self.report=self.store.create(dict(title='test',week='2026-01',expected_teams=['YIELD팀'],references=[],
            template={'name':'t','sections':[{'id':'spica','group':'1. 수율','title':'Spica','instructions':''}]}))

    def test_bad_quote_is_retried_with_fresh_model_output(self):
        class OnceBad(FakeLLM):
            bad=True
            def complete(self,stage,system,payload,schema):
                result=super().complete(stage,system,payload,schema)
                if stage=='extract' and self.bad:
                    self.bad=False
                    result['facts'][0]['quote']='없는 원문'
                return result
        llm=OnceBad()
        harness=Harness(self.store,FakeSource(),llm,Settings())
        job=self.store.create_job(self.report['id'],'generate',{'base_version':0})
        harness.run(job['id'])
        self.assertEqual(self.store.job(job['id'])['status'],'succeeded')
        self.assertEqual(llm.calls.count('extract'),2)

    def test_forty_team_section_keeps_all_products_with_bounded_calls(self):
        seen=[]
        class ManyFacts(FakeLLM):
            def complete(self,stage,system,payload,schema):
                size=json_size(payload)+len(system.encode())+json_size(schema.model_json_schema())
                seen.append(size)
                if size>40000:
                    raise ValueError('too large')
                if stage=='write':
                    return {'blocks':[{'kind':'paragraph','text':f['text'][:25], 'evidence_ids':[f['id']]} for f in payload['facts']], 'warnings':[]}
                if stage=='reduce':
                    return {'blocks':[b for s in payload['partial_sections'] for b in s['blocks']], 'warnings':[]}
                if stage=='verify':
                    return {'supported':True,'issues':[]}
                return super().complete(stage,system,payload,schema)
        facts=[dict(id=f'f{i}',text=f'제품{i} CUM0 92.3%. '+('가'*700),quote=f'제품{i} CUM0 92.3%. '+('가'*700),
                    source_id=f's{i}',section_ids=['spica'],source_kind='current',week='2026-01') for i in range(40)]
        harness=Harness(self.store,FakeSource(),ManyFacts(),Settings())
        job=self.store.create_job(self.report['id'],'generate',{'base_version':0})
        section=harness.write_section(job['id'],self.report['template']['sections'][0],facts,self.report)
        self.assertEqual(len(section['blocks']),40)
        self.assertLessEqual(max(seen),40000)

    def test_edit_current_blocks_are_not_repeated_in_every_batch(self):
        assigned=[]
        class Preserve(FakeLLM):
            def complete(self,stage,system,payload,schema):
                if stage=='edit':
                    assigned.extend(b['text'] for b in payload['current']['blocks'])
                    return payload['current']
                if stage=='reduce':
                    return {'blocks':[b for s in payload['partial_sections'] for b in s['blocks']], 'warnings':[]}
                return super().complete(stage,system,payload,schema)
        facts=[dict(id=f'f{i}',text='가'*1600,quote='가'*1600,source_id=f's{i}',section_ids=['spica']) for i in range(8)]
        current={'blocks':[dict(kind='paragraph',text=f'기존 제품{i}',evidence_ids=[f'f{i}']) for i in range(8)],'warnings':[]}
        harness=Harness(self.store,FakeSource(),Preserve(),Settings())
        job=self.store.create_job(self.report['id'],'edit',{'base_version':0})
        result=harness.write_section(job['id'],self.report['template']['sections'][0],facts,self.report,current=current,instruction='유지')
        self.assertEqual(len(assigned),len(set(assigned)))
        self.assertEqual(len(result['blocks']),8)

    def test_selected_rename_keeps_edited_body_and_does_not_generate(self):
        class Rename(FakeLLM):
            def complete(self,stage,system,payload,schema):
                if stage=='plan_edit':
                    template=json.loads(json.dumps(payload['template']))
                    template['sections'][0]['title']='Spica 현황'
                    return dict(action='template',section_ids=['spica'],instruction='',template=template)
                raise AssertionError('Rename should not call '+stage)
        report=dict(self.report,sections=[dict(self.report['template']['sections'][0],blocks=[{'kind':'paragraph','text':'사용자가 다듬은 문장','evidence_ids':[]}],warnings=[])])
        harness=Harness(self.store,FakeSource(),Rename(),Settings())
        job=self.store.create_job(report['id'],'edit',{'base_version':0})
        changes=harness.edit(job['id'],report,{'section_id':'spica','message':'이름만 Spica 현황으로'})
        self.assertEqual(changes['sections'][0]['title'],'Spica 현황')
        self.assertEqual(changes['sections'][0]['blocks'],report['sections'][0]['blocks'])

    def test_invented_user_assertion_is_not_accepted_as_evidence(self):
        class Invent(FakeLLM):
            def complete(self,stage,system,payload,schema):
                return dict(action='edit',section_ids=['spica'],instruction='수정',user_assertions=['CUM0 99.9%'])
        report=dict(self.report,sections=[dict(self.report['template']['sections'][0],blocks=[],warnings=[])])
        harness=Harness(self.store,FakeSource(),Invent(),Settings())
        job=self.store.create_job(report['id'],'edit',{'base_version':0})
        with self.assertRaisesRegex(ValueError,'실제 요청'):
            harness.edit(job['id'],report,{'section_id':'spica','message':'짧게 수정해줘'})

    def test_new_topic_preserves_old_user_correction_citations(self):
        class AddTopic(FakeLLM):
            def complete(self,stage,system,payload,schema):
                if stage=='plan_edit':
                    template=json.loads(json.dumps(payload['template']))
                    template['sections'].append(dict(id='new',group='1. 수율',title='새 항목',instructions=''))
                    return dict(action='template',section_ids=[],instruction='',template=template)
                if stage=='extract':
                    return dict(facts=[])
                return super().complete(stage,system,payload,schema)
        old_fact=dict(id='user_old',source_id='user_old',quote='수율 93%',text='수율 93%',section_ids=['spica'])
        old_source=dict(id='user_old',team='사용자 정정',week='2026-01',text='수율 93%',source_kind='user')
        old_block=dict(kind='paragraph',text='사용자 정정 수율 93%',evidence_ids=['user_old'])
        report=dict(self.report,facts=[old_fact],sources=[old_source],sections=[dict(self.report['template']['sections'][0],blocks=[old_block],warnings=[])])
        harness=Harness(self.store,FakeSource(),AddTopic(),Settings())
        job=self.store.create_job(report['id'],'edit',{'base_version':0})
        changed=harness.edit(job['id'],report,{'section_id':None,'message':'새 항목 추가'})
        combined=dict(report,**changed)
        Harness.validate_report(combined)
        self.assertIn('user_old',{f['id'] for f in combined['facts']})
        self.assertEqual(combined['sections'][0]['blocks'],[old_block])


if __name__=='__main__':
    unittest.main()
