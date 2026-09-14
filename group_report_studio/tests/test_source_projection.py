import json
import unittest
import httpx

from studio.config import Settings
from studio.source import OpenSearchSource


class SourceProjectionTests(unittest.TestCase):
    def test_excludes_vector_and_retains_only_report_fields(self):
        calls=[]
        def handle(request):
            calls.append(request.method)
            if request.method=='DELETE':
                return httpx.Response(200,json={'succeeded':True})
            body=json.loads(request.content)
            if request.url.path=='/weekly_mail/_search':
                if body.get('_source')!={'excludes':['embedding']}:
                    return httpx.Response(503,json={'error':{'type':'search_phase_execution_exception'}})
                return httpx.Response(200,json={'_scroll_id':'s1','hits':{'total':{'value':1},'hits':[
                    {'_id':'part0','_source':{'text':'DRAM CUM0 93.9%','team':'YIELD팀','week':'2026-37',
                     'mail_id':'mail1','part_index':0,'total_parts':1,'subject':'주보','html_path':'body.html',
                     'unrelated_metadata':'not for the report'}}]}})
            return httpx.Response(200,json={'_scroll_id':'s1','hits':{'hits':[]}})
        source=OpenSearchSource(Settings(),transport=httpx.MockTransport(handle))
        try:
            result=source.fetch_week('2026-37')
        finally:
            source.close()
        self.assertEqual(result[0]['text'],'DRAM CUM0 93.9%')
        self.assertEqual(result[0]['part_index'],0)
        self.assertNotIn('unrelated_metadata',result[0])
        self.assertEqual(calls,['POST','POST','DELETE'])
