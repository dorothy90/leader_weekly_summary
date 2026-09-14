"""Use existing repo environment read-only to launch the report studio."""
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
import uvicorn

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
from studio.api import create_app
from studio.config import Settings

def settings():
    env={**dotenv_values(ROOT.parents[1]/'.env'),**os.environ}
    return Settings(
        os_url=env.get('GR_OPENSEARCH_URL') or ('https' if env.get('OPENSEARCH_USE_SSL','false').lower()=='true' else 'http')+'://'+env.get('OPENSEARCH_HOST','localhost')+':'+env.get('OPENSEARCH_PORT','9200'),
        os_index='weekly_mail',
        os_user=env.get('GR_OPENSEARCH_USER',env.get('OPENSEARCH_USER','')),
        os_password=env.get('GR_OPENSEARCH_PASSWORD',env.get('OPENSEARCH_PASSWORD','')),
        os_verify=env.get('OPENSEARCH_VERIFY_CERTS','true').lower()=='true',
        llm_url=env.get('GR_LLM_BASE_URL',env.get('OPENROUTER_BASE_URL','')),
        llm_key=env.get('GR_LLM_API_KEY',env.get('OPENROUTER_API_KEY','')),
        llm_model=env.get('GR_LLM_MODEL',env.get('LLM_MODEL','')),
    )

if __name__=='__main__':
    uvicorn.run(create_app(settings()),host='127.0.0.1',port=8091)
