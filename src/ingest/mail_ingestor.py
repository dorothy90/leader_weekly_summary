"""
Mail Ingestor using Exchange Web Services (EWS).
Collects weekly team emails with body text and image attachments.
"""

import base64
import logging
from datetime import datetime, timedelta
from typing import Optional

from exchangelib import (
    Account,
    Configuration,
    Credentials,
    DELEGATE,
    EWSDateTime,
    EWSTimeZone,
    Folder,
    FileAttachment,
)
from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter

from config.settings import get_settings
from src.schemas import ImageAttachment, MailContent

logger = logging.getLogger(__name__)


class MailIngestor:
    """
    EWS-based mail ingestor for collecting weekly team emails.

    Supports:
    - Connecting to Exchange/Outlook via EWS
    - Filtering emails by date range, sender, subject
    - Extracting body text and image attachments
    """

    SUPPORTED_IMAGE_TYPES = {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/webp",
        "image/bmp",
    }

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        verify_ssl: bool = True,
    ):
        """
        Initialize the mail ingestor.

        Args:
            email: EWS account email (defaults to settings)
            password: EWS account password (defaults to settings)
            server: EWS server address (defaults to settings)
            verify_ssl: Whether to verify SSL certificates
        """
        settings = get_settings()
        self.email = email or settings.ews_email
        self.password = password or settings.ews_password
        self.server = server or settings.ews_server
        self.verify_ssl = verify_ssl
        self._account: Optional[Account] = None

    def connect(self) -> Account:
        """
        Establish connection to EWS.

        Returns:
            Connected Account instance
        """
        if self._account is not None:
            return self._account

        # Disable SSL verification if needed (for corporate proxies)
        if not self.verify_ssl:
            BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter

        credentials = Credentials(username=self.email, password=self.password)
        config = Configuration(server=self.server, credentials=credentials)

        self._account = Account(
            primary_smtp_address=self.email,
            config=config,
            autodiscover=False,
            access_type=DELEGATE,
        )

        logger.info(f"Connected to EWS as {self.email}")
        return self._account

    def disconnect(self) -> None:
        """Close the EWS connection."""
        if self._account:
            self._account.protocol.close()
            self._account = None
            logger.info("Disconnected from EWS")

    def get_week_string(self, date: datetime) -> str:
        """
        Get week string in YYYY-WW format.

        Args:
            date: Date to convert

        Returns:
            Week string (e.g., "2024-52")
        """
        iso_calendar = date.isocalendar()
        return f"{iso_calendar[0]}-{iso_calendar[1]:02d}"

    def _extract_images(self, message) -> list[ImageAttachment]:
        """
        Extract image attachments from an email message.

        Args:
            message: EWS message object

        Returns:
            List of ImageAttachment objects
        """
        images = []

        for attachment in message.attachments or []:
            if not isinstance(attachment, FileAttachment):
                continue

            content_type = attachment.content_type or ""
            if content_type.lower() not in self.SUPPORTED_IMAGE_TYPES:
                continue

            try:
                images.append(
                    ImageAttachment(
                        filename=attachment.name or "unnamed",
                        content_type=content_type,
                        content=attachment.content,
                        size=len(attachment.content) if attachment.content else 0,
                    )
                )
                logger.debug(f"Extracted image: {attachment.name}")
            except Exception as e:
                logger.warning(f"Failed to extract image {attachment.name}: {e}")

        return images

    def _detect_team_from_sender(self, sender: str, subject: str) -> Optional[str]:
        """
        Detect team from sender email or subject.
        Override this method for custom team detection logic.

        Args:
            sender: Sender email address
            subject: Email subject

        Returns:
            Detected team name or None
        """
        # Default implementation: extract from subject patterns like "[TeamName]" or "TeamName Weekly"
        import re

        # Pattern: [TeamName] in subject
        bracket_match = re.search(r"\[(\w+)\]", subject)
        if bracket_match:
            return bracket_match.group(1)

        # Pattern: TeamName Weekly / TeamName 주간
        weekly_match = re.search(r"(\w+)\s*(?:Weekly|주간)", subject, re.IGNORECASE)
        if weekly_match:
            return weekly_match.group(1)

        return None

    def fetch_weekly_mails(
        self,
        folder_name: str = "inbox",
        subject_filter: Optional[str] = None,
        sender_filter: Optional[str] = None,
        days_back: int = 7,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list[MailContent]:
        """
        Fetch weekly emails matching the criteria.

        Args:
            folder_name: Mail folder to search (default: inbox)
            subject_filter: Filter by subject containing this string
            sender_filter: Filter by sender email containing this string
            days_back: Number of days to look back (default: 7)
            start_date: Start date for filtering (overrides days_back)
            end_date: End date for filtering (default: now)

        Returns:
            List of MailContent objects
        """
        account = self.connect()

        # Determine folder
        if folder_name.lower() == "inbox":
            folder = account.inbox
        else:
            folder = account.root / "Top of Information Store" / folder_name

        # Set up date range
        tz = EWSTimeZone.localzone()
        if end_date is None:
            end_date = datetime.now()
        if start_date is None:
            start_date = end_date - timedelta(days=days_back)

        ews_start = EWSDateTime.from_datetime(start_date.replace(tzinfo=tz))
        ews_end = EWSDateTime.from_datetime(end_date.replace(tzinfo=tz))

        # Build query
        query = folder.filter(datetime_received__range=(ews_start, ews_end))

        if subject_filter:
            query = query.filter(subject__icontains=subject_filter)

        if sender_filter:
            query = query.filter(sender__icontains=sender_filter)

        # Fetch and process messages
        mails: list[MailContent] = []

        for message in query.order_by("-datetime_received"):
            try:
                # Get body text
                body_text = ""
                if message.text_body:
                    body_text = message.text_body
                elif message.body:
                    body_text = message.body

                # Extract sender email
                sender_email = ""
                if message.sender:
                    sender_email = message.sender.email_address or str(message.sender)

                # Detect team
                team = self._detect_team_from_sender(
                    sender_email, message.subject or ""
                )

                # Get received date
                received_date = message.datetime_received
                if hasattr(received_date, "replace"):
                    received_date = received_date.replace(tzinfo=None)

                mail_content = MailContent(
                    mail_id=str(message.message_id or message.id),
                    subject=message.subject or "",
                    sender=sender_email,
                    team=team,
                    received_date=received_date,
                    week=self.get_week_string(received_date),
                    body_text=body_text,
                    images=self._extract_images(message),
                )

                mails.append(mail_content)
                logger.info(
                    f"Fetched mail: {mail_content.subject[:50]}... "
                    f"({len(mail_content.images)} images)"
                )

            except Exception as e:
                logger.error(f"Failed to process message: {e}")

        logger.info(f"Total mails fetched: {len(mails)}")
        return mails

    def fetch_from_specific_folder(
        self,
        folder_path: str,
        **kwargs,
    ) -> list[MailContent]:
        """
        Fetch mails from a specific folder path.

        Args:
            folder_path: Folder path (e.g., "Weekly Reports/Team A")
            **kwargs: Additional filters passed to fetch_weekly_mails

        Returns:
            List of MailContent objects
        """
        return self.fetch_weekly_mails(folder_name=folder_path, **kwargs)


# Convenience function for quick mail fetching
def fetch_weekly_team_mails(
    subject_filter: str = "주간",
    days_back: int = 7,
) -> list[MailContent]:
    """
    Convenience function to fetch weekly team mails.

    Args:
        subject_filter: Subject filter string
        days_back: Days to look back

    Returns:
        List of MailContent objects
    """
    ingestor = MailIngestor()
    try:
        return ingestor.fetch_weekly_mails(
            subject_filter=subject_filter,
            days_back=days_back,
        )
    finally:
        ingestor.disconnect()

