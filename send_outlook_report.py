"""
마지막 단계: Outlook COM으로 주간 리포트 HTML 메일 발송 (Windows 전용)

- generate_outlook_report.py 가 만든 Outlook 호환 HTML 파일을 본문으로 메일 작성
- pywin32(win32com) 를 통해 로컬에 설치/로그인된 Outlook 으로 발송
- 기본 동작은 안전하게 '초안 표시(Display)' — 사람이 확인 후 직접 [보내기] 클릭
- 완전 자동 발송은 --send 플래그로 명시 (스케줄러 자동화용)

사용 예:
    # 초안만 열기 (안전, 기본값)
    python send_outlook_report.py wiki/overview/2026-11_전체요약.html --to a@x.com

    # 자동 발송 (스케줄러)
    python send_outlook_report.py wiki/overview/2026-11_전체요약.html ^
        --to "boss@corp.com;team@corp.com" --cc "me@corp.com" --send

수신자/제목은 CLI 인자 또는 환경변수로 지정 가능:
    REPORT_TO, REPORT_CC, REPORT_SUBJECT
"""

from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _split_recipients(value: str | None) -> str:
    """콤마/세미콜론 혼용 → Outlook 표준 세미콜론 구분으로 정규화"""
    if not value:
        return ""
    parts = [p.strip() for p in value.replace(",", ";").split(";")]
    return "; ".join([p for p in parts if p])


def send_report(
    html_path: Path,
    to: str,
    cc: str = "",
    subject: str | None = None,
    auto_send: bool = False,
) -> None:
    try:
        import win32com.client as win32  # type: ignore
    except ImportError:
        print(
            "❌ pywin32 가 필요합니다. Windows 에서 'pip install pywin32' 후 다시 실행하세요.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not html_path.exists():
        print(f"❌ HTML 파일 없음: {html_path}", file=sys.stderr)
        raise SystemExit(1)

    to = _split_recipients(to)
    cc = _split_recipients(cc)
    if not to:
        print(
            "❌ 수신자가 비어 있습니다. --to 또는 REPORT_TO 환경변수를 지정하세요.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    html_body = html_path.read_text(encoding="utf-8")

    if not subject:
        # 파일명(예: 2026-11_전체요약) 을 제목으로 사용
        subject = f"[주간 리포트] {html_path.stem}"

    print(f"📧 Outlook 메일 작성 중...")
    print(f"   제목 : {subject}")
    print(f"   받는사람: {to}")
    if cc:
        print(f"   참조 : {cc}")

    outlook = win32.Dispatch("Outlook.Application")
    mail = outlook.CreateItem(0)  # 0 = olMailItem
    mail.To = to
    if cc:
        mail.CC = cc
    mail.Subject = subject
    mail.HTMLBody = html_body

    if auto_send:
        mail.Send()
        print("✅ 메일 발송 완료 (자동 발송)")
    else:
        mail.Display(False)
        print("✅ 메일 초안을 Outlook 에 열었습니다. 확인 후 [보내기] 를 누르세요.")
        print("   (자동 발송하려면 --send 플래그를 사용하세요)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Outlook COM 으로 주간 리포트 HTML 메일 발송 (Windows 전용)"
    )
    parser.add_argument("html", type=str, help="발송할 Outlook 호환 HTML 파일 경로")
    parser.add_argument(
        "--to",
        type=str,
        default=os.getenv("REPORT_TO", ""),
        help="받는 사람 (세미콜론/콤마 구분). 기본: 환경변수 REPORT_TO",
    )
    parser.add_argument(
        "--cc",
        type=str,
        default=os.getenv("REPORT_CC", ""),
        help="참조 (세미콜론/콤마 구분). 기본: 환경변수 REPORT_CC",
    )
    parser.add_argument(
        "--subject",
        type=str,
        default=os.getenv("REPORT_SUBJECT"),
        help="메일 제목. 기본: 환경변수 REPORT_SUBJECT 또는 파일명 기반",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="확인 없이 즉시 자동 발송 (스케줄러 자동화용). 미지정 시 초안만 표시",
    )

    args = parser.parse_args()

    send_report(
        html_path=Path(args.html),
        to=args.to,
        cc=args.cc,
        subject=args.subject,
        auto_send=args.send,
    )


if __name__ == "__main__":
    main()
