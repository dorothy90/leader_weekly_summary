"""
Vision Parser using OpenRouter Vision LLM via LangChain.
Extracts text, tables, and numerical data from images.
"""

import base64
import logging
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from config.settings import get_settings
from src.schemas import ImageAttachment, ParsedImage

logger = logging.getLogger(__name__)


# Vision extraction prompt
VISION_EXTRACTION_PROMPT = """당신은 이미지에서 정보를 추출하는 전문가입니다.

다음 규칙을 반드시 따라주세요:
1. 이미지 안의 모든 텍스트, 수치, 표를 빠짐없이 추출하세요
2. 원문 형태를 최대한 유지하되, 의미 단위로 줄바꿈하세요
3. 보이지 않는 내용은 절대 추측하지 마세요
4. 그래프나 차트가 있다면 "항목: 값" 형태로 풀어서 작성하세요
5. 표가 있다면 markdown 표 형식으로 변환하세요
6. 흐릿하거나 읽기 어려운 부분은 [불명확] 으로 표시하세요

이미지의 모든 내용을 구조화된 텍스트로 추출해주세요."""


class VisionParser:
    """
    Vision LLM parser for extracting text and data from images.
    Uses OpenRouter API via LangChain ChatOpenAI.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        """
        Initialize the vision parser.

        Args:
            model: Vision model to use (defaults to settings)
            api_key: OpenRouter API key (defaults to settings)
            base_url: OpenRouter base URL (defaults to settings)
            temperature: LLM temperature (default: 0.0 for deterministic output)
            max_tokens: Maximum tokens in response
        """
        settings = get_settings()

        self.model = model or settings.vision_model
        self.api_key = api_key or settings.openrouter_api_key
        self.base_url = base_url or settings.openrouter_base_url
        self.temperature = temperature
        self.max_tokens = max_tokens

        self._llm: Optional[ChatOpenAI] = None

    @property
    def llm(self) -> ChatOpenAI:
        """Get or create the LLM instance."""
        if self._llm is None:
            self._llm = ChatOpenAI(
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                default_headers={
                    "HTTP-Referer": "https://weekly-mail-agent.local",
                    "X-Title": "Weekly Mail Agent",
                },
            )
        return self._llm

    def _encode_image_to_base64(self, image_content: bytes) -> str:
        """Encode image bytes to base64 string."""
        return base64.b64encode(image_content).decode("utf-8")

    def _get_media_type(self, content_type: str) -> str:
        """Normalize content type for the API."""
        content_type = content_type.lower()
        if "jpeg" in content_type or "jpg" in content_type:
            return "image/jpeg"
        elif "png" in content_type:
            return "image/png"
        elif "gif" in content_type:
            return "image/gif"
        elif "webp" in content_type:
            return "image/webp"
        return "image/jpeg"  # default

    def parse_image(
        self,
        image: ImageAttachment,
        custom_prompt: Optional[str] = None,
    ) -> ParsedImage:
        """
        Parse a single image using vision LLM.

        Args:
            image: ImageAttachment to parse
            custom_prompt: Optional custom prompt (overrides default)

        Returns:
            ParsedImage with extracted text
        """
        prompt = custom_prompt or VISION_EXTRACTION_PROMPT

        try:
            # Encode image to base64
            base64_image = self._encode_image_to_base64(image.content)
            media_type = self._get_media_type(image.content_type)

            # Create message with image
            message = HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{base64_image}",
                        },
                    },
                ]
            )

            # Invoke vision model
            response = self.llm.invoke([message])
            extracted_text = response.content

            # Detect content types
            has_table = self._detect_table(extracted_text)
            has_chart = self._detect_chart(extracted_text)

            logger.info(
                f"Parsed image {image.filename}: "
                f"{len(extracted_text)} chars, table={has_table}, chart={has_chart}"
            )

            return ParsedImage(
                filename=image.filename,
                extracted_text=extracted_text,
                has_table=has_table,
                has_chart=has_chart,
                confidence=1.0,
            )

        except Exception as e:
            logger.error(f"Failed to parse image {image.filename}: {e}")
            return ParsedImage(
                filename=image.filename,
                extracted_text=f"[파싱 실패: {str(e)}]",
                has_table=False,
                has_chart=False,
                confidence=0.0,
            )

    def parse_images(
        self,
        images: list[ImageAttachment],
        custom_prompt: Optional[str] = None,
    ) -> list[ParsedImage]:
        """
        Parse multiple images.

        Args:
            images: List of ImageAttachment objects
            custom_prompt: Optional custom prompt

        Returns:
            List of ParsedImage objects
        """
        results = []
        for image in images:
            result = self.parse_image(image, custom_prompt)
            results.append(result)
        return results

    def _detect_table(self, text: str) -> bool:
        """Detect if the extracted text contains a table."""
        # Check for markdown table patterns or table-like structures
        table_indicators = [
            "|",  # Markdown table separator
            "---",  # Table header separator
            "\t",  # Tab-separated values
        ]
        lines = text.split("\n")

        # Check for multiple lines with consistent separators
        pipe_lines = sum(1 for line in lines if "|" in line)
        if pipe_lines >= 2:
            return True

        return False

    def _detect_chart(self, text: str) -> bool:
        """Detect if the extracted text describes a chart/graph."""
        chart_keywords = [
            "그래프",
            "차트",
            "chart",
            "graph",
            "추이",
            "트렌드",
            "trend",
            "x축",
            "y축",
            "범례",
            "legend",
        ]
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in chart_keywords)


class BatchVisionParser:
    """
    Batch processor for vision parsing with rate limiting and error handling.
    """

    def __init__(
        self,
        parser: Optional[VisionParser] = None,
        batch_size: int = 5,
        delay_between_batches: float = 1.0,
    ):
        """
        Initialize batch parser.

        Args:
            parser: VisionParser instance (creates new if None)
            batch_size: Number of images per batch
            delay_between_batches: Delay in seconds between batches
        """
        self.parser = parser or VisionParser()
        self.batch_size = batch_size
        self.delay = delay_between_batches

    def parse_all(
        self,
        images: list[ImageAttachment],
        custom_prompt: Optional[str] = None,
    ) -> list[ParsedImage]:
        """
        Parse all images with batching.

        Args:
            images: All images to parse
            custom_prompt: Optional custom prompt

        Returns:
            List of all ParsedImage results
        """
        import time

        results = []
        total = len(images)

        for i in range(0, total, self.batch_size):
            batch = images[i : i + self.batch_size]
            batch_results = self.parser.parse_images(batch, custom_prompt)
            results.extend(batch_results)

            logger.info(f"Processed batch {i // self.batch_size + 1}, "
                       f"total: {len(results)}/{total}")

            # Delay between batches (except for last batch)
            if i + self.batch_size < total:
                time.sleep(self.delay)

        return results

