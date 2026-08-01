"""
1단계: EWS 메일 수집 & 로컬 저장
- 받는 사람에 특정 이메일이 포함된 메일 수집
- 주차별/팀별 폴더에 본문, 인라인 이미지, 첨부파일 저장
"""

import os
import re
import json
import base64
import hashlib
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
from imap_tools import MailBox, AND

from app.content.mail import (
    mail_content_id,
    mail_content_locator,
    owner_storage_key,
)


# ========== 설정 ==========
IMAP_SERVER = "imap.gmail.com"
EMAIL = os.getenv("MAIL_EMAIL", "")
PASSWORD = os.getenv("MAIL_PASSWORD", "")

DATA_DIR = Path("data")  # 저장 폴더
MAIL_DIR = Path("mail")  # Deprecated static-serving directory; no new writes.


# %%
def generate_gmail_url(thread_id):
    """Gmail Thread ID로 접근 가능한 웹메일 URL 생성"""
    if not thread_id:
        return None
    return f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"

def connect_imap():
    """Gmail IMAP 연결"""
    if not EMAIL or not PASSWORD:
        raise RuntimeError("MAIL_EMAIL and MAIL_PASSWORD are required")
    print(f"🔄 IMAP 서버({IMAP_SERVER}) 연결 중...")
    try:
        mailbox = MailBox(IMAP_SERVER).login(EMAIL, PASSWORD)
        print(f"✅ IMAP 연결 성공: {EMAIL}")
        return mailbox
    except Exception as e:
        print(f"❌ 로그인 실패: {type(e).__name__}")
        raise


def get_week_string(dt):
    """날짜 → 주차 문자열 (YYYY-WW)"""
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def classify_mail_type(subject: str) -> str:
    """메일 제목으로 유형 분류 (binary)

    Args:
        subject: 메일 제목

    Returns:
        "weekly_report": 주간보고 메일
        "daily_report": 주간보고 외 모든 메일 (일일보고 포함)
    """
    if not subject:
        return "daily_report"

    subject_lower = subject.lower()

    # 주간보고 패턴
    weekly_patterns = [
        "주간보고",
        "주간 보고",
        "weekly",
        "주간업무",
        "주간 업무",
        "금주",
        "차주",
        "주간현황",
        "주간 현황",
        "weekly report",
        "주간실적",
        "주간 실적",
        "주보",
        "w/r",
        "wr",
    ]

    if any(p in subject_lower for p in weekly_patterns):
        return "weekly_report"

    # 주간보고 외 모든 메일은 daily_report
    return "daily_report"


def detect_team(subject, sender):
    """제목/발신자에서 팀 이름 추출"""
    text = f"{subject} {sender}".lower()

    # 팀 키워드 매핑 (필요시 추가)
    teams = {
        "yield": ["yield", "수율"],
        "fa": ["fa", "분석", "failure"],
        "met": ["met", "계측", "metrology"],
        "etch": ["etch", "식각"],
        "diff": ["diff", "확산"],
        "equip": ["equip", "장비"],
    }

    for team, keywords in teams.items():
        if any(kw in text for kw in keywords):
            return team.upper()

    # [팀명] 패턴 추출
    match = re.search(r"\[(\w+)\]", subject)
    if match:
        return match.group(1)

    return "UNKNOWN"


def get_unique_mail_id(mail_data: dict) -> str:
    """메일의 고유 식별자 생성

    형식: mail_MMDD_HHMMSS_xxxxxxxx
    - 날짜시간: 가독성 (언제 온 메일인지 알 수 있음)
    - 해시 8자리: 고유성 보장 (같은 메일 중복 방지)

    Args:
        mail_data: 메일 데이터 딕셔너리

    Returns:
        고유한 mail_id 문자열 (예: mail_0125_143022_a1b2c3d4)
    """
    # 수신 시간
    received = mail_data.get("received")
    if received:
        time_part = received.strftime("%m%d_%H%M%S")
    else:
        time_part = datetime.now().strftime("%m%d_%H%M%S")

    # message_id 기반 해시 (중복 방지)
    message_id = mail_data.get("message_id", "")
    if message_id:
        short_hash = hashlib.md5(message_id.encode()).hexdigest()[:8]
    else:
        # fallback: 제목+발신자+시간 조합
        unique_str = (
            f"{mail_data.get('subject', '')}{mail_data.get('sender', '')}{time_part}"
        )
        short_hash = hashlib.md5(unique_str.encode()).hexdigest()[:8]

    return f"mail_{time_part}_{short_hash}"


def require_mail_user_id() -> str:
    """Return the explicitly configured document owner or fail closed."""
    user_id = os.getenv("MAIL_USER_ID", "").strip()
    if not user_id:
        raise RuntimeError("MAIL_USER_ID is required before collecting mail")
    return user_id


def save_mail(mail_data, week, team, mail_id: str, user_id: str):
    """메일 데이터를 로컬에 저장

    Args:
        mail_data: 메일 데이터 딕셔너리
        week: 주차 (예: 2025-48)
        team: 팀명 (예: FA팀)
        mail_id: 고유 메일 ID (예: mail_0125_143022_a1b2c3d4)
        user_id: 요청/수집 설정에서 명시된 소유자
    """
    owner = str(user_id or "").strip()
    if not owner:
        raise ValueError("user_id is required when saving mail")
    # 폴더 생성: data/YYYY-WW/TEAM/mail_0125_143022_a1b2c3d4/
    owner_key = owner_storage_key(owner)
    mail_dir = DATA_DIR / owner_key / week / team / mail_id
    mail_dir.mkdir(parents=True, exist_ok=True)

    # 1. 텍스트 본문 저장
    body_path = mail_dir / "body.txt"
    body_path.write_text(mail_data["body_text"], encoding="utf-8")

    # 2. HTML 본문 저장 (인라인 이미지 Base64 변환 포함)
    if mail_data.get("body_html"):
        html_content = mail_data["body_html"]

        # 인라인 이미지 CID를 Base64 Data URI로 변환
        for img in mail_data["inline_images"]:
            content_id = img.get("content_id", "")
            if content_id:
                cid = content_id.strip("<>")
                if img["content"] and cid:
                    b64 = base64.b64encode(img["content"]).decode()
                    content_type = img.get("content_type", "image/png")
                    data_uri = f"data:{content_type};base64,{b64}"
                    # cid:xxx 형식을 data URI로 교체
                    html_content = html_content.replace(f"cid:{cid}", data_uri)

        # charset을 UTF-8로 변경 (인코딩 깨짐 방지)
        html_content = re.sub(
            r"charset=(euc-kr|cp949|ks_c_5601-1987|iso-8859-1)",
            "charset=utf-8",
            html_content,
            flags=re.IGNORECASE,
        )

        html_path = mail_dir / "body.html"
        html_path.write_text(html_content, encoding="utf-8")
    else:
        # HTML이 없으면 텍스트를 HTML로 변환 (fallback)
        fallback_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{mail_data.get('subject', 'Email')}</title></head>
<body><pre>{mail_data['body_text']}</pre></body></html>"""
        html_path = mail_dir / "body.html"
        html_path.write_text(fallback_html, encoding="utf-8")

    # 3. 메타데이터 저장 (URL 참조용 필드 포함)
    mail_type = classify_mail_type(mail_data.get("subject", ""))
    content_id = mail_content_id(owner, mail_id)
    meta = {
        "user_id": owner,
        "mail_id": mail_id,
        "content_id": content_id,
        "document_locator": mail_content_locator(content_id),
        "subject": mail_data["subject"],
        "sender": mail_data["sender"],
        "received": (
            mail_data["received"].isoformat() if mail_data["received"] else None
        ),
        "week": week,
        "team": team,
        "mail_type": mail_type,  # 메일 유형 (weekly_report / other)
        "inline_images": [],
        "attachments": [],
        # URL 참조용 필드 (RAG 출처 제공용)
        "item_id": mail_data.get("item_id"),
        "message_id": mail_data.get("message_id"),
        "conversation_id": mail_data.get("conversation_id"),
        "owa_url": mail_data.get("owa_url"),
    }

    # 3. 인라인 이미지 저장
    for i, img in enumerate(mail_data["inline_images"], 1):
        filename = f"inline_{i:03d}_{img['name']}"
        filepath = mail_dir / filename
        if img["content"]:
            filepath.write_bytes(img["content"])
            meta["inline_images"].append(filename)

    # 4. 첨부파일 저장
    for i, att in enumerate(mail_data["attachments"], 1):
        filename = f"attach_{i:03d}_{att['name']}"
        filepath = mail_dir / filename
        if att["content"]:
            filepath.write_bytes(att["content"])
            meta["attachments"].append(filename)

    # 5. 메타데이터 JSON 저장
    meta_path = mail_dir / "meta.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"   💾 저장 완료: owner={owner_key}, mail={mail_id}")
    return mail_dir


def copy_body_html_to_mail_dir(
    source_html_path: Path, week: str, team: str, mail_id: str, user_id: str
):
    """Deprecated: static copies bypass the owner-validated content boundary.

    Args:
        source_html_path: 원본 body.html 경로
        week: 주차 (예: 2025-48)
        team: 팀명 (예: FA팀)
        mail_id: 메일 ID (예: mail_001)

    파일명 형식: {week}_{team}_{mail_id}.html
    예: 2025-48_FA팀_mail_001.html
    """
    raise RuntimeError(
        "static mail copies are disabled; use /v1/mail-content/{content_id}"
    )


def fetch_mails(mailbox, days_back=1):
    """IMAP을 통해 메일 가져오기"""
    import datetime
    
    end_date = datetime.date.today() + datetime.timedelta(days=1)
    start_date = end_date - datetime.timedelta(days=days_back)
    
    results = []
    
    # 받은 편지함에서 날짜 조건으로 메일 가져오기
    for msg in mailbox.fetch(AND(date_gte=start_date, date_lt=end_date)):
        thread_id = msg.headers.get("x-gm-thrid", [""])[0]

        mail_data = {
            "subject": msg.subject,
            "sender": msg.from_,
            "received": msg.date,
            "body_text": msg.text or "",
            "body_html": msg.html or "",
            "inline_images": [],
            "attachments": [],
            # URL 참조용 필드
            "item_id": msg.uid,
            "message_id": msg.headers.get("message-id", [""])[0],
            "conversation_id": thread_id,
            "owa_url": generate_gmail_url(thread_id),
        }

        # text_body가 없으면 HTML에서 가져옴
        if not mail_data["body_text"] and mail_data["body_html"]:
            mail_data["body_text"] = mail_data["body_html"]

        # 첨부파일 및 인라인 이미지 분리
        for att in msg.attachments:
            att_info = {
                "name": att.filename or "unnamed_attachment",
                "content_type": att.content_type,
                "size": len(att.payload),
                "is_inline": bool(att.content_id),
                "content": att.payload,
                "content_id": att.content_id,
            }

            # content_id가 존재하면 인라인 이미지로 간주
            if att_info["is_inline"]:
                mail_data["inline_images"].append(att_info)
            else:
                mail_data["attachments"].append(att_info)

        week = get_week_string(mail_data["received"])
        team = detect_team(mail_data["subject"], mail_data["sender"] or "")

        results.append({
            "week": week,
            "team": team,
            "data": mail_data,
        })
        print(f"📧 [{week}/{team}] {mail_data['subject'][:40]}... (인라인: {len(mail_data['inline_images'])}, 첨부: {len(mail_data['attachments'])})")

    return results


def main():
    print("=" * 50)
    print("Gmail IMAP 메일 수집 & 로컬 저장")
    print("=" * 50)

    mail_user_id = require_mail_user_id()

    # 연결
    mailbox = connect_imap()

    # 메일 수집 (최근 1일)
    mails = fetch_mails(mailbox, days_back=3)

    print("\n" + "=" * 50)
    print("💾 로컬에 저장 중...")
    print("=" * 50)

    # 저장된 메일 통계
    saved_mails = []  # (week, team, mail_id) 튜플 리스트

    for mail in mails:
        week = mail["week"]
        team = mail["team"]
        mail_data = mail["data"]

        # 고유한 mail_id 생성 (날짜시간 + 해시)
        mail_id = get_unique_mail_id(mail_data)

        save_mail(mail_data, week, team, mail_id, mail_user_id)
        saved_mails.append((week, team, mail_id))

    print("\n" + "=" * 50)
    print(f"✅ 총 {len(mails)}개 메일 저장 완료")
    print("📁 저장 위치: configured data directory")
    print("=" * 50)

    # 요약 출력 (주차/팀별 카운트)
    from collections import Counter

    mail_counters = Counter((week, team) for week, team, _ in saved_mails)
    print("\n📊 수집 요약:")
    for (week, team), count in sorted(mail_counters.items()):
        print(f"   {week} / {team}: {count}개")


def sync_existing_body_html(user_id: str):
    """Deprecated: static synchronization is intentionally disabled."""
    raise RuntimeError(
        "static mail synchronization is disabled; use the content API"
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--sync":
        # 기존 파일 동기화 모드
        sync_existing_body_html(require_mail_user_id())
    else:
        # 기본 메일 수집 모드
        main()
