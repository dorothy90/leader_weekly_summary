"""주제별 타임라인 → PPTAgent(DeepPresenter) 기반 고품질 PPTX 생성 어댑터.

PPTAgent(https://github.com/icip-cas/PPTAgent)는 멀티 에이전트 프레임워크로,
소스를 본 프로젝트에 '이식'하지 않고 **외부 도구로 설치한 뒤 CLI로 호출**한다.
(이식 시 LibreOffice/Chrome/Node.js/GPU 모델 등 무거운 의존성과 유지보수 부담이
프로젝트에 전이되므로 권장하지 않음. 자세한 설치/설정은 docs/pptagent_setup.md 참고.)

동작:
  generate_topic_timeline() 가 만든 마크다운 리포트를 PPTAgent에 입력(-f)으로 넘겨
  회사 레퍼런스 템플릿 스타일의 pptx를 생성한다.

설정(환경변수):
  PPTAGENT_CMD            : 실행 명령 (기본 "uvx pptagent generate")
  PPTAGENT_TEMPLATE       : 회사 레퍼런스 템플릿 .pptx 경로 (선택)
  PPTAGENT_TEMPLATE_FLAG  : 템플릿을 넘길 CLI 플래그 (기본 "--reference";
                            실제 PPTAgent 버전의 플래그명에 맞춰 조정)
  PPTAGENT_EXTRA_ARGS     : 추가 인자 (공백 구분, 선택)
  PPTAGENT_PROMPT         : 생성 프롬프트(지시문). 기본값은 한국어 타임라인 보고서 지시
  PPTAGENT_TIMEOUT        : 초 단위 타임아웃 (기본 1200)

  LLM은 PPTAgent 자체 config.yaml 에서 설정한다(예: 기존 OpenRouter 재사용).
  config.yaml 작성법은 docs/pptagent_setup.md 참고.

이 모듈은 본 프로젝트의 무거운 의존성을 끌어오지 않는다(subprocess만 사용).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_PROMPT = (
    "첨부된 마크다운 문서는 특정 주제에 대한 '주차별 타임라인 보고서'입니다. "
    "문서의 내용과 시간 순서를 충실히 반영하여, 표지 → 개요(전체 추이) → "
    "주차별 타임라인 순으로 구성된 전문적인 발표 자료를 한국어로 작성하세요. "
    "수치와 사실을 왜곡하지 말고, 레퍼런스 템플릿의 디자인을 따르세요."
)


def build_command(
    markdown_path: str,
    output_path: str,
    *,
    template: Optional[str] = None,
    prompt: Optional[str] = None,
) -> List[str]:
    """PPTAgent CLI 호출 argv 리스트를 조립한다 (실행 없음 — 테스트 용이).

    환경변수로 명령/플래그를 구성하므로 PPTAgent 버전별 CLI 차이에 유연하게 대응.
    """
    base = os.getenv("PPTAGENT_CMD", "uvx pptagent generate")
    argv = shlex.split(base)

    argv.append(prompt or os.getenv("PPTAGENT_PROMPT", DEFAULT_PROMPT))

    # 입력 문서(마크다운 리포트) 첨부
    argv += ["-f", str(markdown_path)]

    # 레퍼런스(회사) 템플릿
    tmpl = template or os.getenv("PPTAGENT_TEMPLATE")
    if tmpl:
        flag = os.getenv("PPTAGENT_TEMPLATE_FLAG", "--reference")
        argv += [flag, str(tmpl)]

    # 추가 인자
    extra = os.getenv("PPTAGENT_EXTRA_ARGS", "").strip()
    if extra:
        argv += shlex.split(extra)

    # 출력
    argv += ["-o", str(output_path)]
    return argv


def build_with_pptagent(
    result: Dict,
    output_path: str,
    *,
    template: Optional[str] = None,
    prompt: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """generate_topic_timeline() 결과 → PPTAgent로 pptx 생성.

    Args:
        result: generate_topic_timeline() 반환 dict (markdown_path 포함).
        output_path: 출력 pptx 경로.
        template: 레퍼런스 템플릿 .pptx (없으면 PPTAGENT_TEMPLATE env).
        prompt: 생성 지시문 (없으면 env/기본값).
        timeout: 초 단위 (없으면 PPTAGENT_TIMEOUT env, 기본 1200).

    Returns:
        생성된 pptx 경로.

    Raises:
        FileNotFoundError: 입력 마크다운이 없을 때.
        RuntimeError: PPTAgent 실행 실패 / 출력 미생성 시.
    """
    md_path = result.get("markdown_path")
    if not md_path or not Path(md_path).exists():
        raise FileNotFoundError(f"PPTAgent 입력 마크다운이 없습니다: {md_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    argv = build_command(md_path, output_path, template=template, prompt=prompt)
    timeout = timeout or int(os.getenv("PPTAGENT_TIMEOUT", "1200"))

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            f"PPTAgent 실행 파일을 찾을 수 없습니다 ({argv[0]}). "
            f"설치/설정은 docs/pptagent_setup.md 참고: {e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"PPTAgent 타임아웃({timeout}s): {e}") from e

    if proc.returncode != 0:
        raise RuntimeError(
            "PPTAgent 생성 실패 "
            f"(exit={proc.returncode})\nstdout:\n{proc.stdout[-2000:]}\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )

    out = Path(output_path)
    if not out.exists():
        raise RuntimeError(
            f"PPTAgent가 출력 파일을 만들지 않았습니다: {output_path}\n"
            f"stdout:\n{proc.stdout[-1000:]}"
        )
    return str(out)


# ========== CLI (단독 테스트용) ==========

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="주제별 타임라인 마크다운 → PPTAgent pptx")
    ap.add_argument("markdown", help="입력 마크다운 리포트 경로")
    ap.add_argument("-o", "--output", required=True, help="출력 pptx 경로")
    ap.add_argument("--template", default=None, help="레퍼런스 템플릿 .pptx")
    ap.add_argument("--dry-run", action="store_true", help="실행 없이 조립된 명령만 출력")
    args = ap.parse_args()

    if args.dry_run:
        argv = build_command(args.markdown, args.output, template=args.template)
        print(" ".join(shlex.quote(a) for a in argv))
        return 0

    path = build_with_pptagent(
        {"markdown_path": args.markdown}, args.output, template=args.template
    )
    print(f"생성됨: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
