"""
2단계: Vision LLM으로 이미지 텍스트 추출
- data/ 폴더의 인라인 이미지 → 텍스트 변환
- body.txt + vision 결과 → combined.txt 생성
"""

import io
import os
import base64
import random
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps
from openai import (
    OpenAI,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    BadRequestError,
    AuthenticationError,
    PermissionDeniedError,
    UnprocessableEntityError,
    APIStatusError,
    InternalServerError,
)

# ========== 설정 ==========
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "your_api_key")
VISION_MODEL = "gpt-oss-120b"
DATA_DIR = Path("data")

# 이미지 전처리 설정
MAX_IMAGE_SIDE = 1800  # 긴 변 최대 px
MAX_IMAGE_BYTES = 1_500_000  # 전처리 후 목표 크기 1.5MB
FALLBACK_IMAGE_SIDE = 1400  # 재시도 시 다운그레이드 해상도

# 타임아웃 & 재시도 설정
MAX_RETRIES = 4
INITIAL_BACKOFF = 2.0

# OpenRouter 클라이언트 (타임아웃 분리, SDK 재시도 비활성화)
client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    max_retries=0,
    timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0),
)

# Vision 프롬프트
VISION_PROMPT = """이미지에서 모든 정보를 추출해주세요.

규칙:
1. 모든 텍스트, 수치를 빠짐없이 추출
2. 표 처리 규칙:
   - 빈 셀은 생략하고, 데이터가 있는 셀만 추출
   - markdown 표 대신 "행제목: 열제목=값" 형식으로 작성
   - 예: "1월: 매출=100, 비용=50"
   - 빈 행/열이 연속되면 무시
3. 그래프는 "항목: 값" 형태로 풀어서 작성
4. 보이지 않는 내용은 추측하지 말 것
5. 흐릿한 부분은 [불명확]으로 표시
6. "|" 기호는 사용하지 말 것

추출 결과:"""


def preprocess_image(image_path, max_side=MAX_IMAGE_SIDE):
    """이미지 전처리: 리사이즈 + JPEG 압축 + base64 인코딩"""
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)

        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")

        width, height = img.size
        longest = max(width, height)

        if longest > max_side:
            scale = max_side / longest
            new_size = (int(width * scale), int(height * scale))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # 단계적 압축으로 목표 크기 이하 달성
        qualities = [85, 75, 65]
        out_bytes = None
        encoded = None

        for q in qualities:
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=q, optimize=True, progressive=True)
            data = buffer.getvalue()
            if len(data) <= MAX_IMAGE_BYTES or q == qualities[-1]:
                out_bytes = data
                encoded = base64.b64encode(data).decode("utf-8")
                break

        meta = {
            "orig_size": (width, height),
            "final_size": img.size,
            "binary_bytes": len(out_bytes),
            "base64_bytes": len(encoded),
        }
        return encoded, "image/jpeg", meta


def should_retry(exc):
    """재시도 가능한 에러인지 판별"""
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 409, 429) or exc.status_code >= 500
    return False


def classify_error(exc):
    """에러 타입 분류 (로깅용)"""
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, APIConnectionError):
        return "connection"
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return "auth"
    if isinstance(exc, (BadRequestError, UnprocessableEntityError)):
        return "bad_request"
    if isinstance(exc, APIStatusError):
        return f"http_{exc.status_code}"
    return type(exc).__name__


def is_image_file(filename):
    """이미지 파일 여부 확인"""
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    return Path(filename).suffix.lower() in image_exts


def extract_text_from_image(image_path):
    """Vision LLM으로 이미지에서 텍스트 추출 (전처리 + 에러분류 + backoff)"""
    base64_image, media_type, meta = preprocess_image(image_path, max_side=MAX_IMAGE_SIDE)
    print(
        f"      📐 전처리: {meta['orig_size']} → {meta['final_size']}, "
        f"payload={meta['binary_bytes']:,}B, base64={meta['base64_bytes']:,}chars"
    )

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
            )

            return response.choices[0].message.content or ""

        except Exception as exc:
            err = classify_error(exc)
            print(f"      ⚠️ 시도 {attempt} 실패 ({err}): {exc}")

            # 입력/인증 문제는 즉시 종료
            if isinstance(exc, (BadRequestError, UnprocessableEntityError, AuthenticationError, PermissionDeniedError)):
                return f"[Vision 추출 실패: {err} - {exc}]"

            # 재시도 불가하거나 마지막 시도면 종료
            if not should_retry(exc) or attempt == MAX_RETRIES:
                print(f"   ❌ Vision 최종 실패 ({image_path.name}): {MAX_RETRIES}회 시도 후 실패")
                return f"[Vision 추출 실패: {err} - {exc}]"

            # 2회 실패 시 해상도 다운그레이드
            if attempt == 2:
                base64_image, media_type, meta = preprocess_image(image_path, max_side=FALLBACK_IMAGE_SIDE)
                print(
                    f"      📐 다운그레이드: {meta['final_size']}, "
                    f"payload={meta['binary_bytes']:,}B"
                )

            # exponential backoff + jitter
            sleep_s = INITIAL_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, 0.8)
            print(f"      ⏳ {sleep_s:.1f}초 후 재시도...")
            time.sleep(sleep_s)

    return "[Vision 추출 실패: unknown]"


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

            vision_results.append(
                {
                    "filename": file.name,
                    "text": extracted_text,
                }
            )
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


def process_all(week=None):
    """메일 폴더 처리 (week 지정 시 해당 주차만)"""
    print("=" * 50)
    print("Vision LLM 이미지 처리")
    print("=" * 50)

    if not DATA_DIR.exists():
        print(f"❌ data 폴더가 없습니다: {DATA_DIR}")
        return

    # mail_* 폴더 찾기 (week 지정 시 해당 주차만)
    search_root = DATA_DIR / week if week else DATA_DIR
    mail_folders = list(search_root.glob("**/mail_*"))
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
