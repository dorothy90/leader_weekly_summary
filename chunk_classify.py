"""
3단계: LLM으로 문장 분리 + Tech 분류
- combined.txt → 업무 단위 청크 분리
- 각 청크에 domain/tech 분류 (LLM)
- 각 청크에 team/week/mail_id/html_path 메타데이터 추가 (meta.json에서)
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

# Tech → Product 매핑
TECH_TO_PRODUCT = {
    # DRAM
    "1a": "12G LPDDR5",
    "1b": "16G DDR5",
    "1c": "12G LPDDR5X",
    "1d": "24G DDR5",
    # NAND
    "256": "512Gb TLC",
    "312": "1Tb QLC",
    "400": "2Tb QLC",
}

# Product → Tech 역매핑 (product만 언급될 때 사용)
PRODUCT_TO_TECH = {v: k for k, v in TECH_TO_PRODUCT.items()}

# Tech → Domain 매핑
TECH_TO_DOMAIN = {
    "1a": "DRAM",
    "1b": "DRAM",
    "1c": "DRAM",
    "1d": "DRAM",
    "256": "NAND",
    "312": "NAND",
    "400": "NAND",
}


# ========== Pydantic 스키마 ==========
class WorkChunk(BaseModel):
    """단일 업무 항목"""

    text: str = Field(description="업무 내용 (한 문장 또는 불릿)")
    domain: Literal["COMMON", "DRAM", "NAND", "WUXI"] = Field(
        description="도메인: COMMON(공통), DRAM, NAND, WUXI(우시 법인)"
    )
    tech: str = Field(description="세부 Tech: 공통, 1a, 1b, 1c, 1d, 256, 312, 400 등")
    product: str = Field(
        default="",
        description="제품명: 12G LPDDR5, 16G DDR5, 512Gb TLC 등 (없으면 빈 문자열)",
    )


class ChunkList(BaseModel):
    """분리된 업무 청크 리스트"""

    chunks: list[WorkChunk] = Field(description="분리된 업무 항목들")


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

주어진 텍스트를 **업무 단위**로 분리하고, 각각 domain, tech, product를 분류해주세요.

## Domain 분류 기준
- COMMON: 도메인 무관 공통 업무 (스크립트 개발, 방법론 정리 등)
- DRAM: DRAM 관련 업무 (Tech: 1a, 1b, 1c, 1d)
- NAND: NAND 관련 업무 (Tech: 256, 312, 400)
- WUXI: 우시 법인 관련 업무

## Tech ↔ Product 매핑 테이블

### DRAM
| Tech | Product |
|------|---------|
| 1a | 12G LPDDR5 |
| 1b | 16G DDR5 |
| 1c | 12G LPDDR5X |
| 1d | 24G DDR5 |

### NAND
| Tech | Product |
|------|---------|
| 256 | 512Gb TLC |
| 312 | 1Tb QLC |
| 400 | 2Tb QLC |

## ⭐ 분류 규칙 (중요!)

### Case 1: tech/product 둘 다 명시 안 됨
- tech = "공통"
- product = "" (빈 문자열)
- domain = 팀명이나 문맥으로 판단 (판단 불가 시 COMMON)

### Case 2: product만 명시됨 (예: "12G LPDDR5 수율 개선")
- 위 매핑 테이블에서 product → tech 찾기
- 예: "12G LPDDR5" → tech="1a", product="12G LPDDR5", domain="DRAM"

### Case 3: tech만 명시됨 (예: "1a 라인 이슈")
- tech = 명시된 값 (예: "1a")
- product = "" (빈 문자열로 남김)
- domain = 매핑 테이블 참조 (1a/1b/1c/1d → DRAM, 256/312/400 → NAND)

### Case 4: tech와 product 둘 다 명시됨
- 둘 다 그대로 사용

## 분리 규칙
1. 하나의 업무/이슈/액션은 하나의 청크로
2. 수치, 결과, 액션은 반드시 포함
3. 불필요한 인사말, 배경 설명은 제외
4. 원문의 핵심 정보를 유지

## 예시

입력: "주간 스크립트 개발 및 방법론 정리"
→ text: "주간 스크립트 개발 및 방법론 정리", domain: "COMMON", tech: "공통", product: ""

입력: "12G LPDDR5 수율 +1.2% 개선"
→ text: "수율 +1.2% 개선", domain: "DRAM", tech: "1a", product: "12G LPDDR5"

입력: "1a 라인 불량 모드 분석 진행"
→ text: "불량 모드 분석 진행", domain: "DRAM", tech: "1a", product: ""

입력: "312 read disturb 이슈, 1Tb QLC 제품 영향 분석"
→ text: "read disturb 이슈, 영향 분석", domain: "NAND", tech: "312", product: "1Tb QLC"
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

    # ========== meta.json에서 메타데이터 읽기 ==========
    meta_path = mail_dir / "meta.json"
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    team = meta.get("team", "unknown")
    week = meta.get("week", "unknown")
    mail_id = mail_dir.name  # "mail_001"
    html_path = str(mail_dir / "body.html")

    print(f"   📋 메타: team={team}, week={week}")

    # ========== combined.txt 읽기 ==========
    combined_path = mail_dir / "combined.txt"
    if not combined_path.exists():
        print(f"   ⚠️ combined.txt 없음 - 스킵")
        return None

    combined_text = combined_path.read_text(encoding="utf-8")
    if not combined_text.strip():
        print(f"   ⚠️ 빈 파일 - 스킵")
        return None

    print(f"   📄 입력: {len(combined_text)} chars")

    # ========== LLM으로 분류 ==========
    try:
        result = classify_chunks(combined_text)

        # 각 청크에 메타데이터 추가
        chunks = []
        for chunk in result.chunks:
            chunk_data = chunk.model_dump()
            chunk_data["team"] = team
            chunk_data["week"] = week
            chunk_data["mail_id"] = mail_id
            chunk_data["html_path"] = html_path

            # 팀명에 "우시"가 포함되어 있으면 domain을 "WUXI"로 설정
            if "우시" in team:
                chunk_data["domain"] = "WUXI"

            # product만 있고 tech가 "공통"인 경우 → 역매핑으로 tech 찾기
            if chunk_data.get("product") and chunk_data.get("tech") == "공통":
                found_tech = PRODUCT_TO_TECH.get(chunk_data["product"])
                if found_tech:
                    chunk_data["tech"] = found_tech
                    chunk_data["domain"] = TECH_TO_DOMAIN.get(
                        found_tech, chunk_data["domain"]
                    )

            chunks.append(chunk_data)

        print(f"   ✅ {len(chunks)}개 청크 분류 완료")

        # chunks.json 저장
        chunks_path = mail_dir / "chunks.json"
        chunks_path.write_text(
            json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"   💾 저장: chunks.json")

        # 분류 요약 출력
        domains = {}
        for chunk in chunks:
            product_str = f" ({chunk['product']})" if chunk.get("product") else ""
            key = f"{chunk['domain']}-{chunk['tech']}{product_str}"
            domains[key] = domains.get(key, 0) + 1

        for key, count in sorted(domains.items()):
            print(f"      {key}: {count}개")

        return {
            "total_chunks": len(chunks),
            "domains": domains,
            "team": team,
            "week": week,
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
