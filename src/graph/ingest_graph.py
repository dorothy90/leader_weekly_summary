"""
LangGraph-based Mail Ingestion Pipeline.
Orchestrates: Mail Fetch → Vision Parse → Preprocess
"""

import logging
from datetime import datetime
from typing import Annotated, Optional, TypedDict

from langgraph.graph import StateGraph, START, END

from src.ingest.mail_ingestor import MailIngestor
from src.parser.vision_parser import VisionParser, BatchVisionParser
from src.processor.preprocessor import Preprocessor
from src.schemas import MailContent, ParsedImage, ProcessedMail

logger = logging.getLogger(__name__)


class IngestGraphState(TypedDict):
    """State for the mail ingestion graph."""

    # Input parameters
    subject_filter: Optional[str]
    days_back: int
    folder_name: str

    # Intermediate data
    raw_mails: list[MailContent]
    parsed_images_map: dict[str, list[ParsedImage]]  # mail_id -> parsed images

    # Output
    processed_mails: list[ProcessedMail]

    # Metadata
    errors: list[str]
    current_step: str
    started_at: Optional[str]
    completed_at: Optional[str]


def fetch_mails_node(state: IngestGraphState) -> dict:
    """
    Node: Fetch emails from EWS.
    """
    logger.info("=== Node: Fetch Mails ===")

    try:
        ingestor = MailIngestor()
        mails = ingestor.fetch_weekly_mails(
            folder_name=state.get("folder_name", "inbox"),
            subject_filter=state.get("subject_filter"),
            days_back=state.get("days_back", 7),
        )
        ingestor.disconnect()

        logger.info(f"Fetched {len(mails)} mails")

        return {
            "raw_mails": mails,
            "current_step": "fetch_complete",
        }

    except Exception as e:
        logger.error(f"Mail fetch failed: {e}")
        return {
            "raw_mails": [],
            "errors": state.get("errors", []) + [f"Mail fetch error: {str(e)}"],
            "current_step": "fetch_failed",
        }


def parse_images_node(state: IngestGraphState) -> dict:
    """
    Node: Parse images using Vision LLM.
    """
    logger.info("=== Node: Parse Images ===")

    raw_mails = state.get("raw_mails", [])
    parsed_images_map: dict[str, list[ParsedImage]] = {}
    errors = state.get("errors", [])

    # Collect all images with their mail IDs
    all_images_with_ids: list[tuple[str, any]] = []
    for mail in raw_mails:
        for image in mail.images:
            all_images_with_ids.append((mail.mail_id, image))

    if not all_images_with_ids:
        logger.info("No images to parse")
        return {
            "parsed_images_map": {},
            "current_step": "parse_complete",
        }

    logger.info(f"Parsing {len(all_images_with_ids)} images")

    try:
        parser = BatchVisionParser(batch_size=3, delay_between_batches=1.0)

        # Parse each image
        for mail_id, image in all_images_with_ids:
            try:
                parsed = parser.parser.parse_image(image)

                if mail_id not in parsed_images_map:
                    parsed_images_map[mail_id] = []
                parsed_images_map[mail_id].append(parsed)

            except Exception as e:
                logger.error(f"Image parse failed ({image.filename}): {e}")
                errors.append(f"Image parse error ({image.filename}): {str(e)}")

        logger.info(f"Parsed images for {len(parsed_images_map)} mails")

        return {
            "parsed_images_map": parsed_images_map,
            "errors": errors,
            "current_step": "parse_complete",
        }

    except Exception as e:
        logger.error(f"Vision parsing failed: {e}")
        return {
            "parsed_images_map": {},
            "errors": errors + [f"Vision parsing error: {str(e)}"],
            "current_step": "parse_failed",
        }


def preprocess_node(state: IngestGraphState) -> dict:
    """
    Node: Preprocess and merge text content.
    """
    logger.info("=== Node: Preprocess ===")

    raw_mails = state.get("raw_mails", [])
    parsed_images_map = state.get("parsed_images_map", {})
    errors = state.get("errors", [])

    processed_mails: list[ProcessedMail] = []
    preprocessor = Preprocessor()

    for mail in raw_mails:
        try:
            # Get parsed images for this mail
            parsed_images = parsed_images_map.get(mail.mail_id, [])

            # Process mail
            processed = preprocessor.process_mail(mail, parsed_images)

            # Deduplicate sentences
            processed.sentences = preprocessor.merge_duplicate_sentences(
                processed.sentences
            )

            processed_mails.append(processed)

        except Exception as e:
            logger.error(f"Preprocess failed for mail {mail.mail_id}: {e}")
            errors.append(f"Preprocess error ({mail.mail_id}): {str(e)}")

    logger.info(f"Preprocessed {len(processed_mails)} mails")

    total_sentences = sum(len(m.sentences) for m in processed_mails)
    logger.info(f"Total sentences: {total_sentences}")

    return {
        "processed_mails": processed_mails,
        "errors": errors,
        "current_step": "preprocess_complete",
        "completed_at": datetime.now().isoformat(),
    }


def should_continue_after_fetch(state: IngestGraphState) -> str:
    """
    Conditional edge: decide whether to continue after fetch.
    """
    if not state.get("raw_mails"):
        logger.warning("No mails fetched, ending pipeline")
        return "end"
    return "continue"


def create_ingest_graph() -> StateGraph:
    """
    Create the mail ingestion graph.

    Flow:
    START → fetch_mails → (check) → parse_images → preprocess → END
                            ↓
                           END (if no mails)

    Returns:
        Compiled StateGraph
    """
    # Create graph
    graph = StateGraph(IngestGraphState)

    # Add nodes
    graph.add_node("fetch_mails", fetch_mails_node)
    graph.add_node("parse_images", parse_images_node)
    graph.add_node("preprocess", preprocess_node)

    # Add edges
    graph.add_edge(START, "fetch_mails")

    # Conditional edge after fetch
    graph.add_conditional_edges(
        "fetch_mails",
        should_continue_after_fetch,
        {
            "continue": "parse_images",
            "end": END,
        },
    )

    graph.add_edge("parse_images", "preprocess")
    graph.add_edge("preprocess", END)

    return graph.compile()


class MailIngestPipeline:
    """
    High-level interface for the mail ingestion pipeline.
    """

    def __init__(self):
        """Initialize the pipeline."""
        self.graph = create_ingest_graph()

    def run(
        self,
        subject_filter: Optional[str] = "주간",
        days_back: int = 7,
        folder_name: str = "inbox",
    ) -> IngestGraphState:
        """
        Run the complete ingestion pipeline.

        Args:
            subject_filter: Filter emails by subject
            days_back: Number of days to look back
            folder_name: Mail folder to search

        Returns:
            Final state with processed mails
        """
        initial_state: IngestGraphState = {
            "subject_filter": subject_filter,
            "days_back": days_back,
            "folder_name": folder_name,
            "raw_mails": [],
            "parsed_images_map": {},
            "processed_mails": [],
            "errors": [],
            "current_step": "init",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
        }

        logger.info("Starting mail ingestion pipeline")

        # Run the graph
        final_state = self.graph.invoke(initial_state)

        logger.info(
            f"Pipeline complete. "
            f"Mails: {len(final_state.get('processed_mails', []))}, "
            f"Errors: {len(final_state.get('errors', []))}"
        )

        return final_state

    async def arun(
        self,
        subject_filter: Optional[str] = "주간",
        days_back: int = 7,
        folder_name: str = "inbox",
    ) -> IngestGraphState:
        """
        Run the pipeline asynchronously.
        """
        initial_state: IngestGraphState = {
            "subject_filter": subject_filter,
            "days_back": days_back,
            "folder_name": folder_name,
            "raw_mails": [],
            "parsed_images_map": {},
            "processed_mails": [],
            "errors": [],
            "current_step": "init",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
        }

        logger.info("Starting mail ingestion pipeline (async)")

        final_state = await self.graph.ainvoke(initial_state)

        return final_state


# For testing with mock data
class MockMailIngestPipeline:
    """
    Mock pipeline for testing without actual EWS connection.
    """

    def __init__(self):
        self.preprocessor = Preprocessor()
        self.vision_parser = VisionParser()

    def run_with_mock_data(
        self,
        mock_mails: list[MailContent],
    ) -> list[ProcessedMail]:
        """
        Run pipeline with mock mail data.

        Args:
            mock_mails: List of mock MailContent objects

        Returns:
            List of ProcessedMail objects
        """
        processed = []

        for mail in mock_mails:
            # Parse images if any
            parsed_images = []
            for image in mail.images:
                parsed = self.vision_parser.parse_image(image)
                parsed_images.append(parsed)

            # Preprocess
            processed_mail = self.preprocessor.process_mail(mail, parsed_images)
            processed_mail.sentences = self.preprocessor.merge_duplicate_sentences(
                processed_mail.sentences
            )
            processed.append(processed_mail)

        return processed

