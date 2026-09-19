import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from studio.config import Settings
from studio.harness import Harness, EvidenceError
from studio.store import Store


class PerformanceTests(unittest.TestCase):
    def harness(self):
        harness=Harness.__new__(Harness)
        harness.settings=Settings()
        return harness

    def test_eight_blocks_need_two_verifications(self):
        harness=self.harness()
        calls=[]
        def call(job,stage,system,payload,schema):
            calls.append(payload)
            return dict(supported=True,issues=[])
        harness.call=call
        facts=[dict(id=str(i),quote=f'사실 {i}') for i in range(8)]
        blocks=[dict(text=f'사실 {i}',evidence_ids=[str(i)]) for i in range(8)]
        harness.verify('j',dict(blocks=blocks),facts)
        self.assertEqual([len(c['blocks']) for c in calls],[4,4])
        self.assertEqual({f['id'] for f in calls[0]['facts']},{'0','1','2','3'})

    def test_failed_group_checks_individuals_and_does_not_accept_bad_claim(self):
        harness=self.harness()
        calls=[]
        def call(job,stage,system,payload,schema):
            calls.append(payload)
            bad=any(b['text']=='허위' for b in payload['blocks'])
            return dict(supported=not bad,issues=['근거 없음'] if bad else [])
        harness.call=call
        with self.assertRaises(EvidenceError):
            harness.verify('j',dict(blocks=[dict(text=t,evidence_ids=['f']) for t in ['사실','허위']]),[dict(id='f',quote='사실')])
        self.assertEqual([len(c['blocks']) for c in calls],[2,1,1])

    def test_parallel_limit_and_order(self):
        harness=self.harness()
        lock=threading.Lock()
        barrier=threading.Barrier(2)
        active=0
        maximum=0
        def work(i):
            nonlocal active,maximum
            with lock:
                active+=1
                maximum=max(maximum,active)
            if i<2:
                barrier.wait(timeout=3)
            time.sleep(0.005)
            with lock:
                active-=1
            return i
        self.assertEqual(list(harness.parallel(work,range(10))),list(range(10)))
        self.assertEqual(maximum,2)

    def test_parallel_cache_and_metrics_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            report=store.create({})
            job=store.create_job(report['id'],'generate',dict(base_version=0))
            def write(i):
                store.cache_value(job['id'],str(i),i)
                store.record_call(job['id'],'verify',0.1,False,False)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(write,range(20)))
            saved=store.job(job['id'])
            self.assertEqual(len(saved['cache']),20)
            self.assertEqual(saved['metrics']['verify']['calls'],20)
            self.assertEqual(saved['metrics']['verify']['seconds'],2.0)
