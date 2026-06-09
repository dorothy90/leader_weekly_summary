# Windows 자동화 가이드 — 주간 리포트 파이프라인

`fetch → process → embed → wiki_build → wiki_export → email` 6단계를 Windows에서
한 번에 실행/스케줄링하는 방법.

## 파이프라인 단계 매핑

| 단계 | 스크립트 | 산출물 |
|---|---|---|
| **fetch** | `fetch_mail.py` | `data/{주차}/{팀}/mail_*/` (본문·이미지·첨부) |
| **process** | `process_vision.py` → `process_attachment.py` | `combined.txt`, `attachments.json` |
| **embed** | `embed_vectordb.py` | OpenSearch `weekly_mail` 인덱스 |
| **wiki_build** | `wiki_builder.py --week {주차}` | OpenSearch `wiki_summaries` 인덱스 |
| **wiki_export** | `wiki_export.py --week {주차} --type overview` | `wiki/overview/{주차}_전체요약.md` |
| **email** | `generate_outlook_report.py` → `send_outlook_report.py` | Outlook 호환 HTML + 메일 발송 |

오케스트레이션 파일:
- `scripts/run_pipeline.ps1` — 실제 6단계 실행기 (로그/에러중단/주차 자동계산)
- `scripts/run_pipeline.bat` — 더블클릭 & 작업 스케줄러용 런처
- `send_outlook_report.py` — Outlook COM(win32com) 메일 발송기

---

## 1. 사전 준비 (최초 1회)

### 1-1. Python & 의존성

```powershell
# 프로젝트 폴더에서
cd C:\path\to\leader_weekly_summary

# (권장) 가상환경 — 만들어 두면 run_pipeline.ps1 가 .venv\Scripts\python.exe 를 자동 사용
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install pywin32 python-dotenv   # Outlook 발송 + .env 로드용
```

> `requirements.txt` 에는 Outlook 발송용 `pywin32` 와 `.env` 로드용 `python-dotenv`
> 가 빠져 있으므로 위와 같이 별도 설치한다.

### 1-2. 환경변수(.env) 작성

프로젝트 루트에 `.env` 파일을 만들고 채운다. (스크립트들이 `load_dotenv()` 로 읽음)

```dotenv
# --- LLM (OpenRouter / 사내 LLM) ---
OPENROUTER_API_KEY=sk-...

# --- OpenSearch ---
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=********
OPENSEARCH_USE_SSL=true

# --- 메일 발송 수신자 (send_outlook_report.py) ---
REPORT_TO=boss@corp.com; team@corp.com
REPORT_CC=me@corp.com
# REPORT_SUBJECT=[주간 리포트] ...   # 미지정 시 파일명 기반 자동 생성
```

> Gmail 수집 계정/비밀번호는 현재 `fetch_mail.py` 상단에 하드코딩되어 있다.
> 보안상 추후 `.env` 로 옮기는 것을 권장.

### 1-3. OpenSearch 실행 확인

`embed / wiki_build / wiki_export` 는 OpenSearch가 떠 있어야 한다.
파이프라인 실행 전에 OpenSearch(예: Docker 또는 서비스)가 `OPENSEARCH_HOST:PORT` 에서
응답하는지 확인한다. 부팅 시 자동 기동되도록 서비스/컨테이너 재시작 정책을 걸어두면 좋다.

### 1-4. Outlook 준비

`send_outlook_report.py` 는 로컬에 **설치·로그인된 Outlook 데스크톱 앱**을 COM으로 제어한다.
- 데스크톱 Outlook이 설치되어 있고 계정에 로그인되어 있어야 한다.
- 작업 스케줄러로 무인 실행할 때는 **"사용자가 로그온할 때만 실행"** 옵션이 필요할 수 있다
  (COM 자동화는 대화형 세션을 요구). 아래 3-2 참고.

---

## 2. 수동 실행 (.bat 더블클릭)

```text
scripts\run_pipeline.bat                 → 이번 주차 자동계산, 메일 "초안만" 열기(안전)
scripts\run_pipeline.bat 2026-11         → 특정 주차 지정
scripts\run_pipeline.bat 2026-11 send    → 특정 주차 + 자동 발송
scripts\run_pipeline.bat "" send         → 이번 주차 + 자동 발송
```

- 인자 없이 실행하면 email 단계에서 **메일을 발송하지 않고 Outlook 초안만 띄운다.**
  내용 확인 후 사람이 직접 [보내기] → 첫 운영/검증에 안전.
- `send` 를 붙이면 `mail.Send()` 로 **즉시 자동 발송**.

PowerShell에서 직접 실행도 가능:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_pipeline.ps1 -Week 2026-11 -AutoSend
```

옵션:
- `-Week 2026-11` : 주차 수동 지정 (미지정 시 오늘 기준 ISO 주차 자동계산)
- `-AutoSend`     : email 단계 자동 발송
- `-SkipFetch`    : 메일 재수집 없이 기존 데이터로 재실행

실행 로그는 `logs\pipeline_{주차}_{타임스탬프}.log` 에 저장된다.
어느 단계든 실패하면 그 지점에서 **즉시 중단**하고 로그에 exit code를 남긴다.

---

## 3. 작업 스케줄러 등록 (무인 자동 실행)

매주 정해진 시각에 자동 실행하려면 Windows **작업 스케줄러(Task Scheduler)** 에 등록한다.

### 3-1. GUI로 등록

1. `Win + R` → `taskschd.msc` 실행
2. 우측 **작업 만들기(Create Task)** (기본 작업 아님 — 권한 옵션이 더 많음)
3. **일반** 탭
   - 이름: `WeeklyReportPipeline`
   - **"사용자가 로그온할 때만 실행"** 선택 (Outlook COM 자동화에 필요)
   - "가장 높은 수준의 권한으로 실행" 체크
4. **트리거** 탭 → 새로 만들기
   - 매주 / 원하는 요일·시각 (예: 매주 월요일 08:00)
5. **동작** 탭 → 새로 만들기
   - 프로그램/스크립트: `C:\path\to\leader_weekly_summary\scripts\run_pipeline.bat`
   - 인수 추가: `"" send`   ← 이번 주차 + 자동 발송
   - 시작 위치: `C:\path\to\leader_weekly_summary`
6. **조건/설정** 탭에서 "AC 전원" 등 필요에 맞게 조정 후 저장.

### 3-2. PowerShell 한 줄로 등록

관리자 PowerShell에서 (경로는 실제 위치로 수정):

```powershell
$repo = "C:\path\to\leader_weekly_summary"
$action  = New-ScheduledTaskAction -Execute "$repo\scripts\run_pipeline.bat" `
              -Argument '"" send' -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 8:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "WeeklyReportPipeline" `
    -Action $action -Trigger $trigger -Settings $settings `
    -RunLevel Highest -Description "주간 리포트 6단계 파이프라인 자동 실행"
```

> **주의:** Outlook COM 자동 발송은 대화형 데스크톱 세션이 필요하다.
> `-LogonType S4U`(미로그온 백그라운드)로는 Outlook 제어가 실패할 수 있으므로,
> 위 GUI 절차의 "사용자가 로그온할 때만 실행"을 사용하는 것을 권장한다.
> 완전 무인(로그오프 상태) 발송이 꼭 필요하면 Outlook COM 대신 Gmail SMTP 방식으로
> 전환하는 것을 고려한다.

### 3-3. 동작 확인

작업 스케줄러에서 해당 작업을 우클릭 → **실행**으로 즉시 테스트.
`logs\` 폴더의 최신 로그로 각 단계 성공/실패를 확인한다.

---

## 4. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `pywin32 가 필요합니다` | `pip install pywin32` |
| `overview 마크다운이 없습니다` | wiki_export 실패 — 해당 주차의 `wiki_summaries` 문서가 없음. 주차 인자/OpenSearch 데이터 확인 |
| embed/wiki 단계에서 연결 오류 | OpenSearch 미기동 또는 `.env` 의 `OPENSEARCH_*` 값 불일치 |
| `이 시스템에서 스크립트를 실행할 수 없습니다` | `.bat` 가 `-ExecutionPolicy Bypass` 로 호출하므로 정상. 직접 ps1 실행 시 정책 확인 |
| 메일이 발송 안 되고 창만 뜸 | `send` 인자/`-AutoSend` 미지정(기본=초안). 자동발송하려면 추가 |
| embed가 매번 전체 재색인 | `embed_vectordb.py` 의 `RECREATE_INDEX=True` 때문. 의도된 전체 재빌드. 증분만 원하면 해당 값을 조정 |

---

## 5. 첫 운영 권장 순서

1. `.env` 작성 + OpenSearch 기동 + Outlook 로그인 확인
2. `scripts\run_pipeline.bat 2026-11` (인자 없는 = 초안 모드)로 **수동 1회** 실행 →
   각 단계 로그 확인 + Outlook 초안 내용 검수
3. 문제 없으면 `scripts\run_pipeline.bat "" send` 로 자동 발송 검증
4. 작업 스케줄러에 `"" send` 인자로 주간 등록
