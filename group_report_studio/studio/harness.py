import hashlib
import json
from collections import defaultdict

from . import prompts
from .debug import DebugLog
from .llm import LLMFormatError, LLMOutputLimitError, json_size
from .models import EditPlan, EventSelection, Extraction, LineExtraction, ReferenceStyle, SectionContent, Verification
from .source import audit_sources, split_source


class Cancelled(Exception):
    pass


class EvidenceError(ValueError):
    pass


def validate_content(content, facts):
    result = SectionContent.model_validate(content).model_dump()
    known = {f['id'] for f in facts}
    blocks = []
    for block in result['blocks']:
        if not block['evidence_ids'] or not set(block['evidence_ids']).issubset(known):
            raise EvidenceError('작성 결과에 유효하지 않은 근거가 있어 저장하지 않았습니다.')
        if block['kind'] != 'table':
            blocks.append(block)
            continue
        # Retain every header/value and citation; verify these converted sentences below.
        if block['text']:
            blocks.append(dict(kind='paragraph',text=block['text'],evidence_ids=block['evidence_ids']))
        for row in block['rows']:
            blocks.append(dict(kind='bullet',text='; '.join(f'{header}: {value}' for header,value in zip(block['headers'],row)),
                               evidence_ids=block['evidence_ids']))
        if not block['rows']:
            blocks.append(dict(kind='paragraph',text='; '.join(block['headers']),evidence_ids=block['evidence_ids']))
    result['blocks'] = blocks
    return SectionContent.model_validate(result).model_dump()


class Harness:
    def __init__(self, store, source, llm, settings):
        self.store, self.source, self.llm, self.settings = store, source, llm, settings
        self.debug = DebugLog(settings)
        self.debug.purge()

    def run(self, job_id):
        job = self.store.job(job_id)
        if job['status'] not in ('queued', 'running'):
            return
        self.store.update_job(job_id, status='running', error=None)
        try:
            report = self.store.get(job['report_id'])
            if report.get('last_job_id') == job_id:
                self.store.update_job(job_id, status='succeeded', progress=100, message='저장 완료')
                return
            if report['version'] != job['payload']['base_version']:
                raise ValueError('기준 버전이 변경되었습니다. 최신 주보에서 다시 요청하세요.')
            changes = self.generate(job_id, report) if job['kind'] == 'generate' else self.edit(job_id, report, job['payload'])
            self.check_cancel(job_id)
            changes.update(status='draft',last_job_id=job_id)
            self.validate_report(dict(report,**changes))
            self.store.save(report['id'], report['version'], changes,
                            '초안 생성' if job['kind']=='generate' else job['payload']['message'][:120])
            self.store.update_job(job_id,status='succeeded',progress=100,message='주보에 반영했습니다.')
        except Cancelled:
            self.store.update_job(job_id,status='cancelled',message='작업을 취소했습니다.')
        except Exception as exc:
            # Never expose request headers, provider response bodies, or source text in failures.
            message = str(exc) if isinstance(exc, ValueError) else '작업 중 오류가 발생했습니다. 저장된 단계부터 다시 시도하세요.'
            self.store.update_job(job_id,status='failed',error=message[:1000],message='작업을 완료하지 못했습니다.')

    @staticmethod
    def validate_report(report):
        facts = {f['id']:f for f in report['facts']}
        sources = {s['id'] for s in report['sources']}
        for section in report['sections']:
            for block in section['blocks']:
                for id_ in block['evidence_ids']:
                    if id_ not in facts or facts[id_]['source_id'] not in sources:
                        raise ValueError('문서의 근거 연결이 유효하지 않아 저장하지 않았습니다.')

    def check_cancel(self, job_id):
        if self.store.job(job_id).get('cancelled'):
            raise Cancelled()

    def checkpoint(self, job_id, key, build):
        self.check_cancel(job_id)
        cache = self.store.job(job_id)['cache']
        if key in cache:
            return cache[key]
        value = build()
        self.check_cancel(job_id)
        cache = self.store.job(job_id)['cache']
        cache[key] = value
        self.store.update_job(job_id,cache=cache)
        return value

    def call(self, job_id, stage, system, payload, schema, validate=None, depth=0):
        signature = dict(payload=payload,system=system,schema=schema.model_json_schema(),model=self.settings.llm_model)
        key = stage+':'+hashlib.sha256(json.dumps(signature,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        def build():
            attempt_payload = payload
            writing = stage in ('write','edit','reduce')
            attempts = 3 if stage in ('extract','extract_lines','write','edit','reduce') and validate else 1
            for attempt in range(attempts):
                self.check_cancel(job_id)
                try:
                    result = schema.model_validate(self.llm.complete(stage,system,attempt_payload,schema)).model_dump()
                except LLMOutputLimitError:
                    return recover_limit()
                except LLMFormatError:
                    if stage == 'write':
                        return self.source_excerpt(payload)
                    raise
                try:
                    if validate:
                        validate(result)
                except ValueError as exc:
                    if isinstance(exc,LLMOutputLimitError):
                        return recover_limit()
                    if isinstance(exc,LLMFormatError) and stage == 'write':
                        return self.source_excerpt(payload)
                    if writing and not isinstance(exc,EvidenceError):
                        raise
                    if attempt == attempts-1:
                        if attempts == 1:
                            raise
                        if stage == 'extract':
                            result = self.extract_by_lines(job_id,payload)
                            validate(result)
                            return result
                        if stage == 'write':
                            return self.source_excerpt(payload)
                        raise ValueError(f'원문 근거 검증에 3회 실패했습니다: {exc} 완료된 검토 결과는 보존되어 있습니다.') from None
                    if writing:
                        attempt_payload = dict(payload,rejected_draft=result,validation_feedback=str(exc)+
                            ' 검증에서 지적된 주장을 원문 근거 범위 안에서 다시 작성하세요. 관찰을 필요성·계획·원인으로 확대하지 마세요.'
                            ' 근거 있는 제품·수치·기간·조건·조치와 evidence_ids는 유지하고 모든 내용을 다시 검증받아야 합니다.')
                        continue
                    rule = ('start_line과 end_line은 제공된 줄 번호 범위에서 선택하세요.' if stage=='extract_lines' else
                            'quote는 source.text의 연속된 구절을 그대로 복사하세요.')
                    attempt_payload = dict(payload,validation_feedback=str(exc)+rule+
                        ' section_ids는 template의 id만 사용하세요. 모든 사실을 다시 추출하되 수치나 내용을 임의로 수정하거나 누락하지 마세요.')
                    continue
                return result
        def recover_limit():
            field = 'facts' if stage=='write' else 'numbered_lines' if stage=='extract_lines' else None
            items = payload.get(field,[]) if field else []
            if field and len(items)>1 and depth<6:
                middle = len(items)//2
                parts = []
                for part in (items[:middle],items[middle:]):
                    child = dict(payload,**{field:part})
                    if stage=='extract_lines':
                        child['context_lines'] = payload.get('context_lines',items)
                    parts.append(self.call(job_id,stage,system,child,schema,validate,depth+1))
                if stage=='extract_lines':
                    return {'facts':[fact for part in parts for fact in part['facts']]}
                return {'blocks':[block for part in parts for block in part['blocks']],
                        'warnings':list(dict.fromkeys(w for part in parts for w in part['warnings']))}
            if stage=='write':
                return self.source_excerpt(payload)
            raise LLMOutputLimitError(f'{stage}: 출력 초과 자동 분할 한계에 도달했습니다. 원문 분할 크기와 사내 모델 출력 제한을 확인하세요. 완료된 결과는 보존됩니다.')
        with self.debug.call(job_id,stage,payload,depth):
            return self.checkpoint(job_id,key,build)

    @staticmethod
    def source_excerpt(payload):
        blocks = []
        for fact in payload['facts']:
            label = f"{fact['week']} {fact['team']} 원문"
            if fact.get('source_kind') == 'prior':
                label += ' · 금주 미확인'
            elif fact.get('source_kind') == 'user':
                label += ' · 사용자 정정'
            blocks.append(dict(kind='bullet',text=f"[{label}] {fact['quote']}",evidence_ids=[fact['id']]))
        return validate_content(dict(blocks=blocks,warnings=[
            '요약 생성·검증에 반복 실패하여 해당 항목을 원문 발췌로 제공합니다. 검토 후 다듬어 주세요.'
        ]),payload['facts'])

    def extract_by_lines(self, job_id, payload):
        source = payload['source']
        # Every numbered unit is a contiguous source span, including original whitespace.
        lines = [line[i:i+1000] for line in source['text'].splitlines(keepends=True)
                 for i in range(0,len(line),1000)]
        valid_ids = {s['id'] for s in payload['template']['sections']}
        def convert(value):
            facts = []
            for fact in value['facts']:
                start,end = fact['start_line'],fact['end_line']
                if not 0 <= start <= end < len(lines):
                    raise ValueError('선택한 원문 줄 번호가 범위를 벗어났습니다.')
                if not set(fact['section_ids']).issubset(valid_ids):
                    raise ValueError('선택한 소주제 ID가 template에 없습니다.')
                quote = ''.join(lines[start:end+1]).strip()
                if not quote or len(quote)>4000:
                    raise ValueError('원문 범위는 비어 있지 않은 4000자 이하 구절로 나누어 선택하세요.')
                facts.append(dict(text=quote[:2500],quote=quote,section_ids=fact['section_ids']))
            return Extraction.model_validate({'facts':facts}).model_dump()
        system = prompts.BASE+'''원문의 사실을 빠짐없이 골라 관련 소주제에 연결하세요.
인용문을 작성하지 말고 numbered_lines의 start_line, end_line 번호만 반환하세요. 양 끝 줄을 포함합니다.
제품·수치·집계기간·조건·조치·일정의 문맥을 함께 선택하세요. 서로 떨어진 구절은 별개 facts로 선택하세요.
section_ids는 template의 id만 사용하고 관련 항목이 없으면 빈 목록을 사용하세요.
원문이 길면 구절별로 나누세요. 번호 범위는 4000자 이하여야 합니다. 입력에 있는 사실을 임의로 생략하지 마세요.'''
        system += '\ncontext_lines가 있으면 제품·기간·조건의 문맥 확인용입니다. numbered_lines에 있는 사실을 추출하되 문맥에 필요한 인접 줄을 함께 인용할 수 있습니다.'
        value = self.call(job_id,'extract_lines',system,dict(template=payload['template'],
            source={k:v for k,v in source.items() if k!='text'},
            numbered_lines=[dict(line=i,text=line) for i,line in enumerate(lines)]),LineExtraction,convert)
        return convert(value)

    def extract(self, job_id, sources, template):
        facts = []
        valid_ids = {s['id'] for s in template['sections']}
        chunks = [chunk for source in sources for chunk in split_source(source,self.settings.source_chunk_bytes)]
        for i, chunk in enumerate(chunks):
            self.store.update_job(job_id,progress=10+int(45*i/max(1,len(chunks))),message=f'원문 검토 {i+1}/{len(chunks)}')
            payload = dict(template=template,source=chunk)
            result = Extraction.model_validate(self.extract_by_lines(job_id,payload))
            for f in result.facts:
                if f.quote not in chunk['text'] or not set(f.section_ids).issubset(valid_ids):
                    raise ValueError('추출된 인용문 또는 소주제 연결이 원문과 일치하지 않습니다. 다시 시도하세요.')
                fid = hashlib.sha256((chunk['id']+'|'+str(chunk['offset'])+'|'+f.quote).encode()).hexdigest()[:24]
                facts.append(dict(id=fid, text=f.quote, quote=f.quote, source_id=chunk['id'],
                                  section_ids=f.section_ids, week=chunk['week'],team=chunk['team'],
                                  source_kind=chunk.get('source_kind','current')))
        unique = {}
        for f in facts:
            if f['id'] in unique:
                f['section_ids'] = sorted(set(f['section_ids']) | set(unique[f['id']]['section_ids']))
            unique[f['id']] = f
        return list(unique.values())

    def write_section(self, job_id, spec, facts, report, current=None, instruction=''):
        if not facts:
            return dict(**spec, blocks=[dict(kind='paragraph',text='금주 관련 자료 미보고. 확인 필요.',headers=[],rows=[],evidence_ids=[])],
                        warnings=['해당 소주제의 근거를 찾지 못했습니다.'])
        base = dict(section=spec,week=report['week'],instruction=instruction,
                    reference_style=report.get('reference_style',[]))
        # Leave room for prompts, schema, and verification. Never truncate the input silently.
        budget = max(2000,self.settings.max_input_bytes//3-json_size(base))
        batches, batch, used = [], [], 0
        for fact in facts:
            size = json_size(fact)
            if size > budget:
                raise ValueError('하나의 근거가 입력 한도를 초과합니다. 원문 분할 크기를 줄이세요.')
            if batch and used+size > budget:
                batches.append(batch)
                batch, used = [], 0
            batch.append(fact)
            used += size
        if batch:
            batches.append(batch)
        sections = []
        pending_blocks = list((current or {}).get('blocks',[]))
        for batch_index,batch in enumerate(batches):
            batch_ids = {f['id'] for f in batch}
            current_blocks = [b for b in pending_blocks if batch_ids.intersection(b['evidence_ids']) or not b['evidence_ids'] or batch_index==len(batches)-1]
            pending_blocks = [b for b in pending_blocks if b not in current_blocks]
            fragment = dict(blocks=current_blocks,warnings=(current or {}).get('warnings',[]))
            payload = dict(base,facts=batch,current=fragment)
            stage = 'edit' if current else 'write'
            allowed_ids = batch_ids | {id_ for b in current_blocks for id_ in b['evidence_ids']}
            allowed = [f for f in facts if f['id'] in allowed_ids]
            def validate_written(value):
                value.update(validate_content(value,allowed))
                self.verify(job_id,value,batch,previous_blocks=current_blocks)
            raw = self.call(job_id,stage,prompts.EDIT if current else prompts.WRITE,payload,SectionContent,validate_written)
            result = validate_content(raw,allowed)
            sections.append(result)
        if spec['id']=='events':
            return dict(**spec,**self.select_events(job_id,sections))
        # Assemble verified drafts without asking the model to emit the whole section again.
        return dict(**spec,blocks=[b for part in sections for b in part['blocks']],
                    warnings=list(dict.fromkeys(w for part in sections for w in part['warnings'])))

    def verify(self, job_id, content, facts, previous_blocks=None):
        by_id = {f['id']:f for f in facts}
        for block in content['blocks']:
            related = [by_id[id_] for id_ in block['evidence_ids'] if id_ in by_id]
            previous = [b for b in (previous_blocks or []) if set(b['evidence_ids']).intersection(block['evidence_ids'])]
            payload = dict(blocks=[block],facts=related,previous_verified_blocks=previous)
            # Cache negative verdicts too: an unchanged draft must be revised, not rejudged until it passes.
            result = self.call(job_id,'verify',prompts.VERIFY,payload,Verification)
            if not result['supported']:
                raise EvidenceError('근거 검증 실패: '+'; '.join(result['issues'] or ['원문과 작성 내용이 일치하지 않습니다.']))

    def select_events(self,job_id,sections):
        blocks = [b for part in sections for b in part['blocks']]
        system = prompts.BASE+'검증된 candidates에서 서로 다른 가장 중요한 이벤트 최대 3건의 인덱스를 indices로 반환하세요. 문장을 다시 작성하지 않습니다.'
        while len(blocks)>3:
            groups, group = [], []
            for block in blocks:
                if group and json_size(group+[block])>self.settings.max_input_bytes//2:
                    groups.append(group); group=[]
                group.append(block)
            if group:
                groups.append(group)
            selected=[]
            for group in groups:
                if len(group)<=3:
                    selected.extend(group); continue
                def validate_selection(value):
                    indices=value['indices']
                    if not indices or len(set(indices))!=len(indices) or any(i<0 or i>=len(group) for i in indices):
                        raise ValueError('핵심 이벤트 선택 결과가 올바르지 않습니다.')
                result=self.call(job_id,'select_events',system,{'candidates':group},EventSelection,validate_selection)
                selected.extend(group[i] for i in result['indices'])
            if len(selected)>=len(blocks):
                raise ValueError('핵심 이벤트 후보 한 건이 너무 깁니다. 이벤트 작성 지침에 짧은 분량을 지정하세요.')
            blocks=selected
        return dict(blocks=blocks,warnings=list(dict.fromkeys(w for s in sections for w in s['warnings'])))

    def reference_style(self,job_id,references):
        styles=[]
        system=prompts.BASE+'이전 그룹 주보의 목차, 표/문장 형태, 문체, 분량 특징을 5개 이하의 짧은 rules로 추출하세요. 각 규칙 200자 이하. 과거 수치/현상은 규칙에 포함하지 마세요.'
        for ref in references:
            for chunk in split_source({'text':ref['text']},self.settings.source_chunk_bytes):
                result=self.call(job_id,'reference_style',system,{'text':chunk['text']},ReferenceStyle)
                for rule in result['rules']:
                    if rule not in styles:
                        styles.append(rule)
                # This is a style sample, not factual ingestion; all reference facts are extracted separately.
                if len(styles)>=10:
                    return styles[:10]
        return styles

    def generate(self, job_id, report, template=None, snapshot=None):
        template = template or report['template']
        sources = snapshot if snapshot is not None else self.checkpoint(job_id,'sources',lambda:self.source.fetch_week(report['week']))
        current_sources = [s for s in sources if s.get('source_kind','current')=='current']
        if not current_sources:
            raise ValueError('해당 주차의 weekly_report 원문이 없습니다. 주차와 색인 필터를 확인하세요.')
        coverage,warnings = audit_sources(current_sources,report.get('expected_teams',[]))
        if len(report.get('references',[]))<2:
            warnings.append('이전 그룹 주보가 2건보다 적습니다. 등록된 참고 자료만 사용했습니다.')
        if snapshot is None:
            sources = list(sources)
            for i, ref in enumerate(report.get('references',[])):
                sources.append(dict(id=f'reference_{i}',team='이전 그룹 주보',mail_id=ref['name'],part_index=0,total_parts=1,
                                    text=ref['text'],week=ref['week'],source_kind='prior'))
        facts = self.extract(job_id,sources,template)
        style = self.reference_style(job_id,report.get('references',[]))
        report = dict(report,reference_style=style)
        sections_by_id = {}
        # Key events are generated last and include otherwise unclassified facts.
        specs = sorted(template['sections'],key=lambda s:s['id']=='events')
        for i,spec in enumerate(specs):
            self.store.update_job(job_id,progress=55+int(40*i/len(specs)),message=f'{spec["title"]} 작성·검증 중')
            related = [f for f in facts if spec['id'] in f['section_ids'] or spec['id']=='events']
            sections_by_id[spec['id']] = self.write_section(job_id,spec,related,report)
        unmapped = sum(1 for f in facts if not f['section_ids'] and f['source_kind']=='current')
        if unmapped:
            warnings.append(f'기본 소주제 밖의 사실 {unmapped}건을 핵심 이벤트 후보로 검토했습니다.')
        return dict(template=template,sections=[sections_by_id[s['id']] for s in template['sections']],
                    sources=sources,facts=facts,coverage=coverage,warnings=warnings,reference_style=style)

    def edit(self,job_id,report,payload):
        if not report['sections']:
            raise ValueError('먼저 초안을 생성하세요.')
        selection = payload.get('section_id')
        known = {s['id'] for s in report['sections']}
        if selection and selection not in known:
            raise ValueError('선택한 소주제가 없습니다. 최신 주보를 확인하세요.')
        plan_payload = dict(message=payload['message'],selected_section_id=selection,template=report['template'],
                            history=report.get('messages',[])[-6:])
        def validate_plan(value):
            for assertion in value['user_assertions']:
                if assertion not in payload['message']:
                    raise ValueError('사용자 정정 내용이 실제 요청과 일치하지 않습니다. 정정할 사실을 그대로 적어 주세요.')
        plan = EditPlan.model_validate(self.call(job_id,'plan_edit',prompts.PLAN,plan_payload,EditPlan,validate_plan))
        messages = report.get('messages',[]) + [dict(role='user',content=payload['message'])]
        if plan.action=='clarify':
            messages.append(dict(role='assistant',content=plan.question or '수정할 항목과 원하는 내용을 조금 더 구체적으로 알려주세요.'))
            return dict(messages=messages)
        if plan.action=='template':
            if not plan.template:
                raise ValueError('변경된 양식이 없습니다.')
            updated = plan.template.model_dump()
            old_specs = {s['id']:s for s in report['template']['sections']}
            new_specs = {s['id']:s for s in updated['sections']}
            affected = {id_ for id_ in old_specs.keys()|new_specs.keys() if old_specs.get(id_)!=new_specs.get(id_)}
            reordered = [s['id'] for s in updated['sections']] != [s['id'] for s in report['template']['sections']]
            if selection and (affected-{selection} or reordered):
                raise ValueError('목차 변경이 선택한 항목 범위를 벗어납니다. 전체 문서를 선택해 주세요.')
            regenerate = {id_ for id_,spec in new_specs.items() if id_ not in old_specs or spec['instructions']!=old_specs[id_]['instructions']}
            facts = report['facts']
            if regenerate:
                extracted = self.extract(job_id,report['sources'],updated)
                merged_facts = {f['id']:dict(f,section_ids=[id_ for id_ in f['section_ids'] if id_ in new_specs]) for f in report['facts']}
                for fact in extracted:
                    existing_ids=merged_facts.get(fact['id'],{}).get('section_ids',[])
                    merged_facts[fact['id']]=dict(fact,section_ids=sorted(set(existing_ids)|set(fact['section_ids'])))
                facts=list(merged_facts.values())
            sections=[]
            existing = {s['id']:s for s in report['sections']}
            for spec in updated['sections']:
                if spec['id'] in regenerate:
                    related=[f for f in facts if spec['id'] in f['section_ids'] or spec['id']=='events']
                    sections.append(self.write_section(job_id,spec,related,report))
                else:
                    sections.append(dict(existing[spec['id']],**spec))
            changes=dict(template=updated,sections=sections,facts=facts)
            messages.append(dict(role='assistant',content='목차 변경을 반영했습니다. 재사용하려면 양식으로 저장하세요.'))
            changes['messages'] = messages
            return changes
        targets = set(plan.section_ids)
        if not targets or not targets.issubset(known):
            raise ValueError('수정 대상이 올바르지 않습니다. 항목을 선택한 뒤 다시 요청하세요.')
        if selection and targets != {selection}:
            raise ValueError('선택 범위 밖의 수정이 요청되었습니다. 전체 주보를 선택한 뒤 다시 요청하세요.')
        facts, sources = list(report['facts']), list(report['sources'])
        for i, assertion in enumerate(plan.user_assertions):
            id_ = 'user_'+job_id+'_'+str(i)
            sources.append(dict(id=id_,team='사용자 정정',week=report['week'],mail_id=job_id,part_index=0,total_parts=1,text=payload['message'],source_kind='user'))
            facts.append(dict(id=id_,text=assertion,quote=assertion,section_ids=list(targets),source_id=id_,
                              week=report['week'],team='사용자 정정',source_kind='user'))
        sections = []
        for i, section in enumerate(report['sections']):
            if section['id'] in targets:
                spec = next(s for s in report['template']['sections'] if s['id']==section['id'])
                related = [f for f in facts if section['id'] in f['section_ids'] or section['id']=='events']
                self.store.update_job(job_id,progress=20+int(70*i/len(report['sections'])),message=f'{section["title"]} 수정 중')
                sections.append(self.write_section(job_id,spec,related,report,current=section,instruction=plan.instruction))
            else:
                sections.append(section)
        warnings = list(report['warnings'])
        if plan.user_assertions:
            warnings.append('사용자 정정값을 반영했습니다. 기존 원문은 변경하지 않았습니다.')
        # Shared evidence can make an untouched section stale; surface it for review rather than overwrite it.
        touched_ids = {ref for s in report['sections'] if s['id'] in targets for b in s['blocks'] for ref in b['evidence_ids']}
        shared = [s['title'] for s in report['sections'] if s['id'] not in targets and any(touched_ids.intersection(b['evidence_ids']) for b in s['blocks'])]
        if shared:
            warnings.append('같은 근거를 사용하는 항목도 확인하세요: '+', '.join(shared))
        messages.append(dict(role='assistant',content='요청한 내용을 반영했습니다. 변경된 항목을 확인하거나 이전 버전으로 되돌릴 수 있습니다.'))
        return dict(sections=sections,facts=facts,sources=sources,messages=messages,warnings=list(dict.fromkeys(warnings)))
