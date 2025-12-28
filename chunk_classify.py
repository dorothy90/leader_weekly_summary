"""
3단계: LLM으로 문장 분리 + Tech 분류
- combined.txt → 업무 단위 청크 분리
- 각 청크에 domain/tech 분류
- chunks.json 저장
"""

import os
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

# ========== 설정 ==========
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "your_api_key")
MODEL = "gpt-oss-120b"
DATA_DIR = Path("data")


# ========== Pydantic 스키마 ==========
class WorkChunk(BaseModel):
    """단일 업무 항목"""
    text: str = Field(description="업무 내용 (한 문장 또는 불릿)")
    domain: Literal["COMMON", "DRAM", "NAND"] = Field(
        description="도메인: COMMON(공통), DRAM, NAND"
    )
    tech: str = Field(
        description="세부 Tech: 공통, 1a, 1b, 1c, 1d, 256, 312, 400 등"
    )


class ChunkList(BaseModel):
    """분리된 업무 청크 리스트"""
    chunks: list[WorkChunk] = Field(
        description="분리된 업무 항목들"
    )


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
    # Structured output 적용
    return llm.with_structured_output(ChunkList)


# ========== 프롬프트 ==========
SYSTEM_PROMPT = """당신은 반도체 주간 업무 보고서 분석 전문가입니다.

주어진 텍스트를 **업무 단위**로 분리하고, 각각 domain과 tech를 분류해주세요.

## Domain 분류 기준
- COMMON: 도메인 무관 공통 업무 (스크립트 개발, 방법론 정리 등)
- DRAM: DRAM 관련 업무
- NAND: NAND 관련 업무

## Tech 분류 기준
- 공통: 해당 도메인 전반 또는 특정 Tech 미지정
- DRAM Tech: 1a, 1b, 1c, 1d
- NAND Tech: 256, 312, 400

## 분리 규칙
1. 하나의 업무/이슈/액션은 하나의 청크로
2. 수치, 결과, 액션은 반드시 포함
3. 불필요한 인사말, 배경 설명은 제외
4. 원문의 핵심 정보를 유지

## 예시
입력: "DRAM-1a recipe 변경으로 +1.2% 개선, 1b는 open fail FA 진행 중"
출력:
- text: "recipe 변경으로 +1.2% 개선", domain: "DRAM", tech: "1a"
- text: "open fail FA 진행 중", domain: "DRAM", tech: "1b"
"""


def classify_chunks(combined_text: str) -> ChunkList:
    """LLM으로 텍스트를 청크로 분리하고 분류"""
    llm = get_llm()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"다음 텍스트를 분석해주세요:\n\n{combined_text}"},
    ]

    result = llm.invoke(messages)
    return result


def process_mail_folder(mail_dir: Path) -> dict:
    """단일 메일 폴더 처리"""
    print(f"\n📂 처리 중: {mail_dir}")

    # combined.txt 읽기
    combined_path = mail_dir / "combined.txt"
    if not combined_path.exists():
        print(f"   ⚠️ combined.txt 없음 - 스킵")
        return None

    combined_text = combined_path.read_text(encoding="utf-8")
    if not combined_text.strip():
        print(f"   ⚠️ 빈 파일 - 스킵")
        return None

    print(f"   📄 입력: {len(combined_text)} chars")

    # LLM으로 분류
    try:
        result = classify_chunks(combined_text)
        chunks = [chunk.model_dump() for chunk in result.chunks]

        print(f"   ✅ {len(chunks)}개 청크 분류 완료")

        # chunks.json 저장
        chunks_path = mail_dir / "chunks.json"
        chunks_path.write_text(
            json.dumps(chunks, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"   💾 저장: chunks.json")

        # 분류 요약 출력
        domains = {}
        for chunk in chunks:
            key = f"{chunk['domain']}-{chunk['tech']}"
            domains[key] = domains.get(key, 0) + 1

        for key, count in sorted(domains.items()):
            print(f"      {key}: {count}개")

        return {
            "total_chunks": len(chunks),
            "domains": domains,
        }

    except Exception as e:
        print(f"   ❌ 분류 실패: {e}")
        return None


def process_all():
    """모든 메일 폴더 처리"""
    print("=" * 50)
    print("LLM 청크 분리 & Tech 분류")
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
        "total_chunks": 0,
    }

    for mail_dir in mail_folders:
        if not mail_dir.is_dir():
            continue

        result = process_mail_folder(mail_dir)

        if result:
            stats["processed"] += 1
            stats["total_chunks"] += result["total_chunks"]
        else:
            stats["skipped"] += 1

    # 결과 요약
    print("\n" + "=" * 50)
    print("✅ 청크 분류 완료")
    print("=" * 50)
    print(f"   처리됨: {stats['processed']}개")
    print(f"   스킵됨: {stats['skipped']}개")
    print(f"   총 청크: {stats['total_chunks']}개")


if __name__ == "__main__":
    process_all()

