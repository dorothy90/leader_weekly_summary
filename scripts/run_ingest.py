#!/usr/bin/env python3
"""
Run the mail ingestion pipeline.

Usage:
    python scripts/run_ingest.py --subject "주간" --days 7
    python scripts/run_ingest.py --test  # Run with mock data
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.graph.ingest_graph import MailIngestPipeline, MockMailIngestPipeline
from src.schemas import MailContent, ImageAttachment, ProcessedMail

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_mock_data() -> list[MailContent]:
    """Create mock mail data for testing."""
    return [
        MailContent(
            mail_id="mock-001",
            subject="[Yield] 주간 업무 보고",
            sender="yield_team@company.com",
            team="Yield",
            received_date=datetime.now(),
            week=f"{datetime.now().year}-{datetime.now().isocalendar()[1]:02d}",
            body_text="""
            안녕하세요, Yield팀 주간 보고입니다.

            1. DRAM-1a 수율 현황
            - recipe 변경 적용 완료 → +1.2% 개선
            - lot 3ea 추가 모니터 진행 중

            2. DRAM-1b 이슈
            - open fail 발생, 영향 평가 진행 중
            - 원인 분석 FA팀 협업

            3. NAND-312 현황
            - 수율 TF 운영 중
            - target gap 분석 완료

            다음 주 계획:
            - 1a lot 결과 분석
            - 1b FA 결과 반영 검토
            """,
            images=[],
            parsed_images=[],
        ),
        MailContent(
            mail_id="mock-002",
            subject="[FA] Weekly Report",
            sender="fa_team@company.com",
            team="FA",
            received_date=datetime.now(),
            week=f"{datetime.now().year}-{datetime.now().isocalendar()[1]:02d}",
            body_text="""
            FA팀 주간 보고

            • DRAM-1b TEM 분석 진행
              - 원인 가설 3가지 정리
              - 추가 샘플 확보 필요

            • NAND-312 defect FA
              - 주요 defect 유형 분류 완료
              - root cause 분석 중

            • 공통 분석 방법론 정리
              - 신규 분석 기법 documentation
            """,
            images=[],
            parsed_images=[],
        ),
    ]


def print_results(processed_mails: list[ProcessedMail]) -> None:
    """Print processing results."""
    print("\n" + "=" * 60)
    print("📬 Mail Ingestion Results")
    print("=" * 60)

    for mail in processed_mails:
        print(f"\n📧 Mail: {mail.mail_id[:30]}...")
        print(f"   Team: {mail.team}")
        print(f"   Week: {mail.week}")
        print(f"   Sentences: {len(mail.sentences)}")

        if mail.sentences:
            print("   Sample sentences:")
            for sentence in mail.sentences[:3]:
                print(f"     [{sentence.source.value}] {sentence.text[:60]}...")

    print("\n" + "=" * 60)
    total_sentences = sum(len(m.sentences) for m in processed_mails)
    print(f"Total: {len(processed_mails)} mails, {total_sentences} sentences")
    print("=" * 60)


def save_results(processed_mails: list[ProcessedMail], output_path: str) -> None:
    """Save results to JSON file."""
    output = []
    for mail in processed_mails:
        output.append({
            "mail_id": mail.mail_id,
            "team": mail.team,
            "week": mail.week,
            "received_date": mail.received_date.isoformat(),
            "sentence_count": len(mail.sentences),
            "sentences": [
                {
                    "text": s.text,
                    "source": s.source.value,
                    "source_filename": s.source_filename,
                }
                for s in mail.sentences
            ],
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"Results saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run mail ingestion pipeline")
    parser.add_argument(
        "--subject",
        type=str,
        default="주간",
        help="Subject filter string",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Days to look back",
    )
    parser.add_argument(
        "--folder",
        type=str,
        default="inbox",
        help="Mail folder to search",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output JSON file path",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run with mock data (no EWS connection)",
    )

    args = parser.parse_args()

    if args.test:
        logger.info("Running with mock data...")
        mock_pipeline = MockMailIngestPipeline()
        mock_data = create_mock_data()
        processed_mails = mock_pipeline.run_with_mock_data(mock_data)
    else:
        logger.info("Running live pipeline...")
        pipeline = MailIngestPipeline()
        result = pipeline.run(
            subject_filter=args.subject,
            days_back=args.days,
            folder_name=args.folder,
        )
        processed_mails = result.get("processed_mails", [])

        if result.get("errors"):
            logger.warning(f"Errors occurred: {result['errors']}")

    # Print results
    print_results(processed_mails)

    # Save if output path provided
    if args.output:
        save_results(processed_mails, args.output)


if __name__ == "__main__":
    main()

