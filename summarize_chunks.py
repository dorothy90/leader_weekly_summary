"""
4단계: 청크 요약
- chunks.json → text 필드 요약
- chunks_summary.json 저장 (원본 스키마 유지)
"""

import os
import json
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

# ========== 설정 ==========
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "your_api_key")
MODEL = "gpt-oss-120b"
DATA_DIR = Path("data")


# ========== Pydantic 스키마 ==========
class SummaryItem(BaseModel):
    """요약된 업무 항목"""

    original_index: int = Field(description="원본 청크의 인덱스")
    summary: str = Field(description="요약된 업무 내용 (20자 이내)")


class SummaryList(BaseModel):
    """요약된 업무 리스트"""

    summaries: List[SummaryItem] = Field(description="요약된 업무 항목들")


# ========== LLM 설정 ==========
def get_llm():
    """OpenRouter LLM with structured output"""
    llm = ChatOpenAI(
        model=MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
        default_headers={
            "HTTP-Referer": "https://weekly-mail-agent.local",
            "X-Title": "Weekly Mail Agent",
        },
    )
    return llm.with_structured_output(SummaryList)


# ========== 프롬프트 ==========
SYSTEM_PROMPT = """당신은 반도체 주간 업무 보고서 요약 전문가입니다.

주어진 업무 내용들을 각각 **20자 이내**로 간결하게 요약해주세요.

## 요약 규칙
1. 핵심 키워드와 수치만 유지
2. 불필요한 조사, 접속사 제거
3. 동사는 명사형으로 변환 (개선했다 → 개선)
4. 제품명/Tech는 생략 가능 (이미 메타데이터에 있음)

## 예시

입력: "12G LPDDR5 제품의 수율이 1.2% 개선되었으며, Flash 공정 최적화를 통해 달성"
→ 요약: "수율 +1.2% 개선"

입력: "1a 라인에서 발생한 불량 모드에 대한 분석을 진행하였음"
→ 요약: "불량 모드 분석 진행"

입력: "주간 스크립트 개발 및 방법론 정리 작업 완료"
→ 요약: "스크립트 개발, 방법론 정리"

입력: "read disturb 이슈로 인한 영향도 분석 진행 중"
→ 요약: "read disturb 영향 분석"
"""


def summarize_chunks(chunks: List[dict]) -> List[dict]:
    """청크 리스트의 text를 요약"""
    if not chunks:
        return []

    llm = get_llm()

    # 청크 텍스트를 인덱스와 함께 전달
    chunk_texts = []
    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "")
        if text.strip():
            chunk_texts.append(f"[{i}] {text}")

    if not chunk_texts:
        return chunks

    user_prompt = "다음 업무 내용들을 각각 요약해주세요:\n\n" + "\n".join(chunk_texts)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result = llm.invoke(messages)

    # 요약 결과를 인덱스별로 매핑
    summary_map = {item.original_index: item.summary for item in result.summaries}

    # 원본 청크 복사 후 text 교체
    summarized_chunks = []
    for i, chunk in enumerate(chunks):
        new_chunk = chunk.copy()
        if i in summary_map:
            new_chunk["text"] = summary_map[i]
        summarized_chunks.append(new_chunk)

    return summarized_chunks


def process_mail_folder(mail_dir: Path) -> dict:
    """단일 메일 폴더 처리"""
    print(f"\n📂 처리 중: {mail_dir}")

    # ========== chunks.json 읽기 ==========
    chunks_path = mail_dir / "chunks.json"
    if not chunks_path.exists():
        print(f"   ⚠️ chunks.json 없음 - 스킵")
        return None

    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    if not chunks:
        print(f"   ⚠️ 빈 파일 - 스킵")
        return None

    print(f"   📄 입력: {len(chunks)}개 청크")

    # ========== 이미 요약됐는지 확인 ==========
    summary_path = mail_dir / "chunks_summary.json"
    if summary_path.exists():
        print(f"   ⏭️ 이미 요약됨 - 스킵")
        return {"skipped_existing": True}

    # ========== LLM으로 요약 ==========
    try:
        summarized = summarize_chunks(chunks)

        # chunks_summary.json 저장
        summary_path.write_text(
            json.dumps(summarized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"   ✅ 요약 완료: {len(summarized)}개 청크")
        print(f"   💾 저장: chunks_summary.json")

        # 샘플 출력
        if summarized:
            sample = summarized[0]
            print(f"   📝 샘플: {sample.get('text', '')[:50]}...")

        return {"total_chunks": len(summarized)}

    except Exception as e:
        print(f"   ❌ 요약 실패: {e}")
        return None


def process_all(force: bool = False):
    """모든 메일 폴더 처리

    Args:
        force: True면 기존 chunks_summary.json 덮어쓰기
    """
    print("=" * 50)
    print("청크 요약 (chunks.json → chunks_summary.json)")
    print("=" * 50)

    if not DATA_DIR.exists():
        print(f"❌ data 폴더가 없습니다: {DATA_DIR}")
        return

    # 모든 mail_* 폴더 찾기
    mail_folders = list(DATA_DIR.glob("**/mail_*"))
    print(f"📁 발견된 메일 폴더: {len(mail_folders)}개")

    stats = {
        "processed": 0,
        "skipped": 0,
        "skipped_existing": 0,
        "total_chunks": 0,
    }

    for mail_dir in mail_folders:
        if not mail_dir.is_dir():
            continue

        # force 옵션이면 기존 파일 삭제
        if force:
            summary_path = mail_dir / "chunks_summary.json"
            if summary_path.exists():
                summary_path.unlink()

        result = process_mail_folder(mail_dir)

        if result:
            if result.get("skipped_existing"):
                stats["skipped_existing"] += 1
            else:
                stats["processed"] += 1
                stats["total_chunks"] += result.get("total_chunks", 0)
        else:
            stats["skipped"] += 1

    # 결과 요약
    print("\n" + "=" * 50)
    print("✅ 청크 요약 완료")
    print("=" * 50)
    print(f"   처리됨: {stats['processed']}개")
    print(f"   이미 요약됨: {stats['skipped_existing']}개")
    print(f"   스킵됨: {stats['skipped']}개")
    print(f"   총 청크: {stats['total_chunks']}개")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="청크 요약")
    parser.add_argument(
        "--force", action="store_true", help="기존 chunks_summary.json 덮어쓰기"
    )

    args = parser.parse_args()

    process_all(force=args.force)



