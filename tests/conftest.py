"""Keep tests aligned with the declared dependency boundary."""

import langchain_core.globals as langchain_globals


def pytest_sessionstart(session):
    # The shared developer interpreter may contain an undeclared, incompatible
    # full LangChain installation. A clean requirements install contains only
    # langchain-core/langchain-openai/langgraph, so disable the core package's
    # optional legacy-root compatibility path for this test session.
    langchain_globals._HAS_LANGCHAIN = False
