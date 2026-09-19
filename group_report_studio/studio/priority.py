"""Weekly priorities are selected from current evidence, never fixed sample topics."""
PRIORITY_ID = 'priority_dynamic'
RULE = ('이번 주 전체 팀 원문에서 영향, 시급성, 주요 진척과 장애 요인을 기준으로 중요한 추진과제를 최대 3개 선정한다. '
        '과제명을 소주제로 사용하고 근거가 있는 현황·변화·후속 조치를 간결하게 보고한다. '
        '이전 주 과제명은 고정하지 않으며 원문에 없는 중요성이나 조치를 추정하지 않는다. '
        '모든 내용은 텍스트로 작성하며 표를 사용하지 않는다.')


def normalize_priority(template):
    result = dict(template)
    sections = []
    found = False
    for section in template['sections']:
        if '중점추진과제' in ''.join(section['group'].split()):
            if not found:
                sections.append(dict(id=PRIORITY_ID,group=section['group'],title='금주 중점 추진과제 자동 선정',instructions=RULE))
                found = True
        else:
            sections.append(dict(section))
    result['sections'] = sections
    return result
