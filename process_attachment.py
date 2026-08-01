"""
3-1단계: 첨부파일 텍스트 추출
- attach_* 파일에서 텍스트 추출
- Excel, PDF, Word, 이미지 지원
- attachments.json 저장 (RAG용)
"""

import os
import base64
import json
from pathlib import Path

from app.content.mail import safe_mail_log_context

from openai import OpenAI

# ========== 설정 ==========
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "your_api_key")
VISION_MODEL = "gpt-oss-120b"
DATA_DIR = Path("data")


def discover_owned_mail_folders(data_dir: Path, week=None) -> list[Path]:
    folders = []
    for folder in data_dir.glob("**/mail_*"):
        meta_path = folder / "meta.json"
        if not folder.is_dir() or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not str(meta.get("user_id") or "").strip():
            continue
        if week and meta.get("week") != week:
            continue
        folders.append(folder)
    return sorted(folders)

# OpenRouter 클라이언트 (이미지용)
client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)


# ========== 파일 유형별 추출 ==========


def extract_from_excel(file_path: Path) -> str:
    """Excel 파일에서 텍스트 추출"""
    try:
        import pandas as pd

        # 모든 시트 읽기
        xlsx = pd.ExcelFile(file_path)
        texts = []

        for sheet_name in xlsx.sheet_names:
            df = pd.read_excel(xlsx, sheet_name=sheet_name)
            texts.append(f"=== Sheet: {sheet_name} ===")
            texts.append(df.to_string(index=False))

        return "\n\n".join(texts)

    except ImportError:
        return "[Excel 추출 실패: pandas 미설치]"
    except Exception as e:
        return f"[Excel 추출 실패: {type(e).__name__}]"


def extract_from_pdf(file_path: Path) -> str:
    """PDF 파일에서 텍스트 추출"""
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        texts = []

        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if text.strip():
                texts.append(f"=== Page {i} ===")
                texts.append(text)

        return "\n\n".join(texts)

    except ImportError:
        return "[PDF 추출 실패: pypdf 미설치]"
    except Exception as e:
        return f"[PDF 추출 실패: {type(e).__name__}]"


def extract_from_word(file_path: Path) -> str:
    """Word 파일에서 텍스트 추출"""
    try:
        from docx import Document

        doc = Document(file_path)
        texts = []

        for para in doc.paragraphs:
            if para.text.strip():
                texts.append(para.text)

        # 테이블도 추출
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text for cell in row.cells)
                texts.append(row_text)

        return "\n".join(texts)

    except ImportError:
        return "[Word 추출 실패: python-docx 미설치]"
    except Exception as e:
        return f"[Word 추출 실패: {type(e).__name__}]"


def extract_from_image(file_path: Path) -> str:
    """이미지 파일에서 Vision LLM으로 텍스트 추출"""
    try:
        with open(file_path, "rb") as f:
            base64_image = base64.b64encode(f.read()).decode("utf-8")

        ext = file_path.suffix.lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = media_types.get(ext, "image/png")

        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "이미지의 모든 텍스트, 표, 수치를 추출해주세요. 표는 markdown 형식으로.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
            max_tokens=4096,
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"[이미지 추출 실패: {type(e).__name__}]"


def get_file_type(filename: str) -> str:
    """파일 확장자로 타입 판별"""
    ext = Path(filename).suffix.lower()

    if ext in [".xlsx", ".xls"]:
        return "excel"
    elif ext == ".pdf":
        return "pdf"
    elif ext in [".docx", ".doc"]:
        return "word"
    elif ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]:
        return "image"
    else:
        return "unknown"


def extract_text(file_path: Path) -> tuple[str, str]:
    """파일에서 텍스트 추출 (텍스트, 파일타입)"""
    file_type = get_file_type(file_path.name)

    if file_type == "excel":
        return extract_from_excel(file_path), file_type
    elif file_type == "pdf":
        return extract_from_pdf(file_path), file_type
    elif file_type == "word":
        return extract_from_word(file_path), file_type
    elif file_type == "image":
        return extract_from_image(file_path), file_type
    else:
        return f"[지원하지 않는 파일 형식: {file_path.suffix}]", file_type


# ========== 메인 처리 ==========


def process_mail_folder(mail_dir: Path) -> dict:
    """단일 메일 폴더의 첨부파일 처리"""
    print(f"\n📂 처리 중: {safe_mail_log_context(mail_dir)}")

    # attach_* 파일 찾기
    attachments = []
    for file in mail_dir.iterdir():
        if file.name.startswith("attach_") and file.is_file():
            # 이미 txt로 추출된 것은 스킵
            if file.suffix == ".txt":
                continue
            attachments.append(file)

    if not attachments:
        print(f"   📎 첨부파일 없음")
        return None

    print(f"   📎 첨부파일 {len(attachments)}개 발견")

    # 메타데이터 읽기
    meta_path = mail_dir / "meta.json"
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # 각 첨부파일 처리
    results = []
    for attachment_index, attach_file in enumerate(attachments, 1):
        print(f"   📄 첨부 처리: item={attachment_index}")

        text, file_type = extract_text(attach_file)

        # 결과 저장
        result = {
            "filename": attach_file.name,
            "file_type": file_type,
            "text": text,
            "week": meta.get("week"),
            "team": meta.get("team"),
        }
        results.append(result)

        # 개별 txt 파일로도 저장
        txt_path = attach_file.with_suffix(".txt")
        txt_path.write_text(text, encoding="utf-8")
        print(f"   ✅ 첨부 저장: item={attachment_index} ({len(text)} chars)")

    # attachments.json 저장
    if results:
        json_path = mail_dir / "attachments.json"
        json_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("   💾 첨부 메타데이터 저장")

    return {"count": len(results)}


def process_all(week=None):
    """메일 폴더의 첨부파일 처리 (week 지정 시 해당 주차만)"""
    print("=" * 50)
    print("첨부파일 텍스트 추출")
    print("=" * 50)

    if not DATA_DIR.exists():
        print("❌ configured data directory is missing")
        return

    mail_folders = discover_owned_mail_folders(DATA_DIR, week)
    print(f"📁 발견된 메일 폴더: {len(mail_folders)}개")

    stats = {
        "folders_with_attachments": 0,
        "total_attachments": 0,
    }

    for mail_dir in mail_folders:
        if not mail_dir.is_dir():
            continue

        result = process_mail_folder(mail_dir)

        if result:
            stats["folders_with_attachments"] += 1
            stats["total_attachments"] += result["count"]

    # 결과 요약
    print("\n" + "=" * 50)
    print("✅ 첨부파일 처리 완료")
    print("=" * 50)
    print(f"   첨부파일 있는 폴더: {stats['folders_with_attachments']}개")
    print(f"   총 첨부파일: {stats['total_attachments']}개")


if __name__ == "__main__":
    process_all()






