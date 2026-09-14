import tempfile
import unittest
from pathlib import Path


class CoreTests(unittest.TestCase):
    def test_versions_conflict_restore_and_survive_restart(self):
        from studio.store import Store, Conflict
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'studio.db'
            store = Store(path)
            report = store.create({'title': '그룹 주보', 'week': '2026-01', 'template': {}, 'sections': []})
            first = store.save(report['id'], 0, {'sections': [{'id': 's', 'blocks': []}]}, '초안')
            self.assertEqual(first['version'], 1)
            with self.assertRaises(Conflict):
                store.save(report['id'], 0, {'title': '덮어쓰기'}, '잘못된 저장')
            restored = store.restore(report['id'], 1, 0)
            self.assertEqual(restored['version'], 2)
            self.assertEqual(restored['sections'], [])
            self.assertEqual(Store(path).get(report['id'])['version'], 2)
            self.assertEqual(store.get(report['id'], 1)['sections'][0]['id'], 's')

    def test_source_parts_and_batches_preserve_all_text(self):
        from studio.source import audit_sources, split_source
        parts = [dict(id='a',team='A',week='2026-01',mail_id='m',part_index=0,total_parts=3,text='가나다'*100),
                 dict(id='b',team='A',week='2026-01',mail_id='m',part_index=2,total_parts=3,text='끝')]
        coverage, warnings = audit_sources(parts, ['A', 'B'])
        self.assertEqual(coverage['missing_teams'], ['B'])
        self.assertTrue(any('1' in w and '누락' in w for w in warnings))
        chunks = split_source(parts[0], 70)
        self.assertEqual(''.join(c['text'] for c in chunks), parts[0]['text'])
        self.assertTrue(all(len(c['text'].encode('utf-8')) <= 70 for c in chunks))

    def test_schema_rejects_duplicate_sections_and_wrong_week(self):
        from studio.models import CreateReport, Template
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Template(name='t', sections=[dict(id='a',group='1',title='a'),dict(id='a',group='1',title='b')])
        with self.assertRaises(ValidationError):
            CreateReport(week='2026-99', title='x', template={'name':'t','sections':[{'id':'s','group':'1','title':'s'}]})


if __name__ == '__main__':
    unittest.main()
