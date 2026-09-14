# Group Report Studio Implementation Plan

**Goal:** 기존 레포의 파일을 변경하지 않고 주보 생성·웹 대화 편집·Word 출력을 제공한다.
**Architecture:** 새 FastAPI 서버와 정적 웹, SQLite 문서/실행 저장소, 읽기 전용 OpenSearch 어댑터, 제한된 단계의 LLM 하네스. 한 프로세스 워커가 저장된 체크포인트에서 재시도한다.
**Tech stack:** Python 3.11+, FastAPI, httpx, Pydantic v2, sqlite3, python-docx, HTML/CSS/JavaScript. 기존 파일 import는 허용되나 필요하지 않다.

## Global constraints
- 모든 새 파일은 group_report_studio/ 아래에 둔다. 기존 파일과 기존 의존성 파일은 수정하지 않는다.
- weekly_mail은 조회만 한다. 새로운 원문이나 보고서를 기존 인덱스에 쓰지 않는다.
- 회사 Word 원본 양식은 아직 없다. 기본 DOCX 스타일로 시작하며 출력 함수에서 교체한다.
- 이전 2주 그룹 주보는 사용자가 DOCX/TXT/MD로 제공하거나 본문을 붙여 넣는다.
- 실제 연결을 설정하기 전 임의의 주보를 운영 자료처럼 표시하지 않는다.
- 로컬 단일 사용자 서버가 기본이다. 회사 다중 사용자 배포에는 별도 인증 계층이 필요하다.

## Tasks
- [x] 1. 모델·기본 양식·SQLite 버전 저장소: 오래된 버전 수정 거절, 복원은 새 버전, 저장 후 다시 읽기를 unittest로 검증.
- [x] 2. OpenSearch 전체 조회와 사실 추출: 필터, 모든 페이지, 누락 part, 출처 검증, 제한 입력, 실패 후 캐시 재개를 fake HTTP/LLM로 검증.
- [x] 3. 생성·수정 하네스와 API: 생성→선택 항목 수정→복원→확정→출력 통합 테스트. 임의 사실 근거 거절, 실행 취소, 오류 상태 노출.
- [x] 4. 웹 편집기: 세 영역 화면, 작성 설정·가변 목차·이전 주보·진행 상황·수정·근거·버전 복원·Word 출력. Node 구문 검증 및 브라우저 사용 확인.
- [x] 5. DOCX 출력/참고 입력: 문단·표 순서 보존, 한국어 글꼴, 다중 페이지 표, 잘못된 파일 거절. 실제 DOCX 렌더링과 이미지 확인.
- [x] 6. README와 독립 실행 설정: 새 디렉터리만 복사해도 실행 가능. 전체 unittest와 기존 파일 변경 여부 확인.

## HTTP contract
All paths start /api. Errors are JSON {detail:string}.
- GET /config -> {ready:bool, missing:[str], default_template:{name,sections:[{id,group,title,instructions}]}}
- GET /templates -> [{id,name,sections}]; POST /templates body {name,sections} -> saved template.
- GET /reports -> [{id,title,week,version,status,updated_at}]
- POST /reports body {week,title,template:{name,sections},references:[{name,text,week}],expected_teams:[str]} -> report (version 0 empty draft).
- GET /reports/{id} -> {id,title,week,version,status,template,references,sections:[{id,group,title,blocks:[{kind:"paragraph"|"bullet"|"table",text,headers:[str],rows:[[str]],evidence_ids:[str]}],warnings:[str]}],warnings:[str],coverage:object,versions:[{version,reason,created_at}],messages:[{role,content}],facts:[{id,text,source_id,quote,section_ids:[str]}],sources:[{id,team,week,mail_id,part_index,text}],active_job:job|null}
- POST /reports/{id}/generate body {base_version:int} -> job.
- POST /reports/{id}/edit body {base_version:int,message:str,section_id:str|null} -> job.
- POST /reports/{id}/restore body {base_version:int,target_version:int} -> report.
- POST /reports/{id}/finalize body {base_version:int} -> report.
- GET /reports/{id}/export?version=N -> DOCX bytes. Requires finalized version.
- GET /jobs/{id} -> {id,report_id,status:"queued"|"running"|"succeeded"|"failed"|"cancelled",progress:int,message,error:string|null}
- POST /jobs/{id}/retry or /cancel -> job.
- POST /references/parse body {name,content_base64} -> {name,text}; support DOCX/TXT/MD, no multipart.

## Python document contract
studio.documents.export_docx(report:dict) -> bytes; report fields are HTTP report schema above.
studio.documents.parse_reference(name:str,content:bytes) -> str. Raise ValueError with readable Korean message for unsupported or invalid files.

## UI design
문서 중심 3열 작업대: 좁은 파란 회색 목차 / 흰 문서 / 대화. Palette: #183B56 ink, #245A81 accent, #EAF0F5 workspace, #FFFFFF paper, #63788A muted, #B44D36 warning. 시스템 한국어 sans-serif, 제목 22px/본문 14px. 임의 통계·주보 없이 작성 안내를 표시한다. 모바일에서는 영역을 세로로 배치한다.

## Verification
Run from group_report_studio: ../.venv/bin/python -m unittest discover -s tests -v. Use httpx ASGITransport for API tests (existing FastAPI TestClient is incompatible with installed httpx). node --check studio/static/app.js. Use bundled document runtime for DOCX visual QA. No external mail sending or index mutation.
