"""
RAG Chatbot API Server (OpenSearch 버전) - v3 Naive RAG + LLM Router
모듈 구조:
- core.py: 설정, 클라이언트, 스키마, 유틸리티, MongoDB
- graph.py: LangGraph 노드, 그래프, Tools
- main.py: FastAPI 앱, 엔드포인트
"""

from .core import (
    # 설정
    OPENSEARCH_HOST,
    OPENSEARCH_PORT,
    INDEX_NAME,
    TEAMS,
    MAX_HISTORY_TURNS,
    # 클라이언트
    OpenSearchClient,
    get_llm,
    # 스키마
    Reference,
    ChatV2Request,
    ChatV2Response,
    ToolCallInfo,
    ToolResultInfo,
    # MongoDB
    init_mongodb,
    close_mongodb,
    save_full_log,
    save_history,
    get_history,
)

from .graph import (
    get_naive_rag_graph,
    chat_with_agent,
)

__all__ = [
    # core
    "OPENSEARCH_HOST",
    "OPENSEARCH_PORT",
    "INDEX_NAME",
    "TEAMS",
    "MAX_HISTORY_TURNS",
    "OpenSearchClient",
    "get_llm",
    "Reference",
    "ChatV2Request",
    "ChatV2Response",
    "ToolCallInfo",
    "ToolResultInfo",
    "init_mongodb",
    "close_mongodb",
    "save_full_log",
    "save_history",
    "get_history",
    # graph
    "get_naive_rag_graph",
    "chat_with_agent",
]
