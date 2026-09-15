import tempfile
import unittest
from pathlib import Path

import httpx

from studio.config import Settings
from studio.harness import Harness
from studio.llm import ChatModel, LLMOutputLimitError
from studio.models import SectionContent
from studio.store import Store


class OutputLimitTests(unittest.TestCase):
    def test_length_response_has_distinct_error(self):
        model=ChatModel(Settings(llm_url='http://model.test'))
        model.client.close()
        model.client=httpx.Client(transport=httpx.MockTransport(lambda request:
            httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"blocks":'}}]})))
        try:
            with self.assertRaises(LLMOutputLimitError):
                model.complete('write','test',{},SectionContent)
        finally:
            model.close()

    def test_split_write_preserves_all_facts_and_reuses_cache(self):
        class Model:
            calls=0
            def complete(self,stage,system,payload,schema):
                self.calls+=1
                if len(payload['facts'])>1:
                    raise LLMOutputLimitError('length')
                f=payload['facts'][0]
                return {'blocks':[{'kind':'bullet','text':f['quote'],'evidence_ids':[f['id']]}]}
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            report=store.create({'title':'test','week':'2026-37','template':{}})
            job=store.create_job(report['id'],'generate',{'base_version':0})
            model=Model(); h=Harness(store,None,model,Settings())
            facts=[dict(id=str(i),quote=f'제품 {i} matching 미완료.',week='2026-37',team='팀') for i in range(4)]
            result=h.call(job['id'],'write','test',{'facts':facts},SectionContent)
            self.assertEqual([b['text'] for b in result['blocks']],[f['quote'] for f in facts])
            self.assertEqual(model.calls,7)
            h.call(job['id'],'write','test',{'facts':facts},SectionContent)
            self.assertEqual(model.calls,7)

    def test_lines_split_preserves_global_numbers_and_context(self):
        class Model:
            def complete(self,stage,system,payload,schema):
                lines=payload['numbered_lines']
                if len(lines)>1:
                    raise LLMOutputLimitError('length')
                assert len(payload['context_lines'])==4
                n=lines[0]['line']
                return {'facts':[{'start_line':n,'end_line':n,'section_ids':['s']}]}
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            report=store.create({'title':'test','week':'2026-37','template':{}})
            job=store.create_job(report['id'],'generate',{'base_version':0})
            h=Harness(store,None,Model(),Settings())
            value=h.extract_by_lines(job['id'],{'source':{'text':'A\nB\nC\nD'},'template':{'sections':[{'id':'s'}]}})
            self.assertEqual([f['quote'] for f in value['facts']],['A','B','C','D'])

    def test_unsplittable_write_uses_excerpt_but_edit_fails(self):
        class Model:
            def complete(self,*args):
                raise LLMOutputLimitError('length')
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            report=store.create({'title':'test','week':'2026-37','template':{}})
            job=store.create_job(report['id'],'generate',{'base_version':0})
            h=Harness(store,None,Model(),Settings())
            payload={'facts':[dict(id='f',quote='matching 미완료.',week='2026-37',team='팀')]}
            result=h.call(job['id'],'write','test',payload,SectionContent)
            self.assertIn('미완료',result['blocks'][0]['text'])
            self.assertTrue(result['warnings'])
            with self.assertRaises(LLMOutputLimitError):
                h.call(job['id'],'edit','test',payload,SectionContent)
