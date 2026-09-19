import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from studio.format_review import inspect_format, preserve_values, review_final


class FormatReviewTests(unittest.TestCase):
    def report(self):
        return dict(template=dict(writing_prompt='문장당 10자를 초과하지 않는다.',sections=[dict(id='a',group='수율',title='DRAM',instructions='')]),
                    sections=[dict(id='a',group='수율',title='DRAM',blocks=[dict(kind='bullet',text='DRAM 수율은 93.1%로 개선되었다.',evidence_ids=['f'])])],references=[])

    def test_explicit_sentence_limit_without_references(self):
        result=inspect_format(self.report(),20)
        self.assertTrue(any(i['code']=='sentence_length' for i in result['issues']))
        self.assertEqual(result['reference_count'],0)

    def test_dynamic_titles_are_not_compared_to_previous_fixed_topics(self):
        report=self.report()
        report['template']['writing_prompt']=''
        report['references']=[dict(name='old',text='중점 추진과제\nPSDI 개선 진행.')]
        result=inspect_format(report,20)
        self.assertFalse(any(i['code']=='structure' for i in result['issues']))

    def test_two_reference_reports_supply_mean_and_section_baseline(self):
        report=self.report()
        report['template']['writing_prompt']=''
        report['references']=[dict(text='DRAM\n'+'가'*100),dict(text='DRAM\n'+'나'*120)]
        report['sections'][0]['blocks'][0]['text']='다'*140
        result=inspect_format(report,20)
        self.assertEqual(result['reference_count'],2)
        self.assertEqual(result['reference_average'],110)
        self.assertEqual(result['sections'][0]['reference_average'],110)
        self.assertEqual({i['code'] for i in result['issues']},{'total_length','section_length'})

    def test_missing_section_and_table_are_flagged(self):
        report=self.report()
        report['template']['sections'].append(dict(id='b',group='품질',title='출하',instructions=''))
        report['sections'][0]['blocks'][0].update(kind='table')
        codes={i['code'] for i in inspect_format(report,20)['issues']}
        self.assertTrue({'structure','table'}.issubset(codes))

    def test_numeric_values_cannot_be_removed_by_shortening(self):
        before=[dict(text='CUM0 93.1%, 전주 92.3%, 3LOT',evidence_ids=['f'])]
        self.assertFalse(preserve_values(before,[dict(text='CUM0 93.1%',evidence_ids=['f'])]))
        self.assertTrue(preserve_values(before,[dict(text='3LOT: 92.3% → 93.1% CUM0',evidence_ids=['f'])]))

    def test_explicit_rules_override_historical_lengths(self):
        report=self.report()
        report['references']=[dict(text='DRAM\n'+'과거 상세 설명. '*50)]
        result=inspect_format(report,20)
        self.assertFalse(any(i['code'] in ('total_length','section_length') for i in result['issues']))
        self.assertTrue(any(i['code']=='sentence_length' for i in result['issues']))

    def harness(self, drop_number=False, unavailable=False):
        stages=[]
        def call(job,stage,system,payload,schema,*args):
            stages.append(stage)
            if stage=='final_style':
                if unavailable:
                    raise ValueError('model unavailable')
                return dict(issues=[])
            if stage=='format_repair':
                return dict(blocks=[dict(kind='bullet',text='개선.' if drop_number else 'DRAM93.1%.',evidence_ids=['f'])],warnings=[])
            if stage=='format_preservation':
                return dict(supported=True,issues=[])
            raise AssertionError(stage)
        return SimpleNamespace(settings=SimpleNamespace(format_tolerance_percent=20,format_auto_repair=True,max_input_bytes=40000),
                               call=call,check_cancel=Mock(),verify=Mock(),stages=stages)

    def test_repair_once_then_recheck_with_evidence(self):
        report=self.report()
        report['facts']=[dict(id='f',quote='DRAM 수율은 93.1%로 개선되었다.')]
        harness=self.harness()
        output=review_final(harness,'j',report)
        self.assertEqual(harness.stages.count('format_repair'),1)
        self.assertEqual(output['format_review']['status'],'passed')
        harness.verify.assert_called_once()
        self.assertEqual(output['format_review']['repaired_sections'],['a'])

    def test_value_loss_preserves_original_and_warns(self):
        report=self.report()
        report['facts']=[dict(id='f',quote='DRAM 93.1%')]
        harness=self.harness(drop_number=True)
        output=review_final(harness,'j',report)
        self.assertEqual(output['sections'],report['sections'])
        self.assertEqual(output['format_review']['status'],'needs_review')
        self.assertEqual(harness.stages.count('format_repair'),1)
        harness.verify.assert_not_called()

    def test_unavailable_review_does_not_fail_or_modify_draft(self):
        report=self.report()
        report['template']['writing_prompt']=''
        harness=self.harness(unavailable=True)
        output=review_final(harness,'j',report)
        self.assertEqual(output['sections'],report['sections'])
        self.assertEqual(output['format_review']['status'],'needs_review')
        self.assertNotIn('format_repair',harness.stages)

    def test_edit_scope_and_notification_only_mode_do_not_repair(self):
        report=self.report()
        for scope,enabled in [(set(),True),(None,False)]:
            harness=self.harness()
            harness.settings.format_auto_repair=enabled
            output=review_final(harness,'j',report,scope)
            self.assertEqual(output['sections'],report['sections'])
            self.assertNotIn('format_repair',harness.stages)
