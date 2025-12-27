"""
Pydantic schemas for the mail agent.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    """Source type of the content."""
    BODY = "body"
    IMAGE = "image"


class Domain(str, Enum):
    """Domain classification."""
    COMMON = "COMMON"
    DRAM = "DRAM"
    NAND = "NAND"


class ImageAttachment(BaseModel):
    """Represents an image attachment from email."""
    filename: str
    content_type: str
    content: bytes  # Base64 encoded image data
    size: int

    class Config:
        arbitrary_types_allowed = True


class ParsedImage(BaseModel):
    """Result of vision parsing an image."""
    filename: str
    extracted_text: str
    has_table: bool = False
    has_chart: bool = False
    confidence: float = 1.0


class MailContent(BaseModel):
    """Represents a collected email with its content."""
    mail_id: str
    subject: str
    sender: str
    team: Optional[str] = None
    received_date: datetime
    week: str  # YYYY-WW format
    body_text: str
    images: list[ImageAttachment] = Field(default_factory=list)
    parsed_images: list[ParsedImage] = Field(default_factory=list)


class ProcessedSentence(BaseModel):
    """A processed sentence with metadata."""
    text: str
    source: SourceType
    source_filename: Optional[str] = None  # For image sources
    mail_id: str
    team: str
    week: str
    domain: Optional[Domain] = None
    tech: Optional[str] = None


class ProcessedMail(BaseModel):
    """Fully processed mail with all sentences."""
    mail_id: str
    team: str
    week: str
    received_date: datetime
    sentences: list[ProcessedSentence] = Field(default_factory=list)
    raw_body: str
    raw_images: list[ParsedImage] = Field(default_factory=list)


class MailIngestState(BaseModel):
    """State for the mail ingestion graph."""
    mails: list[MailContent] = Field(default_factory=list)
    processed_mails: list[ProcessedMail] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    current_step: str = "init"

    class Config:
        arbitrary_types_allowed = True

