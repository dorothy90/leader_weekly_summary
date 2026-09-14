"""Reproducible synthetic inputs; does not modify application or original repo files."""
import json
import io
import sys
from pathlib import Path
from html import escape
from docx import Document
from docx.shared import Pt

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from studio.models import default_template, CreateReport
from studio.documents import export_docx, parse_reference

# Section, owner, metric, unit, W35/W36/W37, weekly observations, next action.
DATA = [
 ('yield_all','YIELD팀','DRAM / 우시 / NAND 공식 CUM0','%',[[93.1,91.2,90.4],[93.5,91.6,90.8],[93.9,91.8,91.3]],['DRAM 개선 조건 검증 착수','DRAM 조건 확대 후 CUM0 0.4%p 상승','DRAM·우시·NAND 모두 전주 대비 개선'],'제품별 투입량 가중 공식 집계 유지. 단순 평균 사용 금지'),
 ('yield_hbm','HBM수율팀','HBM3E 8H / HBM3E 12H CUM0','%',[[89.4,85.2],[90.1,86.0],[90.6,86.8]],['12H 적층 정렬 조건 평가','12H 정렬 조건 변경 3개 LOT 검증','12H CUM0 0.8%p 상승, 확대 LOT 추적 중'],'12H 확대 LOT 5개 재현성 확인'),
 ('yield_spica','Spica수율팀','Spica A / Spica B CUM0','%',[[92.0,89.8],[92.6,90.4],[93.2,91.1]],['Spica B edge 불량 원인 분석','세정 조건 변경 시험 3개 LOT 완료','Spica B CUM0 0.7%p 개선, edge 불량 감소 확인'],'Spica B 세정 조건 8개 LOT 확대'),
 ('yield_wuxi','우시CP팀','우시CP D1 / D2 CUM0','%',[[91.0,90.2],[91.5,90.6],[91.7,91.0]],['D2 contact 불량 집중 분석','probe 교체 후 contact 안정화','D2 0.4%p 개선, probe 2호기 접촉저항 상승 signal'],'probe 2호기 정비 후 확인 LOT 투입'),
 ('yield_olympus','Olympus수율팀','Olympus A / B CUM0','%',[[90.1,88.7],[90.5,89.2],[90.9,88.9]],['B 제품 etch 잔류물 관찰','챔버 세정 후 B 제품 개선','B 제품 0.3%p 하락, 잔류물 재발 signal'],'B 제품 영향 2개 LOT 보류 후 잔류물 재분석'),
 ('yield_dalian','대련FAB팀','대련 N1 / N2 CUM0','%',[[89.2,87.8],[89.6,88.2],[90.2,88.7]],['N2 WL 저항 산포 개선 DOE 착수','조건 B의 WL 저항 산포 감소','N2 CUM0 0.5%p 개선, 온도 조건 효과 확인'],'조건 B 확대 4개 LOT 평가'),
 ('quality_process','공정품질팀','SPC 이상 건수','건',[[8],[6],[4]],['etch 이상 4건 집중 관리','미종결 이상 2건 원인 분석','신규 SPC 이상 4건 중 3건 조치, 1건 추적'],'미종결 1건의 계측기 교차 검증'),
 ('quality_shipping','출하품질팀','출하검사 불량률','ppm',[[42],[35],[28]],['검사 threshold 재현성 확인','추가 검사 적용 후 35ppm','28ppm으로 개선, 보류 LOT 2개 검사 중'],'보류 LOT 2개 재검 완료 후 출하 판단'),
 ('quality_customer','고객품질팀','고객 불량 접수','건',[[3],[2],[1]],['고객 A사의 HBM3E 온도시험 불량 접수','고객 A사 이슈 재현 완료, 원인 분석 중','고객 A사 HBM3E 고온 불량 1건 지속, 출하 보류 2개 LOT 유지'],'고객 A사 8D 중간보고 제출 및 패키지 분석 완료'),
 ('tf_fab','FAB증산TF팀','증산 검증 LOT 통과','%',[[82],[86],[90]],['검증 50개 LOT 중 41개 통과','검증 50개 LOT 중 43개 통과','검증 50개 LOT 중 45개 통과, 미통과 5개 원인 분류'],'미통과 5개 LOT 재평가'),
 ('tf_dram','DRAM증산팀','DRAM 증산 구간 CUM0','%',[[91.8],[92.2],[92.7]],['병목 공정 recipe 매칭 착수','매칭 완료 장비 3대 양산 평가','증산 구간 0.5%p 개선, 월 2천장 확대 검증 중'],'매칭 장비 2대 추가 검증'),
 ('tf_nand','NAND증산팀','NAND 증산 구간 CUM0','%',[[88.4],[89.0],[89.5]],['etch queue time 민감도 분석','queue time 상한 시범 적용','증산 구간 0.5%p 개선, 상한 초과 LOT 2개 추적'],'대기시간 상한 적용 범위 확대'),
 ('dev_proycon','Proycon개발팀','Proycon 개발 CUM0','%',[[65.0],[68.0],[71.5]],['split A 기초 특성 평가','split B의 누설전류 개선 확인','개발 CUM0 3.5%p 개선, 양산 기준 75% 미달'],'split B 신뢰성 평가 3개 LOT 진행'),
 ('dev_hbm4e','HBM4E개발팀','HBM4E 개발 CUM0','%',[[58.0],[62.5],[67.0]],['적층 정렬 margin 부족 확인','개선 조건 2개 LOT 평가 완료','개발 CUM0 4.5%p 개선, 열시험 검증 미완료'],'열시험 완료 전 양산성 판정 보류'),
 ('dev_sp12g','GD7개발팀','SP 12G GD7 개발 CUM0','%',[[70.2],[72.1],[74.3]],['고속 특성 fail 분포 분석','전압 조건 최적화 진행','2.2%p 개선, 고속 corner 추가 확인 필요'],'고속 corner 3조건 재시험'),
 ('dev_m15x','M15X개발팀','M15X 개발 CUM0','%',[[61.0],[64.2],[66.8]],['장비 간 산포 비교 착수','장비 matching 2대 완료','2.6%p 개선, 신규 장비 1대 matching 미완료'],'신규 장비 matching 후 양산성 검토'),
 ('dev_heraion','Heraion개발팀','Heraion 개발 CUM0','%',[[63.4],[65.0],[64.6]],['WL 편차 관련 fail 분석','조건 변경 후 개선 추세','0.4%p 하락, WL 단선 재발 2개 LOT'],'WL 단선 위치 분석 및 DOE 재설계'),
 ('task_psdi','PSDI팀','PSDI 자동 분류 적용률','%',[[60],[68],[76]],['분류 규칙 12개 정비','추가 8개 규칙 현장 확인','적용률 8%p 상승, 오분류 3건 보완 중'],'오분류 3건 규칙 수정 후 재검증'),
 ('task_wlqm','WLQM팀','NAND WLQM 평균 대기시간','시간',[[5.2],[4.6],[4.1]],['중복 계측 흐름 분석','우선순위 규칙 시범 운영','평균 대기시간 0.5시간 단축, P95 8.2시간'],'야간 대기시간 상위 10개 LOT 흐름 개선'),
]

SUPPORT = [
 ('PHOTO팀','dev_proycon','Photo CD 산포 2.8nm→2.4nm, split B와 동일 LOT 사용','노광 dose 3수준 검증'),
 ('ETCH팀','yield_olympus','Olympus B 잔류물 재발 LOT 2개에서 chamber 04 공통 이력 확인','chamber 04 추가 세정과 단면 분석'),
 ('CVD팀','dev_m15x','M15X 막두께 균일도 2.1%→1.8%, 신규 장비 검증 중','신규 장비 1대 온도 profile 조정'),
 ('CMP팀','yield_spica','Spica B dishing 18nm→15nm, 세정 개선 LOT과 구분 관리','pad 수명 말기 조건 검증'),
 ('DIFF팀','yield_dalian','대련 N2 열처리 온도 편차 1.2℃→0.8℃','조건 B 확대 LOT 4개 온도 이력 추적'),
 ('IMPLANT팀','dev_heraion','Heraion dose 편차 1.5%→1.3%, WL 단선과 인과 미확정','dose와 WL 단선 상관 재분석'),
 ('METROLOGY팀','quality_process','SPC 이상 미종결 1건의 계측기 편차 0.6nm 관찰','기준장비 교차 측정 30점 수행'),
 ('DEFECT팀','yield_spica','Spica B edge defect 밀도 0.28→0.21개/cm²','확대 LOT edge 분포와 CUM0 동시 추적'),
 ('EQUIP팀','tf_fab','증산 대상 장비 가동률 88.0%→90.5%, etch 1대 정비 중','etch 정비 후 3개 LOT 검증'),
 ('PI팀','dev_proycon','Proycon split B 누설전류 fail 비율 8.2%→6.1%','신뢰성 결과 확보 후 공정 조건 동결 검토'),
 ('TEST팀','dev_sp12g','GD7 고속 corner fail 5.4%→4.0%, 상온 기준','고온 corner 추가 시험'),
 ('PACKAGE팀','quality_customer','고객 A사 HBM3E 고온 불량 시료 5개 단면 분석 중, 원인 미확정','계면 박리 유무 확인 후 8D 반영'),
 ('RELIABILITY팀','dev_hbm4e','HBM4E 열시험 500시간 중 300시간 완료, 중간 신규 fail 0개','500시간 완료 후 판정'),
 ('FA팀','quality_customer','고객 A사 HBM3E 재현 시료 2개 확보, 전원 패턴 민감도 관찰','전원 조건별 재현 시험'),
 ('생산관리팀','tf_nand','NAND queue time 상한 초과 LOT 5개→2개','초과 LOT 병목 공정 우선 배정'),
 ('DATA팀','task_psdi','PSDI 오분류 3건의 라벨 재검토 완료, 2건 규칙 충돌 확인','규칙 충돌 제거 후 검증셋 평가'),
 ('AUTOMATION팀','task_wlqm','WLQM 야간 작업 자동 배정률 64%→72%','상위 대기 LOT 10개 배정 규칙 점검'),
]

def blocks(row, period):
    sid, team, metric, unit, values, notes, action = row
    names = metric.replace(' 공식 CUM0','').replace(' CUM0','').split(' / ')
    result = []
    for i,(name,v) in enumerate(zip(names,values[period])):
        text=f'{name}: 금주 {v:g}{unit}.'
        if period:
            delta=v-values[period-1][i]
            text+=f' 전주 {values[period-1][i]:g}{unit} 대비 {delta:+.1f}'+('%p' if unit=='%' else unit)+'.'
        else:
            text+=' 전주 수치는 미제공.'
        result.append(dict(kind='bullet',text=text))
    result.append(dict(kind='paragraph',text=f'{notes[period]}. 담당 {team}.'))
    historical_plan = f'{metric} 원인 분석 및 개선 조건 검증' if period == 0 else f'{metric} 확대 평가 및 미종결 이슈 추적'
    result.append(dict(kind='bullet',text=f'차주 계획: {historical_plan if period < 2 else action}.'))
    return result

def main():
    ROOT.mkdir(exist_ok=True)
    template = default_template()
    references=[]
    for period,week in enumerate(['2026-35','2026-36']):
        sections=[]
        events = [DATA[i][5][period] for i in [2,8,13]]
        for spec in template['sections']:
            content = ([dict(kind='paragraph',text='웹 주보 작성 시험을 위한 가상 그룹 주보입니다. 모든 수치·팀 활동은 더미 데이터입니다.'),
                        *[dict(kind='bullet',text=t) for t in events]] if spec['id']=='events' else
                       blocks(next(r for r in DATA if r[0]==spec['id']),period))
            sections.append(dict(spec,blocks=content))
        report=dict(title='테스트 데이터 그룹 주간보고',week=week,version=1,sections=sections)
        doc=Document(io.BytesIO(export_docx(report)))
        doc.styles['Normal'].font.size=Pt(10)
        doc.styles['Normal'].paragraph_format.space_after=Pt(4)
        doc.styles['Title'].font.size=Pt(20)
        for style_name in ['Heading 1','Heading 2']:
            doc.styles[style_name].paragraph_format.space_before=Pt(9)
            doc.styles[style_name].paragraph_format.space_after=Pt(5)
        for p in doc.paragraphs:
            if p.text and p.style.name == 'Normal':
                p.paragraph_format.keep_with_next=True
        stream=io.BytesIO();doc.save(stream);content=stream.getvalue()
        path=ROOT/f'그룹주보_{week}_더미.docx'; path.write_bytes(content)
        text=parse_reference(path.name,content)
        (ROOT/f'그룹주보_{week}_더미.txt').write_text(text,encoding='utf-8')
        references.append(dict(name=path.name,week=week,text=text))
    docs=[]
    team_records=[]
    for row in DATA:
        sid,team,metric,unit,values,notes,action=row
        main_text=f'담당 소주제: {sid}\n{metric}: '+', '.join(f'{x:g}{unit}' for x in values[2])+'\n전주 동일 기준: '+', '.join(f'{x:g}{unit}' for x in values[1])+f'\n{notes[2]}.'
        detail=f'차주 계획: {action}. 완료 목표 2026-09-18.\n집계 범위: {metric}. 개발 지표와 양산 지표는 별도 관리하며 팀별 수치를 임의 평균하지 않는다.'
        team_records.append((team,[main_text,detail]))
    for team,sid,fact,action in SUPPORT:
        team_records.append((team,[f'담당 소주제: {sid}\n금주 결과: {fact}.',f'차주 계획: {action}. 완료 목표 2026-09-18.\n관련 주관팀과 동일 이슈를 추적하며 지원팀 보고를 별도 신규 사건으로 중복 집계하지 않는다.']))
    for n,(team,parts) in enumerate(team_records,1):
        mail_id=f'demo_gr_2026_37_team_{n:02d}'
        folder=ROOT/'teams'/team; folder.mkdir(parents=True,exist_ok=True)
        prefix=f'[더미 데이터] {team} 2026-37 주간보고\n집계기간 2026-09-07~2026-09-13. 아래 내용은 모두 가상 테스트 데이터입니다.\n'
        text=prefix+'\n\n'.join(parts)
        (folder/'weekly_report.txt').write_text(text,encoding='utf-8')
        (folder/'body.html').write_text('<!doctype html><meta charset="utf-8"><title>더미 팀별 주보</title><pre>'+escape(text)+'</pre>',encoding='utf-8')
        for part_index,part in enumerate(parts):
            source=dict(text=prefix+part,type='original_part',team=team,week='2026-37',mail_id=mail_id,
                        html_path=str((folder/'body.html').relative_to(ROOT.parent.parent)),part_index=part_index,total_parts=2,
                        subject=f'[더미][주보] {team} 2026-37',mail_type='weekly_report')
            docs.append(dict(_index='weekly_mail',_id=f'{mail_id}_part_{part_index}',_source=source))
    (ROOT/'team_documents.json').write_text(json.dumps(docs,ensure_ascii=False,indent=2),encoding='utf-8')
    teams=[t for t,_ in team_records]
    (ROOT/'teams.txt').write_text('\n'.join(teams),encoding='utf-8')
    payload=dict(week='2026-37',title='[더미] 그룹 주간보고 2026-37',template=template,references=references,expected_teams=teams)
    CreateReport.model_validate(payload)
    (ROOT/'create_report.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    assert len(teams)==36 and len(set(teams))==36 and len(docs)==72
    print('Created 2 reference DOCX files, 36 team reports, 72 source parts, validated web input JSON.')

if __name__=='__main__':
    main()
