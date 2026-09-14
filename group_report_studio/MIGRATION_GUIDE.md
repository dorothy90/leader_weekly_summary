# 그룹 주보 스튜디오 사내망 이식 가이드

이 문서는 `group_report_studio/`를 반출이 제한된 사내 저장소에 반입해 실행하는 절차를 설명합니다. 현재 구현은 기존 애플리케이션과 분리된 로컬 단일 사용자 서비스이며, 운영 중인 `weekly_mail` OpenSearch 인덱스는 읽기만 합니다.

## 1. 반입 범위 결정

운영에 필요한 최소 파일은 다음과 같습니다.

```text
group_report_studio/
├── .env.example
├── requirements.txt
├── run.py
└── studio/
    ├── api.py
    ├── config.py
    ├── documents.py
    ├── harness.py
    ├── llm.py
    ├── models.py
    ├── prompts.py
    ├── source.py
    ├── store.py
    └── static/
```

검증과 유지보수를 위해 `tests/`, `README.md`, `DESIGN.md`, `docs/`도 함께 반입하는 것을 권장합니다.

`demo_2026_37/`은 가상의 36개 팀 주보와 이전 2주 그룹 주보가 들어 있는 선택 사항입니다. 운영 설치에는 필요하지 않습니다. 특히 `demo_2026_37/seed.py`는 더미 문서를 OpenSearch에 추가하는 도구이므로 운영 인덱스에서는 실행하지 마세요.

다음 항목은 반입하거나 커밋하지 않습니다.

- 실제 `.env`와 인증정보
- `.local/`의 SQLite DB 및 임베딩 캐시
- `__pycache__/`, `*.pyc`
- 사용자가 내려받은 운영 Word 결과물

## 2. 커밋 또는 폴더 반입

원본 커밋은 다음과 같습니다.

```text
브랜치: codex/group-report-studio
최초 기능 커밋: ec25a725f958d8580ef536dfb6377e8987ff7150
```

설명서를 포함한 최신 상태는 브랜치 HEAD를 기준으로 확인합니다. 사내 저장소가 같은 Git 이력을 공유하면 브랜치를 가져온 뒤 커밋 범위를 적용합니다.

```bash
git cherry-pick ec25a725f958d8580ef536dfb6377e8987ff7150^..origin/codex/group-report-studio
```

Git 이력이 다른 저장소라면 패치 또는 폴더로 옮길 수 있습니다.

외부 구역에서 패치를 만들 때:

```bash
git format-patch --stdout main..codex/group-report-studio > group-report-studio.patch
```

사내 저장소 루트에서 패치를 적용할 때:

```bash
git am group-report-studio.patch
```

패치 적용이 어려우면 `group_report_studio/` 전체를 사내 저장소 루트에 복사합니다. 기존 파일을 덮어쓸 필요가 없으며, 기존 루트의 `requirements.txt`, `.env`, 실행 파일도 수정하지 않습니다.

반입 직후 범위를 확인합니다.

```bash
git status --short
git diff -- group_report_studio/
```

## 3. Python과 패키지 준비

Python 3.11 이상을 권장합니다. 인터넷이 가능한 환경에서는 별도 가상환경을 만들어 설치합니다.

```bash
cd group_report_studio
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

사내망에서 패키지 저장소에 접속할 수 없으면 외부 구역에서 wheel 파일을 함께 준비합니다.

```bash
cd group_report_studio
python3 -m pip download -r requirements.txt --dest wheelhouse
```

`wheelhouse/`를 승인된 절차로 반입한 뒤 사내망에서 설치합니다.

```bash
cd group_report_studio
python3 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse -r requirements.txt
```

프런트엔드는 정적 HTML·CSS·JavaScript이므로 Node.js 빌드나 npm 설치가 필요하지 않습니다.

## 4. OpenSearch 데이터 계약 확인

앱은 아래 조건과 일치하는 문서를 모두 조회합니다.

```json
{
  "week": "2026-37",
  "mail_type": "weekly_report",
  "type": "original_part"
}
```

각 문서에는 다음 필드가 필요합니다.

| 필드 | 필수 | 용도 |
|---|---:|---|
| `text` | 예 | 메일 본문과 인라인 이미지에서 추출한 텍스트 |
| `team` | 예 | 팀별 수집 범위와 누락 확인 |
| `week` | 예 | `YYYY-WW` 형식의 ISO 주차 |
| `mail_id` | 예 | 한 메일의 조각을 묶는 ID |
| `part_index` | 예 | 0부터 시작하는 조각 번호 |
| `total_parts` | 예 | 해당 메일의 전체 조각 수 |
| `subject` | 아니요 | 원문 표시용 제목 |
| `html_path` | 아니요 | 출처 메타데이터 |
| `embedding` | 아니요 | 앱에서는 조회하지 않음 |

`week`, `mail_type`, `type`은 정확 일치 검색이 가능해야 합니다. 필드가 `keyword`이면 `GR_OPENSEARCH_KEYWORD_SUFFIX`를 비워 두고, `text`와 `keyword` 하위 필드를 함께 쓴다면 `.keyword`로 설정합니다.

읽기 전용 계정에는 다음 작업만 허용하면 됩니다.

- 대상 인덱스의 search 및 scroll 조회
- scroll 컨텍스트 해제

일반 앱은 인덱스 생성·문서 추가·수정·삭제·refresh를 호출하지 않습니다. `embedding`은 응답에서 제외하므로 4096차원 벡터가 모델 입력으로 전달되지 않습니다.

## 5. 환경 설정

예시 파일을 복사합니다.

```bash
cd group_report_studio
cp .env.example .env
```

사내 비밀정보 관리 방식에 맞춰 아래 값을 주입합니다. 실제 비밀번호나 API 키를 소스 코드와 Git에 기록하지 마세요.

```dotenv
GR_OPENSEARCH_URL=https://opensearch.company.internal:9200
GR_OPENSEARCH_INDEX=weekly_mail
GR_OPENSEARCH_USER=weekly-report-reader
GR_OPENSEARCH_PASSWORD=SECRET_MANAGER_INJECTS_VALUE
GR_OPENSEARCH_VERIFY_TLS=true
GR_OPENSEARCH_KEYWORD_SUFFIX=

GR_LLM_BASE_URL=https://llm.company.internal/v1
GR_LLM_API_KEY=SECRET_MANAGER_INJECTS_VALUE_IF_REQUIRED
GR_LLM_MODEL=COMPANY_APPROVED_MODEL_ID

GR_DATA_DIR=/var/lib/group-report-studio
GR_MAX_INPUT_BYTES=40000
GR_SOURCE_CHUNK_BYTES=10000
GR_MAX_OUTPUT_TOKENS=6000
```

`GR_DATA_DIR`에는 주보 본문, 원문 인용, 버전 이력과 작업 체크포인트가 SQLite로 저장됩니다. 실행 계정만 읽고 쓸 수 있는 사내 승인 경로를 사용하고 정기 백업 정책에 포함하세요.

TLS 검증을 끄는 방식보다 사내 CA 인증서를 실행 환경의 신뢰 저장소에 등록하는 방식을 권장합니다.

## 6. 사내 모델 API 요구사항

현재 모델 클라이언트는 OpenAI 호환 Chat Completions API를 사용합니다.

- 요청 경로: `{GR_LLM_BASE_URL}/chat/completions`
- 응답 본문: `choices[0].message.content`에 JSON 문자열
- 구조화 출력: `response_format.type=json_schema`
- 인증: 설정된 경우 `Authorization: Bearer ...`

사내 게이트웨이가 `json_schema` 응답 형식을 지원하지 않으면 이식 담당자가 [studio/llm.py](studio/llm.py)의 요청 형식을 사내 API 계약에 맞게 수정해야 합니다. 반환값은 반드시 Pydantic 스키마로 검증할 수 있는 JSON 객체여야 합니다.

메일 원문 일부, 이전 그룹 주보와 사용자의 수정 요청이 이 모델 주소로 전송됩니다. `GR_LLM_BASE_URL`은 승인된 사내 주소만 설정하세요.

## 7. 실행 전 검증

먼저 자동 테스트를 실행합니다.

```bash
cd group_report_studio
.venv/bin/python -m unittest discover -s tests -q
```

현재 기준은 37개 테스트 통과입니다. Python 또는 패키지 버전에 따라 테스트 수가 늘어날 수 있으므로 최종 기준은 실패와 오류가 0건인지 확인하는 것입니다.

설정 파일을 명시해 서버를 실행합니다.

```bash
.venv/bin/python run.py --env-file .env --port 8091
```

다른 터미널에서 준비 상태를 확인합니다.

```bash
curl --fail http://127.0.0.1:8091/api/config
```

응답의 `ready`가 `true`이고 `missing`이 빈 배열이어야 합니다. 이는 설정값이 존재한다는 뜻이며, OpenSearch와 모델의 실제 연결 성공은 첫 생성 작업으로 확인합니다.

브라우저에서 `http://127.0.0.1:8091/`을 열고 다음 순서로 점검합니다.

1. 시험 주차와 제목을 입력합니다.
2. 예상 팀 목록을 입력합니다.
3. 직전 1주와 2주의 그룹 전체 주보를 DOCX, TXT 또는 MD로 등록합니다.
4. 초안 생성을 실행합니다.
5. 팀 수, 원문 수, 누락 팀 경고를 확인합니다.
6. 근거 화면에서 인용문과 원문 연결을 확인합니다.
7. 대화 편집으로 한 항목을 수정하고 버전이 증가하는지 확인합니다.
8. 초안을 확정한 뒤 Word를 내려받아 한글 글꼴과 페이지 구성을 확인합니다.

출력 본문은 문장과 글머리표만 사용하며 표 블록이 생성돼도 문장형 텍스트로 변환합니다.

## 8. 더미 데이터로 폐쇄망 시험하기

더미 자료를 반입한 경우 [demo_2026_37/README.md](demo_2026_37/README.md)를 참고합니다. 모든 팀명·수치·활동은 가상 데이터입니다.

운영 인덱스와 분리된 시험용 OpenSearch 인덱스를 사용하고 싶다면 `seed.py`의 고정 인덱스 `weekly_mail`을 사내 시험 인덱스로 변경한 사본을 별도 검토한 뒤 실행하세요. 원본 `seed.py`는 문서를 추가하며 임베딩 생성 API도 호출하므로 일반 앱의 읽기 전용 보안 특성과 다릅니다.

더미 색인이 필요하지 않다면 다음 파일만으로 화면과 이전 주보 입력을 확인할 수 있습니다.

- `demo_2026_37/그룹주보_2026-35_텍스트전용.docx`
- `demo_2026_37/그룹주보_2026-36_더미.docx`
- `demo_2026_37/teams.txt`

## 9. 운영 배치 시 추가 사항

현재 서버는 `127.0.0.1`에만 바인딩되고 허용 호스트도 localhost 계열로 제한됩니다. 개인 PC 또는 점프 서버의 로컬 브라우저에서 쓰는 구성이 기본입니다.

여러 사용자가 접속하는 사내 서비스로 전환하려면 배포 전에 별도로 구현하고 보안 검토해야 합니다.

- 사용자 인증과 권한별 문서 접근 통제
- HTTPS 종단과 승인된 호스트 이름
- 사용자별 또는 조직별 저장소 분리
- 감사 로그와 보존 기간
- SQLite 대신 동시 쓰기를 지원하는 운영 DB
- 작업 큐와 서버 재시작 정책
- 업로드 문서의 악성 파일 검사

인증 없이 `0.0.0.0`으로 바인딩하거나 현재 Trusted Host 제한만 제거해서 공용망에 노출하지 마세요.

## 10. 주요 오류 점검

### OpenSearch 조회 실패 또는 HTTP 503

1. `GR_OPENSEARCH_URL`, 계정, TLS 인증서를 확인합니다.
2. 읽기 계정으로 대상 인덱스의 `_search`와 scroll이 가능한지 확인합니다.
3. `week`, `mail_type`, `type`의 실제 매핑과 `GR_OPENSEARCH_KEYWORD_SUFFIX`가 맞는지 확인합니다.
4. 해당 주차에 `weekly_report`와 `original_part` 문서가 있는지 확인합니다.
5. 각 메일의 `part_index`가 `0..total_parts-1`을 빠짐없이 가지는지 확인합니다.

### 모델 응답 형식 오류

1. 사내 API가 Chat Completions와 `json_schema`를 지원하는지 확인합니다.
2. 모델 출력 한도가 너무 작지 않은지 확인합니다.
3. 응답의 `message.content`가 빈 값이 아닌 완전한 JSON 문자열인지 확인합니다.
4. 모델 로그에는 원문과 인증정보를 남기지 말고 단계명·HTTP 상태·오류 유형만 기록합니다.

작성이나 검증이 반복해서 실패한 항목은 작업 전체를 버리지 않고 출처가 연결된 원문 발췌로 제공될 수 있습니다. 화면의 경고를 확인하고 사람이 문장을 다듬으세요.

### 근거 검증 실패

작성 문장이 원문보다 상태·원인·계획을 확대했는지 확인합니다. 예를 들어 원문의 `matching 미완료`를 `matching 완료`로 바꾸거나 관찰 사실을 지속 모니터링 계획으로 확장하면 검증에서 차단됩니다.

### Word 다운로드 불가

초안을 먼저 확정해야 내려받을 수 있습니다. 서버에 `python-docx`가 설치되어 있는지, `GR_DATA_DIR`에 쓰기 권한이 있는지 확인합니다. 회사 양식 적용은 [studio/documents.py](studio/documents.py)의 출력 스타일과 템플릿 처리를 사내 양식에 맞춰 변경합니다.

## 11. 이식 완료 체크리스트

- [ ] `group_report_studio/` 외 기존 파일을 덮어쓰지 않았다.
- [ ] 실제 `.env`, 키, 비밀번호, 운영 DB가 Git에 포함되지 않았다.
- [ ] Python 의존성을 사내 승인 경로로 설치했다.
- [ ] OpenSearch 계정에 읽기 권한만 부여했다.
- [ ] 필수 필드와 keyword 매핑을 확인했다.
- [ ] 승인된 사내 모델 주소를 설정했다.
- [ ] 자동 테스트가 오류 없이 통과했다.
- [ ] 시험 주차에서 예상 팀 수와 원문 수를 확인했다.
- [ ] 원문 인용과 생성 문장을 사람이 표본 검토했다.
- [ ] 대화 수정, 버전 복원, 확정 및 Word 출력을 확인했다.
- [ ] 다중 사용자 배포라면 인증·권한·운영 DB를 별도로 구현했다.
