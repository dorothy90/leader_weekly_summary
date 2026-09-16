from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class SectionSpec(Model):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    group: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    instructions: str = Field(default='', max_length=3000)


class Template(Model):
    name: str = Field(min_length=1, max_length=100)
    sections: list[SectionSpec] = Field(min_length=1, max_length=60)

    @model_validator(mode='after')
    def unique_ids(self):
        if len({s.id for s in self.sections}) != len(self.sections):
            raise ValueError('소주제 ID는 중복될 수 없습니다.')
        return self


def check_week(value):
    try:
        year, week = value.split('-')
        date.fromisocalendar(int(year), int(week), 1)
        if value != f'{int(year):04d}-{int(week):02d}':
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError('주차는 유효한 ISO 주차 YYYY-WW 형식이어야 합니다.')
    return value


class Reference(Model):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=200000)
    week: str
    _week = field_validator('week')(check_week)


class CreateReport(Model):
    week: str
    title: str = Field(default='그룹 주간보고', min_length=1, max_length=160)
    template: Template
    references: list[Reference] = Field(default_factory=list, max_length=2)
    expected_teams: list[str] = Field(default_factory=list, max_length=100)
    _week = field_validator('week')(check_week)

    @model_validator(mode='after')
    def previous_weeks(self):
        target = date.fromisocalendar(*map(int, self.week.split('-')), 1)
        seen = set()
        for ref in self.references:
            prior = date.fromisocalendar(*map(int, ref.week.split('-')), 1)
            if (target - prior).days not in (7, 14) or ref.week in seen:
                raise ValueError('참고 주보는 중복 없이 직전 1주·2주 그룹 주보를 등록해 주세요.')
            seen.add(ref.week)
        return self


class VersionRequest(Model):
    base_version: int = Field(ge=0)


class EditRequest(VersionRequest):
    message: str = Field(min_length=1, max_length=6000)
    section_id: str | None = None


class RestoreRequest(VersionRequest):
    target_version: int = Field(ge=0)


class Block(Model):
    kind: Literal['paragraph', 'bullet', 'table']
    text: str = Field(default='', max_length=6000)
    headers: list[str] = Field(default_factory=list, max_length=10)
    rows: list[list[str]] = Field(default_factory=list, max_length=100)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def table_shape(self):
        if self.kind == 'table':
            if not self.headers or any(len(r) != len(self.headers) for r in self.rows):
                raise ValueError('표의 제목과 행의 열 수가 일치해야 합니다.')
        elif not self.text:
            raise ValueError('문단 내용이 필요합니다.')
        return self


class SectionContent(Model):
    blocks: list[Block] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class ExtractedFact(Model):
    text: str = Field(min_length=1, max_length=2500)
    quote: str = Field(min_length=1, max_length=4000)
    section_ids: list[str] = Field(default_factory=list, max_length=60)


class Extraction(Model):
    facts: list[ExtractedFact] = Field(default_factory=list, max_length=100)


class LineFact(Model):
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)
    section_ids: list[str] = Field(default_factory=list, max_length=60)


class LineExtraction(Model):
    facts: list[LineFact] = Field(max_length=100)


class CandidateFact(Model):
    candidate_id: str = Field(min_length=1,max_length=80)
    section_ids: list[str] = Field(default_factory=list,max_length=60)


class CandidateExtraction(Model):
    facts: list[CandidateFact] = Field(max_length=100)


class EditPlan(Model):
    action: Literal['edit', 'clarify', 'template']
    section_ids: list[str] = Field(default_factory=list, max_length=60)
    instruction: str = Field(max_length=6000)
    question: str = Field(default='', max_length=1000)
    template: Template | None = None
    user_assertions: list[str] = Field(default_factory=list, max_length=10)


class Verification(Model):
    supported: bool
    issues: list[str] = Field(default_factory=list, max_length=20)


class EventSelection(Model):
    indices: list[int] = Field(max_length=3)


class ReferenceStyle(Model):
    rules: list[str] = Field(default_factory=list, max_length=5)
    @field_validator('rules')
    @classmethod
    def short_rules(cls, values):
        if any(len(s)>200 for s in values):
            raise ValueError('양식 참고 규칙은 각 200자 이내로 작성하세요.')
        return values


def default_template():
    groups = [
        ('0. 가장 중요한 이벤트 3건', [('events', '핵심 이벤트 3건')]),
        ('1. 양산수율(CUM0)', [('yield_all', 'DRAM / 우시 / NAND CUM0'), ('yield_hbm', 'HBM 제품별 CUM0'),
          ('yield_spica', 'Spica 제품별 CUM0'), ('yield_wuxi', '우시CP 제품별 CUM0'),
          ('yield_olympus', 'Olympus 제품별 CUM0'), ('yield_dalian', '대련 FAB 제품별 CUM0')]),
        ('2. 양산품질', [('quality_process', '공정품질'), ('quality_shipping', '출하품질'), ('quality_customer', '고객품질')]),
        ('3. 증산 TF 수율분과', [('tf_fab', 'FAB수율'), ('tf_dram', 'DRAM분과'), ('tf_nand', 'NAND분과')]),
        ('4. 개발제품수율 및 양산성', [('dev_proycon', 'Proycon'), ('dev_hbm4e', 'HBM4E'),
          ('dev_sp12g', 'SP 12G GD7'), ('dev_m15x', 'M15X 개발'), ('dev_heraion', 'Heraion')]),
        ('5. 중점 추진과제', [('task_psdi', 'PSDI'), ('task_wlqm', 'NAND WLQM 운영 최적화')]),
    ]
    sections = []
    for group, entries in groups:
        for id_, title in entries:
            rule = '금주 사실, 영향, 조치, 다음 일정 순서로 간결하게 작성. 원문에 없는 내용은 만들지 않는다.'
            if id_.startswith('yield_'):
                rule = '제품·FAB·집계기간을 구분하여 CUM0를 문장 또는 글머리표로 작성. 표 사용 금지. 공식 집계값만 사용하고 임의 평균 금지. 개선 또는 signal 1건을 근거가 있을 때만 작성.'
            if id_ == 'events':
                rule = '영향과 긴급성, 금주 변화가 큰 서로 다른 이벤트 최대 3건. 근거가 부족하면 건수를 억지로 채우지 않는다.'
            sections.append(dict(id=id_, group=group, title=title, instructions=rule+' 모든 내용은 텍스트로 작성하며 표를 사용하지 않는다.'))
    return dict(name='그룹 주보 기본 양식', sections=sections)
