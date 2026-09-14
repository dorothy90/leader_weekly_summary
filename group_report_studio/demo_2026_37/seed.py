"""Add only this dataset's missing IDs to the existing weekly_mail index."""
import hashlib
import json
import math
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parent

def main():
    env={**dotenv_values(ROOT.parents[1]/'.env'),**os.environ}
    url=env.get('GR_OPENSEARCH_URL') or ('https' if env.get('OPENSEARCH_USE_SSL','false').lower()=='true' else 'http')+'://'+env.get('OPENSEARCH_HOST','localhost')+':'+env.get('OPENSEARCH_PORT','9200')
    auth=(env.get('GR_OPENSEARCH_USER',env.get('OPENSEARCH_USER','')),env.get('GR_OPENSEARCH_PASSWORD',env.get('OPENSEARCH_PASSWORD','')))
    docs=json.loads((ROOT/'team_documents.json').read_text())
    index='weekly_mail'
    with httpx.Client(base_url=url,auth=auth,verify=env.get('OPENSEARCH_VERIFY_CERTS','true').lower()=='true',timeout=90) as os_client:
        def request(method,path,**kwargs):
            r=os_client.request(method,path,**kwargs); r.raise_for_status(); return r.json()
        mapping=request('GET',f'/{index}/_mapping')[index]['mappings']['properties']
        dimension=mapping['embedding']['dimension']
        assert dimension==4096
        before=request('GET',f'/{index}/_count')['count']
        existing=request('POST',f'/{index}/_mget',json={'ids':[d['_id'] for d in docs]})['docs']
        pending=[]
        for doc,stored in zip(docs,existing):
            if stored.get('found'):
                assert all(stored['_source'].get(k)==v for k,v in doc['_source'].items()), 'Existing demo ID has different content; not overwriting.'
                assert len(stored['_source'].get('embedding',[]))==dimension
            else: pending.append(doc)
        cache_dir=ROOT.parent/'.local'/'demo_embeddings';cache_dir.mkdir(parents=True,exist_ok=True)
        model='qwen/qwen3-embedding-8b'
        headers={'Authorization':'Bearer '+env.get('OPENROUTER_API_KEY','')}
        base=env.get('OPENROUTER_BASE_URL','').rstrip('/')
        for start in range(0,len(pending),8):
            batch=pending[start:start+8]
            missing=[]
            for d in batch:
                digest=hashlib.sha256((model+d['_source']['text']).encode()).hexdigest()
                cache=cache_dir/f'{digest}.json'
                if cache.exists(): d['_source']['embedding']=json.loads(cache.read_text())
                else: missing.append((d,cache))
            if missing:
                r=httpx.post(base+'/embeddings',headers=headers,json={'model':model,'input':[d['_source']['text'] for d,_ in missing]},timeout=180)
                if r.status_code!=200: raise RuntimeError(f'Embedding request failed HTTP {r.status_code}')
                vectors=sorted(r.json()['data'],key=lambda x:x['index'])
                assert [x['index'] for x in vectors]==list(range(len(missing)))
                for (d,cache),item in zip(missing,vectors):
                    vector=item['embedding']
                    assert len(vector)==dimension and all(math.isfinite(v) for v in vector) and any(v!=0 for v in vector)
                    cache.write_text(json.dumps(vector));d['_source']['embedding']=vector
            lines=[]
            for d in batch:
                lines.extend([json.dumps({'create':{'_index':index,'_id':d['_id']}}),json.dumps(d['_source'],ensure_ascii=False)])
            result=request('POST','/_bulk',params={'refresh':'wait_for'},content=('\n'.join(lines)+'\n').encode(),headers={'Content-Type':'application/x-ndjson'})
            assert not result['errors'], [(x['create']['status'],x['create'].get('error',{}).get('type')) for x in result['items']]
            print(f'Indexed {min(start+8,len(pending))}/{len(pending)} new parts',flush=True)
        verified=request('POST',f'/{index}/_mget',json={'ids':[d['_id'] for d in docs]})['docs']
        assert all(d.get('found') and len(d['_source'].get('embedding',[]))==4096 for d in verified)
        assert len({d['_source']['team'] for d in verified})==36
        after=request('GET',f'/{index}/_count')['count']
        result=dict(index=index,week='2026-37',team_count=36,source_count=72,embedding_model=model,embedding_dimension=dimension,
                    added=len(pending),count_before=before,count_after=after,document_ids=[d['_id'] for d in docs])
        (ROOT/'index_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='document_ids'},ensure_ascii=False))

if __name__=='__main__':
    main()
