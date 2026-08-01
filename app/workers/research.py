from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.domain.research import ResearchStatus


class ResearchWorker:
    def __init__(self, jobs, workflow, lease_seconds=180):
        self.jobs = jobs
        self.workflow = workflow
        self.lease_seconds = lease_seconds

    async def _record_failure(self, job, policy, error_code):
        try:
            await self.jobs.fail(job.job_id, policy, job.lease_token, error_code)
        except AppError as transition_error:
            if transition_error.code == ErrorCode.JOB_CANCELLED:
                await self.jobs.mark_cancelled(job.job_id, policy, job.lease_token)
            elif transition_error.code != ErrorCode.UNAUTHORIZED_RESOURCE:
                raise

    async def run_once(self) -> bool:
        job = await self.jobs.claim(self.lease_seconds)
        if job is None:
            return False
        policy = PolicyContext.from_user_id(job.user_id)
        current = await self.jobs.get(job.job_id, policy)
        if current.status == ResearchStatus.CANCELLING:
            await self.jobs.mark_cancelled(job.job_id, policy, job.lease_token)
            return True
        try:
            result = await self.workflow.invoke(job.question, policy, job.filters)
            current = await self.jobs.get(job.job_id, policy)
            if current.status == ResearchStatus.CANCELLING:
                await self.jobs.mark_cancelled(job.job_id, policy, job.lease_token)
            else:
                await self.jobs.complete(job.job_id, policy, job.lease_token, result)
        except AppError as error:
            if error.code == ErrorCode.JOB_CANCELLED:
                await self.jobs.mark_cancelled(job.job_id, policy, job.lease_token)
            elif error.code == ErrorCode.UNAUTHORIZED_RESOURCE:
                # A reclaimed lease makes this worker stale. Never mutate the
                # replacement worker's job state.
                return True
            else:
                await self._record_failure(job, policy, error.code)
        except Exception:
            await self._record_failure(job, policy, "RESEARCH_FAILED")
        return True
