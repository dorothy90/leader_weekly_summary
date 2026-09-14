import json
import logging
import time

import httpx
from pydantic import ValidationError


class LLMError(ValueError):
    pass


class LLMFormatError(LLMError):
    pass


def json_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


class ChatModel:
    """OpenAI-compatible chat endpoint; no provider-specific SDK dependency."""
    def __init__(self, settings):
        self.settings = settings
        headers = {'Authorization': f'Bearer {settings.llm_key}'} if settings.llm_key else {}
        self.client = httpx.Client(timeout=180, headers=headers)

    def close(self):
        self.client.close()

    def complete(self, stage, system, payload, schema):
        # UTF-8 bytes conservatively bound byte-level tokenizers; configure for the actual provider.
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        system += '\n반드시 다음 JSON schema에 맞는 JSON 객체만 반환하세요. 마크다운 코드블록은 사용하지 마세요.\n' + schema_text
        user = json.dumps(payload, ensure_ascii=False)
        if len((system+user).encode()) > self.settings.max_input_bytes:
            raise LLMError('요청이 입력 한도를 초과했습니다. 소주제나 원문 분할 크기를 줄이거나 모델 입력 한도를 조정하세요.')
        format_hint = ''
        for attempt in range(3):
            try:
                response = self.client.post(self.settings.llm_url.rstrip('/')+'/chat/completions', json={
                    'model':self.settings.llm_model, 'temperature':0.1,
                    'max_tokens':self.settings.max_output_tokens,
                    'response_format':{'type':'json_schema','json_schema':{
                        'name':schema.__name__,'strict':False,'schema':schema.model_json_schema()}},
                    'messages':[{'role':'system','content':system}, {'role':'user','content':user}]
                        + ([{'role':'user','content':format_hint}] if format_hint else []),
                })
                if response.status_code in (429,500,502,503,504) and attempt < 2:
                    time.sleep(attempt+1)
                    continue
                response.raise_for_status()
                choice = response.json()['choices'][0]
                if choice.get('finish_reason') == 'length':
                    raise LLMError('모델 출력이 길이 제한으로 중단되었습니다. 출력 토큰 설정을 늘리거나 소주제를 나누세요.')
                content = choice['message'].get('content')
                if not isinstance(content,str) or not content.strip():
                    raise ValueError('empty_content')
                content = content.strip()
                if content.startswith('```') and content.endswith('```'):
                    content = content.split('\n',1)[1].rsplit('```',1)[0]
                return schema.model_validate(json.loads(content)).model_dump()
            except httpx.HTTPStatusError as exc:
                raise LLMError(f'모델 호출 실패 (HTTP {exc.response.status_code}). 모델 주소·이름·인증 설정을 확인하세요.') from None
            except httpx.TransportError:
                if attempt == 2:
                    raise LLMError('모델 서버에 연결할 수 없습니다. 연결 설정을 확인한 뒤 다시 시도하세요.') from None
                time.sleep(attempt+1)
            except (IndexError, KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, LLMError):
                    raise
                # Record diagnostic categories only; never log source text or provider bodies.
                category = 'schema_validation' if isinstance(exc,ValidationError) else 'invalid_json_or_content'
                logging.getLogger(__name__).warning('Model response format: stage=%s attempt=%s category=%s',stage,attempt+1,category)
                if attempt == 2:
                    raise LLMFormatError(f'모델 응답 형식 오류 ({stage}, {category}). 3회 검증에 실패했습니다. 완료된 단계는 보존되어 있습니다.') from None
                format_hint = '응답 형식 검증에 실패했습니다. 필수 필드와 자료형을 JSON schema에 맞추고, 설명이나 코드블록 없이 완전한 JSON 객체 하나만 반환하세요. 원문 사실을 변경하지 마세요.'
