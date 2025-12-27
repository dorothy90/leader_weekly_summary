# Weekly Tech × Team Vision Email Intelligence Agent PRD

## 1. Overview
본 문서는 주간 팀장 보고 메일(본문 + 이미지)을 Vision 모델로 해석하여 자동 수집·분류·요약하고,  
**Layer1 전수 집계표**, **Layer2 팀별 스토리 요약**, 그리고 **히스토리 기반 Q&A Chatbot**을 제공하는  
통합 Email Intelligence Agent의 요구사항을 정의한다.

본 시스템의 목적은:
- 메일과 이미지 속 정보를 **누락 없이 지식화**
- DRAM/NAND + Tech + Team 기준의 **구조적 가시화**
- 최근 히스토리를 반영한 **스토리형 요약**
- 누적된 주간 메일을 활용한 **대화형 질의 응답**
이다.

---

## 2. Goals
- 팀별 weekly 메일(본문 + 이미지) 자동 수집
- Vision LLM을 이용한 이미지 내 텍스트/표/수치 해석
- DRAM/NAND + Tech × Team 기준 Layer1 전수 테이블 생성
- 최근 3주 히스토리를 반영한 Layer2 팀별 요약 생성
- 모든 원문을 embedding하여 Vector DB에 저장
- Vector DB 기반 RAG Chatbot 제공
- HTML 리포트 자동 생성

---

## 4. System Architecture

### 4.1 High-Level Flow

Mail Ingest → Vision Parse Images → Merge Text → Preprocess → Tech Classify  
→ Embed & Vector DB Upsert → Layer1 Generator → Retrieve History → Layer2 Summary → Render

동시에, Vector DB는 Chatbot의 RAG 소스로 사용된다.

### 4.2 Components
1. Mail Ingestor: 메일 본문 및 첨부 이미지 수집
2. Vision Parser: Vision LLM으로 이미지 해석
3. Preprocessor: 문장/불릿 단위 분리
4. Tech Classifier: JSON + LLM 기반 Tech 분류
5. Vector DB: embedding 저장 및 검색
6. Layer1 Generator: 전수 집계 테이블 생성
7. Summary Agent: 최근 3주 히스토리 기반 Layer2 요약
8. RAG Chatbot: 주간 메일 히스토리 질의 응답
9. Renderer: HTML 출력

---

## 5. Layer1 Specification

### 5.1 목적
- 모든 weekly 업무를 **누락 없이** 구조화한 전수 집계 로그
- DRAM/NAND + Tech 기준 Cross-team 현황 가시화

### 5.2 레이아웃
| Domain | Tech | Team A | Team B | Team C | ... |
|--------|------|--------|--------|--------|-----|
| DRAM   | 1a   | 내용   | 내용   | 내용   |     |
| DRAM   | 1b   | 내용   | 내용   | 내용   |     |
| NAND   | 312  | 내용   | 내용   | 내용   |     |

- 좌측: Domain(DRAM/NAND), Tech(공통/1a/1b/.../312/...)
- 우측: 팀 컬럼
- Cell: 해당 Tech에서 팀이 수행한 weekly 업무 요약 (bullet, 다중 줄 허용)

### 5.3 Row 관리
- Tech row는 JSON 파일로 사전 정의
- 키워드 매칭 + LLM 문맥 판단으로 분류
- 분류 불가 문장은 각 Domain 상단의 `공통` row로 배치
- 모든 row는 항상 출력

### 5.4 Cell 요약 원칙
- 수치, 이슈, Action 유지
- 불필요한 배경 제거
- Bullet 형태
- 길이 제한 없음

---

## 6. Layer2 Specification

### 6.1 목적
Layer1 전수 로그와 최근 3주 히스토리를 반영하여  
팀별 **스토리형 요약 리포트** 제공

### 6.2 출력 포맷

```
[Team] Weekly Summary

1. 주요 보고
- 최근 3주 맥락을 반영한 이번 주 핵심 스토리

2. Key Actions
1) DRAM-1b: open fail FA 및 TEM 분석
2) DRAM-1a: recipe 변경 적용 및 lot monitor
3) NAND-312: 수율 TF 운영

3. Issue / Risk
- DRAM-1b: 원인 미확정 → 수율 영향 지속
- NAND-312: target gap 해소 지연

4. 향후 계획
- DRAM-1b: FA 결과 반영 recipe 검증
- NAND-312: 추가 lot 적용 테스트
```

### 6.3 생성 로직
- 입력:
  - Layer1에서 해당 팀 column
  - Vector DB에서 최근 3주(team filter) retrieve 결과
- 요약 원칙:
  - 변화/진척/지속/종결 반영
  - Action에 Tech 명시
  - 반복/미해결 이슈는 Risk로 강조

---

## 7. Vision Model Usage

### 7.1 목적
- 이미지 속 텍스트, 표, 수치, 그래프 정보를 OCR이 아닌 Vision LLM으로 해석

### 7.2 Vision Prompt 원칙
- 이미지 안의 모든 텍스트/수치/표를 빠짐없이 추출
- 원문 형태 유지, 의미 단위 줄바꿈
- 보이지 않는 내용은 추측 금지
- 그래프/표는 "항목: 값" 형태로 풀어쓰기

### 7.3 활용
- Vision 결과는 본문 텍스트와 동일하게 처리
- Layer1/Layer2 및 Vector DB embedding 소스로 사용

---

## 8. Embedding & Vector DB

### 8.1 저장 대상
- 메일 본문 문장
- Vision 파싱 이미지 텍스트
- (선택) Layer2 요약

### 8.2 메타데이터
- team
- week (YYYY-WW)
- domain (DRAM/NAND/COMMON)
- tech (1a, 312, 공통 등)
- source (body/image)
- mail_id
- type (weekly_mail / summary)

### 8.3 활용
- Layer2 요약 시 최근 3주 히스토리 retrieve
- Chatbot RAG 검색 소스

---

## 9. RAG Chatbot Specification

### 9.1 목적
주간 메일 히스토리를 기반으로 한 대화형 Q&A 제공

### 9.2 예시 질문
- "최근 3주 DRAM-1b에서 어떤 이슈 있었어?"
- "FA팀이 지난달에 한 주요 분석은?"
- "312단 수율 개선 액션 히스토리 요약해줘"

### 9.3 Workflow
User Query → Intent/Filter Parsing → Vector DB Retrieve → Context 기반 Answer

### 9.4 Query Parsing
- team, domain, tech, 기간(week range)을 구조화 JSON으로 추출

### 9.5 Answer 원칙
- 제공된 context만 근거로 답변
- 근거 없으면 "정보 없음" 명시
- 추측/창작 금지

---

## 10. Prompt Design Principles

- Vision: 보이는 것만 추출, 추측 금지
- Layer1: JSON row 그대로 출력, 전수 포함
- Layer2: 최근 3주 맥락 기반 스토리 요약
- Chatbot: context 외 정보 사용 금지

---

## 11. Output Formats
- Markdown: 메일/노션 공유용
- HTML: 메일 표 가독성 확보용

---

## 12. Risks

- Vision 모델 hallucination 가능성
- 이미지 수치 오인식
- Layer1 테이블 비대화로 가독성 저하
- LLM 요약 품질 편차
- Vector DB 비용/성능

---

## 13. Milestones

1. Mail + Vision ingest PoC
2. Tech JSON 정의 및 분류 검증
3. Layer1 자동 생성
4. Embedding 파이프라인 구축
5. 최근 3주 기반 Layer2 요약 PoC
6. RAG Chatbot 구축
7. End-to-End 자동 주간 리포트

---

## 14. Success Criteria

- 본문/이미지 정보 누락 없이 Layer1 반영
- 팀/Tech 기준 현황 즉시 파악 가능
- Layer2 요약만으로 주간 스토리 이해 가능
- Chatbot이 히스토리 기반 질문에 근거 있는 답변 제공
- 실사용자가 "메일보다 훨씬 보기 좋고 찾기 쉽다"고 평가

---
