from app.domain.evidence import RetrievalFilters
from app.domain.policy import PolicyContext


def build_owner_filters(
    policy: PolicyContext,
    facets: RetrievalFilters,
) -> list[dict]:
    filters: list[dict] = [{"term": {"user_id": policy.user_id}}]
    if facets.teams:
        filters.append({"terms": {"team": facets.teams}})
    if facets.weeks:
        filters.append({"terms": {"week": facets.weeks}})
    if facets.mail_type:
        filters.append({"term": {"mail_type": facets.mail_type}})
    return filters
