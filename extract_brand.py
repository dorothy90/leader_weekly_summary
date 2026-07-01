"""사내 표준 .pptx → themes/exec.json 브랜드 토큰 추출.

pptxgenjs는 기존 .pptx를 베이스로 열 수 없으므로, 사내 마스터의 색/폰트/로고를
토큰으로 뽑아 renderer(defineSlideMaster 대체)가 재현하게 한다.

사용법:
    python extract_brand.py "/path/사내표준.pptx" [--logo]

theme1.xml 의 <a:clrScheme>, <a:fontScheme> 를 파싱하고,
--logo 지정 시 ppt/media 의 첫 이미지 하나를 assets/brand_logo.png 로 저장한다.
추출이 애매하면 기존 값을 유지한다(파괴적 아님).
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
THEME_OUT = ROOT / "themes" / "exec.json"
LOGO_OUT = ROOT / "assets" / "brand_logo.png"

_NS = re.compile(r"\{[^}]+\}")


def _clr(el) -> str | None:
    """<a:srgbClr val=".."> 또는 <a:sysClr lastClr=".."> → 6자리 hex(대문자, # 없음)."""
    for child in el:
        tag = _NS.sub("", child.tag)
        if tag == "srgbClr" and child.get("val"):
            return child.get("val").upper()
        if tag == "sysClr" and child.get("lastClr"):
            return child.get("lastClr").upper()
    return None


def extract(pptx_path: str, save_logo: bool = False) -> dict:
    import defusedxml.ElementTree as ET  # 네임스페이스 안전 파싱

    with zipfile.ZipFile(pptx_path) as z:
        theme_names = [n for n in z.namelist() if re.match(r"ppt/theme/theme\d+\.xml$", n)]
        if not theme_names:
            raise RuntimeError("theme1.xml 을 찾을 수 없습니다")
        root = ET.fromstring(z.read(sorted(theme_names)[0]))

        scheme = {}
        for el in root.iter():
            if _NS.sub("", el.tag) == "clrScheme":
                for slot in el:
                    name = _NS.sub("", slot.tag)  # dk1, lt1, accent1...
                    c = _clr(slot)
                    if c:
                        scheme[name] = c
                break

        fonts = {}
        for el in root.iter():
            if _NS.sub("", el.tag) == "fontScheme":
                for grp in el:
                    gname = _NS.sub("", grp.tag)  # majorFont / minorFont
                    for f in grp:
                        if _NS.sub("", f.tag) in ("latin", "ea") and f.get("typeface"):
                            fonts[gname] = f.get("typeface")
                            break
                break

        logo_path = None
        if save_logo:
            media = [n for n in z.namelist() if re.match(r"ppt/media/.*\.(png|jpg|jpeg|emf)$", n, re.I)]
            if media:
                LOGO_OUT.parent.mkdir(parents=True, exist_ok=True)
                LOGO_OUT.write_bytes(z.read(sorted(media)[0]))
                logo_path = str(LOGO_OUT)

    # 기존 토큰 로드 후 발견된 값만 덮어쓰기(비파괴)
    tokens = json.loads(THEME_OUT.read_text(encoding="utf-8")) if THEME_OUT.exists() else {"brand": {}, "font": {}}
    b = tokens.setdefault("brand", {})
    # dk2(본문 강조 다크) 우선, 없으면 accent1 → primary
    if scheme.get("dk2"):
        b["primary"] = scheme["dk2"]
    elif scheme.get("accent1"):
        b["primary"] = scheme["accent1"]
    if b.get("primary"):
        b.setdefault("primary_dark", b["primary"])
    if scheme.get("accent2"):
        b["accent"] = scheme["accent2"]
    elif scheme.get("accent1") and scheme.get("accent1") != b.get("primary"):
        b["accent"] = scheme["accent1"]

    f = tokens.setdefault("font", {})
    if fonts.get("majorFont"):
        f["title"] = fonts["majorFont"]
    if fonts.get("minorFont"):
        f["body"] = fonts["minorFont"]
    if logo_path:
        tokens["logo"] = logo_path

    THEME_OUT.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"scheme": scheme, "fonts": fonts, "logo": logo_path, "written": str(THEME_OUT)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pptx", help="사내 표준 .pptx 경로")
    ap.add_argument("--logo", action="store_true", help="ppt/media 첫 이미지를 로고로 저장")
    args = ap.parse_args()
    result = extract(args.pptx, save_logo=args.logo)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
