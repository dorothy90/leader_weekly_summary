import json
import unittest
import httpx

from studio.config import Settings
from studio.llm import ChatModel, LLMError
from studio.models import Extraction


class ModelFormatTests(unittest.TestCase):
    def model(self, outputs):
        calls=[]
        def handle(request):
            calls.append(json.loads(request.content))
            content=outputs[min(len(calls)-1,len(outputs)-1)]
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':content}}]})
        model=ChatModel(Settings(llm_url='https://model.test/v1',llm_model='test'))
        model.client.close()
        model.client=httpx.Client(transport=httpx.MockTransport(handle))
        self.addCleanup(model.close)
        return model,calls

    def test_malformed_response_is_retried_with_json_format(self):
        model,calls=self.model(['not JSON','{"facts":[]}'])
        self.assertEqual(model.complete('extract','instructions',{},Extraction),{'facts':[]})
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0]['response_format']['type'],'json_schema')
        self.assertIn('형식',calls[1]['messages'][-1]['content'])

    def test_invalid_schema_retries_but_never_accepts_invalid_facts(self):
        model,calls=self.model(['{"facts":[{"text":"secret-payload"}]}'])
        with self.assertRaises(LLMError) as caught:
            model.complete('extract','instructions',{},Extraction)
        self.assertEqual(len(calls),3)
        self.assertIn('extract',str(caught.exception))
        self.assertNotIn('secret-payload',str(caught.exception))

    def test_null_content_is_handled(self):
        model,calls=self.model([None,'{"facts":[]}'])
        self.assertEqual(model.complete('extract','instructions',{},Extraction),{'facts':[]})
        self.assertEqual(len(calls),2)
