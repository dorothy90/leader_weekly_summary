"""슬라이드 스펙 → pptxgenjs 렌더 + 시각 QA(이미지 변환) 헬퍼.

deep_mining.build_pptx / visual_qa 노드에서 사용.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
RENDER_JS = ROOT / "ppt_render" / "render_deck.js"
THEME_JSON = ROOT / "themes" / "exec.json"


def _node() -> Optional[str]:
    return shutil.which("node")


def render_deck(spec: dict, out_path: str, theme: Optional[str] = None, timeout: int = 120) -> str:
    """스펙 dict → editable .pptx (pptxgenjs). 실패 시 예외."""
    node = _node()
    if not node:
        raise RuntimeError("node 실행파일을 찾을 수 없습니다 (pptxgenjs 렌더 불가)")
    if not RENDER_JS.exists():
        raise RuntimeError(f"렌더러 없음: {RENDER_JS}")

    theme_path = theme or os.getenv("DEEP_MINING_PPT_THEME") or str(THEME_JSON)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
        spec_path = f.name
    try:
        proc = subprocess.run(
            [node, str(RENDER_JS), spec_path, out_path, theme_path],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0 or not Path(out_path).exists():
            raise RuntimeError(f"render_deck.js 실패: {proc.stderr.strip() or proc.stdout.strip()}")
        logger.info(f"[deck_render] {proc.stdout.strip()}")
        return out_path
    finally:
        try:
            os.unlink(spec_path)
        except OSError:
            pass


def _soffice() -> Optional[str]:
    for c in ("soffice", "libreoffice"):
        p = shutil.which(c)
        if p:
            return p
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    return mac if Path(mac).exists() else None


def pptx_to_images(pptx_path: str, out_dir: str, dpi: int = 110, timeout: int = 120) -> List[str]:
    """.pptx → 슬라이드별 jpg (soffice→pdf→pdftoppm). 도구 없으면 빈 리스트(비차단)."""
    soffice, pdftoppm = _soffice(), shutil.which("pdftoppm")
    if not soffice or not pdftoppm:
        logger.warning("[deck_render] soffice/pdftoppm 없음 — 시각 QA 이미지 생략")
        return []
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out), pptx_path],
                       capture_output=True, text=True, timeout=timeout, check=True)
        pdf = out / (Path(pptx_path).stem + ".pdf")
        if not pdf.exists():
            return []
        subprocess.run([pdftoppm, "-jpeg", "-r", str(dpi), str(pdf), str(out / "slide")],
                       capture_output=True, text=True, timeout=timeout, check=True)
        return sorted(str(p) for p in out.glob("slide-*.jpg"))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"[deck_render] 이미지 변환 실패: {e}")
        return []


_QA_CHECK = (
    "다음은 임원 보고용 슬라이드 이미지들입니다. 각 슬라이드에서 user-visible 결함만 찾으세요: "
    "요소 겹침, 텍스트가 박스/화면 밖으로 넘침(overflow)·잘림, 한글이 네모(tofu)로 깨짐, "
    "저대비(밝은 배경에 밝은 글씨), 표/차트 잘림. 사소한 취향은 무시.\n"
    '출력은 JSON 하나만: {"slides":[{"index":1,"ok":true,"issue":""}, ...]}. index는 1부터.'
)


def visual_inspect(images: List[str], model: Optional[str] = None, timeout: int = 90) -> List[dict]:
    """비전 LLM으로 슬라이드 이미지 점검 → [{index, ok, issue}]. 불가 시 빈 리스트(비차단)."""
    if not images or not os.getenv("OPENROUTER_API_KEY"):
        return []
    try:
        import base64
        from openai import OpenAI
    except Exception:
        return []
    model = model or os.getenv("DEEP_MINING_QA_MODEL") or os.getenv("LLM_MODEL", "gpt-4o-mini")
    content = [{"type": "text", "text": _QA_CHECK}]
    for p in images:
        try:
            b64 = base64.b64encode(Path(p).read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        except OSError:
            continue
    try:
        client = OpenAI(api_key=os.getenv("OPENROUTER_API_KEY", ""),
                        base_url=os.getenv("OPENROUTER_BASE_URL", "") or None)
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": content}],
            temperature=0, max_tokens=1500, timeout=timeout,
        )
        text = (resp.choices[0].message.content or "").strip()
        i, j = text.find("{"), text.rfind("}")
        if i == -1:
            return []
        return json.loads(text[i:j + 1]).get("slides", [])
    except Exception as e:
        logger.warning(f"[deck_render] 비전 QA 실패: {e}")
        return []


def trim_slide(slide: dict) -> bool:
    """overflow 완화용 보수적 트림 — 마지막 항목 1개 제거. 변경했으면 True."""
    for key in ("cards", "rows", "items", "bullets"):
        arr = slide.get(key)
        if isinstance(arr, list) and len(arr) > 1:
            arr.pop()
            return True
    if slide.get("layout") == "two_col":
        for side in ("left", "right"):
            b = (slide.get(side) or {}).get("bullets")
            if isinstance(b, list) and len(b) > 1:
                b.pop()
                return True
    return False
