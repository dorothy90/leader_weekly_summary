"""주간 메일 리포트 파이프라인 오케스트레이터.

매주 화요일 오전 실행 (Windows Task Scheduler).
fetch → process → embed(+wiki_build) → wiki_export → outlook html → email 발송.

실행 주차 W = 오늘(화요일)의 ISO week. 월요일 도착한 주보(전주차 내용)는
수신일 기준 ISO week W로 태깅되므로 W를 그대로 리포트 라벨로 사용한다.
"""

import os
import sys
import time
from pathlib import Path
from datetime import date

# cron 등 임의 위치에서 실행돼도 상대경로(.env, data/, wiki/)가 동작하도록
# 스크립트 자신의 디렉터리로 이동
os.chdir(Path(__file__).resolve().parent)

import fetch_mail
import process_attachment
import process_vision
import embed_vectordb
import wiki_export
import generate_outlook_report
import send_report

OVERVIEW_DIR = Path("wiki") / "overview"


def wait_for_opensearch(retries: int = 5, delay: int = 5) -> None:
    """OpenSearch 연결 확인 (항상 가동 전제, 짧게 재시도)"""
    client = embed_vectordb.get_opensearch_client()
    for attempt in range(1, retries + 1):
        try:
            info = client.info()
            print(f"✅ OpenSearch 연결: {info['version']['number']}")
            return
        except Exception as e:
            print(f"⏳ OpenSearch 대기 {attempt}/{retries}: {e}")
            time.sleep(delay)
    raise RuntimeError("OpenSearch(localhost:9200) 연결 실패")


def main() -> int:
    week = fetch_mail.get_week_string(date.today())
    agenda_enabled = os.getenv("ENABLE_AGENDA_EXTRACTION", "false").lower() == "true"
    category_wiki_enabled = os.getenv("ENABLE_CATEGORY_WIKI", "false").lower() == "true"
    if (agenda_enabled or category_wiki_enabled) and os.getenv(
        "KNOWLEDGE_LLM_DATA_POLICY_ACK", "false"
    ).lower() != "true":
        raise RuntimeError(
            "KNOWLEDGE_LLM_DATA_POLICY_ACK=true 필요: 메일의 LLM 전송 정책을 확인하세요"
        )
    total_steps = 6 + int(agenda_enabled) + int(category_wiki_enabled)
    print("=" * 60)
    print(f"🚀 주간 리포트 파이프라인 시작 — 주차 {week}")
    print("=" * 60)

    # 0. OpenSearch 가동 확인
    wait_for_opensearch()

    # 1. fetch
    print(f"\n[1/{total_steps}] 메일 수집 (fetch)")
    fetch_mail.main()

    # 2. process (combined.txt 생성) — 해당 주차만
    print(f"\n[2/{total_steps}] 전처리 (process, week={week})")
    process_attachment.process_all(week=week)
    process_vision.process_all(week=week)

    step = 3
    if agenda_enabled:
        print(f"\n[{step}/{total_steps}] agenda 추출 (week={week})")
        import process_agendas

        agenda_stats = process_agendas.process_all(
            week=week,
            allow_external_llm=True,
            allow_dummy_taxonomy=False,
            opensearch_client=embed_vectordb.get_opensearch_client(),
        )
        if agenda_stats["failed"]:
            raise RuntimeError(f"agenda 추출 실패: {agenda_stats['failed']}건")
        step += 1

    # embed (+ wiki_build 자동) — 해당 주차만, 인덱스 보존(upsert)
    print(f"\n[{step}/{total_steps}] 임베딩 + wiki 생성 (embed, week={week})")
    embed_vectordb.process_all(recreate_index=False, week=week)
    step += 1

    if category_wiki_enabled:
        print(f"\n[{step}/{total_steps}] 통합 서술형 분류 Wiki 생성 (week={week})")
        import integrated_wiki_builder

        category_stats = integrated_wiki_builder.run(
            weeks=[week],
            allow_external_llm=True,
            allow_dummy_taxonomy=False,
            client=embed_vectordb.get_opensearch_client(),
        )
        if category_stats["failed"] > 0 or category_stats["pending"] > 0:
            raise RuntimeError(
                "통합 Wiki 생성 실패: "
                f"{category_stats['failed']}개 실패, "
                f"{category_stats['pending']}개 상위 문서 대기"
            )
        step += 1

    # wiki_export → overview md
    print(f"\n[{step}/{total_steps}] wiki export (week={week})")
    wiki_export.run_export(week=week, summary_type="overview")
    step += 1

    md_path = OVERVIEW_DIR / f"{week}_전체요약.md"
    if not md_path.exists():
        raise FileNotFoundError(f"overview md 없음: {md_path} (해당 주차 데이터 확인)")

    # outlook 호환 HTML 변환
    print(f"\n[{step}/{total_steps}] Outlook HTML 변환")
    html_path = md_path.with_suffix(".html")
    generate_outlook_report.convert(md_path, html_path)
    step += 1

    # Gmail SMTP 발송
    print(f"\n[{step}/{total_steps}] 메일 발송")
    send_report.send_report(html_path, week)

    print("\n" + "=" * 60)
    print(f"🎉 완료 — {html_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n❌ 파이프라인 실패: {type(e).__name__}: {e}")
        sys.exit(1)
