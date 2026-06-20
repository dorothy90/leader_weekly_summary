# 주제별 타임라인 — PPTAgent 고품질 PPT 연동 가이드

기본 PPT 엔진(`native`, python-pptx 기반)보다 높은 품질의 발표 자료가 필요할 때
[PPTAgent / DeepPresenter](https://github.com/icip-cas/PPTAgent)를 외부 도구로
연동한다. **PPTAgent 소스를 본 프로젝트에 이식(vendoring)하지 않는다** — 대신
별도 설치 후 CLI로 호출하고, 본 프로젝트에는 얇은 어댑터(`topic_timeline_pptagent.py`)만 둔다.

> 이식하지 않는 이유: PPTAgent는 멀티 에이전트 + 샌드박스 툴 20여 개 + LibreOffice /
> Chrome / poppler / Node.js 시스템 의존(권장 GPU 모델 포함)을 가진 대형 프레임워크라,
> 통째로 합치면 의존성 충돌·유지보수 부담이 본 프로젝트로 전이되고 업스트림 업데이트도
> 받기 어렵다.

---

## 1. 사전 요구사항

PPTAgent를 **실행할 호스트**(서버/PC)에 설치한다. (현재 RAG API 서버와 같은 머신이거나,
네트워크로 파일을 주고받을 수 있는 머신)

- Python 3.11+
- LibreOffice, Chrome(or Chromium), poppler-utils, Node.js
- `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

## 2. PPTAgent 설치

```bash
# uvx 로 바로 사용 (권장)
uvx pptagent onboard

# 또는 소스 설치
git clone https://github.com/icip-cas/PPTAgent
cd PPTAgent
uv pip install -e .
cp deeppresenter/config.yaml.example deeppresenter/config.yaml
cp deeppresenter/mcp.json.example   deeppresenter/mcp.json
```

## 3. LLM 설정 — 기존 OpenRouter 재사용

본 프로젝트가 쓰는 OpenRouter(OpenAI 호환) LLM을 PPTAgent에도 그대로 연결한다.
`deeppresenter/config.yaml` 의 언어모델 섹션을 OpenAI 호환 엔드포인트로 지정한다(키 이름은
설치한 PPTAgent 버전의 example 파일을 따른다).

```yaml
# deeppresenter/config.yaml (예시 — 실제 키 구조는 config.yaml.example 기준으로 맞출 것)
language_model:
  base_url: ${OPENROUTER_BASE_URL}   # 본 프로젝트와 동일
  api_key:  ${OPENROUTER_API_KEY}
  model:    gpt-oss-120b             # 또는 원하는 OpenRouter 모델
```

본 프로젝트의 관련 환경변수(`.env`):

| 변수 | 용도 |
|------|------|
| `OPENROUTER_API_KEY` | OpenRouter API 키 |
| `OPENROUTER_BASE_URL` | OpenRouter base URL |
| `LLM_MODEL` | 사용할 모델 (기본 `gpt-oss-120b`) |

> 참고: PPTAgent는 공식적으로 fine-tuned `DeepPresenter-9B` 배포를 권장한다. OpenRouter
> 같은 범용 OpenAI 호환 LLM으로도 동작하도록 설계되어 있으나, 최고 품질이 필요하면 전용
> 모델 배포를 고려한다.

## 4. 회사 레퍼런스 템플릿 지정

품질의 핵심은 **회사 표준 .pptx 템플릿**을 레퍼런스로 주는 것이다. 템플릿 파일을 호스트에
두고 환경변수로 경로를 지정한다.

```bash
export PPTAGENT_TEMPLATE="/abs/path/회사_표준_템플릿.pptx"
```

> CLI에서 템플릿을 받는 플래그명은 PPTAgent 버전에 따라 다를 수 있다. 어댑터는 기본값으로
> `--reference` 를 사용하며, 실제 플래그가 다르면 `PPTAGENT_TEMPLATE_FLAG` 로 교정한다.
> 일부 버전은 config.yaml/템플릿 디렉터리로 레퍼런스를 관리하므로, 그 경우 CLI 플래그 대신
> 해당 방식으로 설정하고 `PPTAGENT_TEMPLATE` 는 비워둔다.

## 5. 본 프로젝트 연동 — 엔진 전환

RAG API 서버(`rag_api_opensearch_v3.py`)에서 PPT 엔진을 환경변수로 고른다.

```bash
# PPT 엔진: native(기본, python-pptx) | pptagent
export TOPIC_TIMELINE_PPT_ENGINE=pptagent

# PPTAgent 호출 설정 (topic_timeline_pptagent.py)
export PPTAGENT_CMD="uvx pptagent generate"   # 실행 명령
export PPTAGENT_TEMPLATE="/abs/path/회사_표준_템플릿.pptx"
export PPTAGENT_TEMPLATE_FLAG="--reference"    # 버전에 맞게 조정
export PPTAGENT_TIMEOUT=1800                    # 초
# export PPTAGENT_EXTRA_ARGS="-p 1-20"         # 추가 인자(선택)
# export PPTAGENT_PROMPT="..."                  # 생성 지시문(선택)
```

동작: `/topic-timeline` 작업이 완료되면 생성된 **마크다운 리포트**를 PPTAgent에 `-f` 로
넘겨 pptx를 만든다. 실패하면 자동으로 native 엔진으로 폴백한다. 결과는 기존과 동일하게
`GET /topic-timeline/{job_id}/download?format=pptx` 로 받는다.

## 6. 빠른 점검 (실행 없이 명령만 확인)

어댑터가 조립하는 CLI 명령을 미리 확인:

```bash
python topic_timeline_pptagent.py exports/topic_timeline/<job_id>.md \
  -o /tmp/out.pptx --template "$PPTAGENT_TEMPLATE" --dry-run
```

실제 생성:

```bash
python topic_timeline_pptagent.py exports/topic_timeline/<job_id>.md \
  -o /tmp/out.pptx --template "$PPTAGENT_TEMPLATE"
```

## 7. 트러블슈팅

| 증상 | 조치 |
|------|------|
| `PPTAgent 실행 파일을 찾을 수 없습니다` | `uvx pptagent` 설치 확인, `PPTAGENT_CMD` 경로 점검 |
| 템플릿이 반영 안 됨 | `PPTAGENT_TEMPLATE_FLAG` 가 설치 버전의 실제 플래그와 일치하는지 확인 (또는 config.yaml 방식으로 전환) |
| LLM 인증 오류 | config.yaml 의 base_url/api_key/model 확인 |
| 타임아웃 | `PPTAGENT_TIMEOUT` 상향 |
| 항상 native로 폴백됨 | 서버 로그의 `PPTAgent 생성 실패` 메시지에서 stderr 확인 |
