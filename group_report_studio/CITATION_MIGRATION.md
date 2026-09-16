# 원문 인용 범위 오류 수정 — 후보 ID 방식

## 참고한 공식 자료

- [LangChain: RecursiveCharacterTextSplitter](https://docs.langchain.com/oss/python/integrations/splitters/recursive_text_splitter): 구분자 우선순위로 원문을 작은 단위로 분할하고, 길이 기준과 중첩 구간을 지정한다.
- [LlamaIndex: CitationQueryEngine 공식 구현](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/query_engine/citation_query_engine.py): 인용용 원문 조각을 생성하고 번호로 출처를 연결한다.

확인일: 2026-09-16. 이 변경은 두 프로젝트의 설계 원칙을 참고한 **이 앱의 독립 구현**이다. 공식 클래스를 직접 호출하거나 그 동작을 완전히 복제한 것은 아니다. 새로운 패키지 설치가 필요하지 않다.

## 해결하는 문제

이전에는 모델이 start_line/end_line을 선택했다. 선택 범위가 비었거나 4,000자를 넘으면 같은 요청으로 세 번 다시 선택하다 중단됐다.

현재 기본 생성 경로는 코드가 인용 후보를 미리 만든 뒤 모델에는 candidate_id와 section_ids만 반환하도록 한다. 모델이 긴 범위를 만들거나 원문을 다시 작성하지 않는다.

## 분할 및 검증 규칙

1. 기존 바이트 기반 입력 분할은 유지한다. 각 입력 조각에서 인용 후보를 만든다.
2. 후보는 최대 1,800자다. 문단 → 줄 → 문장 구분자 → 공백 순으로 경계를 찾으며, 적절한 경계가 없으면 문자 기준으로 나눈다.
3. 다음 후보에 최대 150자의 중첩 구간을 제공한다. 제품명·조건이 모든 경계에서 완전히 보존된다는 보장은 없으므로 의미 검증과 사람의 검토는 유지한다.
4. 공백뿐인 후보는 제외한다. 비공백 원문은 빠짐없이 후보에 포함한다.
5. 후보는 원문의 연속된 부분 문자열이며, start/end 위치와 결정적인 ID를 가진다. 인용문을 잘라 버리거나 재작성하지 않는다.
6. 모델은 요청에 있는 후보 ID를 각각 한 번 반환한다. 관련 소주제가 없으면 section_ids=[]로 반환한다. 누락·중복·외부 ID·잘못된 소주제 ID는 검증 실패다.
7. 한 요청은 최대 40개 후보를 연결한다. 출력 초과 시 후보 목록을 절반씩 나눠 처리하며, 나눠진 요청에서도 그 요청에 속한 후보만 허용한다.
8. 실제 quote와 text는 코드가 후보에서 복원한다. 1,800자이므로 기존 4,000자 quote/2,500자 text 제한을 모두 만족한다.

같은 후보 안에 여러 사실이 포함될 수 있다. 세밀한 사실 추출 대신 제한된 크기의 원문 단위를 소주제에 연결하므로 후속 작성 입력이 늘거나 비슷한 구절이 나타날 수 있다. 중첩 구간의 수치가 중복 실적으로 집계되지 않도록 작성·검증 결과를 검토한다.

## 반입 파일

- 신규: `studio/citations.py`, `tests/test_citations.py`, 이 문서
- 변경: `studio/models.py`, `studio/harness.py`
- 변경 테스트: `tests/test_harness.py`, `tests/test_extraction_retry.py`, `tests/test_regressions.py`

이 문서와 같은 버전의 파일을 함께 반입한다. 앞선 출력 초과 대응 및 호출 기록 기능이 적용된 버전을 기준으로 한다. 사내 원문, .env, 인증정보, DB를 반출하거나 덮어쓸 필요는 없다.

사내에서 harness.py를 따로 수정했다면 다음 항목만 병합한다.

1. citation_candidates 및 CandidateExtraction import
2. call의 extract_candidates 재시도, 후보 ID 집합 검증, 후보 분할 처리
3. 새 extract_candidates 메서드
4. extract의 호출을 extract_by_lines에서 extract_candidates로 변경

기존 extract_by_lines 메서드는 이전 호출 및 시험 코드 호환용으로 남겨둔다. 정상 새 생성의 기본 경로에서는 사용하지 않는다. 그 메서드의 4,000자 검사를 삭제하거나 제한값만 올리는 패치는 하지 않는다.

## 사내 검증과 재개

group_report_studio에서:

```bash
.venv/bin/python -m unittest discover -s tests -p test_citations.py -v
.venv/bin/python -m unittest discover -s tests -q
```

새 후보 테스트 4개는 긴 한국어 원문·줄바꿈 없는 문자열·빈 문자열의 분할, 원문 보존과 범위 상한, 출력 초과 시 자동 분할과 캐시, 잘못된 후보 응답 거절을 검증한다.

서버를 재시작하고 실패한 작업을 재시도한다. 디버그 기록의 추출 단계가 `extract_candidates`인지 확인한다. 이전 `extract_lines` 캐시와 새로운 후보 응답은 단계명·프롬프트·스키마가 달라 섞이지 않는다. 최초 재시도에서는 원문 연결을 다시 수행할 수 있다.

이미 완료된 문서를 검증하려면 새 보고서를 만들어 생성한다. 사내 모델에서 제품명·집계기간·수치·미완료 상태와 누락 여부를 확인한다. 잘못된 ID 선택이나 모델 서버 오류까지 무조건 성공시키는 수정은 아니다.
