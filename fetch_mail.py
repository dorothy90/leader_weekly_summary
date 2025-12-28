"""
1단계: EWS 메일 수집 & 로컬 저장
- 받는 사람에 특정 이메일이 포함된 메일 수집
- 주차별/팀별 폴더에 본문, 인라인 이미지, 첨부파일 저장
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from exchangelib import (
    Account, Credentials, Configuration, DELEGATE,
    EWSDateTime, EWSTimeZone, FileAttachment
)

# ========== 설정 ==========
EWS_SERVER = "ews.skhynix.com"
EMAIL = os.getenv("EWS_EMAIL", "your_email@skhynix.com")
PASSWORD = os.getenv("EWS_PASSWORD", "your_password")
TARGET_RECIPIENT = "2067627@skhynix.com"
DATA_DIR = Path("data")  # 저장 폴더


def connect_ews():
    """EWS 연결"""
    credentials = Credentials(username=EMAIL, password=PASSWORD)
    config = Configuration(server=EWS_SERVER, credentials=credentials)
    account = Account(
        primary_smtp_address=EMAIL,
        config=config,
        autodiscover=False,
        access_type=DELEGATE,
    )
    print(f"✅ EWS 연결 성공: {EMAIL}")
    return account


def get_week_string(dt):
    """날짜 → 주차 문자열 (YYYY-WW)"""
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


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
    match = re.search(r'\[(\w+)\]', subject)
    if match:
        return match.group(1)

    return "UNKNOWN"


def save_mail(mail_data, week, team, mail_idx):
    """메일 데이터를 로컬에 저장"""
    # 폴더 생성: data/YYYY-WW/TEAM/mail_001/
    mail_dir = DATA_DIR / week / team / f"mail_{mail_idx:03d}"
    mail_dir.mkdir(parents=True, exist_ok=True)

    # 1. 본문 저장
    body_path = mail_dir / "body.txt"
    body_path.write_text(mail_data["body_text"], encoding="utf-8")

    # 2. 메타데이터 저장
    meta = {
        "subject": mail_data["subject"],
        "sender": mail_data["sender"],
        "received": mail_data["received"].isoformat() if mail_data["received"] else None,
        "week": week,
        "team": team,
        "inline_images": [],
        "attachments": [],
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
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"   💾 저장: {mail_dir}")
    return mail_dir


def fetch_mails(account, days_back=7):
    """메일 가져오기"""
    tz = EWSTimeZone.localzone()
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)

    ews_start = EWSDateTime.from_datetime(start_date.replace(tzinfo=tz))
    ews_end = EWSDateTime.from_datetime(end_date.replace(tzinfo=tz))

    # 받은 편지함에서 날짜 범위로 필터링
    mails = account.inbox.filter(datetime_received__range=(ews_start, ews_end))

    results = []
    for mail in mails:
        # 받는 사람 중 TARGET_RECIPIENT 포함 여부 확인
        recipients = []
        if mail.to_recipients:
            recipients = [r.email_address for r in mail.to_recipients if r.email_address]
        if mail.cc_recipients:
            recipients += [r.email_address for r in mail.cc_recipients if r.email_address]

        if not any(TARGET_RECIPIENT in r for r in recipients):
            continue

        # 메일 정보 추출
        mail_data = {
            "subject": mail.subject,
            "sender": mail.sender.email_address if mail.sender else None,
            "received": mail.datetime_received,
            "body_text": "",
            "inline_images": [],
            "attachments": [],
        }

        # 본문 텍스트
        if mail.text_body:
            mail_data["body_text"] = mail.text_body
        elif mail.body:
            mail_data["body_text"] = str(mail.body)

        # 첨부파일 & 인라인 이미지
        for attachment in mail.attachments or []:
            if isinstance(attachment, FileAttachment):
                att_info = {
                    "name": attachment.name,
                    "content_type": attachment.content_type,
                    "size": len(attachment.content) if attachment.content else 0,
                    "is_inline": attachment.is_inline,
                    "content": attachment.content,  # 바이트 데이터
                }

                if attachment.is_inline:
                    mail_data["inline_images"].append(att_info)
                else:
                    mail_data["attachments"].append(att_info)

        # 주차, 팀 추출
        week = get_week_string(mail.datetime_received)
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
    print("EWS 메일 수집 & 로컬 저장")
    print("=" * 50)

    # 연결
    account = connect_ews()

    # 메일 수집 (최근 7일)
    mails = fetch_mails(account, days_back=7)

    print("\n" + "=" * 50)
    print("💾 로컬에 저장 중...")
    print("=" * 50)

    # 주차/팀별로 인덱스 관리
    mail_counters = {}  # (week, team) -> count

    for mail in mails:
        week = mail["week"]
        team = mail["team"]
        key = (week, team)

        mail_counters[key] = mail_counters.get(key, 0) + 1
        idx = mail_counters[key]

        save_mail(mail["data"], week, team, idx)

    print("\n" + "=" * 50)
    print(f"✅ 총 {len(mails)}개 메일 저장 완료")
    print(f"📁 저장 위치: {DATA_DIR.absolute()}")
    print("=" * 50)

    # 요약 출력
    print("\n📊 수집 요약:")
    for (week, team), count in sorted(mail_counters.items()):
        print(f"   {week} / {team}: {count}개")


if __name__ == "__main__":
    main()

