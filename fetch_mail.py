"""
1단계: EWS 메일 수집 & 로컬 저장
- 받는 사람에 특정 이메일이 포함된 메일 수집
- 주차별/팀별 폴더에 본문, 인라인 이미지, 첨부파일 저장
"""

import os
import re
import json
import base64
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote
from exchangelib import (
    Account,
    Credentials,
    Configuration,
    DELEGATE,
    EWSDateTime,
    EWSTimeZone,
    FileAttachment,
)

# ========== 설정 ==========
EWS_SERVER = "outlook.office365.com"  # ✅ 호스트명만 입력
EMAIL = "dorothy90@cau.ac.kr"
PASSWORD = "rlaeorka1!"
# EMAIL = os.getenv("EWS_EMAIL", "your_email@skhynix.com")
# PASSWORD = os.getenv("EWS_PASSWORD", "your_password")
TARGET_RECIPIENT = "2067627@skhynix.com"
DATA_DIR = Path("data")  # 저장 폴더
MAIL_DIR = Path("mail")  # body.html 모아두는 폴더 (RAG API 서빙용)


# %%
def generate_owa_url(item_id, ews_server=EWS_SERVER):
    """EWS item_id로 OWA(Outlook Web Access) URL 생성

    Args:
        item_id: EWS 메일 고유 식별자
        ews_server: EWS 서버 주소 (기본값: EWS_SERVER)

    Returns:
        OWA 웹메일 URL 문자열, item_id가 없으면 None
    """
    if not item_id:
        return None

    # OWA 서버 주소 (일반적으로 ews.도메인 → mail.도메인)
    owa_base = ews_server.replace("ews.", "mail.")
    encoded_id = quote(str(item_id), safe="")

    return f"https://{owa_base}/owa/?ItemID={encoded_id}&exvsurl=1&viewmodel=ReadMessageItem"


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


def classify_mail_type(subject: str) -> str:
    """메일 제목으로 유형 분류

    Args:
        subject: 메일 제목

    Returns:
        "weekly_report": 주간보고 메일
        "other": 그 외 메일
    """
    if not subject:
        return "other"

    subject_lower = subject.lower()

    # 주간보고 패턴
    weekly_patterns = [
        "주간보고", "주간 보고", "weekly", "주간업무", "주간 업무",
        "금주", "차주", "주간현황", "주간 현황", "weekly report",
        "주간실적", "주간 실적", "주보", "w/r", "wr"
    ]

    if any(p in subject_lower for p in weekly_patterns):
        return "weekly_report"

    return "other"


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


def save_mail(mail_data, week, team, mail_idx):
    """메일 데이터를 로컬에 저장"""
    # 폴더 생성: data/YYYY-WW/TEAM/mail_001/
    mail_dir = DATA_DIR / week / team / f"mail_{mail_idx:03d}"
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
    meta = {
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
        "local_path": str(mail_dir),  # fallback용 로컬 경로
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

    # 6. mail 폴더에 body.html 복사 (RAG API 서빙용)
    mail_id = f"mail_{mail_idx:03d}"
    copy_body_html_to_mail_dir(html_path, week, team, mail_id)

    print(f"   💾 저장: {mail_dir}")
    return mail_dir


def copy_body_html_to_mail_dir(source_html_path: Path, week: str, team: str, mail_id: str):
    """body.html을 mail 폴더에 복사 (RAG API 서빙용)

    Args:
        source_html_path: 원본 body.html 경로
        week: 주차 (예: 2025-48)
        team: 팀명 (예: FA팀)
        mail_id: 메일 ID (예: mail_001)

    파일명 형식: {week}_{team}_{mail_id}.html
    예: 2025-48_FA팀_mail_001.html
    """
    MAIL_DIR.mkdir(exist_ok=True)

    # 파일명: {week}_{team}_{mail_id}.html
    dest_filename = f"{week}_{team}_{mail_id}.html"
    dest_path = MAIL_DIR / dest_filename

    if source_html_path.exists():
        shutil.copy2(source_html_path, dest_path)
        print(f"   📄 복사: {dest_path}")


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
            recipients = [
                r.email_address for r in mail.to_recipients if r.email_address
            ]
        if mail.cc_recipients:
            recipients += [
                r.email_address for r in mail.cc_recipients if r.email_address
            ]

        if not any(TARGET_RECIPIENT in r for r in recipients):
            continue

        # 메일 정보 추출 (URL 참조용 식별자 포함)
        mail_data = {
            "subject": mail.subject,
            "sender": mail.sender.email_address if mail.sender else None,
            "received": mail.datetime_received,
            "body_text": "",
            "body_html": "",  # HTML 본문 추가
            "inline_images": [],
            "attachments": [],
            # URL 참조용 필드
            "item_id": str(mail.id) if mail.id else None,
            "message_id": mail.message_id,
            "conversation_id": (
                str(mail.conversation_id) if mail.conversation_id else None
            ),
            "owa_url": generate_owa_url(mail.id),
        }

        # 본문 텍스트 & HTML 추출
        if mail.text_body:
            mail_data["body_text"] = mail.text_body
        if mail.body:
            body_content = str(mail.body)
            mail_data["body_html"] = body_content
            # text_body가 없으면 HTML에서 가져옴
            if not mail_data["body_text"]:
                mail_data["body_text"] = body_content

        # 첨부파일 & 인라인 이미지
        for attachment in mail.attachments or []:
            if isinstance(attachment, FileAttachment):
                att_info = {
                    "name": attachment.name,
                    "content_type": attachment.content_type,
                    "size": len(attachment.content) if attachment.content else 0,
                    "is_inline": attachment.is_inline,
                    "content": attachment.content,  # 바이트 데이터
                    "content_id": attachment.content_id,  # CID 참조용
                }

                if attachment.is_inline:
                    mail_data["inline_images"].append(att_info)
                else:
                    mail_data["attachments"].append(att_info)

        # 주차, 팀 추출
        week = get_week_string(mail.datetime_received)
        team = detect_team(mail_data["subject"], mail_data["sender"] or "")

        results.append(
            {
                "week": week,
                "team": team,
                "data": mail_data,
            }
        )
        print(
            f"📧 [{week}/{team}] {mail_data['subject'][:40]}... (인라인: {len(mail_data['inline_images'])}, 첨부: {len(mail_data['attachments'])})"
        )

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


def sync_existing_body_html():
    """기존 data 폴더의 body.html을 mail 폴더로 복사

    이미 수집된 메일들을 mail 폴더로 동기화할 때 사용
    """
    print("=" * 50)
    print("📂 기존 body.html 파일을 mail 폴더로 동기화")
    print("=" * 50)

    MAIL_DIR.mkdir(exist_ok=True)
    copied_count = 0

    # data/{week}/{team}/mail_xxx/body.html 순회
    for week_dir in DATA_DIR.iterdir():
        if not week_dir.is_dir():
            continue
        week = week_dir.name

        for team_dir in week_dir.iterdir():
            if not team_dir.is_dir():
                continue
            team = team_dir.name

            for mail_dir in team_dir.iterdir():
                if not mail_dir.is_dir():
                    continue
                mail_id = mail_dir.name

                body_html = mail_dir / "body.html"
                if body_html.exists():
                    dest_filename = f"{week}_{team}_{mail_id}.html"
                    dest_path = MAIL_DIR / dest_filename
                    shutil.copy2(body_html, dest_path)
                    copied_count += 1
                    print(f"   ✅ {dest_filename}")

    print("=" * 50)
    print(f"✅ 총 {copied_count}개 파일 복사 완료")
    print(f"📁 저장 위치: {MAIL_DIR.absolute()}")
    print("=" * 50)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--sync":
        # 기존 파일 동기화 모드
        sync_existing_body_html()
    else:
        # 기본 메일 수집 모드
        main()
