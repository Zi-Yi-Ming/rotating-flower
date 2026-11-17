import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dsh_adapter import DshAdapter, speed_for_effort
from integrated_bridge import combine_sources
from source_config import enabled_sources, selection_for
from platform_support import open_read_shared

class DshWindowsReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'status.json'
        self.adapter = DshAdapter(self.path)

    def state(self, tasks=(), **changes):
        d = dict(schema=1, instance='test', pid=os.getpid(), updated_at=100, connected=True, error=None, tasks=list(tasks) if tasks is not None else None)
        d.update(changes)
        self.path.write_text(json.dumps(d))
        return self.adapter.poll(100)

    def task(self, **kw):
        t=dict(id='task-a', running=True, blocking=False, effort='max', questions=0, approvals=0)
        t.update(kw)
        return t

    def test_heartbeat_can_be_replaced_after_read_before_decode(self):
        self.state([self.task()])
        newer = json.loads(self.path.read_text())
        newer['tasks'] = []
        replacement = self.path.with_suffix('.tmp')
        replacement.write_text(json.dumps(newer))
        decode = json.loads

        def replace_before_decode(raw):
            # poll must close the OS handle before decoding its snapshot.
            replacement.replace(self.path)
            return decode(raw)

        with patch('dsh_adapter.json.loads', side_effect=replace_before_decode):
            snapshot = self.adapter.poll(100)
        self.assertTrue(snapshot['healthy'])
        self.assertEqual(snapshot['motor'], 'RUN ULTRA')
        self.assertEqual(self.adapter.poll(100)['motor'], 'STOP')

    def test_transient_read_conflict_recovers_on_next_poll(self):
        self.state([self.task()])
        with patch('dsh_adapter.open_read_shared', side_effect=PermissionError('writer replacing')):
            snapshot = self.adapter.poll(100)
        self.assertFalse(snapshot['healthy'])
        self.assertEqual(snapshot['motor'], 'STOP')
        self.assertFalse(snapshot['light'])
        self.assertEqual(self.adapter.poll(100)['motor'], 'RUN ULTRA')

    def test_idle_pending_question(self):
        s=self.state([self.task(running=False,questions=1)]);self.assertEqual(s['motor'],'STOP');self.assertTrue(s['light'])

    def test_other_task_continues(self):
        s=self.state([self.task(blocking=True,questions=1),self.task(id='task-b',effort='low')]);self.assertEqual(s['motor'],'RUN MEDIUM');self.assertTrue(s['light'])

    def test_bad_metadata(self):
        for change in ({'schema':2},{'tasks':None},{'pid':True},{'updated_at':float('nan')},{'error':'incompatible'}):
            self.assertEqual(self.state(**change)['readiness'],'error')
        for change in ({'questions':-1},{'approvals':True},{'running':'yes'},{'effort':{}},{'blocking':0}):
            self.assertEqual(self.state([self.task(**change)])['readiness'],'error')

    def test_selections(self):
        for names in (('dsh',),('codex','dsh'),('workbuddy','dsh'),('codex','workbuddy','dsh')):
            self.assertEqual(enabled_sources(selection_for(names)),names)
        self.assertEqual(enabled_sources('both'),('codex','workbuddy'))
