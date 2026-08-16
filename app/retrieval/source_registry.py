from collections.abc import Sequence
from dataclasses import dataclass

from app.config.settings import Settings
from app.domain.agentic import SourceName, ToolName


@dataclass(frozen=True)
class SourceDefinition:
    name: SourceName
    tool: ToolName
    index: str
    description: str


class SourceRegistry:
    def __init__(self, definitions: Sequence[SourceDefinition]):
        self.definitions = tuple(definitions)
        self._by_tool = {item.tool: item for item in self.definitions}

    @classmethod
    def from_settings(cls, settings: Settings) -> "SourceRegistry":
        return cls(
            (
                SourceDefinition(
                    "domain_knowledge",
                    "search_domain_knowledge",
                    settings.domain_knowledge_index,
                    "반도체 수율, 공정, defect 및 domain knowledge",
                ),
                SourceDefinition(
                    "mail",
                    "search_mail",
                    settings.mail_index_alias,
                    "Outlook 메일 본문과 첨부파일",
                ),
                SourceDefinition(
                    "calendar",
                    "search_calendar",
                    settings.calendar_index_alias,
                    "Outlook 일정, 회의, 회의 첨부파일",
                ),
            )
        )

    def index_for(self, tool: ToolName) -> str:
        if tool == "expand_calendar_event":
            return self._by_tool["search_calendar"].index
        return self._by_tool[tool].index

    def source_for(self, tool: ToolName) -> SourceName:
        if tool == "expand_calendar_event":
            return "calendar"
        return self._by_tool[tool].name
