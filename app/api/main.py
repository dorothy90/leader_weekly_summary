import uuid
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes.chat import router as chat_router
from app.api.routes.content import router as content_router
from app.api.routes.health import router as health_router
from app.api.routes.research import router as research_router
from app.domain.errors import AppError, ErrorCode
from app.observability.tracing import TraceEvent, emit_trace, hash_trace_value
from app.observability.node_runs import ensure_node_recorder

_SAFE_MESSAGES = {
    ErrorCode.INVALID_USER_ID: "요청을 확인할 수 없습니다.",
    ErrorCode.UNAUTHORIZED_RESOURCE: "요청한 리소스를 찾을 수 없습니다.",
    ErrorCode.INDEX_UNAVAILABLE: "검색 서비스를 현재 사용할 수 없습니다.",
    ErrorCode.EMBEDDING_UNAVAILABLE: "임베딩 서비스를 현재 사용할 수 없습니다.",
    ErrorCode.RETRIEVAL_TIMEOUT: "검색 요청 시간이 초과되었습니다.",
    ErrorCode.NO_EVIDENCE: "확인 가능한 근거가 없습니다.",
    ErrorCode.BUDGET_EXCEEDED: "요청 처리 한도를 초과했습니다.",
    ErrorCode.JOB_CANCELLED: "조사 작업이 취소되었습니다.",
    ErrorCode.CONVERSATION_CONFLICT: (
        "대화가 동시에 갱신되었습니다. 다시 시도해주세요."
    ),
    ErrorCode.DEPENDENCY_UNAVAILABLE: "요청한 서비스를 현재 사용할 수 없습니다.",
}
_STATUS_CODES = {
    ErrorCode.INVALID_USER_ID: 422,
    ErrorCode.UNAUTHORIZED_RESOURCE: 404,
    ErrorCode.INDEX_UNAVAILABLE: 503,
    ErrorCode.EMBEDDING_UNAVAILABLE: 503,
    ErrorCode.RETRIEVAL_TIMEOUT: 504,
    ErrorCode.NO_EVIDENCE: 404,
    ErrorCode.BUDGET_EXCEEDED: 429,
    ErrorCode.JOB_CANCELLED: 409,
    ErrorCode.CONVERSATION_CONFLICT: 409,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
}


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", uuid.uuid4().hex)


def _error_response(request, status_code, code, message, retryable=False):
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
            },
            "trace_id": _trace_id(request),
        },
    )


def create_app(container) -> FastAPI:
    app = FastAPI(
        title="Mail Research RAG",
        version="1.0.0",
        description=(
            "This service trusts a verified user_id in each request body. "
            "An upstream gateway is responsible for authentication and for "
            "binding its authenticated principal to that field."
        ),
    )
    app.state.container = container

    @app.middleware("http")
    async def trace_requests(request: Request, call_next):
        request.state.trace_id = uuid.uuid4().hex
        started = perf_counter()
        status = "ok"
        error_class = None
        try:
            with ensure_node_recorder():
                response = await call_next(request)
            if response.status_code >= 500:
                status = "error"
            response.headers["x-trace-id"] = request.state.trace_id
            return response
        except Exception as error:
            status = "error"
            error_class = type(error).__name__
            raise
        finally:
            emit_trace(
                getattr(container, "traces", None),
                TraceEvent(
                    trace_id=request.state.trace_id,
                    node_name="api.request",
                    duration_ms=int((perf_counter() - started) * 1000),
                    status=status,
                    index_version_hash=hash_trace_value("none"),
                    prompt_version_hash=hash_trace_value("api-v1"),
                    model_hash=hash_trace_value("none"),
                    error_class=error_class,
                ),
            )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError):
        return _error_response(
            request,
            _STATUS_CODES.get(error.code, 500),
            error.code.value,
            _SAFE_MESSAGES.get(error.code, "요청을 처리할 수 없습니다."),
            error.retryable,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, _error):
        return _error_response(
            request,
            422,
            "INVALID_REQUEST",
            "요청을 확인할 수 없습니다.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, error: StarletteHTTPException):
        if error.status_code == 404:
            return _error_response(
                request,
                404,
                ErrorCode.UNAUTHORIZED_RESOURCE.value,
                _SAFE_MESSAGES[ErrorCode.UNAUTHORIZED_RESOURCE],
            )
        return await http_exception_handler(request, error)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _error):
        return _error_response(
            request,
            500,
            "INTERNAL_ERROR",
            "요청을 처리할 수 없습니다.",
            retryable=True,
        )

    app.include_router(chat_router)
    app.include_router(content_router)
    app.include_router(research_router)
    app.include_router(health_router)
    return app
