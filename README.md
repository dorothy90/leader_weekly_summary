# Weekly Email Vision Agent

주간 팀장 보고 메일을 Vision LLM으로 해석하여 자동 수집·분류·요약하는 Email Intelligence Agent입니다.

## Features

- **Mail Ingestion**: Outlook EWS를 통한 주간 메일 자동 수집
- **Vision Parsing**: OpenRouter Vision LLM으로 이미지 내 텍스트/표/수치 추출
- **Preprocessing**: 본문 + 이미지 텍스트 통합 및 문장 분리
- **Tech Classification**: DRAM/NAND + Tech 기준 분류 (예정)
- **Layer1/Layer2**: 전수 집계 테이블 및 스토리 요약 (예정)
- **RAG Chatbot**: OpenSearch 기반 Q&A (예정)

## Tech Stack

- **Language**: Python 3.11+
- **Framework**: LangChain / LangGraph
- **LLM**: OpenRouter (GPT-4o, Claude 등)
- **Mail**: exchangelib (EWS)
- **Vector DB**: OpenSearch
- **API**: FastAPI

## Project Structure

```
weekly_mail_agent/
├── config/
│   ├── settings.py          # 환경 설정
│   └── tech_taxonomy.json   # Tech 분류 정의
├── src/
│   ├── ingest/
│   │   └── mail_ingestor.py # EWS 메일 수집
│   ├── parser/
│   │   └── vision_parser.py # Vision LLM 파싱
│   ├── processor/
│   │   └── preprocessor.py  # 텍스트 전처리
│   └── graph/
│       └── ingest_graph.py  # LangGraph 파이프라인
├── scripts/
│   └── run_ingest.py        # 실행 스크립트
└── requirements.txt
```

## Installation

```bash
# 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# 환경 변수 설정
cp .env.example .env
# .env 파일을 편집하여 API 키 및 EWS 정보 입력
```

## Configuration

`.env` 파일에 다음 환경 변수를 설정합니다:

```env
# OpenRouter API
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Vision/Text Models
VISION_MODEL=openai/gpt-4o-mini
TEXT_MODEL=openai/gpt-4o-mini

# Exchange (EWS)
EWS_EMAIL=your_email@company.com
EWS_PASSWORD=your_password
EWS_SERVER=outlook.office365.com

# OpenSearch
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
```

## Usage

### Mock 데이터로 테스트

```bash
python scripts/run_ingest.py --test
```

### 실제 메일 수집

```bash
# 기본 실행 (최근 7일, "주간" 키워드)
python scripts/run_ingest.py

# 옵션 지정
python scripts/run_ingest.py --subject "Weekly" --days 14 --output results.json
```

### Python 코드에서 사용

```python
from src.graph.ingest_graph import MailIngestPipeline

# 파이프라인 실행
pipeline = MailIngestPipeline()
result = pipeline.run(
    subject_filter="주간",
    days_back=7,
)

# 결과 확인
for mail in result["processed_mails"]:
    print(f"Team: {mail.team}, Sentences: {len(mail.sentences)}")
```

## Pipeline Flow

```
Mail Fetch (EWS)
     ↓
Vision Parse (OpenRouter)
     ↓
Preprocess (Text Split)
     ↓
[Phase 3+] Tech Classify
     ↓
[Phase 4+] Embed & Store (OpenSearch)
     ↓
[Phase 5+] Layer1/Layer2 Generate
```

## Development Status

- [x] Phase 1: 프로젝트 설정 및 Mail Ingestion
- [x] Phase 2: Vision Parsing 및 Preprocessing
- [ ] Phase 3: Tech Classification
- [ ] Phase 4: OpenSearch Embedding
- [ ] Phase 5: Layer1/Layer2 Generation
- [ ] Phase 6: FastAPI RAG Chatbot

## License

Private - Internal Use Only

