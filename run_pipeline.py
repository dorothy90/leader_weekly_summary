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
    print("=" * 60)
    print(f"🚀 주간 리포트 파이프라인 시작 — 주차 {week}")
    print("=" * 60)

    # 0. OpenSearch 가동 확인
    wait_for_opensearch()

    # 1. fetch
    print("\n[1/6] 메일 수집 (fetch)")
    fetch_mail.main()

    # 2. process (combined.txt 생성) — 해당 주차만
    print(f"\n[2/6] 전처리 (process, week={week})")
    process_attachment.process_all(week=week)
    process_vision.process_all(week=week)

    # 3. embed (+ wiki_build 자동) — 해당 주차만, 인덱스 보존(upsert)
    print(f"\n[3/6] 임베딩 + wiki 생성 (embed, week={week})")
    embed_vectordb.process_all(recreate_index=False, week=week)

    # 4. wiki_export → overview md
    print(f"\n[4/6] wiki export (week={week})")
    wiki_export.run_export(week=week, summary_type="overview")

    md_path = OVERVIEW_DIR / f"{week}_전체요약.md"
    if not md_path.exists():
        raise FileNotFoundError(f"overview md 없음: {md_path} (해당 주차 데이터 확인)")

    # 5. outlook 호환 HTML 변환
    print("\n[5/6] Outlook HTML 변환")
    html_path = md_path.with_suffix(".html")
    generate_outlook_report.convert(md_path, html_path)

    # 6. Gmail SMTP 발송
    print("\n[6/6] 메일 발송")
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
