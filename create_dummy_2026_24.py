"""[Throwaway] 2026-24 주차 검증용 raw-mail 더미 생성/삭제 (신규 16팀).

team_dict.teams_by_group 의 16개 팀 각각에 대해
data/2026-24/{team}/mail_dummy/{combined.txt, meta.json} 를 생성한다.
→ embed → wiki_build(backfill) → wiki_export 까지 주간 파이프라인 검증용.

Usage:
    python create_dummy_2026_24.py            # 생성
    python create_dummy_2026_24.py --purge    # data/2026-24 더미 삭제
"""

import json
import shutil
import sys
from pathlib import Path

from team_dict import teams_by_group
from create_dummy_data import WEEKLY_REPORT_TEMPLATE

DATA_DIR = Path("data")
WEEK = "2026-24"
YEAR, WEEK_NUM = WEEK.split("-")

TEAMS = [t for members in teams_by_group.values() for t in members]


def generate():
    created = 0
    for team in TEAMS:
        mail_dir = DATA_DIR / WEEK / team / "mail_dummy"
        mail_dir.mkdir(parents=True, exist_ok=True)

        combined = WEEKLY_REPORT_TEMPLATE.format(
            team=team, week=WEEK, year=YEAR, week_num=WEEK_NUM
        )
        (mail_dir / "combined.txt").write_text(combined, encoding="utf-8")

        meta = {
            "team": team,
            "week": WEEK,
            "subject": f"[주간 업무 보고] {team} - {YEAR}년 {WEEK_NUM}주차",
            "mail_id": "mail_dummy",
            "mail_type": "weekly_report",
        }
        (mail_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        created += 1

    print(f"✅ 더미 생성: {created}개 ({len(TEAMS)}팀) → {DATA_DIR / WEEK}")


def purge():
    target = DATA_DIR / WEEK
    if target.exists():
        shutil.rmtree(target)
        print(f"🗑️ 삭제: {target}")
    else:
        print(f"⚠️ 없음: {target}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--purge":
        purge()
    else:
        generate()
