import hashlib
from collections import defaultdict
from urllib.parse import quote

import httpx


class SourceError(ValueError):
    pass


class OpenSearchSource:
    """Only search/scroll operations; never writes documents or index settings."""

    def __init__(self, settings, transport=None):
        self.settings = settings
        auth = (settings.os_user, settings.os_password) if settings.os_user else None
        self.client = httpx.Client(base_url=settings.os_url or 'http://localhost:9200', auth=auth,
                                   verify=settings.os_verify, timeout=60, transport=transport)

    def close(self):
        self.client.close()

    def _request(self, method, path, **kwargs):
        try:
            resp = self.client.request(method, path, **kwargs)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            code = getattr(getattr(exc, 'response', None), 'status_code', None)
            raise SourceError(f'OpenSearch 조회 실패{f" (HTTP {code})" if code else ""}. 연결 주소·인증·필드 매핑을 확인하세요.') from None
        if data.get('timed_out') or data.get('_shards', {}).get('failed', 0):
            raise SourceError('OpenSearch가 일부 결과만 반환했습니다. 전체 조회를 다시 시도하세요.')
        return data

    def fetch_week(self, week):
        suffix = self.settings.keyword_suffix
        filters = [{'term': {key+suffix: value}} for key, value in
                   [('week', week), ('mail_type', 'weekly_report'), ('type', 'original_part')]]
        fields = ('text','team','week','mail_id','part_index','total_parts','subject','html_path')
        # Explicit vector exclusion avoids a source-fetch 503 on the local k-NN index.
        # Keep the reporting field allowlist below, after retrieval.
        body = dict(size=500, sort=['_doc'], track_total_hits=True,
                    query={'bool': {'filter': filters}},
                    _source={'excludes':['embedding']})
        scroll_id = None
        sources = {}
        expected = None
        try:
            page = self._request('POST', f'/{quote(self.settings.os_index, safe="")}/_search',
                                 params={'scroll': '2m'}, json=body)
            while True:
                scroll_id = page.get('_scroll_id', scroll_id)
                hits = page.get('hits', {})
                if expected is None:
                    total = hits.get('total', {})
                    expected = total.get('value') if isinstance(total, dict) else total
                values = hits.get('hits', [])
                if not values:
                    break
                for hit in values:
                    raw = {key:value for key,value in hit['_source'].items() if key in fields}
                    if not all(key in raw for key in ('text','team','week','mail_id','part_index','total_parts')):
                        raise SourceError('원문에 필수 메타데이터가 없습니다. text/team/week/mail_id/part_index/total_parts를 확인하세요.')
                    if raw['week'] != week:
                        raise SourceError('대상 주차와 다른 원문이 반환되었습니다.')
                    sources[hit['_id']] = dict(raw, id=hit['_id'],
                        content_hash=hashlib.sha256(raw['text'].encode()).hexdigest())
                if not scroll_id:
                    if expected is None or len(sources) < expected:
                        raise SourceError('전체 조회를 이어갈 scroll ID가 없습니다.')
                    break
                page = self._request('POST', '/_search/scroll', json={'scroll':'2m','scroll_id':scroll_id})
            if expected is not None and len(sources) != expected:
                raise SourceError('조회 건수와 원문 수가 일치하지 않습니다. 다시 시도하세요.')
        finally:
            if scroll_id:
                try:
                    self.client.request('DELETE', '/_search/scroll', json={'scroll_id':[scroll_id]})
                except httpx.HTTPError:
                    pass
        return sorted(sources.values(), key=lambda s: (s['team'], s['mail_id'], s['part_index']))


def audit_sources(sources, expected_teams):
    teams = sorted({s['team'] for s in sources})
    missing = sorted(set(expected_teams) - set(teams))
    warnings = [f'주보 미수집 팀: {", ".join(missing)}'] if missing else []
    if not expected_teams:
        warnings.append('대상 팀 목록이 없어 전체 팀의 수집 완료 여부를 확인할 수 없습니다.')
    mails = defaultdict(list)
    for source in sources:
        mails[(source['team'], source['mail_id'])].append(source)
        if not source['text'].strip():
            warnings.append(f'{source["team"]} {source["mail_id"]}: 빈 원문')
    for (team, mail), parts in mails.items():
        counts = {p['total_parts'] for p in parts}
        if len(counts) != 1 or min(counts) < 1:
            warnings.append(f'{team} {mail}: total_parts 값 불일치')
            continue
        indices = [p['part_index'] for p in parts]
        absent = sorted(set(range(next(iter(counts)))) - set(indices))
        if absent:
            warnings.append(f'{team} {mail}: 원문 조각 누락 {absent}')
        if len(indices) != len(set(indices)) or any(i < 0 or i >= next(iter(counts)) for i in indices):
            warnings.append(f'{team} {mail}: 중복 또는 범위 밖 원문 조각')
    return dict(teams=teams, team_count=len(teams), missing_teams=missing,
                source_count=len(sources), mail_count=len(mails)), warnings


def split_source(source, max_bytes):
    if max_bytes < 4:
        raise ValueError('원문 분할 크기는 최소 4바이트여야 합니다.')
    text = source['text']
    chunks, start = [], 0
    while start < len(text):
        size, end = 0, start
        while end < len(text) and size + len(text[end].encode()) <= max_bytes:
            size += len(text[end].encode())
            end += 1
        if end < len(text):
            newline = text.rfind('\n', start + (end-start)//2, end)
            if newline >= 0:
                end = newline + 1
        chunks.append(dict(source, text=text[start:end], chunk_index=len(chunks), offset=start))
        start = end
    return chunks
