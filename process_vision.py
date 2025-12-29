"""
2단계: Vision LLM으로 이미지 텍스트 추출
- data/ 폴더의 인라인 이미지 → 텍스트 변환
- body.txt + vision 결과 → combined.txt 생성
"""

import os
import base64
import json
import time
from pathlib import Path
from openai import OpenAI

# ========== 설정 ==========
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "your_api_key")
VISION_MODEL = "gpt-oss-120b"
DATA_DIR = Path("data")

# 타임아웃 & 재시도 설정
VISION_TIMEOUT = 60  # 1분 타임아웃
MAX_RETRIES = 3      # 최대 재시도 횟수

# OpenRouter 클라이언트 (타임아웃 설정)
client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    timeout=VISION_TIMEOUT,
)

# Vision 프롬프트
VISION_PROMPT = """이미지에서 모든 정보를 추출해주세요.

규칙:
1. 모든 텍스트, 수치, 표를 빠짐없이 추출
2. 표는 markdown 형식으로 변환
3. 그래프는 "항목: 값" 형태로 풀어서 작성
4. 보이지 않는 내용은 추측하지 말 것
5. 흐릿한 부분은 [불명확]으로 표시

추출 결과:"""


def encode_image(image_path):
    """이미지를 base64로 인코딩"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def get_media_type(filename):
    """파일 확장자로 media type 추출"""
    ext = filename.lower().split(".")[-1]
    types = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
    }
    return types.get(ext, "image/png")


def extract_text_from_image(image_path):
    """Vision LLM으로 이미지에서 텍스트 추출 (타임아웃 & 재시도 포함)"""
    base64_image = encode_image(image_path)
    media_type = get_media_type(image_path.name)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"      🔄 시도 {attempt}/{MAX_RETRIES}...")

            response = client.chat.completions.create(
                model=VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_PROMPT},
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
                timeout=VISION_TIMEOUT,
            )

            return response.choices[0].message.content

        except Exception as e:
            error_type = type(e).__name__
            print(f"      ⚠️ 시도 {attempt} 실패 ({error_type}): {e}")

            if attempt < MAX_RETRIES:
                wait_time = attempt * 5  # 5초, 10초, 15초 대기
                print(f"      ⏳ {wait_time}초 후 재시도...")
                time.sleep(wait_time)
            else:
                print(f"   ❌ Vision 최종 실패 ({image_path.name}): {MAX_RETRIES}회 시도 모두 실패")
                return f"[Vision 추출 실패: {MAX_RETRIES}회 시도 후 실패 - {e}]"


def is_image_file(filename):
    """이미지 파일 여부 확인"""
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    return Path(filename).suffix.lower() in image_exts


def process_mail_folder(mail_dir):
    """단일 메일 폴더 처리"""
    print(f"\n📂 처리 중: {mail_dir}")

    # 1. body.txt 읽기
    body_path = mail_dir / "body.txt"
    body_text = ""
    if body_path.exists():
        body_text = body_path.read_text(encoding="utf-8")
        print(f"   📄 본문: {len(body_text)} chars")

    # 2. 인라인 이미지 찾기 & Vision 처리
    vision_results = []
    for file in mail_dir.iterdir():
        if file.name.startswith("inline_") and is_image_file(file.name):
            print(f"   🖼️  Vision 처리: {file.name}")

            # Vision LLM 호출
            extracted_text = extract_text_from_image(file)

            # 개별 결과 저장 (inline_001.png → inline_001.txt)
            txt_path = file.with_suffix(".txt")
            txt_path.write_text(extracted_text, encoding="utf-8")

            vision_results.append({
                "filename": file.name,
                "text": extracted_text,
            })
            print(f"   ✅ 저장: {txt_path.name}")

    # 3. combined.txt 생성 (body + vision)
    combined_parts = []

    # 본문
    if body_text.strip():
        combined_parts.append("=== 메일 본문 ===")
        combined_parts.append(body_text.strip())

    # Vision 결과
    for result in vision_results:
        combined_parts.append(f"\n=== 이미지: {result['filename']} ===")
        combined_parts.append(result["text"])

    combined_text = "\n\n".join(combined_parts)
    combined_path = mail_dir / "combined.txt"
    combined_path.write_text(combined_text, encoding="utf-8")

    print(f"   📝 통합 저장: combined.txt ({len(combined_text)} chars)")

    return {
        "body_chars": len(body_text),
        "images_processed": len(vision_results),
        "combined_chars": len(combined_text),
    }


def process_all():
    """모든 메일 폴더 처리"""
    print("=" * 50)
    print("Vision LLM 이미지 처리")
    print("=" * 50)

    if not DATA_DIR.exists():
        print(f"❌ data 폴더가 없습니다: {DATA_DIR}")
        return

    # 모든 mail_* 폴더 찾기
    mail_folders = list(DATA_DIR.glob("**/mail_*"))
    print(f"📁 발견된 메일 폴더: {len(mail_folders)}개")

    stats = {
        "total_mails": 0,
        "total_images": 0,
        "success": 0,
        "failed": 0,
    }

    for mail_dir in mail_folders:
        if not mail_dir.is_dir():
            continue

        try:
            result = process_mail_folder(mail_dir)
            stats["total_mails"] += 1
            stats["total_images"] += result["images_processed"]
            stats["success"] += 1
        except Exception as e:
            print(f"   ❌ 폴더 처리 실패: {e}")
            stats["failed"] += 1

    # 결과 요약
    print("\n" + "=" * 50)
    print("✅ Vision 처리 완료")
    print("=" * 50)
    print(f"   총 메일: {stats['total_mails']}개")
    print(f"   처리된 이미지: {stats['total_images']}개")
    print(f"   성공: {stats['success']}, 실패: {stats['failed']}")


if __name__ == "__main__":
    process_all()

