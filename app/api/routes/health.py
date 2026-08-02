from fastapi import APIRouter, Request, Response, status

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response):
    readiness = getattr(request.app.state.container, "readiness", None)
    if readiness is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "dependencies": {}}
    dependencies = await readiness.check()
    ready_now = bool(dependencies) and all(
        value == "ready" for value in dependencies.values()
    )
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready_now else "not_ready",
        "dependencies": dependencies,
    }
