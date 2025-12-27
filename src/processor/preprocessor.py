"""
Preprocessor for merging and structuring mail content.
Combines body text and vision-parsed image text into sentences.
"""

import logging
import re
from typing import Optional

from src.schemas import (
    MailContent,
    ParsedImage,
    ProcessedMail,
    ProcessedSentence,
    SourceType,
)

logger = logging.getLogger(__name__)


class Preprocessor:
    """
    Preprocessor for structuring mail content.

    Responsibilities:
    - Split body text into sentences/bullets
    - Merge vision-parsed image text
    - Clean and normalize text
    - Create structured ProcessedSentence objects
    """

    # Patterns for sentence splitting
    BULLET_PATTERNS = [
        r"^[\-\•\*]\s*",  # - or • or * bullet
        r"^\d+[\.\)]\s*",  # 1. or 1) numbered
        r"^[가-힣][\.\)]\s*",  # 가. or 가) Korean numbered
        r"^[a-zA-Z][\.\)]\s*",  # a. or a) alphabetical
    ]

    # Patterns to remove
    NOISE_PATTERNS = [
        r"^[\s\-\=]{3,}$",  # separator lines
        r"^From:.*$",  # email headers
        r"^Sent:.*$",
        r"^To:.*$",
        r"^Subject:.*$",
        r"^본 메일은.*$",  # auto-generated footers
        r"^이 이메일은.*$",
    ]

    def __init__(
        self,
        min_sentence_length: int = 5,
        max_sentence_length: int = 1000,
    ):
        """
        Initialize preprocessor.

        Args:
            min_sentence_length: Minimum characters for a valid sentence
            max_sentence_length: Maximum characters before splitting
        """
        self.min_length = min_sentence_length
        self.max_length = max_sentence_length

    def clean_text(self, text: str) -> str:
        """
        Clean and normalize text.

        Args:
            text: Raw text to clean

        Returns:
            Cleaned text
        """
        if not text:
            return ""

        # Remove excessive whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Remove noise patterns
        lines = text.split("\n")
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            is_noise = any(
                re.match(pattern, line, re.IGNORECASE)
                for pattern in self.NOISE_PATTERNS
            )
            if not is_noise:
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    def split_into_sentences(self, text: str) -> list[str]:
        """
        Split text into sentences or bullet points.

        Args:
            text: Text to split

        Returns:
            List of sentences
        """
        if not text:
            return []

        sentences = []
        lines = text.split("\n")

        current_sentence = ""

        for line in lines:
            line = line.strip()
            if not line:
                # Empty line - save current sentence
                if current_sentence:
                    sentences.append(current_sentence.strip())
                    current_sentence = ""
                continue

            # Check if it's a bullet point
            is_bullet = any(
                re.match(pattern, line) for pattern in self.BULLET_PATTERNS
            )

            if is_bullet:
                # Save previous sentence if exists
                if current_sentence:
                    sentences.append(current_sentence.strip())
                # Clean bullet marker and start new sentence
                cleaned_line = line
                for pattern in self.BULLET_PATTERNS:
                    cleaned_line = re.sub(pattern, "", cleaned_line)
                current_sentence = cleaned_line.strip()
            else:
                # Check if line ends sentence
                if line.endswith((".", "!", "?", ":", "다", "요", "음")):
                    if current_sentence:
                        current_sentence += " " + line
                    else:
                        current_sentence = line
                    sentences.append(current_sentence.strip())
                    current_sentence = ""
                else:
                    # Continue current sentence
                    if current_sentence:
                        current_sentence += " " + line
                    else:
                        current_sentence = line

        # Don't forget the last sentence
        if current_sentence:
            sentences.append(current_sentence.strip())

        # Filter by length and clean
        filtered = []
        for s in sentences:
            s = s.strip()
            if len(s) >= self.min_length:
                # Split if too long
                if len(s) > self.max_length:
                    # Split at sentence boundaries
                    sub_sentences = re.split(r"(?<=[.!?다요음])\s+", s)
                    filtered.extend(
                        sub.strip()
                        for sub in sub_sentences
                        if len(sub.strip()) >= self.min_length
                    )
                else:
                    filtered.append(s)

        return filtered

    def process_body(
        self,
        body_text: str,
        mail_id: str,
        team: str,
        week: str,
    ) -> list[ProcessedSentence]:
        """
        Process email body text into sentences.

        Args:
            body_text: Raw email body
            mail_id: Email message ID
            team: Team name
            week: Week string (YYYY-WW)

        Returns:
            List of ProcessedSentence objects
        """
        cleaned = self.clean_text(body_text)
        sentences = self.split_into_sentences(cleaned)

        return [
            ProcessedSentence(
                text=sentence,
                source=SourceType.BODY,
                mail_id=mail_id,
                team=team,
                week=week,
            )
            for sentence in sentences
        ]

    def process_image(
        self,
        parsed_image: ParsedImage,
        mail_id: str,
        team: str,
        week: str,
    ) -> list[ProcessedSentence]:
        """
        Process vision-parsed image text into sentences.

        Args:
            parsed_image: ParsedImage with extracted text
            mail_id: Email message ID
            team: Team name
            week: Week string (YYYY-WW)

        Returns:
            List of ProcessedSentence objects
        """
        if parsed_image.confidence < 0.5:
            logger.warning(
                f"Skipping low-confidence image parse: {parsed_image.filename}"
            )
            return []

        cleaned = self.clean_text(parsed_image.extracted_text)
        sentences = self.split_into_sentences(cleaned)

        return [
            ProcessedSentence(
                text=sentence,
                source=SourceType.IMAGE,
                source_filename=parsed_image.filename,
                mail_id=mail_id,
                team=team,
                week=week,
            )
            for sentence in sentences
        ]

    def process_mail(
        self,
        mail: MailContent,
        parsed_images: Optional[list[ParsedImage]] = None,
    ) -> ProcessedMail:
        """
        Process a complete email with body and images.

        Args:
            mail: MailContent to process
            parsed_images: Optional pre-parsed images (uses mail.parsed_images if None)

        Returns:
            ProcessedMail with all sentences
        """
        team = mail.team or "Unknown"
        images = parsed_images or mail.parsed_images

        # Process body
        body_sentences = self.process_body(
            mail.body_text,
            mail.mail_id,
            team,
            mail.week,
        )

        # Process images
        image_sentences = []
        for parsed_image in images:
            image_sentences.extend(
                self.process_image(
                    parsed_image,
                    mail.mail_id,
                    team,
                    mail.week,
                )
            )

        # Combine all sentences
        all_sentences = body_sentences + image_sentences

        logger.info(
            f"Processed mail {mail.mail_id[:20]}...: "
            f"{len(body_sentences)} body + {len(image_sentences)} image sentences"
        )

        return ProcessedMail(
            mail_id=mail.mail_id,
            team=team,
            week=mail.week,
            received_date=mail.received_date,
            sentences=all_sentences,
            raw_body=mail.body_text,
            raw_images=images,
        )

    def merge_duplicate_sentences(
        self,
        sentences: list[ProcessedSentence],
        similarity_threshold: float = 0.9,
    ) -> list[ProcessedSentence]:
        """
        Remove near-duplicate sentences.

        Args:
            sentences: List of sentences to deduplicate
            similarity_threshold: Threshold for considering duplicates

        Returns:
            Deduplicated list
        """
        if not sentences:
            return []

        # Simple exact match deduplication
        seen_texts = set()
        unique = []

        for sentence in sentences:
            # Normalize for comparison
            normalized = sentence.text.lower().strip()
            if normalized not in seen_texts:
                seen_texts.add(normalized)
                unique.append(sentence)

        if len(unique) < len(sentences):
            logger.info(
                f"Removed {len(sentences) - len(unique)} duplicate sentences"
            )

        return unique

