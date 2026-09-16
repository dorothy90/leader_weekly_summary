import unittest
import tempfile
from pathlib import Path

from studio.citations import citation_candidates
from studio.config import Settings
from studio.harness import Harness
from studio.llm import LLMOutputLimitError
from studio.store import Store


class CitationTests(unittest.TestCase):
    def test_long_text_is_bounded_exact_and_fully_covered(self):
        for text in [('제품 A 92.1%. matching 미완료.\n'*500), '가'*9001, ' \n\t', 'A\n\nB']:
            with self.subTest(length=len(text)):
                candidates=citation_candidates(text)
                covered=set()
                for c in candidates:
                    self.assertEqual(c['text'],text[c['start']:c['end']])
                    self.assertTrue(c['text'].strip())
                    self.assertLessEqual(len(c['text']),1800)
                    covered.update(range(c['start'],c['end']))
                self.assertTrue(all(i in covered for i,char in enumerate(text) if not char.isspace()))
                self.assertEqual(candidates,citation_candidates(text))

    def test_short_paragraph_keeps_product_and_status(self):
        text='M15X 개발\nCUM0 66.8%\n신규 장비 1대 matching 미완료.'
        self.assertEqual(citation_candidates(text)[0]['text'],text)

    def test_candidate_selection_splits_on_output_limit_and_preserves_source(self):
        class Model:
            calls=0
            def complete(self,stage,system,payload,schema):
                self.calls+=1
                if len(payload['candidates'])>1:
                    raise LLMOutputLimitError('length')
                return {'facts':[dict(candidate_id=c['candidate_id'],section_ids=['s']) for c in payload['candidates']]}
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            report=store.create({'title':'test','week':'2026-37','template':{}})
            job=store.create_job(report['id'],'generate',{'base_version':0})
            model=Model(); h=Harness(store,None,model,Settings())
            text='제품 A CUM0 92.1%. matching 미완료.\n'*300
            payload=dict(source={'text':text},template={'sections':[{'id':'s'}]})
            result=h.extract_candidates(job['id'],payload)
            self.assertEqual([f['quote'] for f in result['facts']],[c['text'] for c in citation_candidates(text)])
            self.assertTrue(all(len(f['quote'])<=1800 for f in result['facts']))
            calls=model.calls
            self.assertGreater(calls,1)
            self.assertEqual(h.extract_candidates(job['id'],payload),result)
            self.assertEqual(model.calls,calls)

    def test_missing_duplicate_and_foreign_candidates_cannot_be_cached(self):
        for mode in ('missing','duplicate','foreign'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                class Model:
                    def complete(self,stage,system,payload,schema):
                        f=dict(candidate_id=payload['candidates'][0]['candidate_id'],section_ids=[])
                        return {'facts':[] if mode=='missing' else [f,f] if mode=='duplicate' else [dict(f,candidate_id='foreign')]}
                store=Store(Path(folder)/'db')
                report=store.create({'title':'test','week':'2026-37','template':{}})
                job=store.create_job(report['id'],'generate',{'base_version':0})
                h=Harness(store,None,Model(),Settings())
                with self.assertRaisesRegex(ValueError,'3회'):
                    h.extract_candidates(job['id'],dict(source={'text':'matching 미완료'},template={'sections':[]}))
                self.assertEqual(store.job(job['id'])['cache'],{})
