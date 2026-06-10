"""주간 리포트 HTML을 Gmail SMTP로 발송.

generate_outlook_report.py가 만든 Outlook 변환 HTML
(wiki/overview/{week}_전체요약.html)을 메일 본문에 인라인 렌더링하여 보낸다.
"""

import sys
import smtplib
from pathlib import Path
from email.message import EmailMessage

from fetch_mail import EMAIL, PASSWORD  # Gmail 계정 + 앱 비밀번호 재사용

# 수신자 목록 (사용자가 채워야 함)
TO_EMAILS = ["dorothy90@yonsei.ac.kr"]


def send_report(html_path: Path, week: str) -> None:
    """HTML 파일을 읽어 Gmail SMTP로 본문 발송"""
    html_path = Path(html_path)
    html = html_path.read_text(encoding="utf-8")

    msg = EmailMessage()
    msg["Subject"] = f"[주간보고] {week} 통합 리포트"
    msg["From"] = EMAIL
    msg["To"] = ", ".join(TO_EMAILS)
    msg.set_content("HTML을 지원하는 메일 클라이언트에서 확인하세요.")
    msg.add_alternative(html, subtype="html")

    print(f"📤 메일 발송 중: {html_path} → {TO_EMAILS}")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL, PASSWORD)
        smtp.send_message(msg)
    print("✅ 발송 완료")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python send_report.py <html_path> [week]")
        sys.exit(2)

    path = Path(sys.argv[1])
    # week 미지정 시 파일명에서 추출 (예: 2026-11_전체요약.html → 2026-11)
    wk = sys.argv[2] if len(sys.argv) > 2 else path.stem.split("_")[0]
    send_report(path, wk)
