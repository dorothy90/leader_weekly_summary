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
                    "FA팀": ["내용1", "내용2"],
                    "YIELD팀": ["내용3"],
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

        if text.strip():
            data[domain][tech][team].append(text)

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
    """Layer1 테이블을 Markdown으로 생성"""

    lines = []

    # 헤더
    title = f"# Layer1 전수 집계 테이블"
    if week:
        title += f" ({week})"
    lines.append(title)
    lines.append("")
    lines.append(f"생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 테이블 헤더
    header = "| Domain | Tech |"
    separator = "|--------|------|"
    for team in teams:
        header += f" {team} |"
        separator += "------|"

    lines.append(header)
    lines.append(separator)

    # 테이블 행
    all_techs = get_all_techs(data)

    for domain in DOMAIN_ORDER:
        techs = all_techs.get(domain, ["공통"])

        for tech in techs:
            row = f"| {domain} | {tech} |"

            for team in teams:
                cell_items = data.get(domain, {}).get(tech, {}).get(team, [])

                if cell_items:
                    # 불릿 형태로 합치기 (줄바꿈은 <br>로)
                    cell_text = "<br>".join([f"• {item}" for item in cell_items])
                else:
                    cell_text = "-"

                row += f" {cell_text} |"

            lines.append(row)

    return "\n".join(lines)


# ========== HTML 출력 ==========
def generate_html(
    data: Dict,
    teams: List[str],
    week: Optional[str] = None,
) -> str:
    """Layer1 테이블을 HTML로 생성"""

    title = "Layer1 전수 집계 테이블"
    if week:
        title += f" ({week})"

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif;
            margin: 20px;
            background: #f5f5f5;
        }}
        h1 {{
            color: #1a1a2e;
            margin-bottom: 10px;
        }}
        .meta {{
            color: #666;
            margin-bottom: 20px;
        }}
        .table-container {{
            overflow-x: auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            min-width: 800px;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 12px 8px;
            text-align: left;
            vertical-align: top;
        }}
        th {{
            background: #1a1a2e;
            color: white;
            font-weight: 600;
            position: sticky;
            top: 0;
        }}
        th.domain, th.tech {{
            background: #16213e;
        }}
        tr:nth-child(even) {{
            background: #f9f9f9;
        }}
        tr:hover {{
            background: #f0f0f0;
        }}
        td.domain {{
            font-weight: bold;
            background: #e8e8e8;
        }}
        td.tech {{
            font-weight: 500;
            background: #f0f0f0;
        }}
        .cell-content {{
            font-size: 13px;
            line-height: 1.5;
        }}
        .cell-content ul {{
            margin: 0;
            padding-left: 16px;
        }}
        .cell-content li {{
            margin-bottom: 4px;
        }}
        .empty {{
            color: #ccc;
            text-align: center;
        }}
        /* Domain 색상 */
        .domain-COMMON {{ background: #e3f2fd !important; }}
        .domain-DRAM {{ background: #e8f5e9 !important; }}
        .domain-NAND {{ background: #fff3e0 !important; }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <p class="meta">생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th class="domain">Domain</th>
                    <th class="tech">Tech</th>
"""

    # 팀 헤더
    for team in teams:
        html += f'                    <th>{team}</th>\n'

    html += """                </tr>
            </thead>
            <tbody>
"""

    # 테이블 행
    all_techs = get_all_techs(data)

    for domain in DOMAIN_ORDER:
        techs = all_techs.get(domain, ["공통"])

        for i, tech in enumerate(techs):
            html += f'                <tr>\n'

            # Domain 셀 (첫 번째 Tech일 때만 rowspan)
            if i == 0:
                rowspan = len(techs)
                html += f'                    <td class="domain domain-{domain}" rowspan="{rowspan}">{domain}</td>\n'

            # Tech 셀
            html += f'                    <td class="tech">{tech}</td>\n'

            # 팀별 셀
            for team in teams:
                cell_items = data.get(domain, {}).get(tech, {}).get(team, [])

                if cell_items:
                    html += '                    <td class="cell-content"><ul>\n'
                    for item in cell_items:
                        html += f'                        <li>{item}</li>\n'
                    html += '                    </ul></td>\n'
                else:
                    html += '                    <td class="empty">-</td>\n'

            html += '                </tr>\n'

    html += """            </tbody>
        </table>
    </div>
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

