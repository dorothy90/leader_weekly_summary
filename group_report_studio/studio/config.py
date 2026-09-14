import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = Path(__file__).resolve().parent.parent / '.local'
    os_url: str = ''
    os_index: str = 'weekly_mail'
    os_user: str = ''
    os_password: str = ''
    os_verify: bool = True
    keyword_suffix: str = ''
    llm_url: str = ''
    llm_key: str = ''
    llm_model: str = ''
    max_input_bytes: int = 40000
    source_chunk_bytes: int = 10000
    max_output_tokens: int = 6000

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv('GR_DATA_DIR', str(cls.data_dir))).resolve(),
            os_url=os.getenv('GR_OPENSEARCH_URL', ''),
            os_index=os.getenv('GR_OPENSEARCH_INDEX', 'weekly_mail'),
            os_user=os.getenv('GR_OPENSEARCH_USER', ''),
            os_password=os.getenv('GR_OPENSEARCH_PASSWORD', ''),
            os_verify=os.getenv('GR_OPENSEARCH_VERIFY_TLS', 'true').lower() == 'true',
            keyword_suffix=os.getenv('GR_OPENSEARCH_KEYWORD_SUFFIX', ''),
            llm_url=os.getenv('GR_LLM_BASE_URL', ''),
            llm_key=os.getenv('GR_LLM_API_KEY', ''),
            llm_model=os.getenv('GR_LLM_MODEL', ''),
            max_input_bytes=int(os.getenv('GR_MAX_INPUT_BYTES', '40000')),
            source_chunk_bytes=int(os.getenv('GR_SOURCE_CHUNK_BYTES', '10000')),
            max_output_tokens=int(os.getenv('GR_MAX_OUTPUT_TOKENS', '6000')),
        )

    def missing(self):
        return [key for key, value in [('GR_OPENSEARCH_URL', self.os_url),
                ('GR_LLM_BASE_URL', self.llm_url), ('GR_LLM_MODEL', self.llm_model)] if not value]
