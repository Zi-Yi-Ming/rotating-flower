"""Exercise the real bridge process with synthetic app data and no USB."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


class SoftwareSmokeTests(unittest.TestCase):
    def test_windows_imports_need_neither_posix_modules_nor_serial_dependency(self):
        # Import checks are simulated here; native Win32 behavior has separate
        # Windows-only tests. No app source or transport is started.
        script = '''
import importlib.abc, pathlib, subprocess, sys
class DenyPlatformModules(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fcntl', 'termios', 'serial'}:
            raise ImportError('Unexpected eager platform import: ' + fullname)
sys.meta_path.insert(0, DenyPlatformModules())
for name in ('fcntl', 'termios', 'serial'):
    sys.modules.pop(name, None)
sys.platform = 'win32'
import integrated_bridge, runtime_feed, workbuddy_adapter
assert runtime_feed.default_endpoint() == r'\\\\.\\pipe\\codex-ipc'
'''
        result = subprocess.run([sys.executable, '-c', script],
                                cwd=Path(__file__).parent, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_work_wait_finish_and_stop_in_a_real_process(self):
        with tempfile.TemporaryDirectory(prefix='小花 software smoke ') as directory:
            root = Path(directory)
            app = root / '应用 数据'
            (app / 'sessions').mkdir(parents=True)
            (app / 'logs').mkdir()
            database = app / 'workbuddy.db'
            with closing(sqlite3.connect(database)) as db:
                db.execute('CREATE TABLE sessions(id TEXT,status TEXT,updated_at INTEGER,'
                           'model TEXT,transport TEXT,deleted_at INTEGER)')
                db.commit()
            pid = os.getpid()  # The fixture worker is this live test process.
            (app / f'sessions/{pid}.json').write_text(json.dumps({
                'pid': pid, 'sessionId': 'fixture-task', 'startedAt': 1}), encoding='utf-8')
            status_file, stop_file = root / '状态.json', root / 'stop.request'
            log_path = root / 'bridge.log'

            def transition(state, kind='none'):
                now = time.time()
                with closing(sqlite3.connect(database)) as db:
                    db.execute('DELETE FROM sessions')
                    db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                               ('fixture-task', state, int(now * 1000), 'balanced-model', 'local', None))
                    db.commit()
                record = {'timestamp': datetime.fromtimestamp(now, timezone.utc).isoformat(),
                          'scope': 'daemon-server:task-notify', 'message': [
                              f'[TaskStatusNotifier] upsert received: session=fixture-task '
                              f'prev=working new={state} kind={kind}']}
                with (app / 'logs/daemon.log').open('a', encoding='utf-8') as output:
                    output.write(json.dumps(record) + '\n')

            def wait_for(child, motor, light):
                deadline = time.monotonic() + 10
                last = {}
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        self.fail('Bridge exited: ' + log_path.read_text(encoding='utf-8'))
                    try:
                        last = json.loads(status_file.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        pass
                    if last.get('motor') == motor and last.get('light') is light:
                        self.assertTrue(last['dry_run'])
                        self.assertEqual(last['device_count'], 0)
                        self.assertTrue(last['sources']['workbuddy']['healthy'])
                        return
                    time.sleep(.05)
                self.fail(f'Timed out waiting for {motor}/{light}: {last}')

            transition('working')
            with log_path.open('wb') as log:
                child = subprocess.Popen([
                    sys.executable, '-X', 'utf8', str(Path(__file__).with_name('integrated_bridge.py')),
                    '--source', 'workbuddy', '--workbuddy-root', str(app), '--dry-run',
                    # Never address a real port even if USB isolation regresses.
                    '--port', 'FLOWER_TEST_NO_DEVICE', '--status-file', str(status_file),
                    '--stop-file', str(stop_file), '--attention-cache', str(root / 'cache.json')],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                try:
                    wait_for(child, 'RUN HIGH', False)
                    transition('pending', 'question')
                    wait_for(child, 'STOP', True)
                    transition('completed')
                    wait_for(child, 'STOP', False)
                    stop_file.touch()
                    self.assertEqual(child.wait(timeout=10), 0)
                    self.assertFalse((root / 'cache.json').exists())
                finally:
                    if child.poll() is None:
                        stop_file.touch()
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            child.kill()  # Only our test-created subprocess.
                            child.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
