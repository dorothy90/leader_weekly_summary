from time import perf_counter
import uuid

from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.domain.research import ResearchStatus
from app.observability.tracing import TraceEvent, emit_trace, hash_trace_value


class ResearchWorker:
    def __init__(self, jobs, workflow, lease_seconds=180, trace_sink=None):
        self.jobs = jobs
        self.workflow = workflow
        self.lease_seconds = lease_seconds
        self.trace_sink = trace_sink

    async def _record_failure(self, job, policy, error_code):
        try:
            await self.jobs.fail(job.job_id, policy, job.lease_token, error_code)
        except AppError as transition_error:
            if transition_error.code == ErrorCode.JOB_CANCELLED:
                await self.jobs.mark_cancelled(job.job_id, policy, job.lease_token)
            elif transition_error.code != ErrorCode.UNAUTHORIZED_RESOURCE:
                raise

    async def run_once(self) -> bool:
        started = perf_counter()
        trace = {
            "status": "ok",
            "owner_hash": None,
            "job_hash": None,
            "attempt": 0,
            "error_class": None,
        }
        try:
            return await self._run_once(trace)
        except Exception as error:
            trace["status"] = "error"
            trace["error_class"] = type(error).__name__
            raise
        finally:
            emit_trace(
                self.trace_sink,
                TraceEvent(
                    trace_id=uuid.uuid4().hex,
                    node_name="research_worker.run_once",
                    duration_ms=int((perf_counter() - started) * 1000),
                    status=trace["status"],
                    index_version_hash=hash_trace_value("none"),
                    prompt_version_hash=hash_trace_value("deep-v1"),
                    model_hash=hash_trace_value(
                        str(
                            getattr(
                                getattr(self.workflow, "llm", None),
                                "model",
                                "none",
                            )
                        )
                    ),
                    owner_hash=trace["owner_hash"],
                    job_hash=trace["job_hash"],
                    attempt=min(100, trace["attempt"]),
                    route="deep",
                    error_class=trace["error_class"],
                ),
            )

    async def _run_once(self, trace) -> bool:
        job = await self.jobs.claim(self.lease_seconds)
        if job is None:
            return False
        trace["owner_hash"] = hash_trace_value(job.user_id)
        trace["job_hash"] = hash_trace_value(job.job_id)
        trace["attempt"] = int(getattr(job, "attempts", 0) or 0)
        policy = PolicyContext.from_user_id(job.user_id)
        current = await self.jobs.get(job.job_id, policy)
        if current.status == ResearchStatus.CANCELLING:
            trace["status"] = "cancelled"
            await self.jobs.mark_cancelled(job.job_id, policy, job.lease_token)
            return True
        try:
            result = await self.workflow.invoke(job.question, policy, job.filters)
            current = await self.jobs.get(job.job_id, policy)
            if current.status == ResearchStatus.CANCELLING:
                trace["status"] = "cancelled"
                await self.jobs.mark_cancelled(job.job_id, policy, job.lease_token)
            else:
                await self.jobs.complete(job.job_id, policy, job.lease_token, result)
        except AppError as error:
            trace["error_class"] = type(error).__name__
            if error.code == ErrorCode.JOB_CANCELLED:
                trace["status"] = "cancelled"
                await self.jobs.mark_cancelled(job.job_id, policy, job.lease_token)
            elif error.code == ErrorCode.UNAUTHORIZED_RESOURCE:
                # A reclaimed lease makes this worker stale. Never mutate the
                # replacement worker's job state.
                return True
            else:
                trace["status"] = "error"
                await self._record_failure(job, policy, error.code)
        except Exception as error:
            trace["status"] = "error"
            trace["error_class"] = type(error).__name__
            await self._record_failure(job, policy, "RESEARCH_FAILED")
        return True
