# LLM 호출 기록 기능 적용 및 사용

## 설정

사내 `.env`에 다음 설정을 추가하고 서버를 재시작한다.

```dotenv
GR_DEBUG_ENABLED=true
GR_DEBUG_RETENTION_DAYS=7
```

기본은 꺼짐이다. 일반 `run.py --env-file .env`와 데모 실행 모두 위 설정을 읽는다. 이미 실행 환경에 같은 환경변수가 있으면 일반 실행에서는 환경변수가 `.env`보다 우선한다.

## 보는 방법

웹에서 보고서를 열고 작업 상태 아래 **호출 기록**을 누른다. 실패한 단계의 항목을 펼쳐 실제 요청과 응답을 확인한다. **전체 JSON 저장**으로 조회 시점의 기록을 다운로드한다. 새 기록은 창을 닫고 다시 열어 갱신한다.

API: `GET /api/jobs/{실제 작업 ID}/debug`. 주소의 작업 ID를 문자 그대로 쓰지 않는다. 웹 버튼은 해당 작업의 실제 ID를 자동으로 사용한다.

서버 파일: `GR_DATA_DIR/debug/{작업 ID}/{호출 ID}.json`. 기본 데이터 경로는 `group_report_studio/.local`이다. 요청·응답·결과를 호출별 JSON 하나에 담으며 매번 임시 파일을 통해 교체한다.

## 필드

- `stage`: extract_lines, write, verify 등 호출 단계
- `section`: 소주제 정보(있는 경우)
- `depth`: 자동 분할 깊이
- `parent_id`: 부모 호출 ID. 본문→검증, 분할 전→분할 후 관계를 연결
- `status`: running, completed, failed, completed_without_http
- `error_type`, `error`: 호출 또는 근거 검증 실패 이유
- `elapsed_seconds`: 호출 및 하위 처리 소요 시간
- `attempts`: HTTP 시도 순서. 각 항목의 request는 실제 모델명·messages·출력 한도·response_format을 포함
- `response`: HTTP 상태, 응답 본문 문자열, HTTP 소요 시간. 본문에는 서버가 제공한 usage와 finish_reason도 포함
- `parsed_result`: JSON 파싱 및 응답 스키마 검증을 통과한 값. 의미상 근거 검증 통과를 뜻하지는 않음
- 각 시도의 `error`: JSON/스키마/출력 초과/연결 오류. HTTP 상태 오류는 response 및 호출 전체 error에서도 확인

응답 본문은 파싱 전에 저장하므로 잘린 JSON, 빈 content, HTML 오류 페이지도 확인할 수 있다. 입력 크기 검사에서 중단된 요청에는 `not_sent=true`가 표시된다. HTTP 연결 중단이면 request만 있고 response는 없을 수 있다.

`completed_without_http`는 캐시 재사용 등 실제 HTTP 호출이 없는 경우다. 기존 캐시의 입력/응답을 새로 복구하지 않는다. 디버그 모드를 켜기 전의 실패 요청도 소급 기록되지 않는다. 새 보고서로 재현하면 전체 호출 기록을 얻을 수 있다.

`verify`의 parsed_result가 `supported=false`이면 상위 write 기록의 오류와 함께 확인한다. 자동 분할이나 원문 발췌 전환으로 상위 호출이 completed여도 자식 실패와 응답은 남는다.

## 사내 반입 파일

다음 파일을 같은 버전으로 반입한다.

- 신규: `studio/debug.py`, `tests/test_debug.py`, `DEBUG_MIGRATION.md`
- 변경: `studio/config.py`, `studio/harness.py`, `studio/llm.py`, `studio/api.py`
- 변경: `studio/static/app.js`, `studio/static/index.html`, `studio/static/styles.css`
- 설정 예시: `.env.example`
- 데모를 사용할 때: `demo_2026_37/run_demo.py`

사내 전용 모델 URL·인증·헤더를 수정한 llm.py는 전체 덮어쓰기 대신 trace_event 호출을 병합한다. request는 HTTP 전송 전, response는 파싱 전, parsed_result는 스키마 통과 후 기록한다. Harness.call은 DebugLog.call 문맥 안에서 checkpoint를 실행한다. 모델 자체를 바꾸거나 원문 근거 검증을 제거하지 않는다.

## 검증

group_report_studio에서 실행한다.

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_debug.py' -v
.venv/bin/python -m unittest discover -s tests -q
```

브라우저에서는 모드를 끈 상태의 안내, 켠 뒤 새 작업의 호출 목록, JSON 다운로드를 확인한다. 이 기능은 기존 로컬 단일 사용자 서비스의 접근 범위를 따른다.

## 저장 범위와 보존

요청 헤더와 인증 URL은 저장하지 않는다. 설정된 모델 키와 OpenSearch 비밀번호는 문자열에서도 마스킹하고, 비밀번호·인증 관련 JSON 키를 가린다. 따라서 보안정보가 포함된 부분은 원본과 다를 수 있다.

메일 원문, 프롬프트, 사용자 지시와 생성 본문은 디버깅을 위해 저장된다. 그 안의 일반 개인정보나 업무 비밀까지 자동 익명화하는 기능은 아니다. 사내 경로에서만 보관하고 승인된 범위에서 사용한다.

기록 파일은 소유자 읽기/쓰기 권한으로 생성한다. 보존 기간이 지난 호출 JSON은 서버 시작 또는 기록 조회 시 정리한다(상시 실행되는 삭제 스케줄러는 아님). 모드를 끄면 새로 기록하지 않고 웹에서 기록을 반환하지 않는다. 이미 내려받은 JSON에는 서버 보존 정책이 적용되지 않는다.

디스크 용량/권한 문제로 저장이 실패하면 작업이 실패할 수 있으므로 GR_DATA_DIR의 쓰기 권한과 여유 공간을 확인한다.
