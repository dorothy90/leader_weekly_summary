"""
6단계: Layer1 전수 집계 테이블 생성
- 모든 chunks.json → Domain × Tech × Team 테이블
- HTML 및 Markdown 출력
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict
from datetime import datetime

# ========== 설정 ==========
DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")

# Tech 정의 (순서대로 출력)
TECH_ORDER = {
    "DRAM": ["공통", "1a", "1b", "1c", "1d"],
    "NAND": ["공통", "256", "312", "400"],
    "COMMON": ["공통"],
}

# 도메인 순서
DOMAIN_ORDER = ["COMMON", "DRAM", "NAND"]

# Tech → Product 매핑
TECH_TO_PRODUCT = {
    # DRAM
    "1a": "12G LPDDR5",
    "1b": "16G DDR5",
    "1c": "12G LPDDR5X",
    "1d": "24G DDR5",
    # NAND
    "256": "512Gb TLC",
    "312": "1Tb QLC",
    "400": "2Tb QLC",
}


# ========== 데이터 로드 ==========
def load_all_chunks(week: Optional[str] = None) -> List[Dict]:
    """모든 chunks.json 파일에서 청크 로드

    Args:
        week: 특정 주만 필터 (예: "2025-48"), None이면 전체
    """
    all_chunks = []

    if not DATA_DIR.exists():
        print(f"❌ data 폴더가 없습니다: {DATA_DIR}")
        return all_chunks

    # 모든 chunks.json 찾기
    chunks_files = list(DATA_DIR.glob("**/chunks.json"))
    print(f"📁 발견된 chunks.json: {len(chunks_files)}개")

    for chunks_path in chunks_files:
        # 주차 필터링
        if week:
            # 경로에서 주차 추출 (data/2025-48/팀/mail_001/chunks.json)
            path_parts = chunks_path.parts
            if week not in path_parts:
                continue

        try:
            chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            all_chunks.extend(chunks)
            print(f"   ✅ {chunks_path}: {len(chunks)}개 청크")
        except Exception as e:
            print(f"   ❌ {chunks_path}: {e}")

    return all_chunks


def get_teams_from_chunks(chunks: List[Dict]) -> List[str]:
    """청크에서 팀 목록 추출 (알파벳순 정렬)"""
    teams = set()
    for chunk in chunks:
        team = chunk.get("team", "unknown")
        if team != "unknown":
            teams.add(team)
    return sorted(list(teams))


# ========== 테이블 생성 ==========
def build_layer1_data(chunks: List[Dict]) -> Dict:
    """청크를 Domain × Tech × Team 구조로 그룹핑

    Returns:
        {
            "DRAM": {
                "1a": {
                    "FA팀": [{"text": "내용1", "product": "12G LPDDR5"}, ...],
                    "YIELD팀": [{"text": "내용3", "product": ""}],
                },
                ...
            },
            ...
        }
    """
    # 중첩 defaultdict
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    for chunk in chunks:
        domain = chunk.get("domain", "COMMON")
        tech = chunk.get("tech", "공통")
        team = chunk.get("team", "unknown")
        text = chunk.get("text", "")
        product = chunk.get("product", "")

        if text.strip():
            data[domain][tech][team].append({"text": text, "product": product})

    return data


def get_all_techs(data: Dict) -> Dict[str, List[str]]:
    """실제 데이터에서 사용된 Tech 목록 추출 (정의된 순서 + 추가 Tech)"""
    result = {}

    for domain in DOMAIN_ORDER:
        # 정의된 Tech 순서
        defined_techs = TECH_ORDER.get(domain, ["공통"])

        # 실제 데이터에서 사용된 Tech
        actual_techs = set(data.get(domain, {}).keys())

        # 정의된 순서대로 + 추가 Tech
        ordered_techs = []
        for tech in defined_techs:
            ordered_techs.append(tech)

        # 정의되지 않은 Tech 추가
        for tech in sorted(actual_techs):
            if tech not in ordered_techs:
                ordered_techs.append(tech)

        result[domain] = ordered_techs

    return result


# ========== Markdown 출력 ==========
def generate_markdown(
    data: Dict,
    teams: List[str],
    week: Optional[str] = None,
) -> str:
    """Layer1 테이블을 Markdown으로 생성 (세로형)"""

    lines = []

    # 헤더
    title = f"# Layer1 전수 집계 테이블"
    if week:
        title += f" ({week})"
    lines.append(title)
    lines.append("")
    lines.append(f"생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 테이블 헤더 (세로형: Domain, Tech, Product, 팀, 업무 내용)
    lines.append("| Domain | Tech | Product | 팀 | 업무 내용 |")
    lines.append("|--------|------|---------|-----|-----------|")

    # 테이블 행 (내용이 있는 항목만)
    all_techs = get_all_techs(data)

    for domain in DOMAIN_ORDER:
        techs = all_techs.get(domain, ["공통"])

        for tech in techs:
            for team in teams:
                cell_items = data.get(domain, {}).get(tech, {}).get(team, [])

                if cell_items:
                    for item in cell_items:
                        item_text = item.get("text", "")
                        item_product = item.get("product", "")
                        row = f"| {domain} | {tech} | {item_product} | {team} | {item_text} |"
                        lines.append(row)

    return "\n".join(lines)


# ========== HTML 출력 ==========
def generate_html(
    data: Dict,
    teams: List[str],
    week: Optional[str] = None,
) -> str:
    """Layer1 테이블을 HTML로 생성 (Outlook 이메일 호환 - Domain/Tech별 분리)"""

    title = "Layer1 전수 집계 테이블"
    if week:
        title += f" ({week})"

    # Domain별 색상 정의
    domain_colors = {
        "COMMON": "#6fa8dc",  # 연한 파랑
        "DRAM": "#81c784",  # 연한 초록
        "NAND": "#ffb74d",  # 연한 주황
    }

    all_techs = get_all_techs(data)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
</head>
<body style="margin:0; padding:20px; font-family:'Malgun Gothic','맑은 고딕',sans-serif; background:#ffffff;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:800px;">
        <tr>
            <td style="padding-bottom:20px;">
                <h1 style="color:#1a1a2e; font-size:22px; margin:0 0 8px 0;">{title}</h1>
                <p style="color:#666; font-size:12px; margin:0;">생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </td>
        </tr>
"""

    # Domain-Tech별 테이블 생성
    for domain in DOMAIN_ORDER:
        techs = all_techs.get(domain, ["공통"])
        header_color = domain_colors.get(domain, "#666")

        for tech in techs:
            # 해당 Domain-Tech에 데이터가 있는지 확인
            has_data = False
            for team in teams:
                if data.get(domain, {}).get(tech, {}).get(team, []):
                    has_data = True
                    break

            if not has_data:
                continue

            # 섹션 제목: COMMON/공통, DRAM/1a 등 (product 제외)
            section_title = f"{domain} / {tech}"

            html += f"""
        <tr>
            <td style="padding-bottom:20px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                        <td style="background:{header_color}; color:white; padding:10px 14px; font-size:14px; font-weight:bold;">
                            {section_title}
                        </td>
                    </tr>
                    <tr>
                        <td>
                            <table width="100%" cellpadding="0" cellspacing="0" border="1" style="border-collapse:collapse; border-color:#ddd;">
                                <tr style="background:#f5f5f5;">
                                    <th style="padding:8px 10px; text-align:left; font-size:12px; width:100px; border:1px solid #ddd;">Product</th>
                                    <th style="padding:8px 10px; text-align:left; font-size:12px; width:80px; border:1px solid #ddd;">팀</th>
                                    <th style="padding:8px 10px; text-align:left; font-size:12px; border:1px solid #ddd;">업무 내용</th>
                                </tr>
"""

            # 모든 아이템을 flat하게 수집 후 product, team별로 그룹핑
            all_items = []
            for team in teams:
                cell_items = data.get(domain, {}).get(tech, {}).get(team, [])
                for item in cell_items:
                    all_items.append(
                        {
                            "team": team,
                            "text": item.get("text", ""),
                            "product": item.get("product", ""),
                        }
                    )

            # product, team 순으로 정렬 (빈 문자열은 마지막으로)
            all_items.sort(key=lambda x: (x["product"] == "", x["product"], x["team"]))

            # product별 rowspan 계산
            product_counts = {}
            for item in all_items:
                p = item["product"]
                product_counts[p] = product_counts.get(p, 0) + 1

            # (product, team) 조합별 rowspan 계산
            product_team_counts = {}
            for item in all_items:
                key = (item["product"], item["team"])
                product_team_counts[key] = product_team_counts.get(key, 0) + 1

            # 이미 출력한 product, (product, team) 추적
            rendered_products = set()
            rendered_product_teams = set()

            for item in all_items:
                item_product = item["product"]
                item_team = item["team"]
                item_text = item["text"]
                pt_key = (item_product, item_team)

                is_new_product = item_product not in rendered_products
                is_new_team = pt_key not in rendered_product_teams

                product_rowspan = product_counts[item_product]
                team_rowspan = product_team_counts[pt_key]

                if is_new_product:
                    rendered_products.add(item_product)
                if is_new_team:
                    rendered_product_teams.add(pt_key)

                # 행 생성
                html += "                                <tr>\n"

                # Product 셀 (새 product일 때만)
                if is_new_product:
                    if product_rowspan > 1:
                        html += f'                                    <td rowspan="{product_rowspan}" style="padding:8px 10px; font-size:12px; color:#333; border:1px solid #ddd; background:#fafafa; vertical-align:top;">{item_product}</td>\n'
                    else:
                        html += f'                                    <td style="padding:8px 10px; font-size:12px; color:#333; border:1px solid #ddd; background:#fafafa;">{item_product}</td>\n'

                # Team 셀 (새 (product, team)일 때만)
                if is_new_team:
                    if team_rowspan > 1:
                        html += f'                                    <td rowspan="{team_rowspan}" style="padding:8px 10px; font-size:12px; color:#333; border:1px solid #ddd; background:#fafafa; vertical-align:top;">{item_team}</td>\n'
                    else:
                        html += f'                                    <td style="padding:8px 10px; font-size:12px; color:#333; border:1px solid #ddd; background:#fafafa;">{item_team}</td>\n'

                # Text 셀 (항상)
                html += f'                                    <td style="padding:8px 10px; font-size:12px; color:#333; line-height:1.5; border:1px solid #ddd;">{item_text}</td>\n'
                html += "                                </tr>\n"

            html += """                            </table>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
"""

    html += """    </table>
</body>
</html>
"""

    return html


# ========== 메인 ==========
def generate_layer1(
    week: Optional[str] = None,
    output_format: str = "both",  # "html", "md", "both"
) -> Dict:
    """Layer1 테이블 생성

    Args:
        week: 특정 주만 필터 (예: "2025-48")
        output_format: 출력 형식

    Returns:
        생성된 파일 경로들
    """
    print("=" * 50)
    print("Layer1 전수 집계 테이블 생성")
    print("=" * 50)

    # 1. 청크 로드
    chunks = load_all_chunks(week)
    if not chunks:
        print("❌ 청크 데이터가 없습니다.")
        return {}

    print(f"\n📊 총 {len(chunks)}개 청크 로드됨")

    # 2. 팀 목록 추출
    teams = get_teams_from_chunks(chunks)
    print(f"👥 팀 목록: {teams}")

    # 3. 테이블 데이터 구성
    data = build_layer1_data(chunks)

    # 4. 출력 폴더 생성
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 5. 파일 생성
    result = {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    week_suffix = f"_{week}" if week else ""

    if output_format in ["md", "both"]:
        md_content = generate_markdown(data, teams, week)
        md_path = OUTPUT_DIR / f"layer1{week_suffix}_{timestamp}.md"
        md_path.write_text(md_content, encoding="utf-8")
        print(f"\n📄 Markdown 저장: {md_path}")
        result["markdown"] = str(md_path)

    if output_format in ["html", "both"]:
        html_content = generate_html(data, teams, week)
        html_path = OUTPUT_DIR / f"layer1{week_suffix}_{timestamp}.html"
        html_path.write_text(html_content, encoding="utf-8")
        print(f"📄 HTML 저장: {html_path}")
        result["html"] = str(html_path)

    # 통계
    print("\n" + "=" * 50)
    print("✅ Layer1 생성 완료")
    print("=" * 50)

    # 도메인별 통계
    for domain in DOMAIN_ORDER:
        domain_chunks = [c for c in chunks if c.get("domain") == domain]
        print(f"   {domain}: {len(domain_chunks)}개 청크")

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Layer1 전수 집계 테이블 생성")
    parser.add_argument("--week", type=str, help="특정 주 필터 (예: 2025-48)")
    parser.add_argument(
        "--format",
        type=str,
        choices=["html", "md", "both"],
        default="both",
        help="출력 형식 (기본: both)",
    )

    args = parser.parse_args()

    generate_layer1(week=args.week, output_format=args.format)
