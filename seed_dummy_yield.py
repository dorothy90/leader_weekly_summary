"""더미 수율 주보를 weekly_mail 인덱스에 임베딩·색인 (데모용).

여러 팀·주차에 걸친 일관된 스토리(HBM ECC fail 발생→원인규명→D0 개선→회복)를 심어
deep_mining 파이프라인이 실제 metric/trend/timeline을 추출하도록 한다.

사용법:
    python seed_dummy_yield.py          # 색인
    python seed_dummy_yield.py --delete # 더미 문서 삭제(mail_id prefix 'dummy_')
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
for line in (REPO / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k, v.strip().strip('"').strip("'"))

import embed_vectordb as ev
from opensearchpy import helpers

# (team, week, subject, text)
DOCS = [
    # ---- HBM수율: ECC fail 발생 → 원인 → D0 개선 → 회복 스토리 ----
    ("HBM수율", "2026-30", "HBM3E 주간 수율 현황",
     "이번 주 HBM3E 최종 수율은 94.2%로 전주 대비 0.6%p 하락했다. ECC fail 발생 건수가 월 18건으로 소폭 증가했으며, "
     "웨이퍼 엣지부 결함이 주요 의심 원인으로 파악된다. TSV 접합 공정은 안정적이나 엣지 트리밍 조건의 산포가 확대되는 경향이 관찰되었다."),
    ("HBM수율", "2026-31", "HBM3E ECC fail 원인 분석",
     "HBM3E 수율은 93.9%로 추가 하락했고 ECC fail은 24건으로 증가했다. 분석 결과 엣지 트리밍 조건 이탈이 근본 원인으로 규명되었으며, "
     "결함밀도(D0)가 전주 대비 12% 상승했다. 긴급 대응 TF를 구성해 D0 개선안을 수립 중이다."),
    ("HBM수율", "2026-32", "HBM3E D0 개선안 적용",
     "HBM3E 수율은 93.8%로 저점을 기록했다. D0 개선안(엣지 트리밍 조건 재설정 및 세정 강화) 적용을 착수했으며, "
     "TSV 접합부 재점검을 병행했다. 개선 효과는 차주부터 가시화될 전망이다. ECC fail은 21건으로 소폭 감소했다."),
    ("HBM수율", "2026-33", "HBM3E 수율 회복 전환",
     "D0 개선안 효과로 HBM3E 수율이 95.6%로 반등했다. ECC fail은 15건으로 감소했고 결함밀도도 정상 범위로 복귀했다. "
     "엣지부 결함 비중이 크게 줄어 개선안의 유효성이 확인되었다."),
    ("HBM수율", "2026-34", "HBM3E 수율 안정화",
     "HBM3E 수율은 97.1%로 개선세를 이어갔다. ECC fail 9건으로 안정화 추세이며, 로트간 편차도 축소되었다. "
     "평균 이슈 대응 리드타임은 3주로 단축되었다."),
    ("HBM수율", "2026-35", "HBM3E 양산 전환 준비",
     "HBM3E 최종 수율이 98.3%에 도달했다. ECC fail은 5건으로 최저 수준이며, 개선안을 전 라인으로 확대 적용한다. "
     "양산 전환을 위한 최종 품질 검증에 착수했다."),
    # ---- DRAM수율전략: DDR5 ----
    ("DRAM수율전략", "2026-30", "DDR5 주간 수율",
     "DDR5 수율은 96.1%로 안정적이다. 신규 셀 설계 검증이 진행 중이며 평균 리드타임은 4.2주다. 리프레시 특성 마진 확보가 과제로 남아 있다."),
    ("DRAM수율전략", "2026-32", "DDR5 셀 설계 검증",
     "DDR5 수율이 96.8%로 소폭 개선됐다. 신규 셀 설계 검증이 완료 단계이며, 리텐션 특성이 목표를 상회했다."),
    ("DRAM수율전략", "2026-34", "DDR5 리드타임 단축",
     "DDR5 수율 97.5% 달성. 공정 단순화로 평균 리드타임이 3.6주로 단축되었다. 양산 안정성 지표가 개선되었다."),
    # ---- NAND수율전략: V-NAND 적층 ----
    ("NAND수율전략", "2026-31", "236단 V-NAND 수율",
     "236단 V-NAND 수율은 91.5%다. 채널 홀 식각 편차가 상하단 간에 관찰되어 원인 분석 중이다."),
    ("NAND수율전략", "2026-33", "V-NAND 식각 최적화",
     "식각 조건 최적화로 V-NAND 수율이 92.9%로 개선됐다. 채널 홀 CD 산포가 축소되었다."),
    ("NAND수율전략", "2026-35", "V-NAND 편차 축소",
     "V-NAND 수율 94.4% 달성. 상하단 식각 편차가 목표 범위 내로 축소되어 안정화되었다."),
    # ---- Spica수율: 신제품 양산성 ----
    ("Spica수율", "2026-30", "Spica 양산성 점검",
     "Spica 신제품 초기 수율은 88.7%로 낮은 편이다. 양산성 점검을 시작했으며 로트간 편차가 큰 상태다."),
    ("Spica수율", "2026-32", "Spica 로트 편차 모니터링",
     "Spica 수율이 90.3%로 개선됐다. 로트간 편차 상시 모니터링 체계를 도입했고 주요 결함 모드를 분류했다."),
    ("Spica수율", "2026-34", "Spica 수율전략 재수립",
     "Spica 수율 92.1% 달성. 수율전략을 재수립하고 병목 공정을 식별했다. 양산 전환 로드맵을 갱신했다."),
    # ---- 공정기술PTE: 파라미터/장비 ----
    ("공정기술PTE", "2026-31", "장비 파라미터 이슈",
     "일부 장비에서 RF TIME 불량이 발생해 파라미터 재튜닝을 실시했다. 재발 방지를 위한 조건 표준화를 검토 중이다."),
    ("공정기술PTE", "2026-33", "엣지 트리밍 조건 확정",
     "HBM 엣지 트리밍 조건을 확정하고 재발 방지 절차를 수립했다. 관련 공정의 산포가 안정화되었다."),
    ("공정기술PTE", "2026-35", "파라미터 표준화 완료",
     "장비 파라미터 표준화를 완료하고 전 라인으로 확대 적용했다. 공정 편차가 유의미하게 감소했다."),
]

DUMMY_PREFIX = "dummy_"


def delete_dummy(client):
    q = {"query": {"prefix": {"mail_id": DUMMY_PREFIX}}}
    r = client.delete_by_query(index=ev.INDEX_NAME, body=q, refresh=True)
    print(f"🗑️ 삭제된 더미 문서: {r.get('deleted')}건")


def seed(client, emb_client):
    actions = []
    for team, week, subject, text in DOCS:
        mail_id = f"{DUMMY_PREFIX}{team}_{week}"
        emb = ev.get_embedding(text, emb_client)
        actions.append({
            "_index": ev.INDEX_NAME,
            "_id": f"{mail_id}_part_0",
            "_source": {
                "text": text, "embedding": emb, "type": "original_part",
                "team": team, "week": week, "mail_id": mail_id,
                "html_path": "", "part_index": 0, "total_parts": 1,
                "subject": subject, "mail_type": "weekly_report",
            },
        })
        print(f"  embed {team}/{week}")
    helpers.bulk(client, actions)
    client.indices.refresh(index=ev.INDEX_NAME)
    print(f"✅ 색인 완료: {len(actions)}건 (weeks 2026-30~35)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    client = ev.get_opensearch_client()
    if args.delete:
        delete_dummy(client)
        return
    seed(client, ev.get_embedding_client())


if __name__ == "__main__":
    main()
