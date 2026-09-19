"""Text format checks; page layout and factual verification are separate concerns."""
import re
import copy
from statistics import mean

from pydantic import Field
from .models import Model
from .models import SectionContent, Verification
from .llm import json_size
from . import prompts


class StyleIssue(Model):
    section_id: str
    reason: str = Field(min_length=1,max_length=500)


class StyleReview(Model):
    issues: list[StyleIssue] = Field(default_factory=list,max_length=30)


def body(section):
    return '\n'.join(b.get('text','') for b in section.get('blocks',[]))


def count(text):
    return len(re.sub(r'\s','',text))


def sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.!?。])\s+|\n+',text) if s.strip()]


def limit(rules, scope, unit):
    match=re.search(scope+r'[^\n.]{0,25}?(\d+)\s*'+unit+r'\s*(?:이내|이하|를?\s*초과하지)',rules)
    return int(match[1]) if match else None


def preserve_values(before, after):
    def values(blocks):
        return set(re.findall(r'\d+(?:[.,]\d+)*(?:%p|%|ppm|LOT|시간)?',' '.join(b['text'] for b in blocks)))
    old_ids={i for b in before for i in b['evidence_ids']}
    new_ids={i for b in after for i in b['evidence_ids']}
    return values(before).issubset(values(after)) and old_ids==new_ids


def reference_sections(text, specs):
    """Only use recognizable standalone headings; never guess where a section starts."""
    def heading(value):
        return re.sub(r'^[\s#*\d.()\-]+','',value).strip()
    titles={heading(s['title']):s['id'] for s in specs}
    groups={heading(s['group']) for s in specs}
    result={}
    active=None
    for line in text.splitlines():
        key=heading(line)
        if key in titles:
            active=titles[key]
            result.setdefault(active,[])
        elif key in groups:
            active=None
        elif re.match(r'^\s*(?:#{1,6}\s+|\d+(?:[-.]\d+)*[.)]?\s+)',line):
            active=None
        elif active:
            result[active].append(line)
    return {key:'\n'.join(lines) for key,lines in result.items()}


def inspect_format(report, tolerance):
    specs=report['template']['sections']
    sections=report.get('sections',[])
    common=report['template'].get('writing_prompt','')
    issues=[]
    def issue(code,section_id,message):
        issues.append(dict(code=code,section_id=section_id,message=message))
    if [s['id'] for s in specs]!=[s['id'] for s in sections]:
        issue('structure','', '현재 양식의 소주제 또는 순서와 본문이 다릅니다.')
    refs=report.get('references',[])
    # Exclude recognized heading lines consistently from the whole-document comparison.
    headings={s[k].strip() for s in specs for k in ('title','group')}
    lengths=[count('\n'.join(l for l in r['text'].splitlines() if l.strip() not in headings)) for r in refs]
    lengths=[n for n in lengths if n]
    average=round(mean(lengths)) if lengths else None
    current=sum(count(body(s)) for s in sections)
    maximum=limit(common,r'(?:전체|문서당)',r'자')
    explicit_lengths=any(re.search(r'(?:문장당|소주제당|섹션당|항목당)[^\n.]{0,30}(?:\d+|한두)\s*(?:자|문장)',rule)
                         for rule in [common]+[s.get('instructions','') for s in specs])
    if maximum and current>maximum:
        issue('total_length','',f'전체 본문 {current}자: 저장 규칙의 {maximum}자 상한 초과.')
    elif not maximum and not explicit_lengths and average and not average*(1-tolerance/100)<=current<=average*(1+tolerance/100):
        issue('total_length','',f'전체 본문 {current}자: 이전 주보 평균 {average}자 대비 ±{tolerance}% 범위 밖입니다.')
    profiles=[reference_sections(r['text'],specs) for r in refs]
    metrics=[]
    for section in sections:
        id_=section['id']
        spec=next((s for s in specs if s['id']==id_),{})
        rules=spec.get('instructions','')+'\n'+common
        text=body(section)
        size=count(text)
        section_max=limit(rules,r'(?:소주제|섹션|항목)당',r'자')
        sentence_max=limit(rules,r'문장당',r'자')
        number_max=limit(rules,r'(?:소주제|섹션|항목)당',r'문장')
        if re.search(r'(?:소주제|섹션|항목)당\s*한두\s*문장',rules):
            number_max=number_max or 2
        if spec and (section.get('title')!=spec['title'] or section.get('group')!=spec['group']):
            issue('structure',id_,'현재 양식과 제목 또는 대주제가 다릅니다.')
        if any(b['kind']=='table' for b in section.get('blocks',[])) or re.search(r'<table\b|\|\s*:?-{3,}:?\s*\|',text,re.I):
            issue('table',id_,'표 형식이 포함돼 있습니다.')
        if sentence_max and any(len(s)>sentence_max for s in sentences(text)):
            issue('sentence_length',id_,f'문장당 {sentence_max}자 상한을 초과했습니다.')
        if number_max and len(sentences(text))>number_max:
            issue('sentence_count',id_,f'소주제당 {number_max}문장 상한을 초과했습니다.')
        prior=[count(p[id_]) for p in profiles if p.get(id_)]
        prior_average=round(mean(prior)) if prior else None
        if section_max and size>section_max:
            issue('section_length',id_,f'소주제 {size}자: 저장 규칙의 {section_max}자 상한 초과.')
        elif not (section_max or sentence_max or number_max) and prior_average and not prior_average*(1-tolerance/100)<=size<=prior_average*(1+tolerance/100):
            issue('section_length',id_,f'소주제 {size}자: 이전 평균 {prior_average}자 대비 ±{tolerance}% 범위 밖입니다.')
        metrics.append(dict(section_id=id_,title=section['title'],characters=size,sentences=len(sentences(text)),reference_average=prior_average))
    return dict(issues=issues,characters=current,total_limit=maximum,reference_average=average,reference_count=len(lengths),
                tolerance_percent=tolerance,sections=metrics,
                comparison_note='공백 제외 본문 글자 수를 비교합니다. 이전 문서의 제목·날짜 등은 일부 포함될 수 있어 전체 비교는 근사치입니다. 소주제 비교는 제목을 식별한 항목만 수행합니다.')


def style_issues(harness, job_id, report, sections, instruction):
    issues=[]
    batches=[]
    batch=[]
    for section in sections:
        if batch and (len(batch)>=4 or json_size(batch+[section])>harness.settings.max_input_bytes//2):
            batches.append(batch); batch=[]
        batch.append(section)
    if batch:
        batches.append(batch)
    for batch in batches:
        ids={s['id'] for s in batch}
        def validate(value):
            if any(i['section_id'] not in ids for i in value['issues']):
                raise ValueError('형식 검토의 소주제 ID가 올바르지 않습니다.')
        try:
            result=harness.call(job_id,'final_style',prompts.BASE+'''
완성된 sections의 문체·글머리표 방식·숫자 단위 표기·불필요한 서론·중복 설명만 검토하세요.
최신 instruction, 소주제 instructions, writing_prompt, reference_style 순으로 표현 규칙을 우선하세요.
과거 문서의 수치나 사건은 요구하지 마세요. 중점 추진과제 제목은 매주 달라도 됩니다.
분량과 제목 순서는 코드가 검사하므로 중복 지적하지 마세요. 명시된 규칙 위반만 issues에 반환하세요.
적용할 문체 규칙이 없으면 임의 기준을 만들지 말고 issues=[]를 반환하세요.''',
                dict(sections=batch,writing_prompt=report['template'].get('writing_prompt',''),
                     reference_style=report.get('reference_style',[]),instruction=instruction),StyleReview,validate)
            issues.extend(dict(code='style',section_id=i['section_id'],message=i['reason']) for i in result['issues'])
        except ValueError:
            issues.append(dict(code='review_unavailable',section_id='',message='일부 문체 검토를 완료하지 못했습니다. 본문을 보존했으므로 직접 확인하세요.'))
    return issues


def review_final(harness, job_id, report, eligible=None, instruction=''):
    from .harness import validate_content
    result=inspect_format(report,harness.settings.format_tolerance_percent)
    result['issues']+=style_issues(harness,job_id,report,report['sections'],instruction)
    result['initial_issues']=copy.deepcopy(result['issues'])
    result['repaired_sections']=[]
    result['repair_notes']=[]
    if not harness.settings.format_auto_repair:
        result['status']='needs_review' if result['issues'] else 'passed'
        return dict(report,format_review=result)
    targets={i['section_id'] for i in result['issues'] if i['section_id'] and i['code'] not in ('structure',)}
    # A short report is never padded merely to match a historical length.
    target_length=result['total_limit'] or result['reference_average']
    if any(i['code']=='total_length' for i in result['issues']) and target_length and result['characters']>target_length:
        targets.update(s['id'] for s in sorted(report['sections'],key=lambda s:count(body(s)),reverse=True)[:3])
    if eligible is not None:
        targets.intersection_update(eligible)
    sections=copy.deepcopy(report['sections'])
    attempts=0
    for index,section in enumerate(sections):
        if section['id'] not in targets or attempts>=3 or not section.get('blocks') or not all(b['evidence_ids'] for b in section['blocks']):
            continue
        attempts+=1
        harness.check_cancel(job_id)
        ids={i for b in section['blocks'] for i in b['evidence_ids']}
        facts=[f for f in report['facts'] if f['id'] in ids]
        feedback=[i['message'] for i in result['issues'] if i['section_id'] in ('',section['id'])]
        try:
            revised=harness.call(job_id,'format_repair',prompts.EDIT+'''
형식만 한 번 보정하세요. current의 모든 제품·수치·기간·조건·조치·일정을 보존하세요.
근거 인용을 유지하고 반복 표현만 줄이세요. 짧다는 이유로 내용을 추가하지 마세요.
최신 사용자 instruction과 충돌하는 형식 보정은 하지 마세요.''',
                dict(current=section,facts=facts,feedback=feedback,instruction=instruction,
                     writing_prompt=report['template'].get('writing_prompt',''),reference_style=report.get('reference_style',[])),SectionContent)
            revised=validate_content(revised,facts)
            if not preserve_values(section['blocks'],revised['blocks']):
                raise ValueError('수치 또는 인용 누락')
            conservation=harness.call(job_id,'format_preservation',prompts.BASE+'''
before와 after가 모든 사실·제품·수치·조건·일정·조치·상태를 같은 의미로 보존하는지 양방향으로 검사하세요.
반복 표현만 생략할 수 있습니다. after가 사실을 추가하거나 before의 고유 사실을 누락하면 supported=false입니다.''',
                dict(before=section['blocks'],after=revised['blocks']),Verification)
            if not conservation['supported']:
                raise ValueError('의미 보존 실패')
            harness.verify(job_id,revised,facts)
            candidate=dict(section,**revised)
            sections[index]=candidate
            result['repaired_sections'].append(section['id'])
        except ValueError:
            result['repair_notes'].append(f"{section['title']}: 자동 보정을 적용하지 않고 검증 전 본문을 보존했습니다.")
    updated=dict(report,sections=sections)
    if result['repaired_sections']:
        checked=inspect_format(updated,harness.settings.format_tolerance_percent)
        # Cached unchanged sections avoid repeated style calls; only changed sections need a new review.
        old_styles=[i for i in result['issues'] if i['code'] in ('style','review_unavailable') and i['section_id'] not in result['repaired_sections']]
        changed=[s for s in sections if s['id'] in result['repaired_sections']]
        checked['issues']+=old_styles+style_issues(harness,job_id,updated,changed,instruction)
        result.update(checked)
    result['status']='needs_review' if result['issues'] else 'passed'
    return dict(updated,format_review=result)
