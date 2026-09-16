import tempfile
import unittest
from pathlib import Path

from studio.config import Settings
from studio.harness import Harness
from studio.store import Store


class ExtractionRetryTests(unittest.TestCase):
    def setup_case(self, folder, mode):
        store=Store(Path(folder)/'db')
        template={'name':'test','sections':[{'id':'tf_nand','group':'3','title':'NAND','instructions':''}]}
        report=store.create({'title':'test','week':'2026-37','template':template})
        job=store.create_job(report['id'],'generate',{'base_version':0})
        source=dict(id='p1',text='NAND 증산 CUM0 89.5%. 전주 89.0%.',team='NAND팀',week='2026-37',mail_id='m1',part_index=0,total_parts=1)
        class Model:
            def __init__(self): self.calls=[]
            def complete(self,stage,system,payload,schema):
                self.calls.append(payload)
                if stage == 'extract_candidates':
                    bad=mode=='always_bad' or len(self.calls)==1
                    return {'facts':[{'candidate_id':'unknown' if bad and mode!='bad_id' else c['candidate_id'],
                                      'section_ids':['unknown' if bad and mode=='bad_id' else 'tf_nand']}
                                     for c in payload['candidates']]}
                if stage == 'extract_lines':
                    bad=mode=='always_bad' or len(self.calls)==1
                    return {'facts':[{'start_line':999 if bad and mode!='bad_id' else 0,
                                     'end_line':999 if bad and mode!='bad_id' else 0,
                                     'section_ids':['unknown' if bad and mode=='bad_id' else 'tf_nand']}]}
                bad=mode=='always_bad' or len(self.calls)==1
                return {'facts':[{'text':'NAND 수율','quote':'NAND 증산 CUM0 99.5%.' if bad and mode!='bad_id' else source['text'],
                                  'section_ids':['NAND분과' if bad and mode=='bad_id' else 'tf_nand']}]}
        llm=Model()
        return Harness(store,None,llm,Settings()),store,job,source,template,llm

    def test_bad_quote_or_id_is_reextracted_and_cached_only_after_validation(self):
        for mode in ['bad_quote','bad_id']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                h,s,j,source,t,llm=self.setup_case(folder,mode)
                result=h.extract(j['id'],[source],t)
                self.assertEqual(len(llm.calls),2)
                self.assertIn('validation_feedback',llm.calls[1])
                self.assertEqual(result[0]['quote'],source['text'])
                self.assertEqual(result[0]['section_ids'],['tf_nand'])
                h.extract(j['id'],[source],t)
                self.assertEqual(len(llm.calls),2)

    def test_persistent_mismatch_stops_without_caching_bad_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            h,s,j,source,t,llm=self.setup_case(folder,'always_bad')
            with self.assertRaisesRegex(ValueError,'3회'):
                h.extract(j['id'],[source],t)
            self.assertEqual(len(llm.calls),3)
            self.assertEqual(s.job(j['id'])['cache'],{})

    def test_whitespace_difference_restores_exact_original_quote(self):
        with tempfile.TemporaryDirectory() as folder:
            h,s,j,source,t,llm=self.setup_case(folder,'whitespace')
            source['text']='NAND 증산 CUM0 89.5%.\n전주 89.0%.'
            def complete(stage,system,payload,schema):
                return {'facts':[{'candidate_id':c['candidate_id'],'section_ids':['tf_nand']} for c in payload['candidates']]}
            llm.complete=complete
            result=h.extract(j['id'],[source],t)
            self.assertEqual(result[0]['quote'],source['text'])
            self.assertIn('\n',result[0]['quote'])

    def test_repeated_rewritten_quotes_fall_back_to_source_line_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            h,s,j,source,t,llm=self.setup_case(folder,'always_bad')
            source['text']='제품 NAND\nCUM0 89.5%\n전주 89.0%\n조치 진행 중'
            stages=[]
            def complete(stage,system,payload,schema):
                stages.append(stage)
                if stage == 'extract_lines':
                    return {'facts':[{'start_line':0,'end_line':2,'section_ids':['tf_nand']},
                                     {'start_line':3,'end_line':3,'section_ids':['tf_nand']}]}
                return {'facts':[{'text':'요약','quote':'NAND CUM0는 89.5%입니다.','section_ids':['tf_nand']}]}
            llm.complete=complete
            result=h.extract_by_lines(j['id'],dict(source=source,template=t))['facts']
            self.assertEqual(stages,['extract_lines'])
            self.assertEqual(result[0]['quote'],'제품 NAND\nCUM0 89.5%\n전주 89.0%')
            self.assertEqual(result[1]['quote'],'조치 진행 중')
            h.extract_by_lines(j['id'],dict(source=source,template=t))
            self.assertEqual(len(stages),1)
