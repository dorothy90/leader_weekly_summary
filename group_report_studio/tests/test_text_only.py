import unittest
import io
import tempfile
from pathlib import Path
from docx import Document

from studio.harness import validate_content
from studio.models import default_template
from studio.config import Settings
from studio.documents import export_docx
from studio.harness import Harness
from studio.store import Store
from test_harness import FakeLLM, FakeSource


class TextOnlyTests(unittest.TestCase):
    def test_table_is_converted_before_verification_and_word_export(self):
        class TableModel(FakeLLM):
            def complete(self,stage,system,payload,schema):
                if stage=='write':
                    return {'blocks':[{'kind':'table','headers':['제품','CUM0'],
                        'rows':[['Spica','92.3%']],'evidence_ids':[payload['facts'][0]['id']]}]}
                if stage=='verify':
                    assert all(b['kind']!='table' for b in payload['blocks'])
                    assert '92.3%' in payload['blocks'][0]['text']
                return super().complete(stage,system,payload,schema)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            report=store.create(dict(title='test',week='2026-01',references=[],expected_teams=['YIELD팀'],
                template={'name':'test','sections':[{'id':'spica','group':'수율','title':'Spica','instructions':''}]}))
            job=store.create_job(report['id'],'generate',{'base_version':0})
            Harness(store,FakeSource(),TableModel(),Settings()).run(job['id'])
            self.assertEqual(store.job(job['id'])['status'],'succeeded')
            saved=store.get(report['id'])
            self.assertEqual(saved['sections'][0]['blocks'][0]['kind'],'bullet')
            self.assertEqual(len(Document(io.BytesIO(export_docx(saved))).tables),0)

    def test_generated_tables_become_text_without_losing_values_or_evidence(self):
        content={'blocks':[{'kind':'table','text':'공식 집계','headers':['제품','금주','전주','증감'],
                 'rows':[['Spica','93.2%','92.6%','+0.6%p'],['HBM','86.8%','86.0%','+0.8%p']],
                 'evidence_ids':['f1','f2']} ],'warnings':['집계기간 확인']}
        result=validate_content(content,[{'id':'f1'},{'id':'f2'}])
        self.assertEqual([b['kind'] for b in result['blocks']],['paragraph','bullet','bullet'])
        self.assertEqual(result['blocks'][0]['text'],'공식 집계')
        self.assertEqual(result['blocks'][1]['text'],'제품: Spica; 금주: 93.2%; 전주: 92.6%; 증감: +0.6%p')
        self.assertEqual(result['blocks'][2]['evidence_ids'],['f1','f2'])
        self.assertEqual(result['warnings'],content['warnings'])
        self.assertEqual(content['blocks'][0]['kind'],'table')

    def test_table_with_unknown_evidence_is_still_rejected(self):
        content={'blocks':[{'kind':'table','headers':['제품'], 'rows':[['Spica']], 'evidence_ids':['unknown']}]}
        with self.assertRaisesRegex(ValueError,'근거'):
            validate_content(content,[{'id':'f1'}])

    def test_text_keeps_comparison_and_evidence(self):
        text='Spica: 금주 93.2%, 전주 92.6% 대비 +0.6%p.'
        result=validate_content({'blocks':[{'kind':'bullet','text':text,'evidence_ids':['f1']}]},[{'id':'f1'}])
        self.assertEqual(result['blocks'][0]['text'],text)
        self.assertEqual(result['blocks'][0]['evidence_ids'],['f1'])
        self.assertTrue(all('표를 사용하지 않는다' in s['instructions'] for s in default_template()['sections']))
